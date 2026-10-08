#!/usr/bin/env node
/**
 * 文档结构与引用检查（`AGENTS.md` / `NOTES.md` 这对文件的"语法检查"）。
 *
 *   node tools/doc-refs-check.mjs          # 检查，失败退出码 1
 *   node tools/doc-refs-check.mjs --quiet  # 只报结果行
 *
 * 三件事（对应 `AGENTS.md` §8 的三条规矩）：
 *   ① **预算**：`AGENTS.md` 是自动加载的工作区指令，超了会被**静默截断**（正文丢尾巴、不报错）——
 *      这里按 **65244** 给硬线（⚠ **实测截断点**：2026-10-07 两次复现 `65,337 → 65,244`、`65,345 → 65,244`，
 *      即 harness 实际卡在 65,244 B；名义值 65,536 是旧的、**报 PASS 不代表喂得进去**），
 *      另按 61440（=60 KB）给"该往 NOTES 搬东西了"的提醒线。
 *   ② **编号**：`AGENTS.md` 必须保有 §2.3-1..15、L1..L5 与全部 §x.y 章节标题
 *      （`§2.3-n` 被代码注释与 docs 大量引用，**不许重排或删号**）；
 *      另两条防"号数撞车 / 目录说谎"：`NOTES.md` **独有**的章节编号必须 ≥10
 *      （AGENTS 里同号常是别的主题，例如 AGENTS §8=文档维护、NOTES §10=已知限制），
 *      且 `NOTES.md` 目录表里列的每一节都真的存在；
 *      **同号同题**（`SAME_TOPIC` 表）：NOTES 与 AGENTS 的**同号小节主题必须一致** ——
 *      标题是各写各的，光查"号在不在"抓不到"§3.2 一份讲服务端、另一份讲 build/dist"。
 *   ③ **引用**：全仓文本文件里的 `AGENTS §x.y` / `NOTES §x.y` 引用，目标章节必须在**对应文件**里真的存在
 *      （拆文档时最容易漏的就是这条：内容搬走了、引用还指着旧文件）。
 *
 * ⚠ 只读脚本，不改任何文件；不需要起服务端。
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';

const QUIET = process.argv.includes('--quiet');
const BUDGET = 65244;          // 实测截断点（2026-10-07 两次复现）；再往上会被 harness 静默截断
const SOFT = 61440;            // 提醒线：逼近就该往 NOTES 搬

const AGENTS = 'AGENTS.md';
const NOTES = 'NOTES.md';
// 跳过：构建产物、发布包、Qt/工具链缓存，以及 Python 侧的环境与 scratch
// （⚠ 实测：沙箱下 `tempfile` 建的 scratch 目录连 scandir 都会 EPERM —— 不能让它把文档自检带崩）
const SKIP_DIR = /(^|\/)(\.git|build|dist|release|\.qt|\.tools|node_modules|players|replays|\.tmp|\.venv|\.uv-cache|\.uv-python)(\/|$)/;
const SCAN_EXT = /\.(md|mjs|ps1|sh|java|cpp|h|txt)$/;

let failed = 0;
const ok = (cond, msg, detail = '') => {
    if (cond) {
        if (!QUIET) console.log(`  [ok]   ${msg}`);
    } else {
        failed++;
        console.error(`  [FAIL] ${msg}${detail ? '\n         ' + detail : ''}`);
    }
};

if (!existsSync(AGENTS) || !existsSync(NOTES)) {
    console.error(`[doc-refs] 找不到 ${AGENTS} 或 ${NOTES}（在仓库根目录跑）`);
    process.exit(1);
}
const agentsText = readFileSync(AGENTS, 'utf8');
const notesText = readFileSync(NOTES, 'utf8');

// ---------------------------------------------------------------- ① 预算
const agentsBytes = Buffer.byteLength(agentsText, 'utf8');
const notesBytes = Buffer.byteLength(notesText, 'utf8');
ok(agentsBytes < BUDGET,
   `${AGENTS} ${agentsBytes} B < 硬上限 ${BUDGET} B`,
   `超了会被静默截断：把"查一次就够的细节"搬进 ${NOTES}`);
if (agentsBytes >= SOFT) {
    console.log(`  [warn] ${AGENTS} 已到 ${agentsBytes} B（提醒线 ${SOFT} B，距实测截断点还剩 ${BUDGET - agentsBytes} B）`
                + ` —— 再加内容前先考虑搬进 ${NOTES}`);
} else if (!QUIET) {
    console.log(`         ${AGENTS} ${agentsBytes} B（距实测截断点 ${BUDGET - agentsBytes} B）`);
}
if (!QUIET) console.log(`         ${NOTES} ${notesBytes} B（细节分册，不占指令预算）`);

// ---------------------------------------------------------------- ② 编号完整
for (let i = 1; i <= 15; i++) {
    ok(agentsText.includes(`\n${i}. **`), `${AGENTS} §2.3-${i} 判据还在`);
}
for (const L of ['L1', 'L2', 'L3', 'L4', 'L5']) {
    ok(agentsText.includes(`### ${L} `), `${AGENTS} ${L} 还在`);
}
for (const s of ['## 0.', '## 1. ', '### 2.1', '### 2.2', '### 2.3', '### 2.4', '## 3. ', '## 4. ',
                 '## 5. ', '### 6.1', '### 6.2', '### 6.3', '### 6.4', '### 6.5', '### 6.6',
                 '### 6.7', '### 6.8', '### 6.9', '## 7. ', '## 8. ', '## 9. ']) {
    ok(agentsText.includes(s), `${AGENTS} 章节 ${s.trim()} 还在`);
}

// ---------------------------------------------------------------- ③ 引用可解
const sectionsOf = (text) => {
    const set = new Set();
    let cur = null;
    for (const line of text.split('\n')) {
        // ⚠ 深度到 `####`（`§6.4.1` / `§9.6.4` 这类小节也要能被引用解出）
        const m = /^#{2,6} +(?:§)?([0-9]+(?:\.[0-9]+)*)/.exec(line);
        if (m) cur = m[1];
        const ml = /^#{2,3} +(L[1-5])/.exec(line);
        if (ml) cur = ml[1];
        if (cur) set.add(cur);
    }
    return set;
};
const aSec = sectionsOf(agentsText);
const nSec = sectionsOf(notesText);

// `NOTES.md` 独有的章节（AGENTS 里没有同号节）必须取编号 ≥10。
// 反例就是这次拆分踩到的：「已知限制」一度也叫 §8，而 AGENTS §8 是"文档维护约定"，
// 于是代码注释里那句「见 §8」谁也说不清指哪一份 —— 号数撞车比缺号更难发现。
// ⚠ §9.1 这种**小节**不算独有：它的父节 §9 在 AGENTS 里是同一主题（素材管线），
// 只要"主号"对得上就放过。
for (const s of nSec) {
    if (aSec.has(s)) continue;
    const major = s.split('.')[0];
    if (aSec.has(major)) continue;
    ok(Number(major) >= 10, `${NOTES} 独有章节 §${s} 编号 ≥10`,
       `§${s} 的父节 §${major} 在 ${AGENTS} 里没有，独有章节要从 §10 起编（避开 AGENTS 的号）`);
}
// 目录表不许说谎：列了 §x.y 就得真有那一节（拆分后目录最容易忘改）
const tocBlock = (notesText.split('## 目录')[1] || '').split('\n## ')[0];
for (const s of new Set([...tocBlock.matchAll(/^\| §([0-9]+(?:\.[0-9]+)?) \|/gm)].map((m) => m[1]))) {
    ok(nSec.has(s), `${NOTES} 目录里列的 §${s} 真的有这一节`);
}

// 同号同题：两份文件**同号**的小节必须是**同一主题**。
// 光查"号在不在"抓不到这个（2026-10 实测：AGENTS 的 §3.2 是「服务端」，而 NOTES 里同一个号在讲 build/dist）。
// 表里存**两侧标题的字面片段**：任一侧改名/挪号都会红 ⇒ 强制同时更新两侧与本表。
// ⚠ 只列"两边都有"的号；NOTES 独有号（≥10）不在此表。
const SAME_TOPIC = [
    // [号, NOTES 侧标题片段, AGENTS 侧标题片段]
    ['2.3', '2.3 十五条坑：完整案例', '2.3 十五条曾经踩过的坑'],
    ['3.1', '3.1 本机实测的工具链位置', '3.1 工具链：脚本自己找'],
    ['3.4', '3.4 `build\\` 与 `dist\\` 的关系', '3.4 `build\\` 与 `dist\\` 的关系'],
    ['4', '4 五层验证：每个工具的注解', '4. 验证：五层，从便宜到贵'],
    ['6.2', '6.2 牌桌布局与绘制', '6.2 牌桌布局与绘制'],
    ['6.4', '6.4 协议、文案与结算', '6.4 协议、文案与结算'],
    ['6.5', '6.5 训练接口', '6.5 训练接口'],
    ['6.6', '6.6 teacher', '6.6 teacher'],
    ['6.7', '6.7 无用代码清理', '6.7 无用代码清理'],
    ['7', '7 症状表', '7. 常见症状'],
    ['9', '9 素材与字体管线', '9. 素材与字体管线'],
];
for (const [num, nFrag, aFrag] of SAME_TOPIC) {
    ok(notesText.includes(nFrag), `同号同题：${NOTES} §${num} 标题仍是「${nFrag}」`,
       '两文件同号必须同题；改标题就把 tools/doc-refs-check.mjs 的 SAME_TOPIC 一起改');
    ok(agentsText.includes(aFrag), `同号同题：${AGENTS} §${num} 标题仍是「${aFrag}」`,
       '同上');
}

const walk = (dir, acc = []) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name);
        const rel = relative('.', p).replace(/\\/g, '/');
        if (SKIP_DIR.test(rel)) continue;
        if (e.isDirectory()) walk(p, acc);
        else if (SCAN_EXT.test(e.name)) acc.push(rel);
    }
    return acc;
};

let refs = 0;
const bad = [];
for (const f of walk('.')) {
    let text;
    try { text = readFileSync(f, 'utf8'); } catch { continue; }
    // ⚠ 号要取全：`§2.3-15`（条目号）与 `§6.4.1`（三级小节）都算引用，不能只校验到父号
    const re = /`?(AGENTS\.md|AGENTS|NOTES\.md|NOTES)`?\s*§\s*(L[1-5]|[0-9]+(?:\.[0-9]+)*(?:-[0-9]+)?)/g;
    let m;
    while ((m = re.exec(text))) {
        refs++;
        const notesSide = m[1].startsWith('NOTES');
        const target = notesSide ? nSec : aSec;
        const targetText = notesSide ? notesText : agentsText;
        const dash = m[2].indexOf('-');
        if (dash > 0) {
            // `§2.3-15` = 父节在 **且** 那一条有序列表项真的在（两文件都写成 `\n15. **`）
            const base = m[2].slice(0, dash);
            const item = m[2].slice(dash + 1);
            if (!target.has(base) || !targetText.includes(`\n${item}. **`)) bad.push(`${f}: ${m[0]}`);
        } else if (!target.has(m[2])) {
            bad.push(`${f}: ${m[0]}`);
        }
    }
}
ok(bad.length === 0, `${refs} 条 §引用全部解得出目标`, bad.slice(0, 20).join('\n         '));

// ---------------------------------------------------------------- ④ 索引完整
// `docs/INDEX.md` 是文档总索引：它**列出的路径必须存在**，且 `docs/` 下**每份 .md 都要被列出**
// —— 加文档忘了登记，或者搬家后索引指着旧路径，都会在这里红（负向对照见 `AGENTS.md` §8）。
const INDEX = 'docs/INDEX.md';
if (!existsSync(INDEX)) {
    ok(false, `${INDEX} 存在（文档总索引）`, 'AGENTS.md §0 与 §5 都指向它');
} else {
    const idxText = readFileSync(INDEX, 'utf8').replace(/\r\n/g, '\n');
    // ① docs/ 下每份 .md（除索引自己）都要在索引里出现
    const docFiles = readdirSync('docs').filter((f) => f.endsWith('.md') && f !== 'INDEX.md');
    const unlisted = docFiles.filter((f) => !idxText.includes(f));
    ok(unlisted.length === 0, `${INDEX} 登记了 docs/ 下全部 ${docFiles.length} 份 .md`,
       `未登记：${unlisted.join(', ')}`);
    // ② 索引里以反引号列出的仓库内文件路径都要真的存在（`*` 通配写法只展示、不校验）
    const paths = new Set([...idxText.matchAll(/`([^`\n]+\.(?:md|mjs|ps1|sh))`/g)]
        .map((m) => m[1])
        .filter((p) => /^(docs|tools|client|python|trainer|server)\//.test(p) && !p.includes('*')));
    const badPaths = [...paths].filter((p) => !existsSync(p));
    ok(badPaths.length === 0, `${INDEX} 里提到的 ${paths.size} 条路径都存在`, badPaths.join(', '));
}

console.log(failed === 0 ? '[doc-refs] PASS' : `[doc-refs] FAIL（${failed} 条）`);
process.exit(failed === 0 ? 0 : 1);
