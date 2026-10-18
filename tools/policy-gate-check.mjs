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
/** 格式 3 = 64 B 头 + **arch 段** + 块表 + 张量表（`ARCH-REDESIGN` §11）。 */
const NET_FORMAT_V4 = 3;
/** 格式 2 = A− 之前的老包（硬切退役；只有冻结快照能读，但门控仍可解析）。 */
const NET_FORMAT_V2 = 2;
const HEADER_BYTES = 64;
const ARCH_CORE_BYTES = 96;
const ARCH_SEG_BYTES = 2048;
const TOPO_IDS = new Map([[2, 'v4'], [3, 'a-']]);
const FLAG_B3 = 1;
const FLAG_TILE_ID = 1 << 1;
const FLAG_FILM = 1 << 2;
const FLAG_MEM_GATE = 1 << 3;

const args = process.argv.slice(2);
const strict = args.includes('--strict');
const files = args.filter((a) => !a.startsWith('--'));

/**
 * 解析 `net.bin` → `{format, topo, arch, dims, tensors:{name:{shape,maxAbs,sumAbs,n}}}`。
 *
 * ⚠ **格式 3 的块表从 `64 + archLen` 开始**（`archLen` = 整段 2,048 B）：少读这一段就是
 * "张量名乱码 / 恰好读到尾"那类静默错位（`A-REFACTOR-IMPL-PLAN` §5 风险 3）。
 * ⚠ 格式 2 没有 arch 段（块表就在 64）——**仍要能解析**：9 个 `bot-ai/` 老包要判门控，
 * 只是判据里写明它们**已被硬切退役**（新服务端 `V4Policy` 拒载）。⛔ 不许把"读不了"当成"没问题"。
 */
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
    if (format !== NET_FORMAT_V4 && format !== NET_FORMAT_V2) {
        return { format, topo: null, arch: null, dims, tensors: null, bytes: raw.length };
    }
    let off = HEADER_BYTES;
    let arch = null;
    if (format === NET_FORMAT_V4) {
        if (raw.length < HEADER_BYTES + 4) {
            throw new Error(`被截断：连 arch 段的 archLen 都读不到（${raw.length} B）`);
        }
        const alen = raw.readUInt32LE(HEADER_BYTES);
        if (alen < ARCH_CORE_BYTES || alen > ARCH_SEG_BYTES || alen % 4 !== 0) {
            throw new Error(`archLen=${alen} 越界（必须在 [${ARCH_CORE_BYTES}, ${ARCH_SEG_BYTES}] 且 4 的倍数）`);
        }
        if (raw.length < HEADER_BYTES + alen) {
            throw new Error(`arch 段声明 ${alen} B，文件只剩 ${raw.length - HEADER_BYTES} B`);
        }
        const s = HEADER_BYTES;
        const topoId = raw.readUInt16LE(s + 6);
        arch = {
            archLen: alen,
            archVersion: raw.readUInt16LE(s + 4),
            topoId,
            topoName: TOPO_IDS.get(topoId) ?? `未知(${topoId})`,
            flags: raw[s + 27],
            valueBins: raw.readUInt16LE(s + 28),
            valueRange: raw.readFloatLE(s + 32),
            nHeads: raw.readUInt16LE(s + 30),
            dTile: raw.readUInt16LE(s + 8),
            dFuse: raw.readUInt16LE(s + 12),
            actId: raw[s + 23],
            normId: raw[s + 24],
            segEmbMode: raw[s + 25],
            memTokenMode: raw[s + 26],
        };
        if (arch.archVersion !== 1) {
            throw new Error(`archVersion=${arch.archVersion} 不支持（本工具只认 1）`);
        }
        if (!TOPO_IDS.has(topoId)) {
            throw new Error(`topoId=${topoId} 未注册（已注册：2=v4 / 3=a-）`);
        }
        if ((arch.flags & ~(FLAG_B3 | FLAG_TILE_ID | FLAG_FILM | FLAG_MEM_GATE)) !== 0) {
            throw new Error(`arch flags=0x${arch.flags.toString(16)} 用了保留位（bit4–7 必须为 0）`);
        }
        off = HEADER_BYTES + alen;
    }
    const nBlocks = raw.readUInt32LE(36);
    const nTensors = raw.readUInt32LE(40);
    const blocks = [];
    for (let i = 0; i < nBlocks; i++) {
        if (off + 2 > raw.length) {
            throw new Error(`块表越界（第 ${i} 个块的 id 长度，偏移 ${off}）`);
        }
        const ln = raw.readUInt16LE(off);
        off += 2;
        if (off + ln + 4 > raw.length) {
            throw new Error(`块表越界（第 ${i} 个块，偏移 ${off}）`);
        }
        const bid = raw.toString('utf8', off, off + ln);
        off += ln;
        const w = raw.readUInt32LE(off);
        off += 4;
        blocks.push([bid, w]);
    }
    const tensors = new Map();
    for (let i = 0; i < nTensors; i++) {
        if (off + 2 > raw.length) {
            throw new Error(`张量表越界（第 ${i} 个张量的名字长度，偏移 ${off}）`);
        }
        const ln = raw.readUInt16LE(off);
        off += 2;
        if (off + ln + 1 > raw.length) {
            throw new Error(`张量表越界（第 ${i} 个张量的名字，偏移 ${off}）`);
        }
        const name = raw.toString('utf8', off, off + ln);
        off += ln;
        const nd = raw[off];
        off += 1;
        const shape = [];
        for (let k = 0; k < nd; k++) {
            if (off + 4 > raw.length) {
                throw new Error(`张量 ${name} 的形状越界（偏移 ${off}）`);
            }
            shape.push(raw.readUInt32LE(off));
            off += 4;
        }
        const n = shape.reduce((a, b) => a * b, 1);
        if (off + 4 * n > raw.length) {
            throw new Error(`张量 ${name} 的数据越界（需要 ${4 * n} B，剩 ${raw.length - off} B）`);
        }
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
        throw new Error(`张量表与头部/arch 段不符：读完 ${off} B，文件 ${raw.length} B`);
    }
    return { format, topo: arch ? arch.topoName : 'v4(无 arch 段)', arch, dims, tensors, bytes: raw.length, blocks };
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
    const isA = net.arch?.topoName === 'a-';
    // ⚠ `a-` **没有** mem 门：`h_evt` 改作第 95 个 token（0 参数，§2.3）⇒ 这一列不是"退化"。
    const m = isA ? null : gateVerdict(net, 'fusion.mem.weight', 'fusion.mem.bias', 'h_evt');
    const retired = net.format === NET_FORMAT_V2;
    if (!g.live) {
        degenerate++;
    }
    rows.push({
        label: t.label,
        fmt: String(net.format),
        topo: net.topo,
        retired,
        dm: net.dims.d_model,
        gateW: g.maxW, gateB: g.maxB, gateShape: g.shape,
        gateLive: g.live, gateWhy: g.why, gatePresent: g.present,
        memW: m === null ? null : m.maxW,
        memB: m === null ? null : m.maxB,
        memPresent: m === null ? null : m.present,
        memLive: m === null ? null : m.live,
        verdict: g.live
            ? `在线（belief_tenpai 经 policy_gate 进 policy，max|w|=${g.maxW.toPrecision(3)}）`
            : `**退化** — ${g.why} ⇒ belief_tenpai 不在推理路径（= {policy}）`,
    });
}

const fmtNum = (x) => (x === 0 ? '0' : x.toPrecision(3));
console.log('判据：`heads.policy_gate.weight` 的 max|w| == 0（或缺张量）⇒ 该 ckpt 的 `belief_tenpai`');
console.log('      **不在推理路径**（gate = 1 + tanh(0·sigmoid(bt) + b)，与 bt 无关）；> 0 ⇒ 在线。');
console.log('      `fusion.mem.weight` 同理管事件塔隐状态 `h_evt`（v4）；**a- 没有 mem 门**——`h_evt`');
console.log('      是第 95 个 token（0 参数，§2.3）⇒ 该列记 "—"。'
            + '（AGENTS §6.5「头是否上线 = 前向是否读取」）');
console.log('');
console.log('| 包 | 格式 | 拓扑 | dm | policy_gate.weight max|w| | bias max|b| | 形状 | fusion.mem.w max|w| | 判定 |');
console.log('| --- | --- | --- | --- | --- | --- | --- | --- | --- |');
for (const r of rows) {
    if (r.note) {
        console.log(`| ${r.label} | ${r.fmt} | - | - | - | - | - | - | ${r.note} |`);
        continue;
    }
    const memCell = r.memW === null ? '—（a- 无 mem 门）'
        : `${fmtNum(r.memW)}${r.memPresent ? '' : ' (缺)'}`;
    const topoCell = r.retired ? `${r.topo}（格式 2 已退役）` : r.topo;
    console.log(`| ${r.label} | ${r.fmt} | ${topoCell} | ${r.dm} | ${fmtNum(r.gateW)}${r.gatePresent ? '' : ' (缺)'}`
                + ` | ${fmtNum(r.gateB)} | ${r.gateShape} | ${memCell}`
                + ` | ${r.gateLive ? '✅ 在线' : '🟡 退化'} |`);
}
console.log('');
console.log(`合计 ${rows.length} 个 net.bin：在线 ${rows.filter((r) => r.gateLive).length}`
            + ` · 退化 ${degenerate} · 非 v4 ${rows.filter((r) => r.note).length}`
            + ` · 格式 2（已退役）${rows.filter((r) => r.retired).length}`
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
