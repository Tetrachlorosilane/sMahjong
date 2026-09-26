#!/usr/bin/env node
/**
 * **接管验收：`MAHJONG_PRODUCER=cpp` 能不能整条走通训练回路**（用户要求"完全进入训练工作流"的判据）。
 *
 *   node tools/trainer-takeover-check.mjs [场数] [每场小局数] [策略] [种子]
 *
 * 与 `trainer-selfplay-parity.mjs` / `trainer-features-parity.mjs` 的分工：那两条是**单元级**闸门
 * （只比字节），这条是**整条链**，而且**命令由 Python 侧的 `mahjong_ml.producer` 自己组**——
 * 也就是说它顺带证明"接线对了"（Python 真的会把活派给 C++，而不是我们手敲了一条等价的命令）。
 *
 * 链路（每一步都必须过，否则退出码 1）：
 *   ① producer.selfplay_cmd(..., name='cpp')  → 跑 C++ 采集
 *   ② producer.selfplay_cmd(..., name='java') → 跑同一 (seed, policy, hands) 的 Java 采集
 *   ③ 两边的 g<g>.jsonl 逐字节相同
 *   ④ producer.features_cmd(dir, name='cpp'|'java') → sidecar 逐字节相同
 *   ⑤ node tools/selfplay-check.mjs <cpp 目录>  → DATASET PASS
 *   ⑥ python -m mahjong_ml.dataset build <cpp 目录> <紧凑目录>  → 能建出紧凑集
 *
 * 退出码：0 = 全过；1 = 有环节不过；2 = 环境问题（缺 exe / 缺 jar / 缺 venv）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const BUILD = join(ROOT, 'trainer', 'build');
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;
const PY = join(ROOT, 'python', '.venv', 'Scripts', 'python.exe');

const argv = process.argv.slice(2);
const GAMES = Number(argv[0] ?? 1);
const HANDS = Number(argv[1] ?? 1);
const POLICY = argv[2] ?? 'pass';
const SEED = Number(argv[3] ?? 20260101);

for (const [what, p] of [['trainer 可执行', EXE], ['mahjong-server.jar', JAR], ['python venv', PY]]) {
    if (!p || !existsSync(p)) {
        console.error(`[takeover] 缺 ${what}：${p}`);
        process.exit(2);
    }
}

/** 用 Python 侧的 producer 模块组命令（这才是"接线对不对"的判据）。
 *  ⚠ 不能直接抓子进程 stdout：受限沙箱下 `execFileSync` 的默认管道会 EPERM —— 让子进程写 fd 到文件再读。 */
function producerCmd(fn, args, name) {
    const code = `from mahjong_ml import producer as p;`
        + `import json,sys;print(json.dumps(p.${fn}(*json.loads(sys.argv[1]), name=${JSON.stringify(name)})))`;
    const tmp = join(BUILD, 'takeover-producer.json');
    const fd = openSync(tmp, 'w');
    try {
        execFileSync(PY, ['-c', code, JSON.stringify(args)],
            { cwd: join(ROOT, 'python'), stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
    return JSON.parse(readFileSync(tmp, 'utf8').trim());
}

function run(cmd, what) {
    console.log(`\n$ ${cmd.join(' ')}`);
    try {
        execFileSync(cmd[0], cmd.slice(1), { stdio: 'inherit', cwd: ROOT });
    } catch {
        console.error(`== ${what} 失败`);
        return false;
    }
    console.log(`== ${what} 完成`);
    return true;
}

let fails = 0;
const bad = (msg) => {
    console.error(`  [FAIL] ${msg}`);
    fails++;
};

const dirCpp = join(BUILD, 'takeover-cpp');
const dirJava = join(BUILD, 'takeover-java');
for (const d of [dirCpp, dirJava]) {
    rmSync(d, { recursive: true, force: true });
    mkdirSync(d, { recursive: true });
}

// ① / ② 采集（命令全部由 producer 模块给出）
const cmdCpp = producerCmd('selfplay_cmd', [GAMES, 1, POLICY, SEED, dirCpp], 'cpp');
if (cmdCpp[0] !== EXE) {
    bad(`producer 给的 cpp 命令第一段不是 trainer 可执行：${cmdCpp[0]}`);
}
const cmdJava = producerCmd('selfplay_cmd', [GAMES, 1, POLICY, SEED, dirJava], 'java');
if (!run(cmdCpp, 'C++ 采集')) {
    console.error(`\n[takeover] FAIL：C++ 侧还没法采集（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局）`);
    process.exit(1);
}
run(cmdJava, 'Java 采集（参考）');

// ③ 轨迹逐字节
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.jsonl`;
    const a = join(dirJava, f);
    const b = join(dirCpp, f);
    if (!existsSync(b)) {
        bad(`${f} 不存在（C++ 没产出）`);
        continue;
    }
    const ba = readFileSync(a);
    const bb = readFileSync(b);
    if (ba.equals(bb)) {
        console.log(`  [ok]   ${f}：${ba.length} B 与 Java 逐字节一致`);
    } else {
        let off = 0;
        while (off < ba.length && off < bb.length && ba[off] === bb[off]) off++;
        bad(`${f} 不同（java=${ba.length} B / cpp=${bb.length} B，第一个不同字节 @${off}）`);
    }
}

// ④ sidecar 逐字节
if (!run(producerCmd('features_cmd', [dirCpp, 1], 'cpp'), 'C++ 派生特征')) {
    console.error('\n[takeover] FAIL：C++ 侧还没法算派生特征');
    process.exit(1);
}
run(producerCmd('features_cmd', [dirJava, 1], 'java'), 'Java 派生特征（参考）');
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.feat.bin`;
    const a = join(dirJava, f);
    const b = join(dirCpp, f);
    if (!existsSync(b)) {
        bad(`${f} 不存在（C++ 没产出 sidecar）`);
        continue;
    }
    const ba = readFileSync(a);
    const bb = readFileSync(b);
    if (ba.equals(bb)) {
        console.log(`  [ok]   ${f}：${ba.length} B 与 Java 逐字节一致`);
    } else {
        let off = 0;
        while (off < ba.length && off < bb.length && ba[off] === bb[off]) off++;
        bad(`${f} 不同（java=${ba.length} B / cpp=${bb.length} B，第一个不同字节 @${off}）`);
    }
}

// ⑤ 独立校验器（Python 侧同一份）
try {
    run(['node', join(ROOT, 'tools', 'selfplay-check.mjs'), dirCpp], '数据集校验');
} catch {
    bad('selfplay-check.mjs 没通过（见上面的输出）');
}

// ⑥ 紧凑集（Python 消费链；这一条不过说明 sidecar/字段契约还有问题）
const compact = join(BUILD, 'takeover-compact');
rmSync(compact, { recursive: true, force: true });
try {
    run([PY, '-m', 'mahjong_ml.dataset', 'build', dirCpp, compact], '建紧凑集');
} catch {
    bad('dataset build 没通过（见上面的输出）');
}

if (fails) {
    console.error(`\n[takeover] FAIL：${fails} 处不过（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局）`);
    process.exit(1);
}
console.log(`\n[takeover] PASS：MAHJONG_PRODUCER=cpp 整条链走通`
    + `（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局，轨迹与 sidecar 均与 Java 逐字节一致）`);
