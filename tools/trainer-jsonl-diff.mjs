#!/usr/bin/env node
/**
 * **两份轨迹的字段级差异定位**（配合 `trainer-selfplay-parity.mjs` 用）。
 *
 *   node tools/trainer-jsonl-diff.mjs <java.jsonl> <cpp.jsonl>
 *
 * 为什么需要它：逐字节对拍只会说"第 N 个字节不同、第 K 行不同"，而一行里有
 * `obs` 的 29 个字段（手牌 34 维 / 副露 / 牌河 / 可见牌 …）—— 光看截断的 200 字符经常
 * 认不出是哪个字段。这里做三件事：
 *   ① **键序**：两边键的**顺序**不同也报（逐字节契约里键序是契约的一部分）；
 *   ② **字段路径**：递归找出第一个不同的**路径**（如 `obs.round.riichi_sticks`）与两边的值；
 *   ③ **结构差**：缺键/多键、数组长度不同、类型不同（`int` vs `string`）分别报。
 *
 * 退出码：0 = 两份完全相同；1 = 有差异；2 = 用法/读文件错。
 */
import { readFileSync } from 'node:fs';

const [fa, fb] = process.argv.slice(2);
if (!fa || !fb) {
    console.error('用法：node tools/trainer-jsonl-diff.mjs <a.jsonl> <b.jsonl>');
    process.exit(2);
}
const A = readFileSync(fa, 'utf8').split('\r\n');
const B = readFileSync(fb, 'utf8').split('\r\n');
if (A.length && A[A.length - 1] === '') A.pop();
if (B.length && B[B.length - 1] === '') B.pop();

const short = (v) => {
    const s = typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v);
    return s === undefined ? String(v) : (s.length > 120 ? s.slice(0, 120) + '…' : s);
};

/** 递归找第一个不同的路径。返回 null 表示相等。 */
function firstDiff(x, y, path) {
    if (Array.isArray(x) || Array.isArray(y)) {
        if (!Array.isArray(x) || !Array.isArray(y)) {
            return { path, a: x, b: y, why: '一方不是数组' };
        }
        if (x.length !== y.length) {
            return { path: `${path}.length`, a: x.length, b: y.length, why: '数组长度不同' };
        }
        for (let i = 0; i < x.length; i++) {
            const d = firstDiff(x[i], y[i], `${path}[${i}]`);
            if (d) return d;
        }
        return null;
    }
    if (x && y && typeof x === 'object' && typeof y === 'object') {
        const kx = Object.keys(x);
        const ky = Object.keys(y);
        if (kx.join(',') !== ky.join(',')) {
            const miss = kx.filter((k) => !ky.includes(k));
            const extra = ky.filter((k) => !kx.includes(k));
            if (miss.length || extra.length) {
                return { path, a: kx.join(','), b: ky.join(','), why: `键集不同（缺 ${miss.join('/') || '-'}，多 ${extra.join('/') || '-'}）` };
            }
            return { path: `${path}#keys`, a: kx.join(','), b: ky.join(','), why: '键序不同（逐字节契约里键序也算）' };
        }
        for (const k of kx) {
            const d = firstDiff(x[k], y[k], path ? `${path}.${k}` : k);
            if (d) return d;
        }
        return null;
    }
    if (x !== y) {
        return { path, a: x, b: y, why: typeof x === typeof y ? '值不同' : `类型不同（${typeof x} vs ${typeof y}）` };
    }
    return null;
}

if (A.length !== B.length) {
    console.log(`行数不同：a=${A.length} b=${B.length}`);
}
const n = Math.min(A.length, B.length);
let diffs = 0;
for (let i = 0; i < n; i++) {
    if (A[i] === B[i]) continue;
    diffs++;
    if (diffs > 3) break;
    console.log(`\n=== 第 ${i + 1} 行不同 ===`);
    let ja = null;
    let jb = null;
    try { ja = JSON.parse(A[i]); } catch (e) { console.log(`  a 不是合法 JSON：${e.message}`); }
    try { jb = JSON.parse(B[i]); } catch (e) { console.log(`  b 不是合法 JSON：${e.message}`); }
    if (!ja || !jb) {
        console.log(`  a: ${A[i].slice(0, 200)}`);
        console.log(`  b: ${B[i].slice(0, 200)}`);
        continue;
    }
    const who = (o) => `type=${o.type} game=${o.game} hand_no=${o.hand_no} step=${o.step} seat=${o.seat} kind=${o.kind}`;
    console.log(`  a: ${who(ja)}`);
    console.log(`  b: ${who(jb)}`);
    const d = firstDiff(ja, jb, '');
    if (d) {
        console.log(`  第一个不同的字段：${d.path || '<根>'} —— ${d.why}`);
        console.log(`    a: ${short(d.a)}`);
        console.log(`    b: ${short(d.b)}`);
    } else {
        console.log('  JSON 语义相同但字节不同 —— 只能是**键序/空白/数字格式**的差异：'
            + `\n    a 前 120 字符: ${A[i].slice(0, 120)}\n    b 前 120 字符: ${B[i].slice(0, 120)}`);
    }
}
if (!diffs && A.length === B.length) {
    console.log(`8 两份完全相同（${A.length} 行）`.replace('8 ', ''));
    process.exit(0);
}
console.log(`\n共 ${diffs}${diffs > 3 ? '+' : ''} 行不同（a=${A.length} 行 / b=${B.length} 行）`);
process.exit(1);
