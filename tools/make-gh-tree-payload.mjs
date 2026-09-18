#!/usr/bin/env node
/**
 * 为「无 git push」的环境生成 GitHub API 请求体：**先建 blob、再引用 sha**。
 *
 *   node tools/make-gh-tree-payload.mjs <remote-tree.json> <outdir> <tracked-files.txt>
 *
 * ## 为什么不用 `POST /git/trees` 的 `content` 字段
 *
 * 踩过：用 tree 的内联 `content`（base64）时，服务端对**文本文件**做了换行/转码处理，
 * 结果**存进仓库的是那段 base64 文本本身**（抓回 blob 解码一次才等于源文件，
 * 体积恰好 4/3），而二进制文件有的过、有的不过（实测 `.otf` 对、`.wav` 不对）。
 * 改用官方的两步路径就没这个问题：
 *
 *   1. `POST /git/blobs`        {content: <base64>, encoding: "base64"} → sha
 *   2. `POST /git/trees`        {tree:[{path, mode, type:"blob", sha}]} →
 *      目录子树（路径相对该目录、**不带 base_tree**，否则会继承整棵树 → `client/client/...`）
 *   3. 根树 = 根文件（引用 blob sha）+ 各子树（type:"tree", sha）
 *
 * ## 判据同 git：blob 哈希（`sha1("blob " + len + "\0" + content)`）
 *   内容没变就复用远程 sha —— 只上传真正改动的文件（本仓库实测 49 个）。
 *
 * `tracked-files.txt` 由 `git -c core.quotepath=false ls-files` 生成
 * （⚠ 必须 `core.quotepath=false`，否则非 ASCII 路径被转义成 `"docs/\346..."`，
 *   脚本按字面找不到文件会误判成"已删除"；也**不要**在 Node 里 spawn git ——
 *   受限沙箱下 child_process 拿不到管道，直接 EPERM）。
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const [remoteTreePath, outDir, listPath] = process.argv.slice(2);
if (!remoteTreePath || !outDir || !listPath) {
  console.error('用法: node tools/make-gh-tree-payload.mjs <remote-tree.json> <outdir> <tracked-files.txt>');
  process.exit(2);
}

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..');
const remote = JSON.parse(fs.readFileSync(remoteTreePath, 'utf8'));
const remoteBlobs = new Map();
for (const e of remote.tree) {
  if (e.type === 'blob') remoteBlobs.set(e.path, e.sha);
}

function gitBlobSha(buf) {
  const header = Buffer.from(`blob ${buf.length}\0`, 'utf8');
  return crypto.createHash('sha1').update(Buffer.concat([header, buf])).digest('hex');
}

const tracked = fs.readFileSync(listPath, 'utf8')
  .split(/\r?\n/).map((s) => s.trim()).filter(Boolean);

fs.mkdirSync(outDir, { recursive: true });

// 每个文件：算 blob 哈希；远程同内容 → 复用 sha，否则记下待上传（含 base64）
const pending = new Map();     // path → content(base64)
const blobSha = new Map();     // path → sha（复用的或**占位**，上传后由调用方回填）
const stats = { upload: 0, unchanged: 0 };

for (const p of tracked) {
  const buf = fs.readFileSync(path.join(root, p));
  const sha = gitBlobSha(buf);
  blobSha.set(p, sha);
  if (remoteBlobs.get(p) === sha) {
    stats.unchanged++;
  } else {
    pending.set(p, buf.toString('base64'));
    stats.upload++;
  }
}

// ---- blob 上传请求体：逐文件一个（字段只有 content/encoding，没有歧义）----
const manifest = [];
for (const [p, b64] of pending) {
  const safe = p.replace(/[\\/]/g, '__');
  const file = path.join(outDir, `blob-${safe}.json`);
  fs.writeFileSync(file, JSON.stringify({ content: b64, encoding: 'base64' }));
  manifest.push({ path: p, sha: blobSha.get(p), body: path.basename(file) });
}
fs.writeFileSync(path.join(outDir, 'blob-manifest.json'), JSON.stringify(manifest, null, 1));

// ---- 最终树请求体 ----
// `blob-shas.json`（可选）是"**已上传**到远端的 blob sha"映射（path → sha）。
// 有它就用它填 `sha` 字段（新建 / 覆盖过的文件都在里面）；没有就用本地算的 blob 哈希
// —— 对"远端已有同内容"的文件两者相等，所以两种情形都成立。
const uploaded = fs.existsSync(path.join(outDir, 'blob-shas.json'))
  ? JSON.parse(fs.readFileSync(path.join(outDir, 'blob-shas.json'), 'utf8'))
  : {};
const shaOf = (p) => uploaded[p] ?? blobSha.get(p);

const shards = new Map();
for (const p of tracked) {
  const idx = p.indexOf('/');
  if (idx < 0) continue;
  const top = p.slice(0, idx);
  if (!shards.has(top)) shards.set(top, []);
  shards.get(top).push({ path: p.slice(idx + 1), mode: '100644', type: 'blob', sha: shaOf(p) });
}
const shardInfo = [];
for (const [dir, list] of shards) {
  const safe = dir.replace(/[\\/]/g, '_');
  const file = path.join(outDir, `sub-${safe}.json`);
  fs.writeFileSync(file, JSON.stringify({ tree: list }));
  shardInfo.push({ dir, entries: list.length, file: path.basename(file), placeholder: `__SUB_${safe}__` });
}

// 根树请求体：根文件（blob sha）+ 各子树（占位符，调用方换成子树 sha）
const rootFiles = tracked.filter((p) => !p.includes('/'));
const rootTemplate = {
  tree: [
    ...rootFiles.map((p) => ({ path: p, mode: '100644', type: 'blob', sha: shaOf(p) })),
    ...[...shards.keys()].map((d) => ({
      path: d, mode: '040000', type: 'tree', sha: `__SUB_${d.replace(/[\\/]/g, '_')}__`,
    })),
  ],
};
fs.writeFileSync(path.join(outDir, 'root-final.template.json'), JSON.stringify(rootTemplate));

const trackedSet = new Set(tracked);
const deletions = [...remoteBlobs.keys()].filter((p) => !trackedSet.has(p));
fs.writeFileSync(path.join(outDir, 'stats.json'), JSON.stringify({
  baseTree: remote.sha, tracked: tracked.length, upload: stats.upload, unchanged: stats.unchanged,
  usedUploadedShas: Object.keys(uploaded).length, deletions, shards: shardInfo, rootFiles,
}, null, 1));

console.log(JSON.stringify({
  tracked: tracked.length,
  upload: stats.upload,
  unchanged: stats.unchanged,
  usedUploadedShas: Object.keys(uploaded).length,
  deletions,
  shards: shardInfo.map((s) => ({ dir: s.dir, entries: s.entries, file: s.file })),
  rootFiles,
  outDir,
}, null, 1));
