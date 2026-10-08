#!/usr/bin/env node
/**
 * 头表**跨端一致性**判据：`ONLINE_HEADS`（推理必需）/ `TRAIN_HEADS`（训练与契约必需）
 * 必须在**三端源码 + 唯一规格文档**里同名同值。
 *
 * 背景（2026-10-07）：这两个概念以前混在一个词"上线必需"里，`docs/TRAINING-V4.md` 的三处口径互相打架，
 * 谁也说不清"哪些头在决定打哪张"。现在拆成两张表，规格写在 `docs/TRAINER-CPP.md` §6.25；
 * 但**规格只写在文档里就会漂**（本仓已经漂过：`inference_heads` 这个名字把"训练必需"说成"上线必需"），
 * 所以这里把"三端 + 文档同值"变成机械判据。
 *
 * 用法：`node tools/head-tables-check.mjs`
 *
 * 判据（全部必须满足，任一不满足即 FAIL）：
 *   ① Python 侧**实际运行**取真值（`model.ONLINE_HEADS` / `model.TRAIN_HEADS`）—— 不用正则猜；
 *   ② Java 侧 `V4Policy.ONLINE_HEADS` / `TRAIN_HEADS` 的**字面量**与 ①相同；
 *   ③ C++ 侧 `trainer/src/v4policy.hpp` 的两个常量与 ①相同；
 *   ④ `docs/TRAINER-CPP.md` §6.25 的表里同时出现这两个集合的文本；
 *   ⑤ 行为判据（"清零后动作是否变"）不在本脚本里 —— 它由 `python -m mahjong_ml.v4 check` ⑦/⑦′
 *      与 `node tools/trainer-v4-parity.mjs --golden`（Java/C++ 的 `dValue/dBelief/dGate`）分别实测。
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const problems = [];
const info = [];

/** 从源码里抽一个"名字 = 一串字符串字面量"的集合（跨行、容忍 `{…}` / `(…)` / `List.of(…)`）。 */
function literals(file, name) {
    if (!existsSync(join(ROOT, file))) {
        return null;
    }
    const lines = readFileSync(join(ROOT, file), 'utf8').split('\n');
    for (let i = 0; i < lines.length; i++) {
        if (!new RegExp(`\\b${name}\\b\\s*[:=]`).test(lines[i])) {
            continue;
        }
        let buf = '';
        for (let j = i; j < Math.min(lines.length, i + 8); j++) {
            buf += lines[j] + '\n';
            if (/[})];?\s*$/.test(lines[j]) && /[})]/.test(buf.slice(buf.indexOf(name)))) {
                break;
            }
        }
        const out = [...buf.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
        if (out.length > 0) {
            return out;
        }
    }
    return null;
}

// ---------------------------------------------------------------- ① Python 真值
const pyExe = join(ROOT, 'python', '.venv', 'Scripts', 'python.exe');
let truth = null;
try {
    const raw = execFileSync(pyExe, ['-c',
        'from mahjong_ml.v4 import model as m;'
        + 'print(",".join(m.ONLINE_HEADS));print(",".join(m.TRAIN_HEADS))'],
        { cwd: join(ROOT, 'python'), encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
        .split(/\r?\n/).map((s) => s.trim()).filter((s) => s !== '');
    truth = { online: raw[0].split(','), contract: raw[1].split(',') };
    info.push(`Python（实跑）: ONLINE=${truth.online.join(',')}  TRAIN=${truth.contract.join(',')}`);
} catch (e) {
    problems.push(`① Python 侧取真值失败（${pyExe}）：${String(e.message).split('\n')[0]}`
                  + ' —— 说明符/依赖不在位，判据无法建立');
}

// ---------------------------------------------------------------- ②③ 三端字面量
const ENDS = [
    ['Java', 'server/src/main/java/mahjong/ai/V4Policy.java'],
    ['C++', 'trainer/src/v4policy.hpp'],
];
if (truth) {
    for (const [end, file] of ENDS) {
        for (const [key, want] of [['ONLINE_HEADS', truth.online], ['TRAIN_HEADS', truth.contract]]) {
            const got = literals(file, key);
            if (got === null) {
                problems.push(`③ ${end} 侧 ${file} 里找不到 ${key}（或它为空）—— 三端未统一`);
                continue;
            }
            if (got.join(',') !== want.join(',')) {
                problems.push(`${end} 侧 ${key} = ${got.join(',')} ≠ Python ${want.join(',')}`);
            } else {
                info.push(`${end}（字面量）: ${key} = ${got.join(',')} ✓`);
            }
        }
    }
}

// ---------------------------------------------------------------- ④ 规格文档
const specFile = 'docs/TRAINER-CPP.md';
const spec = readFileSync(join(ROOT, specFile), 'utf8');
// 文档里可能写成 `('policy', 'belief_tenpai')` / `policy, belief_tenpai` / 反引号包起来 —— 归一化后再比
const flat = spec.replace(/[`'"\s]/g, '');
if (!spec.includes('### 6.25')) {
    problems.push(`④ ${specFile} 里找不到 §6.25（两张表的唯一规格）`);
} else if (truth) {
    for (const [key, want] of [['ONLINE_HEADS', truth.online], ['TRAIN_HEADS', truth.contract]]) {
        const joined = want.join(',');
        if (!flat.includes(joined)) {
            problems.push(`④ ${specFile} §6.25 的 ${key} 文本与 Python 不一致（缺 ${joined}）`);
        } else {
            info.push(`${specFile} §6.25: ${key} 文本一致 ✓`);
        }
    }
}

for (const l of info) {
    console.log('  ' + l);
}
if (problems.length > 0) {
    console.error('[head-tables-check] FAIL：三端 / 文档的头表不一致');
    for (const p of problems) {
        console.error('  ✗ ' + p);
    }
    process.exit(1);
}
console.log('[head-tables-check] PASS：三端源码 + §6.25 规格同名同值'
            + `（online=${truth.online.join(',')} · contract=${truth.contract.join(',')}）`
            + '；行为判据见 `v4 check` ⑦/⑦′ 与 `trainer-v4-parity --golden` 的 d* 字段');
