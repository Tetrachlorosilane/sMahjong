#!/usr/bin/env node
/**
 * `verify-push` —— 推送上/推送后**一套**核对远端到底是不是本地这一份。
 *
 * 为什么要有它：本仓库的推送通道有两条（`github_git_push` 走 git，`github_commit_files` 走 REST），
 * 而**broker 的 git 通道会 502**、REST 通道却正常（`NOTES.md` §9.5 记了两次）。
 * 于是每次推送后都要手工拼这么一串：`git fetch` → `git ls-remote` → 比 sha → 比 **tree**
 * → 再看 `origin/<分支>` 这个**本地跟踪 ref** 是不是滞后 —— 少看一眼就得出错结论
 * （2026-09-28 那次：提交成功、工具报"回读校验一致"，但**远端 tree ≠ 本地 tree**，
 * 只因手抄正文差了一个空格；只看"已提交"是发现不了的）。
 *
 * 判据（**内容等价看 tree，位置看 sha**）：
 *   ① 远端头 sha == 本地 `HEAD` ⇒ **远端 == 本地**；
 *   ② 远端提交的 tree == 本地 `HEAD^{tree}` ⇒ **内容逐字节等价**（即便 sha 不同，
 *      例如远端是同一个 tree 的另一个提交/另一条链）；
 *   ③ `origin/<分支>`（本地跟踪 ref）== 远端头？落后几个提交？⇒ 只看本地 ref 会得出错结论。
 *   ⛔ 拿不到远端 tree 时**不许**印"通过"：状态是 `UNKNOWN`，退出码 2。
 *
 * 远端 sha 的取法（按可靠性递降）：`git fetch`（失败不致命）→ `git ls-remote`
 * → **被网络拦时回退 GitHub API** `GET /repos/<owner>/<repo>/git/ref/heads/<branch>`
 * （tree 走 `GET /repos/<owner>/<repo>/git/commits/<sha>`）。
 *
 * 用法（一行可复制）：
 *   node tools/verify-push.mjs                 # 核对当前分支（推完就跑）
 *   node tools/verify-push.mjs Training --handover
 *   node tools/verify-push.mjs --selftest      # 自检 + 负向自证
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const SELFTEST = argv.includes('--selftest');

function opt(name, dflt) {
    const i = argv.indexOf(name);
    if (i < 0) return dflt;
    const v = argv[i + 1];
    return v === undefined || v.startsWith('--') ? dflt : v;
}
const BRANCH_ARG = argv.find((a) => !a.startsWith('--'));
const REMOTE = opt('--remote', 'origin');
const JSON_OUT = opt('--json', '');
const DO_HANDOVER = argv.includes('--handover');
const NO_FETCH = argv.includes('--no-fetch');
const TIMEOUT = Number(opt('--timeout-ms', '30000'));

/** 跑一条 git（失败不抛，返回 `{ok, out, err}`）—— 网络类失败要能区分"命令失败"与"输出为空"。 */
function git(args, { timeout = TIMEOUT } = {}) {
    try {
        const out = execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', timeout, stdio: ['ignore', 'pipe', 'pipe'] });
        return { ok: true, out: out.trim(), err: '' };
    } catch (e) {
        return { ok: false, out: (e.stdout || '').toString().trim(), err: (e.stderr || e.message || '').toString().trim() };
    }
}

/** 从 remote URL 解出 `owner/repo`（HTTPS 与 SSH 两种写法都认）。 */
function parseRepo(url) {
    const m = /github\.com[:/]+([^/]+)\/([^/]+?)(?:\.git)?$/.exec(url || '');
    return m ? { owner: m[1], repo: m[2] } : null;
}

/**
 * 走 GitHub API 取远端头（网络被拦时的回退通道）。`{ok, sha, tree, why}`。
 *
 * ⚠ 超时**必须用自己管的 AbortController + `unref()` 的定时器**，⛔ 别用 `AbortSignal.timeout()`：
 * 实测（负向自证那次，2026-10-10）用它会在 `process.exit()` 时撞上 libuv 的
 * `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING) … win\async.c line 76` ——
 * 判决**打印是对的**（NO_REMOTE），退出码却是 `0xC0000409` 崩溃码。
 * 一个"结论对但退出码是崩溃"的工具，在脚本里等同于"结论错"。
 */
async function apiRemoteHead(owner, repo, branch) {
    const tok = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || '';
    const headers = { accept: 'application/vnd.github+json' };
    if (tok) headers.authorization = `Bearer ${tok}`;
    const get = async (p) => {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), TIMEOUT);
        if (typeof timer.unref === 'function') timer.unref();
        try {
            const res = await fetch(`https://api.github.com${p}`, { headers, signal: ctl.signal });
            return { status: res.status, body: await res.json().catch(() => ({})) };
        } finally {
            clearTimeout(timer);
        }
    };
    try {
        const ref = await get(`/repos/${owner}/${repo}/git/ref/heads/${branch}`);
        if (ref.status !== 200) {
            return { ok: false, why: `GitHub API ${ref.status}：${ref.body.message || '取不到 ref'}` };
        }
        const sha = ref.body.object.sha;
        const commit = await get(`/repos/${owner}/${repo}/git/commits/${sha}`);
        if (commit.status !== 200) {
            return { ok: false, sha, why: `GitHub API ${commit.status}：取不到提交 ${sha}` };
        }
        return { ok: true, sha, tree: commit.body.tree.sha, via: 'api' };
    } catch (e) {
        return { ok: false, why: `GitHub API 不可达：${e.message}` };
    }
}

/**
 * 纯函数：拿五项事实出判决（自检直接打这里）。
 * ⛔ `remoteTree` 缺失 ⇒ `UNKNOWN`（退出码 2）—— "比不了就说比不了"，不许当成通过。
 */
function judge({ localHead, localTree, remoteSha, remoteTree, trackSha, trackAhead }) {
    if (!remoteSha) {
        return { state: 'NO_REMOTE', exit: 2, sameSha: null, sameTree: null,
            lines: ['⛔ 取不到远端头（网络被拦 + API 也没给出结果）⇒ 无法判定，不是"通过"'] };
    }
    const sameSha = remoteSha === localHead;
    let sameTree = null;
    if (remoteTree) sameTree = remoteTree === localTree;
    const lines = [];
    lines.push(`  远端 == 本地（sha）   ${sameSha ? 'YES' : 'NO'}（远端 ${remoteSha.slice(0, 8)} / 本地 ${localHead.slice(0, 8)}）`);
    if (sameTree === null) {
        lines.push('  tree 是否一致          UNKNOWN（拿不到远端 tree ⇒ 不印通过）');
    } else {
        lines.push(`  tree 是否一致          ${sameTree ? 'YES（内容逐字节等价）' : 'NO（内容不同）'}`
            + `（远端 ${remoteTree.slice(0, 12)}… / 本地 ${localTree.slice(0, 12)}…）`);
    }
    if (trackSha === undefined || trackSha === null) {
        lines.push('  本地跟踪 ref           (无 origin/<分支>)');
    } else if (trackSha === remoteSha) {
        lines.push('  本地跟踪 ref           不滞后（== 远端头）');
    } else {
        lines.push(`  本地跟踪 ref           **滞后**（origin/<分支> = ${trackSha.slice(0, 8)}`
            + `，远端头 = ${remoteSha.slice(0, 8)}${trackAhead === undefined ? '' : `，差 ${trackAhead} 个提交`}）`
            + ' —— ⚠ 只看本地 ref 会得出"没推上去"的错结论');
    }
    if (sameTree === false) return { state: 'TREE_MISMATCH', exit: 1, sameSha, sameTree, lines };
    if (sameTree === null) return { state: 'UNKNOWN', exit: 2, sameSha, sameTree, lines };
    if (!sameSha) {
        lines.push('  ✅ 内容等价：sha 不同但 tree 相同（同一份内容的不同提交）');
        return { state: 'TREE_EQUAL', exit: 0, sameSha, sameTree, lines };
    }
    lines.push('  ✅ 远端头就是本地这一份（sha + tree 都对上）');
    return { state: 'IDENTICAL', exit: 0, sameSha, sameTree, lines };
}

// ---------------------------------------------------------------- --selftest
function selftest() {
    const H = 'a'.repeat(40);
    const T = 'b'.repeat(40);
    const cases = [
        { desc: '① sha + tree 全等 ⇒ IDENTICAL / 退出码 0',
          facts: { localHead: H, localTree: T, remoteSha: H, remoteTree: T, trackSha: H },
          want: { state: 'IDENTICAL', exit: 0 } },
        { desc: '② sha 不同但 tree 相同 ⇒ TREE_EQUAL / 退出码 0（内容等价）',
          facts: { localHead: H, localTree: T, remoteSha: 'c'.repeat(40), remoteTree: T, trackSha: 'c'.repeat(40) },
          want: { state: 'TREE_EQUAL', exit: 0 } },
        { desc: '③ tree 不同 ⇒ TREE_MISMATCH / 退出码 1（负向自证：内容不同必须红）',
          facts: { localHead: H, localTree: T, remoteSha: 'c'.repeat(40), remoteTree: 'd'.repeat(40), trackSha: 'c'.repeat(40) },
          want: { state: 'TREE_MISMATCH', exit: 1 } },
        { desc: '④ 拿不到远端 tree ⇒ UNKNOWN / 退出码 2（负向自证：比不了不许当通过）',
          facts: { localHead: H, localTree: T, remoteSha: 'c'.repeat(40), remoteTree: '', trackSha: 'c'.repeat(40) },
          want: { state: 'UNKNOWN', exit: 2 } },
        { desc: '⑤ 取不到远端头 ⇒ NO_REMOTE / 退出码 2（负向自证）',
          facts: { localHead: H, localTree: T, remoteSha: '', remoteTree: '', trackSha: H },
          want: { state: 'NO_REMOTE', exit: 2 } },
    ];
    let bad = 0;
    for (const c of cases) {
        const r = judge(c.facts);
        const ok = r.state === c.want.state && r.exit === c.want.exit;
        if (!ok) bad++;
        console.log(`${ok ? '  [ok]  ' : '  [FAIL]'} ${c.desc} ⇒ state=${r.state} exit=${r.exit}`);
        if (!ok) console.log(`         期望 state=${c.want.state} exit=${c.want.exit}`);
    }
    console.log(bad === 0 ? '[verify-push] SELFTEST PASS（5/5，含 3 条负向自证）'
        : `[verify-push] SELFTEST FAIL（${bad} 例）`);
    process.exit(bad === 0 ? 0 : 1);
}

// ---------------------------------------------------------------- main
async function main() {
    const local = {
        branch: BRANCH_ARG || git(['rev-parse', '--abbrev-ref', 'HEAD']).out,
        head: git(['rev-parse', 'HEAD']).out,
        tree: git(['rev-parse', 'HEAD^{tree}']).out,
    };
    const trackRef = `${REMOTE}/${local.branch}`;
    const track = git(['rev-parse', trackRef]);
    const trackSha = track.ok ? track.out : null;
    const remoteUrl = git(['remote', 'get-url', REMOTE]).out;
    const slug = parseRepo(remoteUrl);

    console.log(`[verify-push] 分支 ${local.branch} · 本地 HEAD ${local.head.slice(0, 8)} · tree ${local.tree.slice(0, 12)}…`);
    console.log(`[verify-push] 远端 ${REMOTE} = ${remoteUrl || '(没有这个远端)'}${slug ? ` ⇒ ${slug.owner}/${slug.repo}` : ''}`);

    // ① fetch（**失败不致命**：fetch 只是为了让本地能读到远端对象，判断本身走 ls-remote/API）
    if (!NO_FETCH) {
        const f = git(['fetch', REMOTE, local.branch]);
        console.log(`  ① git fetch      ${f.ok ? 'ok' : `失败（不致命）：${(f.err || '').split('\n')[0]}`}`);
    } else {
        console.log('  ① git fetch      显式跳过（--no-fetch）');
    }

    // ② 远端头：ls-remote → 失败/空 回退 API
    let remoteSha = '';
    let remoteTree = '';
    let via = '';
    const ls = git(['ls-remote', REMOTE, `refs/heads/${local.branch}`]);
    if (ls.ok && ls.out) {
        remoteSha = ls.out.split(/\s+/)[0];
        via = 'ls-remote';
    } else {
        console.log(`  ② git ls-remote  ${ls.ok ? '空输出（远端没有这个分支？）' : `失败：${(ls.err || '').split('\n')[0]}`}`
            + ' ⇒ 回退 GitHub API');
        if (slug) {
            const api = await apiRemoteHead(slug.owner, slug.repo, local.branch);
            if (api.ok) {
                remoteSha = api.sha;
                remoteTree = api.tree;
                via = 'api';
            } else {
                console.log(`     GitHub API   失败：${api.why}`);
            }
        } else {
            console.log('     GitHub API   跳过：远端 URL 不是 GitHub 形状，解不出 owner/repo');
        }
    }
    // 远端 tree：优先本地对象（fetch 成功时就有），否则 API
    if (remoteSha && !remoteTree) {
        const t = git(['rev-parse', `${remoteSha}^{tree}`]);
        if (t.ok) remoteTree = t.out;
    }
    if (remoteSha) console.log(`  ② 远端头       ${remoteSha.slice(0, 8)}（取自 ${via}）${remoteTree ? ` · tree ${remoteTree.slice(0, 12)}…` : ''}`);

    const verdict = judge({ localHead: local.head, localTree: local.tree, remoteSha, remoteTree, trackSha });
    console.log('');
    console.log('=== 判决 ===');
    for (const l of verdict.lines) console.log(l);
    console.log(`  状态 ${verdict.state} ⇒ 退出码 ${verdict.exit}`);

    if (DO_HANDOVER) {
        console.log('');
        console.log('=== 推后 handover-facts --check ===');
        const h = execFileSync('node', ['tools/handover-facts.mjs', '--check'], { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
        process.stdout.write(h);
    }

    if (JSON_OUT) {
        const p = resolve(ROOT, JSON_OUT);
        mkdirSync(dirname(p), { recursive: true });
        writeFileSync(p, JSON.stringify({ branch: local.branch, remote: REMOTE, via, local, remoteSha, remoteTree, trackSha, ...verdict }, null, 2), 'utf8');
        console.log(`  汇总 JSON      ${p}`);
    }
    // ⚠ 这里**只能设 `process.exitCode`，不能 `process.exit()`**：本机实测（2026-10-10，负向自证那次）
    //   `fetch()`（undici）之后调 `process.exit()` 会在退出瞬间撞 libuv 断言
    //   `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING) … win\async.c line 76`，
    //   退出码变成 `0xC0000409` —— 判决打印是对的、退出码却是崩溃码，脚本里等同于结论错。
    //   ⛔ 也别试 `await globalThis[Symbol.for('undici.globalDispatcher.1')].close()`：同样崩（都试过）。
    process.exitCode = verdict.exit;
}

if (SELFTEST) selftest();
else await main();
