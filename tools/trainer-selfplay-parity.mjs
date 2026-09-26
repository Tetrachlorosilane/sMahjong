#!/usr/bin/env node
/**
 * **训练端 C++ 自对弈 ↔ Java `--selfplay` 逐字节对拍**（把 C++ 接进训练工作流的硬判据）。
 *
 *   node tools/trainer-selfplay-parity.mjs [场数] [每场小局数] [策略] [种子]
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

const dirJava = join(BUILD, 'sp-java');
const dirCpp = join(BUILD, 'sp-cpp');
const common = ['--workers', '1', '--policy', POLICY, '--seed', SEED, '--hands', String(HANDS)];

runToFile('java', ['-jar', JAR, '--selfplay', String(GAMES), ...common, '--out', dirJava],
    join(BUILD, 'sp-java.log'));
runToFile(EXE, ['selfplay', String(GAMES), ...common, '--out', dirCpp],
    join(BUILD, 'sp-cpp.log'));

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
    console.error(`[selfplay-parity] FAIL：${fails} 处不一致（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局）`);
    process.exit(1);
}
console.log(`[selfplay-parity] PASS：策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局 逐字节一致`);
