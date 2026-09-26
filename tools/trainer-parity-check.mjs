#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎** 的差分对拍（本工程的核心判据）。
 *
 *   node tools/trainer-parity-check.mjs [用例数] [--quick]
 *
 * 做什么：同一组 `(seed, aka, dealer)` 分别喂给
 *   ① Java 侧只读探针 `tools/WallProbe.java`（import 服务端 jar 的公开 API）
 *   ② C++ 侧 `trainer/build/trainer.exe wall …`
 * 然后**逐个整数**比对：136 张牌山 + 四家配牌（含庄家第 14 张）+ 表/里宝指示牌 + 4 张岭上。
 *
 * 为什么这么比：牌山是整条训练数据链的输入端，`Wall.java` 自己的注释写着它是"最容易被写错、
 * 而且错了也不报错"的一块。RNG（`java.util.Random`）与洗牌（`Collections.shuffle`）必须逐位
 * 复刻，否则整场牌就换了 —— 而数据集是**被消费的契约**（AGENTS §6.5、docs/TRAINER-CPP.md §2）。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题（jar / C++ 可执行 / java 缺失）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'WallProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = join(BUILD, 'trainer.exe');
const EXE_UNIX = join(BUILD, 'trainer');

const args = process.argv.slice(2);
const QUIET = args.includes('--quick');
const cases = Number(args.find((a) => /^\d+$/.test(a)) ?? 32);

const exe = existsSync(EXE) ? EXE : existsSync(EXE_UNIX) ? EXE_UNIX : null;
if (!existsSync(JAR)) {
    console.error(`[trainer-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!exe) {
    console.error('[trainer-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}

// ⚠ 子进程的输出**不能用管道接**：受限沙箱下 Node 走管道会 `spawnSync … EPERM`
//   （命名管道被禁；PowerShell 自己的管道不受影响，见 AGENTS「沙箱边界」）。
//   所以这里让子进程**直接写文件描述符**，再由 Node 读文件 —— 等价于重定向，不碰管道。
if (!existsSync(BUILD)) {
    mkdirSync(BUILD, { recursive: true });
}
const OUT_JAVA = join(BUILD, 'parity-java.json');
const OUT_CPP = join(BUILD, 'parity-cpp.json');

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
    return readFileSync(outFile, 'utf8').trim();
}

const javaOut = (seed, aka, dealer) =>
    runToFile('java', ['-cp', JAR, PROBE, String(seed), String(aka), String(dealer)], OUT_JAVA);
const cppOut = (seed, aka, dealer) =>
    runToFile(exe, ['wall', String(seed), String(aka), String(dealer)], OUT_CPP);

const KEYS = ['wall', 'dora', 'ura', 'rinshan'];
let fails = 0;
let checked = 0;

function compare(tag, a, b, key) {
    const av = key === 'wall' ? a[key] : a[key];
    if (!Array.isArray(av) || !Array.isArray(b[key])) {
        fails++;
        console.error(`  [FAIL] ${tag} ${key} 形状不对`);
        return;
    }
    if (av.length !== b[key].length) {
        fails++;
        console.error(`  [FAIL] ${tag} ${key} 长度 ${av.length} != ${b[key].length}`);
        return;
    }
    const bad = [];
    for (let i = 0; i < av.length; i++) {
        if (av[i] !== b[key][i]) {
            bad.push(`#${i}: java=${av[i]} cpp=${b[key][i]}`);
            if (bad.length >= 5) break;
        }
    }
    if (bad.length) {
        fails++;
        console.error(`  [FAIL] ${tag} ${key} 有 ${bad.length} 处不同 —— ${bad.join(' / ')}`);
    }
}

if (!QUIET) {
    console.log(`[trainer-parity] Java jar  : ${JAR}`);
    console.log(`[trainer-parity] C++ 可执行: ${exe}`);
}

const seeds = [];
for (let i = 0; i < cases; i++) {
    // 覆盖：正种子 / 负数种子 / **Long.MAX_VALUE 附近** / 大步长 ——
    // RNG 的 `initialScramble` 对符号与高位敏感，边界必须打到。
    // ⚠ 用 BigInt 算：JS 的 Number 存不下 2^63-1（会四舍五入成 …776000，Java parseLong 直接抛）
    const big = (2n ** 63n - 1n - BigInt(i)).toString();
    seeds.push(i % 4 === 0 ? String(20260101 + i)
        : i % 4 === 1 ? String(-(i + 1))
        : i % 4 === 2 ? big
        : String(1 + i * 7919));
}
const akas = [3, 0];
const dealers = [0, 2];
console.log(`[trainer-parity] 用例：${cases} 组种子 × {aka 3/0} × {dealer 0/2} = ${cases * akas.length * dealers.length} 组`);

for (const seed of seeds) {
    for (const aka of akas) {
        for (const dealer of dealers) {
            const tag = `seed=${seed} aka=${aka} dealer=${dealer}`;
            let j;
            let c;
            try {
                j = JSON.parse(javaOut(seed, aka, dealer));
                c = JSON.parse(cppOut(seed, aka, dealer));
            } catch (e) {
                fails++;
                console.error(`  [FAIL] ${tag} 运行失败：${e.message}`);
                continue;
            }
            const before = fails;
            for (const k of KEYS) compare(tag, j, c, k);
            if (JSON.stringify(j.hands) !== JSON.stringify(c.hands)) {
                fails++;
                const jh = j.hands.map((h) => h.join(','));
                const ch = c.hands.map((h) => h.join(','));
                const diff = jh.map((s, i) => (s === ch[i] ? null : `家${i}: java=[${s}] cpp=[${ch[i]}]`)).filter(Boolean);
                console.error(`  [FAIL] ${tag} 配牌不同 —— ${diff.join(' | ')}`);
            }
            checked++;
            if (fails === before && !QUIET && checked <= 4) {
                console.log(`  [ok]   ${tag}  牌山 136 / 配牌 4 家 / 指示牌 ${j.dora.length}+${j.ura.length} / 岭上 ${j.rinshan.length} 全部一致`);
            }
        }
    }
}

if (fails === 0) {
    console.log(`[trainer-parity] PASS：${checked}/${checked} 组逐整数一致（牌山 + 配牌 + 表里宝指示牌 + 岭上）`);
    process.exit(0);
}
console.error(`[trainer-parity] FAIL：${fails} 处不一致（共 ${checked} 组）`);
process.exit(1);
