#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎：自家回合「询问内容」差分对拍**。
 *
 *   node tools/trainer-opts-parity.mjs [每场小局数] [策略] [场数]
 *
 * 为什么这一层要单独对拍：`turnOptions` 的输出**顺序就是协议的一部分** ——
 * `Action.enumerate` 按 `discard → riichi → tsumo → kan → kyuushu` 展开，
 * 而 `legal` 的下标就是轨迹里的 `chosen_index`。少一个选项、顺序换一下，
 * 训练标签就整体错位（**不会报错**）。
 *
 * 口径：
 *   · 局面由 Java 用**真实 `Table.playGame()` + 真实策略**推进（`tools/RoundProbe.java`），
 *     探针只读状态、不重写生成逻辑；
 *   · 选项文本直接来自 Java 自己的 `Decision.options`；
 *   · C++ 只吃 `@` 左边那半局面，重算一遍与右边逐字符比。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'RoundProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const HANDS = Number(argv[0] ?? 4);
const POLICIES = argv[1] ? argv[1].split(',') : ['first', 'pass', 'random'];
const GAMES = Number(argv[2] ?? 2);

if (!existsSync(JAR)) {
    console.error(`[opts-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[opts-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
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

function checkOne(policy) {
    const corpus = join(BUILD, `turn-${policy}.txt`);
    const outCpp = join(BUILD, `turn-${policy}-cpp.txt`);
    runToFile('java', ['-cp', JAR, PROBE, String(HANDS), policy, '20260101', corpus,
        String(GAMES)], join(BUILD, `turn-${policy}-java.log`));
    const lines = readFileSync(corpus, 'utf8').split('\n');
    if (lines.length && lines[lines.length - 1] === '') lines.pop();
    if (lines.length === 0) {
        console.error(`  [FAIL] ${policy}：探针一行都没产出`);
        return { rows: 0, fails: 1 };
    }
    runToFile(EXE, ['turnopts', corpus, outCpp], join(BUILD, `turn-${policy}-cpp.log`));
    const cpp = readFileSync(outCpp, 'utf8').split('\n');
    if (cpp.length && cpp[cpp.length - 1] === '') cpp.pop();
    if (cpp.length !== lines.length) {
        console.error(`  [FAIL] ${policy}：行数不同 java=${lines.length} cpp=${cpp.length}`);
        return { rows: Math.min(lines.length, cpp.length), fails: 1 };
    }
    let fails = 0;
    const dist = new Map();
    for (let i = 0; i < lines.length; i++) {
        const at = lines[i].lastIndexOf(' @ ');
        const want = lines[i].slice(at + 3);
        const got = cpp[i];
        // 选项种类的分布（覆盖判据：看取值分布，不只看行数）
        const shape = want.replace(/=[^;]*/g, '=');
        dist.set(shape, (dist.get(shape) || 0) + 1);
        if (want !== got) {
            fails++;
            if (fails <= 5) {
                console.error(`  [FAIL] ${policy} 第 ${i + 1} 行`);
                console.error(`         java = ${want}`);
                console.error(`         cpp  = ${got}`);
                console.error(`         局面 = ${lines[i].slice(0, at)}`);
            }
        }
    }
    const top = [...dist.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
        .map(([k, v]) => `${k}×${v}`).join('  ');
    if (fails === 0) {
        console.log(`  [ok]   ${policy}：${lines.length}/${lines.length} 行一致`);
        console.log(`         选项形状态分布：${top}`);
    }
    return { rows: lines.length, fails };
}

console.log(`[opts-parity] 策略 ${POLICIES.join(',')} · 每场 ${HANDS} 小局 · ${GAMES} 场`);
let total = 0;
let bad = 0;
for (const p of POLICIES) {
    const r = checkOne(p);
    total += r.rows;
    bad += r.fails;
}
if (bad) {
    console.error(`[opts-parity] FAIL：${bad} 处不一致（共 ${total} 行）`);
    process.exit(1);
}
console.log(`[opts-parity] PASS：${total} 次自家回合询问的选项与 Java 逐字符一致`);
