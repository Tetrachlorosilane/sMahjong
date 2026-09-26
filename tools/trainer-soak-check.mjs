#!/usr/bin/env node
/**
 * **soak：大规模完整半庄的自对弈一致性**（并行不改产出 + 与 Java 逐字节一致 + 失败场次 0）。
 *
 *   node tools/trainer-soak-check.mjs [场数] [策略列表] [种子] [cppWorkers] [javaWorkers]
 *
 * 例：`node tools/trainer-soak-check.mjs 500 first,random 20260101 8 24`
 *
 * 每条策略做四件事：
 *   ① Java `--selfplay N --hands 0 --workers <javaWorkers>`（参考；⚠ Java 侧**可以用并行** ——
 *      它的 `--workers` 同样不改产出，把参考侧也压到 24 核才等得起）；
 *   ② C++ `--selfplay N --hands 0 --workers K`（并行）→ 与 Java **逐字节**比 `g*.jsonl`；
 *   ③ C++ `--selfplay N --hands 0 --workers 1`（串行）→ 与 ② **逐字节**比（"并行不改产出"）；
 *   ④ 数 C++ 两次跑的 stderr 里 `[trainer] 引擎兜底出牌` 的行数（"自家回合 legal 为空"的实测频率）。
 *
 * ⚠ stderr 必须**重定向到文件**再读 —— 受限沙箱里 Node 抓子进程的管道会 EPERM
 *   （见 docs/TRAINER-CPP.md §6.0 的沙箱坑）。这里用 `stdio: ['ignore', fd, fd2]`。
 *
 * 退出码：0 = 全过；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const BUILD = join(ROOT, 'trainer', 'build');
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const GAMES = Number(argv[0] ?? 500);
const POLICIES = String(argv[1] ?? 'first,random').split(',');
const SEED = argv[2] ?? '20260101';
const CPP_WORKERS = Number(argv[3] ?? 8);
const JAVA_WORKERS = Number(argv[4] ?? 1);

if (!EXE) {
    console.error('[soak] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
if (!existsSync(JAR)) {
    console.error(`[soak] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}

/** 跑一次采集：stderr 与 stdout 都进文件（不碰管道）。返回 stderr 文本。 */
function run(dir, args) {
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    const outLog = `${dir}.out.log`;
    const errLog = `${dir}.err.log`;
    const fo = openSync(outLog, 'w');
    const fe = openSync(errLog, 'w');
    try {
        execFileSync(args[0], args.slice(1), { stdio: ['ignore', fo, fe] });
    } finally {
        closeSync(fo);
        closeSync(fe);
    }
    return readFileSync(errLog, 'utf8');
}

/** 逐文件比字节；返回 {相同数, 不同, 缺失, 第一条差异描述}。 */
function compareDirs(a, b, games) {
    let same = 0;
    const diff = [];
    const missing = [];
    for (let g = 0; g < games; g++) {
        const f = `g${g}.jsonl`;
        const pa = join(a, f);
        const pb = join(b, f);
        if (!existsSync(pa) || !existsSync(pb)) {
            missing.push(f);
            continue;
        }
        const ba = readFileSync(pa);
        const bb = readFileSync(pb);
        if (ba.equals(bb)) {
            same++;
            continue;
        }
        if (diff.length < 3) {
            let off = 0;
            while (off < Math.min(ba.length, bb.length) && ba[off] === bb[off]) off++;
            diff.push(`${f}（${ba.length} B vs ${bb.length} B，第一个不同字节 @${off}）`);
        }
    }
    return { same, diff, missing };
}

const countFallback = (text) => text.split('\n').filter((l) => l.includes('引擎兜底出牌')).length;

let fails = 0;
const t0 = Date.now();
for (const policy of POLICIES) {
    console.log(`\n===== soak：${GAMES} 场完整半庄 / --policy ${policy} / seed ${SEED} / cpp --workers ${CPP_WORKERS} =====`);
    const dirJava = join(BUILD, `soak-${policy}-java`);
    const dirCppP = join(BUILD, `soak-${policy}-cpp-w${CPP_WORKERS}`);
    const dirCppS = join(BUILD, `soak-${policy}-cpp-w1`);

    const tJ = Date.now();
    run(dirJava, ['java', '-jar', JAR, '--selfplay', String(GAMES), '--workers', '1',
        '--policy', policy, '--seed', SEED, '--hands', '0', '--out', dirJava]);
    const secJava = (Date.now() - tJ) / 1000;

    const tP = Date.now();
    const errP = run(dirCppP, [EXE, 'selfplay', String(GAMES), '--workers', String(CPP_WORKERS),
        '--policy', policy, '--seed', SEED, '--hands', '0', '--out', dirCppP]);
    const secCppP = (Date.now() - tP) / 1000;

    const tS = Date.now();
    const errS = run(dirCppS, [EXE, 'selfplay', String(GAMES), '--workers', '1',
        '--policy', policy, '--seed', SEED, '--hands', '0', '--out', dirCppS]);
    const secCppS = (Date.now() - tS) / 1000;

    const nJava = readdirSync(dirJava).filter((f) => f.endsWith('.jsonl')).length;
    const nCppP = readdirSync(dirCppP).filter((f) => f.endsWith('.jsonl')).length;
    const nCppS = readdirSync(dirCppS).filter((f) => f.endsWith('.jsonl')).length;
    const summary = JSON.parse(readFileSync(join(dirCppP, 'summary.json'), 'utf8'));
    console.log(`  产出文件：java=${nJava} / cpp(并行)=${nCppP} / cpp(串行)=${nCppS}`
        + `（期望 ${GAMES}）；决策 ${summary.decisions} / 小局 ${summary.hands}`);
    console.log(`  墙钟：java(1w)=${secJava.toFixed(1)}s  cpp(${CPP_WORKERS}w)=${secCppP.toFixed(1)}s`
        + `  cpp(1w)=${secCppS.toFixed(1)}s`);
    console.log(`  「引擎兜底出牌」：cpp(${CPP_WORKERS}w)=${countFallback(errP)} 次`
        + `  cpp(1w)=${countFallback(errS)} 次`);

    const vsJava = compareDirs(dirJava, dirCppP, GAMES);
    const vsSerial = compareDirs(dirCppS, dirCppP, GAMES);
    console.log(`  C++ 并行 vs Java      ：相同 ${vsJava.same}/${GAMES}`
        + `${vsJava.missing.length ? ` · 缺失 ${vsJava.missing.length}` : ''}`
        + `${vsJava.diff.length ? ` · 不同：${vsJava.diff.join(' | ')}` : ''}`);
    console.log(`  C++ 并行 vs C++ 串行  ：相同 ${vsSerial.same}/${GAMES}`
        + `${vsSerial.missing.length ? ` · 缺失 ${vsSerial.missing.length}` : ''}`
        + `${vsSerial.diff.length ? ` · 不同：${vsSerial.diff.join(' | ')}` : ''}`);

    if (nJava !== GAMES || nCppP !== GAMES || nCppS !== GAMES) {
        fails++;
        console.error(`  [FAIL] 文件数不对（失败场次 > 0）：java=${nJava} / cpp并行=${nCppP} / cpp串行=${nCppS}`);
    }
    if (vsJava.same !== GAMES || vsJava.missing.length || vsJava.diff.length) {
        fails++;
        console.error('  [FAIL] C++ 并行产物与 Java 不逐字节相同');
    }
    if (vsSerial.same !== GAMES || vsSerial.missing.length || vsSerial.diff.length) {
        fails++;
        console.error('  [FAIL] C++ 并行与 C++ 串行产物不逐字节相同');
    }
    // 只保留 C++ 两份（Java 参考目录留给下一个策略对比时复用价值不大，删掉省盘）
    rmSync(dirJava, { recursive: true, force: true });
    rmSync(dirCppS, { recursive: true, force: true });
}
console.log(`\n[soak] 总用时 ${((Date.now() - t0) / 1000 / 60).toFixed(1)} 分钟`);
if (fails) {
    console.error(`[soak] FAIL：${fails} 处不过`);
    process.exit(1);
}
console.log(`[soak] PASS：${POLICIES.join('/')} 各 ${GAMES} 场完整半庄 —— 与 Java 逐字节一致、`
    + `并行与串行逐字节一致、失败场次 0`);
