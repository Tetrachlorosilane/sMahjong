#!/usr/bin/env node
/**
 * **C++ 训练端：`features` 的 `--workers` 只改调度、不改 sidecar**。
 *
 *   node tools/trainer-features-workers-check.mjs <轨迹目录> [w1] [w2]
 *
 * 干什么：把同一份轨迹（`g*.jsonl`）拷成两份，分别用 `trainer features <dir> --workers w1|w2`
 * 富化，然后逐文件比 `g*.feat.bin` 的**字节**（+ 文件集 + 汇总行）。
 *
 * 与 `trainer-features-parity.mjs` 的分工：那条比的是"C++ vs Java"（要 JAR，慢），
 * 这条比的是"C++ 串行 vs C++ 并行"（不依赖 JAR，秒级）。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, copyFileSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const ROOT = process.cwd();
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;
if (!EXE) {
    console.error('[features-workers] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}

const argv = process.argv.slice(2);
const SRC = argv[0] ?? join(BUILD, 'sp-cpp');
const W1 = Number(argv[1] ?? 1);
const W2 = Number(argv[2] ?? 8);
if (!existsSync(SRC)) {
    console.error(`[features-workers] 轨迹目录不存在：${SRC}（先跑一次 selfplay --out）`);
    process.exit(2);
}

function stage(dest) {
    rmSync(dest, { recursive: true, force: true });
    mkdirSync(dest, { recursive: true });
    for (const f of readdirSync(SRC)) {
        if (f.endsWith('.jsonl')) copyFileSync(join(SRC, f), join(dest, f));
    }
    return readdirSync(dest).filter((f) => f.endsWith('.jsonl')).length;
}

function run(dir, workers) {
    const log = `${dir}.log`;
    const fd = openSync(log, 'w');
    try {
        execFileSync(EXE, ['features', dir, '--workers', String(workers)],
            { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
    return readFileSync(log, 'utf8').trim();
}

const dirA = join(BUILD, `fw-${W1}`);
const dirB = join(BUILD, `fw-${W2}`);
const n = stage(dirA);
stage(dirB);
if (n === 0) {
    console.error(`[features-workers] ${SRC} 里没有 g*.jsonl`);
    process.exit(2);
}
const outA = run(dirA, W1);
const outB = run(dirB, W2);
console.log(`[features-workers] ${SRC}（${n} 个 jsonl）· workers ${W1} vs ${W2}`);
console.log(`  workers=${W1}: ${outA}`);
console.log(`  workers=${W2}: ${outB}`);

const bins = (d) => readdirSync(d).filter((f) => f.endsWith('.feat.bin')).sort();
const a = bins(dirA);
const b = bins(dirB);
let fails = 0;
if (a.join(',') !== b.join(',')) {
    fails++;
    console.error(`  [FAIL] 文件集不同：${W1}=${a.length} 个 / ${W2}=${b.length} 个`);
}
for (const f of a) {
    if (!b.includes(f)) continue;
    const ba = readFileSync(join(dirA, f));
    const bb = readFileSync(join(dirB, f));
    if (ba.equals(bb)) continue;
    fails++;
    const lim = Math.min(ba.length, bb.length);
    let off = 0;
    while (off < lim && ba[off] === bb[off]) off++;
    console.error(`  [FAIL] ${f}：${W1}=${ba.length} B / ${W2}=${bb.length} B，第一个不同字节 @${off}`);
}
if (fails === 0) {
    const total = a.reduce((s, f) => s + statSync(join(dirA, f)).size, 0);
    const sha = createHash('sha256').update(Buffer.concat(a.map((f) => readFileSync(join(dirA, f)))))
        .digest('hex').slice(0, 16);
    console.log(`  [ok]   ${a.length} 个 sidecar 逐字节相同（合计 ${total} B，合并 sha256=${sha}…）`);
    console.log(`[features-workers] PASS：features 串行与并行的 sidecar 逐字节相同`);
    process.exit(0);
}
console.error(`[features-workers] FAIL：${fails} 处不一致`);
process.exit(1);
