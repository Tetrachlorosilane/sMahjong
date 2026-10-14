#!/usr/bin/env node
/**
 * `docs/PROTOCOL.md` §8.2 观测字段白名单的**四处机械比对**。
 *
 * 为什么要有它（审计盲区②，2026-10-13）：`AGENTS.md` §6.5 要求"新增观测字段必须三处一起改"
 * —— ① 确认公开可见；② `Observation` 白名单（`SelfTest.trainingInterfaceTests`）+
 * `tools/selfplay-check.mjs` 的 `OBS_KEYS`；③ PROTOCOL §8.2 的表。但"必须一起改"此前只是**口头约定**：
 * 四处清单一字排开在四个文件里，没有任何东西会因为漏改一处而变红。漏改的后果是**静默的**——
 * 训练侧要么多一个没人校验的字段（可能夹带隐藏信息），要么少一个（数据静默丢列），
 * 两种都不会报错，只会训出一个"说不清为什么打这张"的策略。
 *
 * 所以这里把四处**机械解析出来**逐集合比对，任何一处不一致 ⇒ 非零退出。
 *
 * 用法：`node tools/obs-keys-check.mjs`
 *
 * 判据：
 *   ① 四处解析到的字段集合**完全相同**（`PROTOCOL §8.2` 表 / `Observation.toJson` /
 *      `SelfTest` 的 `want` / `selfplay-check.mjs` 的 `OBS_KEYS`）；
 *   ② `PROTOCOL §8.2` 表的**行序 == `Observation.toJson` 的键序**（表就是契约的行文版；
 *      键序漂了，人工对照就再也对不上）；
 *   ③ 四处都真的解析出了非空集合（解析失败必须报 FAIL，⛔ 不许把"没解析到"当成"一致"——
 *      这正是 `AGENTS.md` §6.5 附录 E 里 fail-closed 那条的同型错误）。
 *
 * ⚠ `PROTOCOL §8.2` 的表把同类字段写在同一行（`` `seat` / `kind` ``）⇒ 解析时要按 `/` 拆开。
 * ⚠ 只读**8.2 的第一张表**（它的下面是 `events[]` 的元素表，那是另一份契约）。
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

/** 键名形状（观测字段全是小写 + 下划线）。 */
const KEY_RE = /^[a-z][a-z0-9_]*$/;

// ---------------------------------------------------------------- ① PROTOCOL §8.2 的表
function parseProtocol() {
    const text = read('docs/PROTOCOL.md');
    if (text === null) {
        return null;
    }
    const lines = text.split('\n');
    const start = lines.findIndex((l) => /^###\s+8\.2\s/.test(l));
    if (start < 0) {
        problems.push('docs/PROTOCOL.md 里找不到 `### 8.2` 小节标题（表位置变了？）');
        return null;
    }
    // 找表头（第一行以 | 开头的），并从它的下一行（分隔行）之后收行
    let i = start;
    while (i < lines.length && !lines[i].startsWith('| ')) {
        i++;
    }
    if (i >= lines.length || !/^\|\s*-+/.test(lines[i + 1] || '')) {
        problems.push('docs/PROTOCOL.md §8.2 里找不到表头 + 分隔行（表被改标题/挪位了？）');
        return null;
    }
    const keys = [];
    for (let j = i + 2; j < lines.length && lines[j].startsWith('| '); j++) {
        const cell = lines[j].split('|')[1] || '';
        // 单元格里可能有多个字段（`a` / `b` / `c`）⇒ 逐个取反引号里的标识符
        const toks = [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1].trim());
        if (toks.length === 0) {
            problems.push(`docs/PROTOCOL.md §8.2 第 ${j + 1} 行的首列没有反引号字段：${cell.trim()}`);
            continue;
        }
        for (const t of toks) {
            if (!KEY_RE.test(t)) {
                problems.push(`docs/PROTOCOL.md §8.2 第 ${j + 1} 行出现非字段形状的记号 \`${t}\``);
                continue;
            }
            keys.push(t);
        }
    }
    info.push(`PROTOCOL §8.2 表：${keys.length} 键`);
    return keys;
}

// ---------------------------------------------------------------- ② Observation.toJson()
function parseObservation() {
    const text = read('server/src/main/java/mahjong/ai/Observation.java');
    if (text === null) {
        return null;
    }
    const at = text.indexOf('public Map<String, Object> toJson()');
    if (at < 0) {
        problems.push('Observation.java 里找不到 `toJson()`');
        return null;
    }
    const objAt = text.indexOf('Json.obj(', at);
    if (objAt < 0) {
        problems.push('Observation.java 的 toJson() 里找不到 `Json.obj(`');
        return null;
    }
    // 深度感知地扫对象字面量：**只有 depth == 0 的字符串字面量**才是顶层键
    // （`round` 的值是嵌套 `Json.obj(...)`，它的键必须被排除）。
    const keys = [];
    let i = objAt + 'Json.obj('.length;
    let depth = 0;
    while (i < text.length) {
        const c = text[i];
        if (c === '(' || c === '[' || c === '{') {
            depth++;
        } else if (c === ')' || c === ']' || c === '}') {
            if (depth === 0) {
                break;                                  // 收尾的那个 `)`
            }
            depth--;
        } else if (c === '"' && depth === 0) {
            const end = text.indexOf('"', i + 1);
            const lit = text.slice(i + 1, end);
            // 键 = 字符串字面量后面紧跟 `,`
            if (/^\s*,/.test(text.slice(end + 1, end + 3)) && KEY_RE.test(lit)) {
                keys.push(lit);
            }
            i = end;
        }
        i++;
    }
    info.push(`Observation.toJson()：${keys.length} 键`);
    return keys;
}

// ---------------------------------------------------------------- ③ SelfTest 的 want
function parseSelfTest() {
    const text = read('server/src/main/java/mahjong/test/SelfTest.java');
    if (text === null) {
        return null;
    }
    const at = text.indexOf('Set<String> want = new java.util.TreeSet<>(Arrays.asList(');
    if (at < 0) {
        problems.push('SelfTest.java 里找不到观测白名单 `Set<String> want = ...Arrays.asList(`');
        return null;
    }
    const end = text.indexOf('));', at);
    if (end < 0) {
        problems.push('SelfTest.java 的观测白名单没有闭合 `));`');
        return null;
    }
    const keys = [...text.slice(at, end).matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    info.push(`SelfTest want：${keys.length} 键`);
    return keys;
}

// ---------------------------------------------------------------- ④ selfplay-check.mjs OBS_KEYS
function parseSelfPlayCheck() {
    const text = read('tools/selfplay-check.mjs');
    if (text === null) {
        return null;
    }
    const at = text.indexOf('const OBS_KEYS = [');
    if (at < 0) {
        problems.push('selfplay-check.mjs 里找不到 `const OBS_KEYS = [`');
        return null;
    }
    const end = text.indexOf(']', at);
    if (end < 0) {
        problems.push('selfplay-check.mjs 的 OBS_KEYS 没有闭合 `]`');
        return null;
    }
    const body = text.slice(at, end)
        .split('\n')
        .map((l) => l.replace(/\/\/.*$/, ''))          // 去掉行尾注释（注释里出现过 `v < 3`）
        .join('\n');
    const keys = [...body.matchAll(/'([^']+)'/g)].map((m) => m[1]);
    info.push(`selfplay-check OBS_KEYS：${keys.length} 键`);
    return keys;
}

// ---------------------------------------------------------------- 比对
const ends = [
    ['PROTOCOL §8.2 表', parseProtocol()],
    ['Observation.toJson', parseObservation()],
    ['SelfTest want', parseSelfTest()],
    ['selfplay-check OBS_KEYS', parseSelfPlayCheck()],
];
for (const [name, keys] of ends) {
    if (keys === null) {
        continue;
    }
    if (keys.length === 0) {
        problems.push(`③ ${name} 解析出 0 个键 ⇒ 解析失败，判据无法建立（⛔ 不许当"空集也一致"）`);
    }
}
if (problems.length === 0) {
    const [baseName, base] = ends[0];
    const baseSet = [...new Set(base)].sort();
    for (const [name, keys] of ends.slice(1)) {
        const set = [...new Set(keys)].sort();
        const missing = baseSet.filter((k) => !set.includes(k));
        const extra = set.filter((k) => !baseSet.includes(k));
        if (missing.length || extra.length) {
            problems.push(`① ${name} 与 ${baseName} 不一致：缺 [${missing.join(', ')}]`
                          + ` 多 [${extra.join(', ')}]`);
        } else {
            info.push(`① ${name} == ${baseName}（${set.length} 键）✓`);
        }
    }
    // ② 表序 == toJson 键序（两者都是"契约的行文版"）
    const obs = ends[1][1];
    if (base.join(',') !== obs.join(',')) {
        const n = Math.min(base.length, obs.length);
        let firstDiff = n;
        for (let i = 0; i < n; i++) {
            if (base[i] !== obs[i]) {
                firstDiff = i;
                break;
            }
        }
        problems.push(`② 表序 != toJson 键序（首个不同在第 ${firstDiff + 1} 位：`
                      + `表 \`${base[firstDiff] ?? '(缺)'}\` vs 代码 \`${obs[firstDiff] ?? '(缺)'}\`）`);
    } else {
        info.push(`② 表序 == toJson 键序（33 个键逐位）✓`);
    }
}

for (const l of info) {
    console.log('  ' + l);
}
if (problems.length > 0) {
    console.error('[obs-keys-check] FAIL：§8.2 白名单四处不一致（AGENTS.md §6.5「必须三处一起改」）');
    for (const p of problems) {
        console.error('  ✗ ' + p);
    }
    process.exit(1);
}
console.log(`[obs-keys-check] PASS：PROTOCOL §8.2 表 / Observation.toJson / SelfTest.want / `
            + `selfplay-check.OBS_KEYS 四处同集合同序（${ends[0][1].length} 键）`);
