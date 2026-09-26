#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java：派生特征 sidecar（`g*.feat.bin`）逐字节对拍**。
 *
 *   node tools/trainer-features-parity.mjs [轨迹目录] [workers] [--self-check]
 *
 * 为什么单独有一条：`--features` 是训练回路的**第二步贵活**（Java 实测 24 workers 下
 * 27.9 万决策 6.6 分钟），也是 `dataset.build(require_derived=True)` 的硬前置 ——
 * 缺 sidecar 直接报错、不做降级。判据与 `--selfplay` 一样：**同一份轨迹、两个生产者、
 * 逐字节相同的 sidecar**（格式细节不必在这里解析：比字节就够，解析反而会引入第二份实现）。
 *
 * `--self-check`：两边都跑 Java（同一份轨迹、两个目录）—— 用来验证**这个脚本本身**
 * 与 Java 侧的可复现性，不需要 C++ 侧就绪。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
    closeSync, copyFileSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, rmSync, statSync,
} from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const SELF_CHECK = argv.includes('--self-check');
const positional = argv.filter((a) => !a.startsWith('--'));
const SRC = positional[0] ?? join(BUILD, 'sp-java');
const WORKERS = Number(positional[1] ?? 1);

if (!existsSync(JAR)) {
    console.error(`[features-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!existsSync(SRC)) {
    console.error(`[features-parity] 轨迹目录不存在：${SRC}（先跑 tools\\trainer-selfplay-parity.mjs）`);
    process.exit(2);
}
if (!SELF_CHECK && !EXE) {
    console.error('[features-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}

/** 只拷轨迹（jsonl + summary）；sidecar 由各自的生产者现算，不能带过去。 */
function stage(dest) {
    rmSync(dest, { recursive: true, force: true });
    mkdirSync(dest, { recursive: true });
    for (const f of readdirSync(SRC)) {
        if (f.endsWith('.jsonl') || f === 'summary.json') {
            copyFileSync(join(SRC, f), join(dest, f));
        }
    }
    return readdirSync(dest).filter((f) => f.endsWith('.jsonl')).length;
}

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

function sha256(p) {
    return createHash('sha256').update(readFileSync(p)).digest('hex');
}

const dirJava = join(BUILD, 'feat-java');
const dirCpp = join(BUILD, 'feat-cpp');
const n = stage(dirJava);
stage(dirCpp);

console.log(`[features-parity] 轨迹 ${SRC}（${n} 个 jsonl）· workers=${WORKERS}`
    + `${SELF_CHECK ? ' · 自检模式（两边都跑 Java）' : ''}`);

runToFile('java', ['-jar', JAR, '--features', dirJava, '--workers', String(WORKERS)],
    join(BUILD, 'feat-java.log'));
if (SELF_CHECK) {
    runToFile('java', ['-jar', JAR, '--features', dirCpp, '--workers', String(WORKERS)],
        join(BUILD, 'feat-cpp.log'));
} else {
    runToFile(EXE, ['features', dirCpp, '--workers', String(WORKERS)], join(BUILD, 'feat-cpp.log'));
}

const binOf = (d) => readdirSync(d).filter((f) => f.endsWith('.feat.bin')).sort();
const a = binOf(dirJava);
const b = binOf(dirCpp);
let fails = 0;

if (a.length === 0) {
    console.error('  [FAIL] Java 侧一个 sidecar 都没产出（看 trainer/build/feat-java.log）');
    fails++;
}
if (a.join(',') !== b.join(',')) {
    fails++;
    console.error(`  [FAIL] 文件集不同：java=${a.length} 个 / cpp=${b.length} 个`);
    console.error(`         java: ${a.slice(0, 5).join(' ')}`);
    console.error(`         cpp : ${b.slice(0, 5).join(' ')}`);
}

for (const f of a) {
    if (!b.includes(f)) {
        continue;
    }
    const pa = join(dirJava, f);
    const pb = join(dirCpp, f);
    const sa = statSync(pa).size;
    const sb = statSync(pb).size;
    const ba = readFileSync(pa);
    const bb = readFileSync(pb);
    if (ba.equals(bb)) {
        console.log(`  [ok]   ${f}：${sa} B 逐字节一致  sha256=${sha256(pa).slice(0, 16)}…`);
        continue;
    }
    fails++;
    const lim = Math.min(ba.length, bb.length);
    let off = 0;
    while (off < lim && ba[off] === bb[off]) off++;
    console.error(`  [FAIL] ${f}：java=${sa} B / cpp=${sb} B，第一个不同字节 @${off}`
        + `（java ${ba[off]?.toString(16).padStart(2, '0') ?? 'EOF'}`
        + ` vs cpp ${bb[off]?.toString(16).padStart(2, '0') ?? 'EOF'}）`);
}

if (fails) {
    console.error(`[features-parity] FAIL：${fails} 处不一致`);
    process.exit(1);
}
console.log(`[features-parity] PASS：${a.length} 个 sidecar 与 `
    + `${SELF_CHECK ? 'Java（自检）' : 'Java'} 逐字节一致`);
