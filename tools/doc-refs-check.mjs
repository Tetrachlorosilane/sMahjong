#!/usr/bin/env node
/**
 * 文档结构与引用检查（`AGENTS.md` / `NOTES.md` 这对文件的"语法检查"）。
 *
 *   node tools/doc-refs-check.mjs          # 检查，失败退出码 1
 *   node tools/doc-refs-check.mjs --quiet  # 只报结果行
 *
 * 三件事（对应 `AGENTS.md` §8 的三条规矩）：
 *   ① **预算**：`AGENTS.md` 是自动加载的工作区指令，超过 64 KB 会被**静默截断** ——
 *      这里按 65536 给硬线，另按 61440（=60 KB）给"该往 NOTES 搬东西了"的提醒线。
 *   ② **编号**：`AGENTS.md` 必须保有 §2.3-1..14、L1..L5 与全部 §x.y 章节标题
 *      （`§2.3-n` 被代码注释与 docs 大量引用，**不许重排或删号**）；
 *      另两条防"号数撞车 / 目录说谎"：`NOTES.md` **独有**的章节编号必须 ≥10
 *      （AGENTS 里同号常是别的主题，例如 AGENTS §8=文档维护、NOTES §10=已知限制），
 *      且 `NOTES.md` 目录表里列的每一节都真的存在。
 *   ③ **引用**：全仓文本文件里的 `AGENTS §x.y` / `NOTES §x.y` 引用，目标章节必须在**对应文件**里真的存在
 *      （拆文档时最容易漏的就是这条：内容搬走了、引用还指着旧文件）。
 *
 * ⚠ 只读脚本，不改任何文件；不需要起服务端。
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';

const QUIET = process.argv.includes('--quiet');
const BUDGET = 65536;          // 加载器的硬上限（再往上会被截断）
const SOFT = 61440;            // 提醒线：逼近就该往 NOTES 搬

const AGENTS = 'AGENTS.md';
const NOTES = 'NOTES.md';
const SKIP_DIR = /(^|\/)(\.git|build|dist|release|\.qt|\.tools|node_modules|players|replays)(\/|$)/;
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
    console.log(`  [warn] ${AGENTS} 已到 ${agentsBytes} B（提醒线 ${SOFT} B）—— 再加内容前先考虑搬进 ${NOTES}`);
}
if (!QUIET) console.log(`         ${NOTES} ${notesBytes} B（细节分册，不占指令预算）`);

// ---------------------------------------------------------------- ② 编号完整
for (let i = 1; i <= 14; i++) {
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
        const m = /^#{2,3} +(?:§)?([0-9]+(?:\.[0-9]+)?)/.exec(line);
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
    const re = /`?(AGENTS\.md|AGENTS|NOTES\.md|NOTES)`?\s*§\s*(L[1-5]|[0-9]+(?:\.[0-9]+)?)/g;
    let m;
    while ((m = re.exec(text))) {
        refs++;
        const target = m[1].startsWith('NOTES') ? nSec : aSec;
        if (!target.has(m[2])) bad.push(`${f}: ${m[0]}`);
    }
}
ok(bad.length === 0, `${refs} 条 §引用全部解得出目标`, bad.slice(0, 20).join('\n         '));

console.log(failed === 0 ? '[doc-refs] PASS' : `[doc-refs] FAIL（${failed} 条）`);
process.exit(failed === 0 ? 0 : 1);
