#!/usr/bin/env node
/**
 * v4 **派生段 / 容错位**的「三端同改」机械判据（审计盲区②⑤，2026-10-13 补）。
 *
 * 两件事，都是"设计里写了、但没有任何东西会因为漏改一端而变红"的欠账：
 *
 * 1. **`cand` 派生 ⑨⑩⑪ 恒 0**（`docs/FEATURES-V4.md` §4.3 / `python/.../v4/blocks.py` 的
 *    `cand_matrix` / `server/.../ai/V4Features.candMatrix`）：派生段**声明 11 维**，而三端都只
 *    写**已实现的 8 维**（`DERIVED_CANDIDATE` / `PER_CANDIDATE` / `kPerCandidate`），
 *    末尾 3 维（`score_expected` / `danger_after_all` / `danger_after_riichi`）**恒 0**。
 *    ⇒ 判据 = ① 三段宽度 88 / 11 / 29 / 128 三端同值；② "已实现维数"= 8 三端同值；
 *    ③ 比例尺向量 8 个值逐位相同；④ 文档 §4.3 写的是同一组数（88/11/29/128）+ 同一句"恒 0"。
 *    ⚠ 这一条**只证"定义一致"**；"这一端真的留了 0"由 `SelfTest.candDerivedTests` 在前向张量上量。
 *
 * 2. **`seq_delta` 恒缺省**（`PROTOCOL §8.2`「本实现不发 / 缺省 = 1」）：三端读它时必须都是
 *    「**缺省 1**、夹到 **8**、再 **÷8**」，EVT 槽宽 1、下标 95。任一端换了缺省值或夹取上限，
 *    "同一份事件流"在三端就会算出不同的通道值 —— 而三端对拍用的是 golden 夹具，
 *    夹具里恰好没有 `seq_delta` ⇒ **测不到**。所以这里直接比源码文本里的口径。
 *
 * 用法：`node tools/v4-derived-check.mjs`
 * 退出码：0 = 一致；1 = 不一致（含解析失败 —— ⛔ 解析不到一律 FAIL，不许当"一致"）。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const problems = [];
const info = [];

const read = (rel) => {
    const p = join(ROOT, rel);
    if (!existsSync(p)) {
        problems.push(`文件不存在：${rel}`);
        return null;
    }
    return readFileSync(p, 'utf8').replace(/\r\n/g, '\n');
};

/** 抽一个整数常量（`NAME = 128` / `NAME: int = 128` / `inline constexpr int NAME = 128`）。 */
function intConst(rel, name) {
    const text = read(rel);
    if (text === null) {
        return null;
    }
    const m = text.match(new RegExp(`\\b${name}\\b\\s*(?::[^=\\n]+)?=\\s*([0-9]+)`));
    if (!m) {
        problems.push(`${rel} 里找不到整数常量 ${name}`);
        return null;
    }
    return Number(m[1]);
}

/** 抽一个数向量（`{8f, 34f, ...}` / `(8.0, 34.0, ...)`）—— 归一化成 number[]。
 *  ⚠ 必须锚在 `NAME =`（而不是 `NAME` 的第一次出现）：它常先出现在 javadoc 的 `{@link …}` 里。 */
function numVector(rel, name) {
    const text = read(rel);
    if (text === null) {
        return null;
    }
    // 允许 `NAME[8] = {…}`（C++ 的定长数组）与 `NAME: tuple = (…)` 两种声明形态
    const m = text.match(new RegExp(
        `\\b${name}\\b\\s*(?:\\[[^\\]]*\\])?\\s*(?::[^=\\n]+)?=\\s*([\\[({])`));
    if (!m) {
        problems.push(`${rel} 里找不到 ${name} = (向量)`);
        return null;
    }
    const from = m.index + m[0].length;
    const pairs = { '[': ']', '(': ')', '{': '}' };
    const closer = pairs[m[1]];
    let depth = 1;
    let i = from;
    while (i < text.length && depth > 0) {
        if (text[i] === m[1]) {
            depth++;
        } else if (text[i] === closer) {
            depth--;
        }
        i++;
    }
    const body = text.slice(from, i - 1);
    const out = [...body.matchAll(/([0-9]+(?:\.[0-9]+)?)f?/g)].map((x) => Number(x[1]));
    if (out.length === 0) {
        problems.push(`${rel} 的 ${name} 解析出 0 个数`);
        return null;
    }
    return out;
}

/** 同一组值在三端必须逐位相同。 */
function sameVec(what, entries) {
    const [refName, ref] = entries[0];
    for (const [name, v] of entries.slice(1)) {
        if (v === null || ref === null) {
            continue;
        }
        if (v.join(',') !== ref.join(',')) {
            problems.push(`${what}：${name} = [${v.join(',')}] != ${refName} = [${ref.join(',')}]`);
        } else {
            info.push(`${what}：${name} == ${refName}（${ref.length} 项）✓`);
        }
    }
}

function sameInt(what, entries) {
    const [refName, ref] = entries[0];
    for (const [name, v] of entries.slice(1)) {
        if (v === null || ref === null) {
            continue;
        }
        if (v !== ref) {
            problems.push(`${what}：${name} = ${v} != ${refName} = ${ref}`);
        } else {
            info.push(`${what}：${name} == ${refName}（= ${ref}）✓`);
        }
    }
}

// ================================================================ 1) cand 派生段宽度
const C_CAND = [
    ['Python spec.py', intConst('python/mahjong_ml/v4/spec.py', 'C_CAND')],
    ['Java V4Features', intConst('server/src/main/java/mahjong/ai/V4Features.java', 'C_CAND')],
    ['C++ v4features.hpp', intConst('trainer/src/v4features.hpp', 'kCCand')],
];
const CAND_BASE = [
    ['Java V4Features', intConst('server/src/main/java/mahjong/ai/V4Features.java', 'CAND_BASE')],
    ['C++ v4features.hpp', intConst('trainer/src/v4features.hpp', 'kCandBase')],
];
const CAND_DERIVED = [
    ['Python spec.py', intConst('python/mahjong_ml/v4/spec.py', 'CAND_DERIVED')],
    ['Java V4Features', intConst('server/src/main/java/mahjong/ai/V4Features.java', 'CAND_DERIVED')],
    ['C++ v4features.hpp', intConst('trainer/src/v4features.hpp', 'kCandDerived')],
];
const CAND_RESERVED = [
    ['Python spec.py', null],
    ['Java V4Features', null],
    ['C++ v4features.hpp', null],
];
sameInt('cand 总宽', C_CAND);
sameInt('cand 基础段宽', CAND_BASE);
sameInt('cand 派生段宽（声明）', CAND_DERIVED);
// 预留段三端都写成算式（`C_CAND - 88 - CAND_DERIVED`）⇒ 机械地自己算，别去解析表达式
if (CAND_BASE[0][1] !== null && CAND_DERIVED[0][1] !== null && C_CAND[0][1] !== null) {
    CAND_RESERVED[0][1] = C_CAND[0][1] - CAND_BASE[0][1] - CAND_DERIVED[0][1];
    CAND_RESERVED[1][1] = CAND_RESERVED[0][1];
    CAND_RESERVED[2][1] = CAND_RESERVED[0][1];
    const sum = CAND_BASE[0][1] + CAND_DERIVED[0][1] + CAND_RESERVED[0][1];
    if (sum !== C_CAND[0][1]) {
        problems.push(`cand 三段之和不等于总宽：${CAND_BASE[0][1]} + ${CAND_DERIVED[0][1]}`
                      + ` + ${CAND_RESERVED[0][1]} = ${sum} != ${C_CAND[0][1]}`);
    } else {
        info.push(`cand 三段之和 == 总宽（${CAND_BASE[0][1]} + ${CAND_DERIVED[0][1]}`
                  + ` + ${CAND_RESERVED[0][1]} = ${sum}）✓`);
    }
    if (CAND_RESERVED[0][1] !== 29) {
        problems.push(`cand 预留段算出来是 ${CAND_RESERVED[0][1]} != 29（文档与三端同改锚点）`);
    }
}

// 「已实现维数」= 8（三端同名）：⑨⑩⑪ 就是这么来的 3 列
const IMPLEMENTED = [
    ['Python features.py', intConst('python/mahjong_ml/features.py', 'DERIVED_CANDIDATE')],
    ['Java ObsFeatures', intConst('server/src/main/java/mahjong/ai/ObsFeatures.java',
        'PER_CANDIDATE')],
    ['C++ obffeatures.hpp', intConst('trainer/src/obffeatures.hpp', 'kPerCandidate')],
];
sameInt('cand 已实现派生维数（①–⑧）', IMPLEMENTED);
const nImpl = IMPLEMENTED[0][1];
const nDecl = CAND_DERIVED[0][1];
if (nImpl !== null && nDecl !== null) {
    if (nDecl - nImpl !== 3) {
        problems.push(`cand 派生段里"尚未接线的维数" = ${nDecl} - ${nImpl} = ${nDecl - nImpl}`
                      + ' != 3（⑨⑩⑪）—— 声明/实现漂了，文档与三端要一起改');
    } else {
        info.push(`cand 派生段 = 已实现 ${nImpl} + 未接线 3（⑨⑩⑪）✓`);
    }
}

// 比例尺向量（`DERIVED_CAND_SCALE` / `DERIVED_SCALE_CAND` / `kDerivedCandScale`）逐位相同
sameVec('cand 派生比例尺', [
    ['Java Features.DERIVED_CAND_SCALE',
        numVector('server/src/main/java/mahjong/ai/Features.java', 'DERIVED_CAND_SCALE')],
    ['Python features.DERIVED_SCALE_CAND',
        numVector('python/mahjong_ml/features.py', 'DERIVED_SCALE_CAND')],
    ['C++ kDerivedCandScale', numVector('trainer/src/net.cpp', 'kDerivedCandScale')],
]);
const scale = numVector('server/src/main/java/mahjong/ai/Features.java', 'DERIVED_CAND_SCALE');
if (scale !== null && nImpl !== null && scale.length !== nImpl) {
    problems.push(`比例尺长度 ${scale.length} != 已实现维数 ${nImpl}（加一维派生量要同时加尺子）`);
} else if (scale !== null) {
    info.push(`比例尺长度 == 已实现维数（${scale.length}）✓`);
}

// `blocks.py` 的 `cand_matrix` 必须**只**消费已实现的那 8 维（这就是"⑨⑩⑪ 恒 0"的机制）
const blocks = read('python/mahjong_ml/v4/blocks.py');
if (blocks !== null) {
    if (!/\[:f3\.DERIVED_CANDIDATE\]/.test(blocks)) {
        problems.push('blocks.py 的 cand_matrix 没有按 `f3.DERIVED_CANDIDATE` 截断'
                      + '（⑨⑩⑪ 恒 0 的机制被改掉了？）');
    } else {
        info.push('blocks.py：`cand_matrix` 按 `DERIVED_CANDIDATE` 截断 ✓');
    }
    if (!/v4 新增 3 维/.test(blocks)) {
        problems.push('blocks.py 里找不到"v4 新增 3 维…先留 0"的锚点注释（口径笔记被删了？）');
    } else {
        info.push('blocks.py：⑨⑩⑪「先留 0」锚点注释在 ✓');
    }
}

// 文档 §4.3 必须写同一组数 + 同一句"恒 0"
const features = read('docs/FEATURES-V4.md');
if (features !== null) {
    const a = features.indexOf('### 4.3');
    const b = features.indexOf('### 4.4');
    if (a < 0 || b < 0 || b <= a) {
        problems.push('docs/FEATURES-V4.md 里找不到 §4.3 / §4.4 的边界（章节被挪了？）');
    } else {
        const sec = features.slice(a, b);
        for (const [what, want] of [['总宽', String(C_CAND[0][1])],
            ['基础段', String(CAND_BASE[0][1])], ['派生段', String(CAND_DERIVED[0][1])],
            ['预留段', String(CAND_RESERVED[0][1])]]) {
            if (!sec.includes(want)) {
                problems.push(`docs/FEATURES-V4.md §4.3 里没有写 ${what} ${want}`);
            }
        }
        if (!/⑨⑩⑪[^\n]*恒 0/.test(sec)) {
            problems.push('docs/FEATURES-V4.md §4.3 里没有「⑨⑩⑪ … 恒 0」那句'
                          + '（三端同改的锚点之一）');
        } else {
            info.push('docs/FEATURES-V4.md §4.3：88/11/29/128 + 「⑨⑩⑪ 恒 0」在 ✓');
        }
    }
}

// ================================================================ 2) seq_delta 恒缺省
const SEQ_READERS = [
    ['Python blocks.py', 'python/mahjong_ml/v4/blocks.py'],
    ['Java V4Features.java', 'server/src/main/java/mahjong/ai/V4Features.java'],
    ['C++ v4features.cpp', 'trainer/src/v4features.cpp'],
];
for (const [name, rel] of SEQ_READERS) {
    const text = read(rel);
    if (text === null) {
        continue;
    }
    const line = text.split('\n').find((l) => l.includes('seq_delta') && /8\.0/.test(l)
                                                    && /\/\s*8/.test(l));
    if (!line) {
        problems.push(`${name} 里找不到 seq_delta 的读取式（缺省 1 / 夹 8 / ÷8 三件事同一行）`);
        continue;
    }
    if (!/seq_delta"[^,]*,\s*1(?:\.0)?\s*\)/.test(line)) {
        problems.push(`${name} 的 seq_delta 缺省值不是 1：${line.trim()}`);
    } else if (!/min\s*\(/.test(line)) {
        problems.push(`${name} 的 seq_delta 没有夹取（min(…, 8.0)）：${line.trim()}`);
    } else {
        info.push(`${name}：seq_delta = min(缺省 1, 8)/8 ✓`);
    }
}
// EVT 槽：seq_delta 是**最后**一个字段、宽 1（三端同序）
const EVT_SLOT = [
    ['Python spec.py', read('python/mahjong_ml/v4/spec.py'), /\(\s*"seq_delta"\s*,\s*1\s*\)/],
    ['Java EVT_FIELDS/EVT_WIDTHS',
        read('server/src/main/java/mahjong/ai/V4Features.java'),
        /"rip_phase",\s*"seq_delta",\s*\};\s*public static final int\[\] EVT_WIDTHS = \{[^}]*, 1\};/],
    ['C++ kEvtFields/kEvtWidths',
        read('trainer/src/v4features.cpp'),
        /"rip_phase",\s*"seq_delta",\s*\};\s*const int kEvtWidths\[13\] = \{[^}]*, 1\};/],
];
for (const [name, text, re] of EVT_SLOT) {
    if (text === null) {
        continue;
    }
    if (!re.test(text)) {
        problems.push(`${name} 里 seq_delta 不是"最后一个 EVT 槽、宽 1"（槽位漂了 ⇒ 通道下标不再是 95）`);
    } else {
        info.push(`${name}：seq_delta = 末槽、宽 1 ✓`);
    }
}
// 文档必须写"本实现不发 / 缺省 = 1"
for (const [name, rel, re] of [
    ['PROTOCOL §8.2', 'docs/PROTOCOL.md', /`seq_delta`[^\n]*本实现不发[^\n]*缺省 = 1/],
    ['FEATURES-V4.md §4.2', 'docs/FEATURES-V4.md', /`seq_delta`[^\n]*恒缺省（=1）/],
]) {
    const text = read(rel);
    if (text === null) {
        continue;
    }
    if (!re.test(text)) {
        problems.push(`${name} 里找不到「seq_delta 本实现不发 / 缺省 = 1」的判据行`);
    } else {
        info.push(`${name}：seq_delta 缺省口径在 ✓`);
    }
}

// ================================================================ 汇总
for (const l of info) {
    console.log('  ' + l);
}
if (problems.length > 0) {
    console.error('[v4-derived-check] FAIL：派生段 / 容错位的三端口径不一致');
    for (const p of problems) {
        console.error('  ✗ ' + p);
    }
    process.exit(1);
}
console.log(`[v4-derived-check] PASS：cand 派生段 ${CAND_BASE[0][1]}+${CAND_DERIVED[0][1]}+`
            + `${CAND_RESERVED[0][1]}=${C_CAND[0][1]}（已实现 ${nImpl} 维，⑨⑩⑪ 恒 0）`
            + '与 seq_delta 缺省口径（min(1,8)/8、末槽宽 1）在三端源码 + 两份文档同名同值');
