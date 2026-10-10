#!/usr/bin/env node
/**
 * `docs/HANDOVER.md` §1（十秒速览）里那两行**仓库事实**的生成器 + 校验器。
 *
 * 为什么要有它：§1 的「分支 / HEAD」与「远端」两行是**手工誊抄** git 输出的 —— 抄完就烂
 * （2026-10 实测：§1 写着父提交 `8b069fa`、远端头 `daacdc68` 且"已同步"，实际 HEAD 的父是
 * `7daa979`、`origin/Training` 停在 `c026e89`、**落后 46 个提交**）。手抄的数字没有判据，
 * 于是"下一会话读到的第一张表"就是错的。这里把它变成**一条命令 + 一条判据**。
 *
 * 用法：
 *   node tools/handover-facts.mjs            # 打印可直接粘进 §1 的两行 + 相关事实
 *   node tools/handover-facts.mjs --check    # 断言 HANDOVER §1 记的 sha 与现实一致（不一致退出 1）
 *   node tools/handover-facts.mjs --check --file <路径>   # 校验另一份 HANDOVER 副本（自证 / 试跑用）
 *
 * 判据（`--check` 到底查什么）：
 *   ① §1 记的**基线 HEAD** 必须是当前 HEAD 的**祖先**（⚠ 不能要求相等：§1 就在它后面那个提交里，
 *      所以"记本文件所在提交的 sha"是**结构性必错**的写法 —— 提交一落地 sha 就变了）；
 *   ② §1 记的**远端**（sha + 日期）必须**自洽且不超前**：记的 sha 要么就是 `origin/<分支>` 的当前头，
 *      要么是它的**祖先**（`git merge-base --is-ancestor` 为真）；日期必须等于**那个被记录 sha 自己**的提交日期。
 *      ⚠ 不能要求"与远端头逐字相等"：§1 自身就住在某个提交里，它一提交并推送，远端头必然又 +1
 *      ⇒ 旧口径是**结构性假红**（2026-10-10 实测：§1 记 `0a60ece`，推送后 `origin/Training` = `4fa2c6b`，
 *      差的就是 §1 自己那个提交）。本行与①用**同一把尺子**（祖先判定）。
 *   ③ §1 关于"已同步 / 落后"的措辞必须与 `@{u}..HEAD` 的计数**同向** —— 计数 >0 时不许写"已同步"。
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = process.argv.includes('--check');

/** 要校验的 HANDOVER：缺省 `docs/HANDOVER.md`；`--file <路径>` 可指向副本（自证用，别改真文件做实验）。 */
function handoverPath() {
    const i = process.argv.indexOf('--file');
    const p = i >= 0 ? process.argv[i + 1] : undefined;
    if (p === undefined || p.startsWith('--')) {
        return join(ROOT, 'docs', 'HANDOVER.md');
    }
    return isAbsolute(p) ? p : resolve(ROOT, p);
}
const HANDOVER = handoverPath();

/** 跑一条 git 命令（失败返回 null，不抛）。 */
function git(args) {
    try {
        return execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
            .trim();
    } catch {
        return null;
    }
}

const facts = {
    branch: git(['rev-parse', '--abbrev-ref', 'HEAD']) || '?',
    head: git(['rev-parse', '--short', 'HEAD']) || '?',
    headSubject: git(['log', '-1', '--pretty=%s']) || '?',
    parent: git(['rev-parse', '--short', 'HEAD^']) || '?',
    tree: git(['rev-parse', 'HEAD^{tree}']) || '?',
    dirty: (git(['status', '--porcelain']) || '').split('\n').filter((l) => l.trim() !== '').length,
    ahead: Number(git(['rev-list', '--count', '@{u}..HEAD']) || '0'),
    behind: Number(git(['rev-list', '--count', 'HEAD..@{u}']) || '0'),
    upstream: git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']) || '(无上游)',
};

// 远端那一行：`origin/<分支>` 的 sha + 提交日期（`full` 供祖先/相等判定，`sha` 供打印）
const remoteRef = `origin/${facts.branch}`;
const remote = {
    ref: remoteRef,
    full: git(['rev-parse', remoteRef]) || '',
    sha: git(['rev-parse', '--short', remoteRef]) || '(无此远端分支)',
    date: git(['log', '-1', '--date=short', '--pretty=%cd', remoteRef]) || '?',
};

const now = new Date();
const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

/** §1 那两行的**标准写法**（`--check` 就按这两行里的值去核对）。 */
const lines = {
    head: `| 分支 / HEAD | \`${facts.branch}\` · 基线 **\`${facts.head}\`**（\`${facts.headSubject.slice(0, 40)}\`）；其父 \`${facts.parent}\`；tree \`${facts.tree.slice(0, 12)}…\`；**未推送 ${facts.ahead} 个提交**（\`@{u}..HEAD\`）。⚠ 本行记的是**基线**（§1 改动建立在它之上）：§1 自身提交后 HEAD 会 +1，所以判据是"它仍是 HEAD 的祖先"，不是"它 == HEAD" |`,
    remote: `| 远端 | \`${facts.upstream}\` 头 = **\`${remote.sha}\`**（${remote.date}）⇒ **落后本地 ${facts.ahead} 个提交**（\`behind ${facts.behind}\`）。⚠ 推送前的判据见 §5 |`,
};

if (!CHECK) {
    console.log(`=== docs/HANDOVER.md §1 的两行（生成于 ${today}）===`);
    console.log(lines.head);
    console.log(lines.remote);
    console.log('');
    console.log('=== 事实明细 ===');
    console.log(`分支 ${facts.branch} · HEAD ${facts.head} · 父 ${facts.parent} · tree ${facts.tree}`);
    console.log(`上游 ${facts.upstream} · ahead ${facts.ahead} / behind ${facts.behind} · 工作树未提交文件 ${facts.dirty} 个`);
    console.log(`远端 ${remote.ref} = ${remote.sha}（${remote.date}）`);
    process.exit(0);
}

// ---------------- --check ----------------
let md;
try {
    md = readFileSync(HANDOVER, 'utf8');
} catch (e) {
    console.error(`[handover-facts] 读不到要校验的文件：${HANDOVER}（${e.code || e.message}）`);
    process.exit(1);
}
const problems = [];
const ok = (cond, msg) => {
    if (!cond) {
        problems.push(msg);
    }
};

// ① 基线 HEAD 必须是当前 HEAD 的祖先
const recordedHead = (md.match(/基线 \*\*`([0-9a-f]{7,40})`\*\*/) || [])[1];
ok(recordedHead !== undefined, '§1 没找到「基线 **`<sha>`**」字段（按生成器打印的那两行誊抄）');
if (recordedHead !== undefined) {
    const isAncestor = git(['merge-base', '--is-ancestor', recordedHead, 'HEAD']) !== null;
    ok(isAncestor, `§1 记的基线 HEAD \`${recordedHead}\` 不是当前 HEAD 的祖先（当前 ${facts.head}）⇒ 表已过期`);
}

// ② 远端那一行：sha 必须 **== `origin/<分支>` 头 或是它的祖先**；日期必须与**那个 sha 自己**的提交日期一致
//   ⚠ 旧口径是"与远端头逐字相等 + 日期逐字相等" —— 那是**结构性假红**：§1 自身提交并推送后远端必 +1，
//   于是每次推送后都差一个提交（2026-10-10 实测）。现在与①「基线」用同一把尺子：祖先判定 + 日期自洽。
const recordedRemote = (md.match(/头 = \*\*`([0-9a-f]{7,40})`\*\*（(\d{4}-\d{2}-\d{2})）/) || []);
ok(recordedRemote.length === 3, '§1 没找到「远端 … 头 = **`<sha>`**（<日期>）」字段');
if (recordedRemote.length === 3) {
    const recSha = recordedRemote[1];
    const recDate = recordedRemote[2];
    // 先确认这个 sha 在本地**是个提交**（不存在 / 形状不对 ⇒ 单独报，别把"读不到"混成"不是祖先"）
    const exists = git(['rev-parse', '--verify', '--quiet', `${recSha}^{commit}`]) !== null;
    ok(exists, `§1 记的远端 sha \`${recSha}\` 在本地不是一个提交（对象不存在或形状不对）`);
    if (exists) {
        const sameAsHead = git(['rev-parse', recSha]) === remote.full;
        const isAncestor = sameAsHead || git(['merge-base', '--is-ancestor', recSha, remoteRef]) !== null;
        ok(isAncestor,
           `§1 记的远端 sha \`${recSha}\` 既不是 \`${remoteRef}\` 的头（\`${remote.sha}\`）`
           + ' 也不是它的祖先 ⇒ 表已过期（记了"未来"的提交或一个无关 sha）');
        // 日期断言改成**自洽**口径：记录的日期 == 那个被记录 sha 自己的提交日期
        // （若记的就是远端头本身，两者自然一致 ⇒ 旧口径下能通过的写法在新口径下仍然通过）
        const shaDate = git(['log', '-1', '--date=short', '--pretty=%cd', recSha]);
        ok(recDate === shaDate,
           `§1 记的远端日期 ${recDate} ≠ 那个被记录 sha \`${recSha}\` 自己的提交日期 ${shaDate}`
           + ' ⇒ 日期必须与"它记的那个 sha"自洽（不是与远端头的日期自洽）');
    }
}

// ③ "已同步" 只能在没有未推送提交时写
//   ⚠ 判据要认**否定用法**（2026-10-08 实测踩到假红）：§1 写「⛔ 别把这里读成"已同步"」本意是提醒别误读，
//   纯子串匹配会把**提醒**当成**断言**判红。所以只有"已同步"**前面 6 个字符内没有否定词**时才算断言。
//   ⛔ 别退化成"看到 已同步 就红"：那会逼着文档不敢提这个词，反而更容易被误读。
if (facts.ahead > 0) {
    const syncedLine = md.split('\n').find((l) => l.includes('| 远端 |')) || '';
    const claims = [...syncedLine.matchAll(/已同步/g)]
        .filter((m) => !/[别不非未没]/.test(syncedLine.slice(Math.max(0, m.index - 12), m.index)));
    ok(claims.length === 0,
       `§1 的远端行**断言**了「已同步」，而实际 **未推送 ${facts.ahead} 个提交** ⇒ 措辞与事实相反`
       + '（⚠ 否定用法如"别读成已同步"不算违规）');
}

// ④ 「当前下一步」只允许**一个**权威位置（§1 的那一行）；其余"下一步"类小节必须标历史 + 指向它。
//    为什么：本项目"下一步"曾在同一份文件里出现过三份互相冲突的清单（2026-10-07 审计），
//    靠人盯必然复发 —— 这条把它变成机械判据。
const mdLines = md.split('\n');
ok(/\| \*\*下一步（唯一清单）\*\* \|/.test(md),
   '§1 里找不到 `| **下一步（唯一清单）** |` 这一行 —— "当前下一步"必须有唯一权威位置');
mdLines.forEach((line, i) => {
    if (!/^#{2,4}\s.*下一步/.test(line)) {
        return;
    }
    const block = mdLines.slice(i + 1, i + 8).join('\n');
    ok(/历史/.test(block) && /见 §1/.test(block),
       `标题「${line.trim()}」（L${i + 1}）是"下一步"类小节，但没标**历史** + 指向 §1 ⇒ `
       + '「当前下一步」只允许有一个权威位置（§1），其余历史规划节一律要显式标日期 + 指向它');
});

if (problems.length > 0) {
    console.error('[handover-facts] FAIL：HANDOVER §1 与现实不一致');
    for (const p of problems) {
        console.error('  ✗ ' + p);
    }
    console.error('  ⇒ 跑 `node tools/handover-facts.mjs` 取当前事实，按它的两行改 §1');
    process.exit(1);
}
const remoteNote = recordedRemote[1] === remote.sha
    ? `就是 \`${remote.ref}\` 的头`
    : `是 \`${remote.ref}\` 头 \`${remote.sha}\` 的祖先`;
console.log(`[handover-facts] PASS：§1 的基线（\`${recordedHead}\`，HEAD \`${facts.head}\` 的祖先）`
            + ` 与远端（记录 \`${recordedRemote[1]}\`（${recordedRemote[2]}），${remoteNote}）都与现实一致`);
