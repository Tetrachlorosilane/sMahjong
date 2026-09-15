#!/usr/bin/env node
// 通过 GitHub REST API 把本地提交推上去（Git Data API：blobs → tree → commit → ref）。
//
// 为什么需要这个脚本：
//   有些网络环境里 **github.com:443 被拦**（`git push` 连不上、`git ls-remote` 也超时），
//   但 **api.github.com 是通的**。这时 `git push` 无论如何都推不上去，改走 REST API 就行 ——
//   本仓库的首次推送就是这么完成的（实测：node fetch api.github.com → 200，github.com → fetch failed）。
//
// 为什么不让脚本自己调 git：
//   受限沙箱里子进程的管道（stdio: 'pipe'）会被拒（EPERM），Node 拿不到 `git` 的输出。
//   所以改为读一份由 PowerShell 预先生成的**清单文件**（含 mode/sha/path 与提交元信息），
//   文件内容则直接从磁盘读 —— 脚本本身不依赖 git 可执行文件。
//
// 用法：
//   node tools/push-github-api.mjs --manifest .qt/push-manifest.json [--dry-run]
//
// token 来源（按优先级，均不会被打印到输出里）：
//   --token <值>  →  $GITHUB_TOKEN  →  $GH_TOKEN  →  DSH 插件状态文件
//   （默认 <用户目录>/.dsh/storages/dsh-github/state.json 里的 token 字段）
//
// 完整性保证：每个文件上传后都会核对返回的 blob SHA 与本地 git 索引里的 SHA 一致
//   （Git blob SHA = sha1("blob <字节数>\0" + 内容)，是确定性的），
//   最后再用 tree 接口回读全树逐条比对 —— 少一个文件、内容差一个字节都会被发现。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

const API_VERSION = '2022-11-28';
const CONCURRENCY = 6;

// ---------------------------------------------------------------- 参数解析
const argv = process.argv.slice(2);
const opt = { manifest: '', dryRun: false, token: '', branch: '', repo: '' };
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--manifest') opt.manifest = argv[++i] || '';
  else if (a === '--token') opt.token = argv[++i] || '';
  else if (a === '--branch') opt.branch = argv[++i] || '';
  else if (a === '--repo') opt.repo = argv[++i] || '';
  else if (a === '--dry-run') opt.dryRun = true;
  else if (a === '-h' || a === '--help') {
    console.log('用法: node tools/push-github-api.mjs --manifest <清单.json> [--dry-run]');
    process.exit(0);
  } else die(`未知参数 ${a}`);
}
if (!opt.manifest) die('缺少 --manifest');

function die(msg) { process.stderr.write(`错误：${msg}\n`); process.exit(1); }
function log(msg) { process.stdout.write(`${msg}\n`); }

// ---------------------------------------------------------------- token 解析
function resolveToken() {
  if (opt.token) return opt.token;
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN;
  if (process.env.GH_TOKEN) return process.env.GH_TOKEN;
  const dsh = path.join(os.homedir(), '.dsh', 'storages', 'dsh-github', 'state.json');
  if (fs.existsSync(dsh)) {
    try {
      const st = JSON.parse(fs.readFileSync(dsh, 'utf8'));
      if (st && typeof st.token === 'string' && st.token) return st.token;
    } catch { /* 忽略：下面统一报错 */ }
  }
  die('找不到 token。请用 --token 传入，或设 GITHUB_TOKEN / GH_TOKEN');
}

const TOKEN = resolveToken();
// ⚠ 只报告长度与来源，绝不回显内容
log(`token: 已加载（长度 ${TOKEN.length}，不会打印）`);

// ---------------------------------------------------------------- 读清单
const manifestPath = path.resolve(opt.manifest);
if (!fs.existsSync(manifestPath)) die(`清单不存在：${manifestPath}`);
const m = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const repo = opt.repo || m.repo;
const branch = opt.branch || m.branch || 'main';
if (!repo || !repo.includes('/')) die('清单里缺少 repo（owner/name）');
if (!Array.isArray(m.entries) || m.entries.length === 0) die('清单里没有文件条目');
log(`仓库: ${repo}   分支: ${branch}   文件: ${m.entries.length} 个`);

// ---------------------------------------------------------------- HTTP 封装
async function api(apiPath, method = 'GET', body) {
  const url = `https://api.github.com${apiPath}`;
  for (let attempt = 1; attempt <= 4; attempt++) {
    let res;
    try {
      res = await fetch(url, {
        method,
        headers: {
          authorization: `Bearer ${TOKEN}`,
          accept: 'application/vnd.github+json',
          'x-github-api-version': API_VERSION,
          'content-type': 'application/json',
          'user-agent': 'sMahjong-api-push/1.0',
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      if (attempt === 4) die(`${method} ${apiPath} 网络失败：${e.message}`);
      await sleep(500 * attempt);
      continue;
    }
    if (res.status === 429 || (res.status >= 500 && res.status !== 501)) {
      const wait = Number(res.headers.get('retry-after') || 0) * 1000 || 800 * attempt;
      log(`  ${res.status} 重试中（${Math.round(wait / 1000)}s）…`);
      await sleep(wait);
      continue;
    }
    const text = await res.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch { /* 非 JSON */ }
    if (!res.ok) {
      const detail = json && json.message ? json.message : text.slice(0, 300);
      const err = new Error(`${method} ${apiPath} → HTTP ${res.status}：${detail}`);
      err.status = res.status;
      err.body = json;
      throw err;
    }
    return json;
  }
  die(`${method} ${apiPath} 重试次数用尽`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// git 默认（core.quotePath=true）对**含非 ASCII 的路径**会给两样东西：
//   1. 外面加一对双引号：   "docs/\346\227\245..."
//   2. 非 ASCII 字节按八进制转义（见下）
// 两个都必须处理，否则路径会带着引号（表现为 existsSync 永远 false —— 踩过）。
// 引号内还可能出现 C 风格转义（\t \n \" \\），一并还原。
function decodeGitPath(p) {
  let s = p;
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    s = s.slice(1, -1).replace(/\\(["\\])/g, '$1').replace(/\\t/g, '\t').replace(/\\n/g, '\n');
  }
  if (!/\\[0-7]{3}/.test(s)) return s;
  const bytes = [];
  for (let i = 0; i < s.length; i++) {
    const m = /^\\([0-7]{3})/.exec(s.slice(i));
    if (m) {
      bytes.push(parseInt(m[1], 8));
      i += 3;
    } else {
      for (const b of Buffer.from(s[i], 'utf8')) bytes.push(b);
    }
  }
  return Buffer.from(bytes).toString('utf8');
}

// git 的 blob 哈希：sha1("blob <长度>\0" + 内容)
function gitBlobSha(buf) {
  return crypto.createHash('sha1').update(`blob ${buf.length}\0`, 'utf8').update(buf).digest('hex');
}

// ---------------------------------------------------------------- 主流程
const root = m.root || process.cwd();
let decoded = 0;
for (const e of m.entries) {
  const real = decodeGitPath(e.path);
  if (real !== e.path) decoded++;
  e.path = real;
}
if (decoded) log(`  已还原 ${decoded} 个被 git 八进制转义的非 ASCII 路径`);
log(`工作目录: ${root}`);

// 1) 逐个文件核对本地内容与 git 索引一致，然后上传 blob
const entries = [];
let uploaded = 0;
let cursor = 0;

async function worker() {
  while (true) {
    const i = cursor++;
    if (i >= m.entries.length) return;
    const e = m.entries[i];
    const abs = path.join(root, e.path);
    if (!fs.existsSync(abs)) die(`文件不存在：${e.path}`);
    const buf = fs.readFileSync(abs);
    const localSha = gitBlobSha(buf);
    if (localSha !== e.sha) {
      die(`本地内容与 git 索引不一致：${e.path}\n  索引 ${e.sha}\n  实际 ${localSha}`);
    }
    if (!opt.dryRun) {
      const res = await api(`/repos/${repo}/git/blobs`, 'POST', {
        content: buf.toString('base64'),
        encoding: 'base64',
      });
      if (!res || res.sha !== localSha) {
        die(`blob 校验失败：${e.path}（返回 ${res && res.sha}，期望 ${localSha}）`);
      }
    }
    entries[i] = { path: e.path, mode: e.mode || '100644', type: 'blob', sha: localSha };
    if (++uploaded % 20 === 0 || uploaded === m.entries.length) {
      log(`  ${opt.dryRun ? '已校验' : '已上传'} ${uploaded}/${m.entries.length} 个文件`);
    }
  }
}
await Promise.all(Array.from({ length: CONCURRENCY }, worker));
if (opt.dryRun) {
  log(`[dry-run] 本地校验通过：${entries.length} 个文件的 blob SHA 与 git 索引一致，未做任何上传`);
  process.exit(0);
}

// 2) 建树（tree 接口允许直接给带斜杠的完整路径，一次调用即可）
log('创建 tree…');
const tree = await api(`/repos/${repo}/git/trees`, 'POST', { tree: entries });
if (m.tree && tree.sha !== m.tree) {
  log(`  ⚠ tree SHA 与本地 HEAD^{tree} 不同（远端 ${tree.sha}，本地 ${m.tree}）——继续，稍后按内容校验`);
} else {
  log(`  tree = ${tree.sha}${m.tree ? '（与本地 HEAD^{tree} 一致）' : ''}`);
}

// 3) 建提交
//    ⚠ 清单里的 message 可能是**字符串数组**：PowerShell 把原生命令的多行输出按行拆开，
//      直接 ConvertTo-Json 就成了 JSON 数组（GitHub 会回 422 "'message' is not a string"）。
//      这里统一按 \n 拼回字符串。用 -join "\n" 而不是 filter，末尾那个空元素正好还原
//      提交信息结尾的换行（SHA 才能与本地一致）。
if (Array.isArray(m.message)) m.message = m.message.join('\n');
if (typeof m.message !== 'string' || !m.message) die('清单里的 message 不是有效字符串');
log('创建 commit…');
const commit = await api(`/repos/${repo}/git/commits`, 'POST', {
  message: m.message,
  tree: tree.sha,
  parents: m.parents || [],
  author: m.author,
  committer: m.committer,
});
log(`  commit = ${commit.sha}${m.commit ? (commit.sha === m.commit ? '（与本地提交一致）' : `（本地 ${m.commit}）`) : ''}`);

// 4) 建/更新分支引用
log(`更新 refs/heads/${branch}…`);
try {
  await api(`/repos/${repo}/git/refs`, 'POST', { ref: `refs/heads/${branch}`, sha: commit.sha });
  log('  已创建分支引用');
} catch (e) {
  if (e.status === 422 || e.status === 409) {
    await api(`/repos/${repo}/git/refs/heads/${branch}`, 'PATCH', { sha: commit.sha, force: false });
    log('  已更新分支引用');
  } else throw e;
}

// 5) 回读全树逐条比对（最终体检：文件数、路径、内容三者都要对得上）
log('回读远端全树校验…');
const remote = await api(`/repos/${repo}/git/trees/${commit.sha}?recursive=1`);
const remoteMap = new Map();
for (const t of remote.tree) if (t.type === 'blob') remoteMap.set(t.path, t.sha);
let bad = 0;
for (const e of entries) {
  const got = remoteMap.get(e.path);
  if (got !== e.sha) {
    bad++;
    if (bad <= 10) log(`  ✘ ${e.path}：远端 ${got || '(缺失)'}，期望 ${e.sha}`);
  }
}
const extra = [...remoteMap.keys()].filter((p) => !entries.some((e) => e.path === p));
if (extra.length) log(`  ⚠ 远端多出 ${extra.length} 个文件：${extra.slice(0, 5).join(', ')}`);
log(`  远端 blob 数：${remoteMap.size}，期望 ${entries.length}，不一致 ${bad} 个`);
if (bad > 0 || extra.length > 0) die('推送后校验未通过');

log('');
log(`✔ 推送完成：https://github.com/${repo}/tree/${branch}`);
log(`  提交：https://github.com/${repo}/commit/${commit.sha}`);
log(`  分支：${branch}   文件：${entries.length}   提交对象：${commit.sha}`);
