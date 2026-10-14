#!/usr/bin/env node
/**
 * **零 gate 判据**（审计盲区⭐，2026-10-13 补）：给定 `net.bin`，直接解析张量表量
 * `heads.policy_gate.*`（+ `fusion.mem.*`）的**非退化性**。
 *
 * 为什么必须有它：`AGENTS.md` §6.5「头是否'上线'= 前向是否读取（⛔ 非'训练过'）」的判据是
 * "去掉该头后动作逐位不变"，而**零初始化的门控会假绿**——
 * `gate = 1 + tanh(policy_gate(sigmoid(bt)))`，`heads.policy_gate.weight` 全 0 时
 * `gate ≡ 1 + tanh(bias)` **与 `bt` 无关** ⇒ `belief_tenpai` **不在推理路径上**，
 * 却仍然"被前向读取、张量存在、形状正确"（`python -m mahjong_ml.v4 check` ⑦′ 就是这条的假绿对照）。
 *
 * ⇒ 判据（一行）：**`heads.policy_gate.weight` 的 `max|w| == 0`（或缺张量，旧网补 0）⇒
 * 该 ckpt 的 `belief_tenpai` 不在推理路径（退化为 `{policy}`）；`> 0` ⇒ 在线，报量级。**
 * `fusion.mem.weight` 同理管"事件塔隐状态 `h_evt` 是否经逐候选门控进决策"。
 *
 * ⚠ 这条是**逐 ckpt 的性质**，不是代码拓扑常量（`V4Policy.ONLINE_HEADS` 只说明"代码读它"）：
 * 表级常量由 `tools/head-tables-check.mjs` 钉，"清零后动作变不变"由 `v4 check` ⑦/⑦′ 实测，
 * 这里钉**权重里那一组数到底是不是 0**。
 *
 * 用法：
 *   node tools/policy-gate-check.mjs                    # 扫 bot-ai/* /net.bin（缺文件=跳过）
 *   node tools/policy-gate-check.mjs <net.bin> [...]     # 只查指定文件
 *   node tools/policy-gate-check.mjs --strict            # 任何退化/缺张量 ⇒ 退出码 1
 * 退出码：0 = 报告完成；1 = --strict 下有退化；2 = 解析失败 / 一个文件都没找到。
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NET_MAGIC = 0x4d4a4e4e;                 // "MJNN"
const NET_FORMAT_V4 = 2;
const HEADER_BYTES = 64;

const args = process.argv.slice(2);
const strict = args.includes('--strict');
const files = args.filter((a) => !a.startsWith('--'));

/** 解析 `net.bin`（v4 格式 2）→ `{magic, format, dims, tensors:{name:{shape,max,w,b}}}`。 */
function parseNet(path) {
    const raw = readFileSync(path);
    if (raw.length < HEADER_BYTES) {
        throw new Error(`太短（${raw.length} B < ${HEADER_BYTES} B）`);
    }
    const magic = raw.readUInt32LE(0);
    if (magic !== NET_MAGIC) {
        throw new Error(`魔数不对：0x${magic.toString(16)}（期望 0x${NET_MAGIC.toString(16)}）`);
    }
    const format = raw.readUInt32LE(4);
    const dims = {
        feature_version: raw.readUInt32LE(8),
        obs_version: raw.readUInt32LE(12),
        derived_version: raw.readUInt32LE(16),
        d_model: raw.readUInt32LE(20),
        tile_d: raw.readUInt32LE(24),
        n_heads: raw.readUInt32LE(28),
        value_bins: raw.readUInt32LE(32),
    };
    if (format !== NET_FORMAT_V4) {
        return { format, dims, tensors: null, bytes: raw.length };
    }
    const nBlocks = raw.readUInt32LE(36);
    const nTensors = raw.readUInt32LE(40);
    let off = HEADER_BYTES;
    const blocks = [];
    for (let i = 0; i < nBlocks; i++) {
        const ln = raw.readUInt16LE(off);
        off += 2;
        const bid = raw.toString('utf8', off, off + ln);
        off += ln;
        const w = raw.readUInt32LE(off);
        off += 4;
        blocks.push([bid, w]);
    }
    const tensors = new Map();
    for (let i = 0; i < nTensors; i++) {
        const ln = raw.readUInt16LE(off);
        off += 2;
        const name = raw.toString('utf8', off, off + ln);
        off += ln;
        const nd = raw[off];
        off += 1;
        const shape = [];
        for (let k = 0; k < nd; k++) {
            shape.push(raw.readUInt32LE(off));
            off += 4;
        }
        const n = shape.reduce((a, b) => a * b, 1);
        // 只对**要判的门控张量**算 max|w|：其余张量跳过（省掉 130 万次读）
        const want = name === 'heads.policy_gate.weight' || name === 'heads.policy_gate.bias'
            || name === 'fusion.mem.weight' || name === 'fusion.mem.bias';
        let maxAbs = 0;
        let sum = 0;
        if (want) {
            for (let k = 0; k < n; k++) {
                const v = Math.abs(raw.readFloatLE(off + 4 * k));
                if (v > maxAbs) {
                    maxAbs = v;
                }
                sum += v;
            }
        }
        if (want) {
            tensors.set(name, { shape, maxAbs, sumAbs: sum, n });
        }
        off += 4 * n;
    }
    if (off !== raw.length) {
        throw new Error(`张量表与头部不符：读完 ${off} B，文件 ${raw.length} B`);
    }
    return { format, dims, tensors, bytes: raw.length, blocks };
}

/** 一个门控组的判定（缺张量 ⇒ 补 0 ⇒ 退化）。 */
function gateVerdict(net, wName, bName, what) {
    const w = net.tensors?.get(wName) ?? null;
    const b = net.tensors?.get(bName) ?? null;
    const missing = w === null;
    const maxW = w === null ? 0 : w.maxAbs;
    const maxB = b === null ? 0 : b.maxAbs;
    return {
        what, wName,
        present: !missing,
        maxW, maxB,
        shape: w === null ? (b === null ? '-' : `? / ${b.shape.join('x')}`) : w.shape.join('x'),
        live: maxW > 0,
        why: missing ? '张量缺失（旧网补 0）' : (maxW > 0 ? '' : '权重全 0'),
    };
}

// ---------------------------------------------------------------- 收集文件
let targets = [];
if (files.length > 0) {
    targets = files.map((f) => ({ label: f, path: f }));
} else {
    const base = join(ROOT, 'bot-ai');
    if (!existsSync(base)) {
        console.error('[policy-gate-check] 找不到 bot-ai/ 目录');
        process.exit(2);
    }
    for (const name of readdirSync(base).sort()) {
        const p = join(base, name, 'net.bin');
        if (existsSync(p) && statSync(p).isFile()) {
            targets.push({ label: name, path: p });
        }
    }
}
if (targets.length === 0) {
    console.error('[policy-gate-check] 一个 net.bin 都没找到（⛔ 不许当"全都非退化"）');
    process.exit(2);
}

// ---------------------------------------------------------------- 逐包
const rows = [];
let parseErrors = 0;
let degenerate = 0;
for (const t of targets) {
    let net;
    try {
        net = parseNet(t.path);
    } catch (e) {
        parseErrors++;
        console.error(`  ✗ ${t.label}：解析失败 —— ${e.message}`);
        continue;
    }
    if (net.tensors === null) {
        rows.push({ label: t.label, fmt: `${net.format}(非 v4)`, note: 'v3 格式 1，不适用' });
        continue;
    }
    const g = gateVerdict(net, 'heads.policy_gate.weight', 'heads.policy_gate.bias', 'belief_tenpai');
    const m = gateVerdict(net, 'fusion.mem.weight', 'fusion.mem.bias', 'h_evt');
    if (!g.live) {
        degenerate++;
    }
    rows.push({
        label: t.label,
        fmt: String(net.format),
        dm: net.dims.d_model,
        gateW: g.maxW, gateB: g.maxB, gateShape: g.shape,
        gateLive: g.live, gateWhy: g.why, gatePresent: g.present,
        memW: m.maxW, memB: m.maxB, memPresent: m.present,
        memLive: m.live,
        verdict: g.live
            ? `在线（belief_tenpai 经 policy_gate 进 policy，max|w|=${g.maxW.toPrecision(3)}）`
            : `**退化** — ${g.why} ⇒ belief_tenpai 不在推理路径（= {policy}）`,
    });
}

const fmtNum = (x) => (x === 0 ? '0' : x.toPrecision(3));
console.log('判据：`heads.policy_gate.weight` 的 max|w| == 0（或缺张量）⇒ 该 ckpt 的 `belief_tenpai`');
console.log('      **不在推理路径**（gate = 1 + tanh(0·sigmoid(bt) + b)，与 bt 无关）；> 0 ⇒ 在线。');
console.log('      `fusion.mem.weight` 同理管事件塔隐状态 `h_evt`。（AGENTS §6.5「头是否上线 = 前向是否读取」）');
console.log('');
console.log('| 包 | 格式 | dm | policy_gate.weight max|w| | bias max|b| | 形状 | fusion.mem.w max|w| | 判定 |');
console.log('| --- | --- | --- | --- | --- | --- | --- | --- |');
for (const r of rows) {
    if (r.note) {
        console.log(`| ${r.label} | ${r.fmt} | - | - | - | - | - | ${r.note} |`);
        continue;
    }
    console.log(`| ${r.label} | ${r.fmt} | ${r.dm} | ${fmtNum(r.gateW)}${r.gatePresent ? '' : ' (缺)'}`
                + ` | ${fmtNum(r.gateB)} | ${r.gateShape} | ${fmtNum(r.memW)}`
                + `${r.memPresent ? '' : ' (缺)'} | ${r.gateLive ? '✅ 在线' : '🟡 退化'} |`);
}
console.log('');
console.log(`合计 ${rows.length} 个 net.bin：在线 ${rows.filter((r) => r.gateLive).length}`
            + ` · 退化 ${degenerate} · 非 v4 ${rows.filter((r) => r.note).length}`
            + ` · 解析失败 ${parseErrors}`);
if (degenerate > 0) {
    console.log('退化包（这些 ckpt 的 `belief_tenpai` 实际不在推理路径）：'
                + rows.filter((r) => r.gateLive === false).map((r) => r.label).join(', '));
}
if (parseErrors > 0) {
    console.error(`[policy-gate-check] FAIL：${parseErrors} 个文件解析失败（⛔ 不许当成"没问题"）`);
    process.exit(2);
}
if (strict && degenerate > 0) {
    console.error(`[policy-gate-check] --strict FAIL：${degenerate} 个包的 policy_gate 退化`);
    process.exit(1);
}
console.log('[policy-gate-check] OK：报告完成（--strict 时退化会以退出码 1 失败）');
