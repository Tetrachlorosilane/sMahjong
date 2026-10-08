#!/usr/bin/env node
/**
 * `NOTES.md` §6.5 的**轮次索引表**：生成器 + 校验器。
 *
 * 背景：v4 的轮次日志按**写入时间**堆在 `NOTES.md` §6.5 里（57 个小节，第五轮 → 第六十七轮）。
 * 新会话想找"第 N 轮做了什么"只能全文搜，而且**编号本身不单调**（第六十七轮物理上在第六十六轮之前，
 * 48–54 缺号）—— 谁都以为是自己翻错了。所以：**正文一律不重排**（编号被 `docs/TRAINING-V4.md`、
 * `HANDOVER.md`、代码注释大量引用），改成在 §6.5 开头放一张**索引表**。
 *
 * 用法：
 *   node tools/round-index.mjs           # 打印索引表（markdown，可直接粘进 NOTES §6.5 开头）
 *   node tools/round-index.mjs --check   # 断言 NOTES 里那张表**恰好**覆盖正文的全部轮次小节
 *
 * 判据（`--check`）：
 *   ① 正文里每个 `**第N轮：…**` 小节都能在表里找到（加了轮次忘了登记 ⇒ 红）；
 *   ② 表里没有正文里不存在的轮次（表比正文多 ⇒ 红，防"表里写着一个不存在的轮次"）；
 *   ③ **不要求编号单调** —— 编号顺序是历史事实；只在**缺号 / 逆序**时打印提示（不算失败）。
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NOTES = join(ROOT, 'NOTES.md');
const CHECK = process.argv.includes('--check');

const CN = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };

/** 中文数字 → 整数（够用到 九十九：「六十七」= 67）。 */
function cn2int(s) {
    if (/^\d+$/.test(s)) return Number(s);
    if (s === '十') return 10;
    const m = s.match(/^(.)?十(.)?$/);
    if (m) return (m[1] ? CN[m[1]] : 1) * 10 + (m[2] ? CN[m[2]] : 0);
    return CN[s] ?? NaN;
}

const lines = readFileSync(NOTES, 'utf8').split('\n');
const rounds = [];
for (let i = 0; i < lines.length; i++) {
    // 标题形态有几种：`**第N轮：主题（日期）**`、`**第N轮（顺手做的）：主题**`、标题里还嵌 `**` 的
    const m = lines[i].match(/^\*\*第([一二三四五六七八九十百千0-9]+)轮[^：]{0,24}[:：]\s*(.*?)\*\*\s*$/);
    if (!m) continue;
    const n = cn2int(m[1]);
    let title = m[2];
    let date = '';
    // 日期可能不在末尾（如 `第一轮 teacher 预训练（P1/P2 雏形，2026-09-27）`）：取最后一个，再把它抠掉
    const all = [...title.matchAll(/(\d{4}-\d{2}-\d{2})/g)];
    if (all.length > 0) {
        date = all[all.length - 1][1];
        title = title.replace(new RegExp(`[，,]?\\s*${date}`), '')
                     .replace(/（\s*[，,]?\s*）/g, '')
                     .replace(/[（(]\s*[）)]$/g, '')
                     .trim();
    }
    rounds.push({ n, cn: m[1], title, date, line: i + 1 });
}

// 索引表：轮次 / 日期 / 主题（表格里**不放行号** —— 行号每次编辑都变，等于埋一颗必烂的种子）
const table = ['| 轮次 | 日期 | 主题（点进去搜这串就行） |', '| --- | --- | --- |',
    ...rounds.map((r) => `| 第${r.cn}轮 | ${r.date || '—'} | ${r.title} |`)].join('\n');

const missing = [];
const max = rounds.reduce((a, r) => Math.max(a, r.n), 0);
const present = new Set(rounds.map((r) => r.n));
for (let i = 1; i <= max; i++) if (!present.has(i)) missing.push(i);

if (!CHECK) {
    console.log(table);
    console.error(`\n[round-index] ${rounds.length} 个轮次小节（第五轮 起）· 最大编号 ${max}`);
    if (missing.length) console.error(`[round-index] 缺号（正文里没有独立小节）：${missing.join(', ')}`);
    const out = rounds.filter((r, i) => i > 0 && r.n < rounds[i - 1].n);
    if (out.length) {
        console.error(`[round-index] 逆序（物理顺序与编号不一致，⚠ **不要重排**）：`
                      + out.map((r) => `第${r.cn}轮(L${r.line})`).join(', '));
    }
    process.exit(0);
}

// ---------------- --check ----------------
const md = readFileSync(NOTES, 'utf8');
// 表里出现的轮次：只在 §6.5 那张表的行里找（`| 第N轮 | 日期 |`），避免把正文引用误当表项
const inTable = new Set();
for (const m of md.matchAll(/^\|\s*第([一二三四五六七八九十百千0-9]+)轮\s*\|/gm)) {
    inTable.add(cn2int(m[1]));
}
if (inTable.size === 0) {
    console.error('[round-index] FAIL：NOTES 里找不到轮次索引表（应该是 §6.5 开头那张 `| 轮次 | 日期 | 主题 |`）');
    process.exit(1);
}
const problems = [];
for (const r of rounds) {
    if (!inTable.has(r.n)) problems.push(`正文有第${r.cn}轮（L${r.line}：${r.title.slice(0, 30)}…）但表里没有`);
}
for (const n of inTable) {
    if (!present.has(n)) problems.push(`表里有第${n}轮，但正文找不到以它开头的小节`);
}
if (problems.length > 0) {
    console.error('[round-index] FAIL：索引表与正文不一致');
    for (const p of problems) console.error('  ✗ ' + p);
    console.error('  ⇒ 跑 `node tools/round-index.mjs` 重新生成那张表');
    process.exit(1);
}
console.log(`[round-index] PASS：索引表覆盖正文全部 ${rounds.length} 个轮次小节`
            + `（编号 ${Math.min(...present)}–${max}${missing.length ? `，缺号 ${missing.join(',')}` : ''}）`);
