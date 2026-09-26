#!/usr/bin/env node
/**
 * **C++ 训练端：`--workers` 只改调度、不改产出**（并行不改内容的判据）。
 *
 *   node tools/trainer-workers-check.mjs [场数] [每场小局数] [策略] [种子] [w1] [w2] [额外开关…]
 *
 * 干什么：用**同一个命令**（只差 `--workers`）跑两遍 `trainer selfplay` 到两个目录，
 * 然后逐场比 `g<g>.jsonl` 的**字节**（含长度、首个不同字节偏移、首个不同行），
 * 并比 `summary.json` 里**除计时/核数外的所有字段**。
 *
 * 为什么单独有一条：`--workers` 的正确性是"**内容与调度无关**"——
 * Java 侧有 `SelfPlay` 的注释与 `--workers` 口径兜着，C++ 侧一旦把共享状态
 * （向听缓存、策略实例、全局计数器）接错，**输出会悄悄变**，而 `--selftest` 看不出来。
 * 与 `trainer-selfplay-parity.mjs` 的分工：那条比的是"C++ vs Java"，
 * 这条比的是"C++ 串行 vs C++ 并行"（不依赖 JAR，秒级）。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;
if (!EXE) {
    console.error('[workers-check] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}

const argv = process.argv.slice(2);
const GAMES = Number(argv[0] ?? 300);
const HANDS = Number(argv[1] ?? 2);
const POLICY = argv[2] ?? 'first';
const SEED = argv[3] ?? '20260101';
const W1 = Number(argv[4] ?? 1);
const W2 = Number(argv[5] ?? 8);
/** 第 7 个及以后的参数原样透传（`--rotate` / `--sample k` / `--no-claims` / `--preset x`）。 */
const EXTRA = argv.slice(6);
if (EXTRA.includes('--out') || EXTRA.includes('--workers')) {
    console.error('[workers-check] 别透传 --out / --workers（脚本自己管）');
    process.exit(2);
}

function runToFile(dir, workers) {
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    const log = `${dir}.log`;
    const fd = openSync(log, 'w');
    try {
        execFileSync(EXE, ['selfplay', String(GAMES), '--workers', String(workers),
            '--policy', POLICY, '--seed', SEED, '--hands', String(HANDS), ...EXTRA, '--out', dir],
        { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
    return dir;
}

const dirA = runToFile(join(BUILD, `wk-${W1}`), W1);
const dirB = runToFile(join(BUILD, `wk-${W2}`), W2);

const base = `策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局 / seed ${SEED}`
    + `${EXTRA.length ? ' / ' + EXTRA.join(' ') : ''}`;
console.log(`[workers-check] workers ${W1} vs ${W2} · ${base}`);

let fails = 0;
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.jsonl`;
    const pa = join(dirA, f);
    const pb = join(dirB, f);
    if (!existsSync(pa) || !existsSync(pb)) {
        console.error(`  [FAIL] ${f}：${existsSync(pa) ? '' : `workers=${W1} 缺失 `}`
            + `${existsSync(pb) ? '' : `workers=${W2} 缺失`}`);
        fails++;
        continue;
    }
    const ba = readFileSync(pa);
    const bb = readFileSync(pb);
    if (!ba.equals(bb)) {
        fails++;
        const lim = Math.min(ba.length, bb.length);
        let off = 0;
        while (off < lim && ba[off] === bb[off]) off++;
        console.error(`  [FAIL] ${f}：${W1}=${ba.length} B / ${W2}=${bb.length} B，`
            + `第一个不同字节 @${off}`);
    }
}
if (fails === 0) {
    console.log(`  [ok]   ${GAMES}/${GAMES} 个 g*.jsonl 逐字节相同（workers ${W1} vs ${W2}）`);
}

// summary.json：比内容字段（计时/核数是墙钟量与调度量，本来就不该相同）
const sa = join(dirA, 'summary.json');
const sb = join(dirB, 'summary.json');
if (existsSync(sa) && existsSync(sb)) {
    const strip = (p) => {
        const c = JSON.parse(readFileSync(p, 'utf8'));
        for (const k of ['seconds', 'games_per_second', 'decisions_per_second', 'workers']) delete c[k];
        return JSON.stringify(c);
    };
    if (strip(sa) === strip(sb)) {
        console.log('  [ok]   summary.json：除计时/核数外逐字段相同');
    } else {
        fails++;
        console.error('  [FAIL] summary.json 内容字段不同');
        console.error(`         ${W1}: ${strip(sa).slice(0, 300)}`);
        console.error(`         ${W2}: ${strip(sb).slice(0, 300)}`);
    }
} else {
    fails++;
    console.error('  [FAIL] summary.json 缺失');
}

// 目录文件集也必须一致（并行不能多写/漏写）
const setOf = (d) => readdirSync(d).sort().join(',');
if (setOf(dirA) !== setOf(dirB)) {
    fails++;
    console.error(`  [FAIL] 文件集不同：\n         ${W1}: ${setOf(dirA).slice(0, 200)}\n         ${W2}: ${setOf(dirB).slice(0, 200)}`);
} else {
    console.log(`  [ok]   文件集相同（${readdirSync(dirA).length} 个）`);
}

if (fails) {
    console.error(`[workers-check] FAIL：${fails} 处不一致（${base}）`);
    process.exit(1);
}
console.log(`[workers-check] PASS：workers=${W1} 与 workers=${W2} 的产出逐字节相同（${base}）`);
