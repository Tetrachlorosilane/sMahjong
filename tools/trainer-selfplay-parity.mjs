#!/usr/bin/env node
/**
 * **训练端 C++ 自对弈 ↔ Java `--selfplay` 逐字节对拍**（把 C++ 接进训练工作流的硬判据）。
 *
 *   node tools/trainer-selfplay-parity.mjs [场数] [每场小局数] [策略] [种子] [额外开关…]
 *
 * 第 5 个及以后的参数**原样透传**给两侧（`--rotate` / `--sample k` / `--no-claims` /
 * `--preset xxx`）—— 前四个位置参数的行为与加这个透传之前**完全一致**。
 * ⚠ 别透传 `--out`（脚本自己管输出目录）。
 *
 * 判据（与 `docs/TRAINER-CPP.md` §2 的契约一致）：
 *   · 同一 `(seedBase, 策略串, 小局数)` ⇒ `g<g>.jsonl` **逐字节相同**（含 CRLF 与键序）；
 *   · `summary.json` 除 `seconds` / `games_per_second` / `decisions_per_second` / `workers`
 *     之外逐字段相同（那四项是墙钟/核数，Java 自己也复现不了）。
 *
 * ⚠ 为什么从「1 场 / 1 小局 / `pass`」开始：`pass` 策略不鸣牌，先把
 * 「配牌 → 摸切 → 荒牌流局 → 终局余棒 → 轨迹三段（decision/hand/game）」这条主干钉死，
 * 再往上加鸣牌、立直、杠、和了。对拍失败时**报第一个不同的行号与两边的行文本**，
 * 不要只看行数。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const GAMES = Number(argv[0] ?? 1);
const HANDS = Number(argv[1] ?? 1);
const POLICY = argv[2] ?? 'pass';
const SEED = argv[3] ?? '20260101';
/** 第 5 个及以后的参数：原样透传给两侧（`--rotate` / `--sample` / `--no-claims` / `--preset`）。 */
const EXTRA = argv.slice(4);
if (EXTRA.includes('--out') || EXTRA.includes('--workers')) {
    console.error('[selfplay-parity] 别透传 --out / --workers'
        + '（--out 由脚本自己管；要并行用 `--cpp-workers K`）');
    process.exit(2);
}
/**
 * `--cpp-workers K`：**只**给 C++ 侧加 `--workers K`（Java 参考侧仍串行）。
 * 用来一次同时验两件事：C++ 并行与 Java **逐字节相同** + C++ 并行与 C++ 串行相同
 * （后者另有 `tools/trainer-workers-check.mjs`，不依赖 JAR）。
 * 缺省 0 = 不给这个开关（两侧都按各自缺省跑，Java 缺省是核数、C++ 缺省是 1）。
 */
const wi = EXTRA.indexOf('--cpp-workers');
const CPP_WORKERS = wi >= 0 ? Number(EXTRA[wi + 1]) : 0;
if (wi >= 0) {
    if (!Number.isFinite(CPP_WORKERS) || CPP_WORKERS < 0) {
        console.error('[selfplay-parity] --cpp-workers 需要一个非负整数');
        process.exit(2);
    }
    EXTRA.splice(wi, 2);
}

if (!existsSync(JAR)) {
    console.error(`[selfplay-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[selfplay-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
mkdirSync(BUILD, { recursive: true });

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

// ⚠ 输出目录**按进程唯一化**：这两个目录以前是硬编码的 `sp-java` / `sp-cpp`，
// 于是"两个进程同时跑这个闸门"会互相覆盖 —— 本项目真被这个坑过一次
// （一次"30/50 不一致"的假红，其实是另一路 `net:` 闸门正在往同一对目录里写）。
// 现在每次调用都有自己的目录（`--out` 由脚本自己管，用完即可删）。
const RUN_TAG = process.env.SP_TAG || `p${process.pid}`;
const dirJava = join(BUILD, `sp-${RUN_TAG}-java`);
const dirCpp = join(BUILD, `sp-${RUN_TAG}-cpp`);
console.log(`[selfplay-parity] 策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局 / seed ${SEED}`
    + `${EXTRA.length ? ' / ' + EXTRA.join(' ') : ''}`
    + `${CPP_WORKERS > 0 ? ` / cpp --workers ${CPP_WORKERS}` : ''}`
    + ` / 目录 sp-${RUN_TAG}-{java,cpp}`);
const tail = ['--policy', POLICY, '--seed', SEED, '--hands', String(HANDS), ...EXTRA];
// Java 参考侧恒为 `--workers 1`（参考轨迹不随核数变）；C++ 侧按 `--cpp-workers` 给定
const javaArgs = ['--workers', '1', ...tail];
const cppArgs = CPP_WORKERS > 0 ? ['--workers', String(CPP_WORKERS), ...tail] : tail;

runToFile('java', ['-jar', JAR, '--selfplay', String(GAMES), ...javaArgs, '--out', dirJava],
    join(BUILD, `sp-${RUN_TAG}-java.log`));
runToFile(EXE, ['selfplay', String(GAMES), ...cppArgs, '--out', dirCpp],
    join(BUILD, `sp-${RUN_TAG}-cpp.log`));

let fails = 0;
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.jsonl`;
    const a = join(dirJava, f);
    const b = join(dirCpp, f);
    if (!existsSync(a) || !existsSync(b)) {
        console.error(`  [FAIL] ${f}：${existsSync(a) ? '' : 'Java 侧缺失 '}${existsSync(b) ? '' : 'C++ 侧缺失'}`);
        fails++;
        continue;
    }
    const ba = readFileSync(a);
    const bb = readFileSync(b);
    if (ba.equals(bb)) {
        console.log(`  [ok]   ${f}：${ba.length} B 逐字节一致`);
        continue;
    }
    fails++;
    console.error(`  [FAIL] ${f}：java=${ba.length} B / cpp=${bb.length} B`);
    let off = 0;
    while (off < ba.length && off < bb.length && ba[off] === bb[off]) off++;
    const line = (buf, at) => buf.subarray(0, at).toString('utf8').split('\n').length;
    console.error(`         第一个不同字节 @${off}（java 第 ${line(ba, off)} 行 / cpp 第 ${line(bb, off)} 行）`);
    const slice = (buf) => buf.subarray(Math.max(0, off - 60), off + 120).toString('utf8')
        .replace(/\r/g, '\\r').replace(/\n/g, '\\n');
    console.error(`         java: …${slice(ba)}…`);
    console.error(`         cpp : …${slice(bb)}…`);
    const la = ba.toString('utf8').split('\r\n');
    const lb = bb.toString('utf8').split('\r\n');
    for (let i = 0; i < Math.max(la.length, lb.length); i++) {
        if (la[i] !== lb[i]) {
            console.error(`         首个不同的行：第 ${i + 1} 行`);
            console.error(`           java: ${(la[i] ?? '<无>').slice(0, 200)}`);
            console.error(`           cpp : ${(lb[i] ?? '<无>').slice(0, 200)}`);
            break;
        }
    }
}

// summary.json：只比"内容字段"（计时/核数是墙钟量）
const sj = join(dirJava, 'summary.json');
const sc = join(dirCpp, 'summary.json');
if (existsSync(sj) && existsSync(sc)) {
    const strip = (o) => {
        const c = JSON.parse(JSON.stringify(o));
        for (const k of ['seconds', 'games_per_second', 'decisions_per_second', 'workers']) delete c[k];
        return JSON.stringify(c);
    };
    const a = strip(JSON.parse(readFileSync(sj, 'utf8')));
    const b = strip(JSON.parse(readFileSync(sc, 'utf8')));
    if (a === b) {
        console.log('  [ok]   summary.json：除计时/核数外逐字段一致');
    } else {
        fails++;
        console.error('  [FAIL] summary.json 内容字段不同');
        console.error(`         java: ${a.slice(0, 300)}`);
        console.error(`         cpp : ${b.slice(0, 300)}`);
    }
} else {
    console.log(`  [skip] summary.json：${existsSync(sc) ? 'Java 侧缺失' : 'C++ 侧还没写'}`);
}

if (fails) {
    console.error(`[selfplay-parity] FAIL：${fails} 处不一致（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局`
        + `${EXTRA.length ? ' / ' + EXTRA.join(' ') : ''}）`);
    process.exit(1);
}
console.log(`[selfplay-parity] PASS：策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局`
    + `${EXTRA.length ? ' / ' + EXTRA.join(' ') : ''}`
    + `${CPP_WORKERS > 0 ? ` / cpp --workers ${CPP_WORKERS}` : ''} 逐字节一致`);
