#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎：规则判据差分对拍**（向听 / 进张 / 听牌形）。
 *
 *   node tools/trainer-rule-parity.mjs [shanten|of|discard|all] [行数] [--tenpai-only|--random-only]
 *
 * `--tenpai-only` / `--random-only` 只影响**语料构造**：前者全是"拼出和了形再拆一张"的手
 * （专打 `waitShapes` 那条路），后者全是随机暗手（专打 `advanceKinds` 那条路）——
 * 用来把 M1 的两段性能分别量干净（§5 的判据 ②）。
 * 做什么：按同一份**确定性语料**（`meldCount c0..c33 v0..v33`）分别跑
 *   ① Java 侧只读探针 `tools/RuleProbe.java`（用的是 `Shanten.min` / `HandEval.of` / `afterDiscard`）
 *   ② C++ 侧 `trainer/build/trainer.exe rules <corpus> <mode> <out>`
 * 然后**逐行逐字段**比对（含两个 34 维数组的 FNV-1a 哈希：数组级差异也逃不掉）。
 *
 * 为什么要有它：`HandEval` 是观测特征（特征 v3 起 615/96）里最贵也最容易写错的一块 —— C++ 侧用
 * **花色分组 5 进制查表**替代 Java 的逐手 DFS（性能差一个数量级），"看起来一样"不算数，
 * 必须拿 Java 的真实输出逐个整数证明等价（判据见 docs/TRAINER-CPP.md §4）。
 *
 * 语料分两半（都能被两边整除，不靠运气）：
 *   · **边界手**：国士十三面 / 七对子 / 九莲 / §2.3-3 的 `1112223334445m` / 三面听 / 四张同种 …；
 *   · **随机构造**：一半是"先拼出一副和了形再拆一张"的**听牌手**（专打 `waitShapes` 那条路），
 *     一半是完全随机的暗手；副露数 0..4 均匀（`meldCount` 参与 `Shanten.min` 与 need 的计算）。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题（jar / 可执行 / java 缺失）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'RuleProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const QUIET = argv.includes('--quick');
const modes = [];
const rawMode = argv.find((a) => /^(shanten|of|discard|all)$/.test(a)) ?? 'all';
if (rawMode === 'all') modes.push('shanten', 'of', 'discard');
else modes.push(rawMode);
const rowsArg = Number(argv.find((a) => /^\d+$/.test(a)) ?? 0);
/** 语料里"听牌手"的比例：1 = 全听牌（量 waitShapes），0 = 全随机（量 advanceKinds），0.5 = 混合。 */
const TENPAI_PROB = argv.includes('--tenpai-only') ? 1 : argv.includes('--random-only') ? 0 : 0.5;

if (!existsSync(JAR)) {
    console.error(`[rule-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[rule-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

// ------------------------------------------------------------------ 语料
const KIND = 34;
const DEFAULT_ROWS = { shanten: 200000, of: 20000, discard: 5000 };

/** mulberry32：小、确定、够用（语料只是"覆盖"，不是密码学随机源）。 */
function rng(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/** `"123m456p789s11z"` → 34 维计数（z 是字牌 1..7；忽略其它字符）。 */
function parseHand(s) {
    const c = new Array(KIND).fill(0);
    const base = { m: 0, p: 9, s: 18, z: 27 };
    let digits = [];
    for (const ch of s) {
        if (ch >= '0' && ch <= '9') {
            digits.push(Number(ch));
            continue;
        }
        const b = base[ch];
        if (b === undefined) continue;
        for (const d of digits) c[b + (d - 1)]++;
        digits = [];
    }
    return c;
}

/** 边界手（**手写**，专挑"公式边界 + 多解"的位置；每行都会被两边算一遍）。 */
const EDGE13 = [
    '1112223334445m',        // AGENTS §2.3-3：melds+partials 必须封顶到 4，否则误判和了
    '1122334455667m',        // 七对子 / 一般型双解
    '11223344556677m',       // 14 张：七对子与一般型同时成立（discard 段用）
    '19m19p19s1234567z',     // 国士十三面（听 13 种，全是"单骑"形）
    '19m19p19s12345677z',    // 14 张：国士和了形
    '123456789m11p22p',      // 九莲宝灯形（13 张听 1~9m）
    '234m56m789p111s22z',    // 三面听 1m/4m/7m（waitShapes 的多解）
    '22345m678p111s22z',     // 2m：两面还是单骑？（分解多解）
    '1111m234p567p789s22z',  // 四张同种
    '111m222m333m444m55z',   // 四暗刻听牌
    '123m123p123s11122z',    // 纯全 + 役牌对子
    '11122233344455z',       // 字牌四面子 + 对子（14 张，discard 段用）
    '234567m234567p11s',     // 两面大量
    '1234567899m123p11s',    // 边张 / 嵌张混合
];

function buildCorpus(mode, wantRows) {
    const rows = [];
    const push = (c, mc, v) => {
        let total = 0;
        for (const x of c) total += x;
        const expect = mode === 'discard' ? 14 - 3 * mc : 13 - 3 * mc;
        if (total !== expect) return;               // 边界手里尺寸不合这个 mode 的，跳过
        rows.push({ mc, c: c.slice(), v: v.slice() });
    };

    const r = rng(0x5eed1234 ^ mode.length * 7919);
    const visibleFor = (c) => {
        const v = new Array(KIND).fill(0);
        for (let k = 0; k < KIND; k++) {
            const room = 4 - c[k];
            // 一半牌种"看得见 1~2 张"，另一半看不见 —— 让 `4 − 可见 − 自己手里` 的钳制走到
            v[k] = room <= 0 || r() < 0.5 ? 0 : 1 + Math.floor(r() * Math.min(2, room));
        }
        return v;
    };

    // ① 边界手（mc=0）
    for (const s of EDGE13) {
        const c = parseHand(s);
        push(c, 0, visibleFor(c));
    }
    // ② 随机构造
    let guard = 0;
    while (rows.length < wantRows && guard++ < wantRows * 20) {
        const mc = Math.floor(r() * 5);
        const needSets = 4 - mc;
        const c = new Array(KIND).fill(0);
        let size;
        if (mode === 'discard') {
            size = 14 - 3 * mc;
        } else {
            size = 13 - 3 * mc;
        }
        if (r() < TENPAI_PROB) {
            // 听牌手：拼出 needSets 个面子 + 1 对，再拆掉一张 → 13−3mc 张
            let placed = 0;
            const pair = Math.floor(r() * KIND);
            c[pair] += 2;
            placed += 2;
            let sets = 0;
            let spin = 0;
            while (sets < needSets && spin++ < 200) {
                if (r() < 0.5) {
                    const k = 27 + Math.floor(r() * 7);
                    if (c[k] > 1) continue;             // ≥2 时再加 3 会超过 4 张
                    c[k] += 3;
                } else {
                    const suit = Math.floor(r() * 3);
                    const start = suit * 9 + Math.floor(r() * 7);
                    let ok = true;
                    for (let d = 0; d < 3; d++) if (c[start + d] >= 4) ok = false;
                    if (!ok) continue;
                    for (let d = 0; d < 3; d++) c[start + d]++;
                }
                placed += 3;
                sets++;
            }
            if (sets < needSets) continue;
            // 拆一张（优先拆重复的张，保证拆完还是合法计数）
            const drop = Math.floor(r() * KIND);
            if (c[drop] === 0) continue;
            c[drop]--;
            size = 13 - 3 * mc;
        }
        // 随机补齐 / 随机暗手
        let placed = 0;
        for (const x of c) placed += x;
        let spin = 0;
        while (placed < size && spin++ < 500) {
            const k = Math.floor(r() * KIND);
            if (c[k] >= 4) continue;
            c[k]++;
            placed++;
        }
        if (placed !== size) continue;
        if (mode === 'discard' && placed < 14 - 3 * mc) {
            let spin2 = 0;
            while (placed < 14 - 3 * mc && spin2++ < 500) {
                const k = Math.floor(r() * KIND);
                if (c[k] >= 4) continue;
                c[k]++;
                placed++;
            }
            if (placed !== 14 - 3 * mc) continue;
        }
        push(c, mc, visibleFor(c));
    }
    return rows;
}

function writeCorpus(path, rows) {
    const chunks = [];
    for (const row of rows) {
        chunks.push(`${row.mc} ${row.c.join(' ')} ${row.v.join(' ')}\n`);
    }
    writeFileSync(path, chunks.join(''), 'utf8');
    return rows.length;
}

// ⚠ 子进程输出**不能走管道**（受限沙箱下 Node 走管道会 EPERM）——一句话不了，直接读文件。
function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
    return readFileSync(outFile, 'utf8');
}

let totalFails = 0;
for (const mode of modes) {
    const want = rowsArg || DEFAULT_ROWS[mode];
    const corpus = join(BUILD, `rule-corpus-${mode}.txt`);
    const outJava = join(BUILD, `rule-java-${mode}.txt`);
    const outCpp = join(BUILD, `rule-cpp-${mode}.txt`);
    const rows = buildCorpus(mode, want);
    writeCorpus(corpus, rows);
    console.log(`[rule-parity] ${mode}：语料 ${rows.length} 行 → ${corpus}`);

    const t0 = Date.now();
    runToFile('java', ['-cp', JAR, PROBE, corpus, mode, outJava], join(BUILD, 'rule-java.log'));
    const tJava = Date.now() - t0;
    const t1 = Date.now();
    runToFile(EXE, ['rules', corpus, mode, outCpp], join(BUILD, 'rule-cpp.log'));
    const tCpp = Date.now() - t1;

    const j = readFileSync(outJava, 'utf8').split('\n');
    const c = readFileSync(outCpp, 'utf8').split('\n');
    if (j.length && j[j.length - 1] === '') j.pop();
    if (c.length && c[c.length - 1] === '') c.pop();

    let fails = 0;
    const firstBad = [];
    if (j.length !== c.length) {
        fails++;
        console.error(`  [FAIL] 行数不同：java=${j.length} cpp=${c.length}`);
    }
    const n = Math.min(j.length, c.length);
    for (let i = 0; i < n; i++) {
        if (j[i] !== c[i]) {
            fails++;
            if (firstBad.length < 8) firstBad.push(i);
        }
    }
    for (const i of firstBad) {
        console.error(`  [FAIL] 第 ${i + 1} 行\n         java = ${j[i]}\n         cpp  = ${c[i]}`);
    }
    const speed = tCpp > 0 ? (tJava / tCpp).toFixed(2) : '—';
    if (fails === 0) {
        console.log(`  [ok]   ${n}/${n} 行逐字段一致（java ${tJava} ms / cpp ${tCpp} ms → ${speed}×）`);
    } else {
        console.error(`  [FAIL] ${fails} 行不一致（共 ${n} 行）`);
        totalFails += fails;
    }
}

if (totalFails === 0) {
    console.log('[rule-parity] PASS：向听 / 进张 / 听牌形与 Java 逐字段一致');
    process.exit(0);
}
console.error(`[rule-parity] FAIL：${totalFails} 处不一致`);
process.exit(1);
