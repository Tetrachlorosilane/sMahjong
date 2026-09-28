#!/usr/bin/env node
/**
 * **标签侧 `g*.aux.npz` 的两生产者对拍**（C++ 训练端 ↔ Java 服务端）——
 * "训练端脱离 Java"的硬判据之一。
 *
 *   node tools/trainer-aux-parity.mjs [场数] [每场小局数] [策略] [种子] [额外开关…]
 *
 * 与 `tools/trainer-selfplay-parity.mjs` 同一套参数口径（第 5 个及以后的参数原样透传给两侧），
 * 区别是它**额外**逐字节比 `g<g>.aux.npz`：标签侧是 zip（成员是 `.npy`），两边必须连容器
 * 一起相同（写得不一样会让"同种子产物逐字节相同"这条在数据集层失效）。
 *
 * 为什么必须单独有这一条：`--aux` 过去只有 Java 能产（数据集里 `aux_*` 那几列是信念/危险头的
 * 监督），C++ 侧补齐后如果只比 trace、不比 aux，"标签悄悄不一样"能一路溜到训练里
 * （症状是辅助头指标莫名其妙地差，而没有任何一步报错）。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * 把 npz 拆成 `成员名 → 字节`（自己读 zip 的本地头）—— 用于"字节不同时定位到成员"。
 */
function readMembers(buf) {
    const out = new Map();
    let i = 0;
    while (i + 30 <= buf.length && buf.readUInt32LE(i) === 0x04034b50) {
        const size = buf.readUInt32LE(i + 18);
        const nameLen = buf.readUInt16LE(i + 26);
        const extraLen = buf.readUInt16LE(i + 28);
        const name = buf.subarray(i + 30, i + 30 + nameLen).toString('utf8');
        const dataAt = i + 30 + nameLen + extraLen;
        out.set(name, buf.subarray(dataAt, dataAt + size));
        i = dataAt + size;
    }
    return out;
}

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const GAMES = Number(argv[0] ?? 3);
const HANDS = Number(argv[1] ?? 2);
const POLICY = argv[2] ?? 'teacher';
const SEED = argv[3] ?? '20260101';
/** 第 5 个及以后的参数：原样透传给两侧（`--rotate` / `--sample` / `--no-claims` / `--preset`）。 */
const EXTRA = argv.slice(4);
/** `--selfcheck`：脚本自己的负向对照（比较器不能空转），**不透传**给两侧。 */
const SELFCHECK = EXTRA.includes('--selfcheck');
if (SELFCHECK) {
    EXTRA.splice(EXTRA.indexOf('--selfcheck'), 1);
}
if (EXTRA.includes('--out') || EXTRA.includes('--workers')) {
    console.error('[aux-parity] 别透传 --out / --workers（脚本自己管）');
    process.exit(2);
}
if (EXTRA.includes('--aux')) {
    console.error('[aux-parity] 别自己透传 --aux（脚本两侧都会加）');
    process.exit(2);
}
if (!existsSync(JAR) || !EXE) {
    console.error(`[aux-parity] 缺 ${existsSync(JAR) ? '' : JAR + ' '}${EXE ? '' : 'trainer 可执行'}`
        + ' —— 先 pwsh -File server\\build.ps1 / trainer\\build.ps1');
    process.exit(2);
}
mkdirSync(BUILD, { recursive: true });

const RUN_TAG = process.env.SP_TAG || `ax${process.pid}`;
const dirJava = join(BUILD, `aux-${RUN_TAG}-java`);
const dirCpp = join(BUILD, `aux-${RUN_TAG}-cpp`);
const tail = ['--policy', POLICY, '--seed', SEED, '--hands', String(HANDS), '--aux', ...EXTRA];
console.log(`[aux-parity] 策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局 / seed ${SEED}`
    + `${EXTRA.length ? ' / ' + EXTRA.join(' ') : ''} / 目录 aux-${RUN_TAG}-{java,cpp}`);

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}
runToFile('java', ['-jar', JAR, '--selfplay', String(GAMES), '--workers', '1', ...tail,
    '--out', dirJava], join(BUILD, `aux-${RUN_TAG}-java.log`));
runToFile(EXE, ['selfplay', String(GAMES), ...tail, '--out', dirCpp],
    join(BUILD, `aux-${RUN_TAG}-cpp.log`));

/**
 * 比两份 npz：先比容器（zip 头/成员序/CRC），字节不同再拆成员定位到**哪一个成员**。
 *
 * @return `{equal, notes[]}`；`notes` 是给人看的差异说明（相等时为空）。
 */
function compareNpz(ba, bb) {
    const notes = [];
    if (ba.equals(bb)) {
        return { equal: true, notes };
    }
    let off = 0;
    while (off < ba.length && off < bb.length && ba[off] === bb[off]) off++;
    notes.push(`java=${ba.length} B / cpp=${bb.length} B，第一个不同字节 @${off}`);
    notes.push(`java: …${ba.subarray(Math.max(0, off - 24), off + 40).toString('hex')}…`);
    notes.push(`cpp : …${bb.subarray(Math.max(0, off - 24), off + 40).toString('hex')}…`);
    // ⚠ 不 spawn Python：本仓沙箱下 node 用管道抓子进程输出会被拒（EPERM），
    //   而 zip 本地头就三十行 —— 自己拆更快也更可诊断。
    const ma = readMembers(ba);
    const mb = readMembers(bb);
    const namesA = [...ma.keys()].join(',');
    const namesB = [...mb.keys()].join(',');
    if (namesA !== namesB) {
        notes.push(`成员清单不同：java=[${namesA}] cpp=[${namesB}]`);
        return { equal: false, notes };
    }
    for (const k of ma.keys()) {
        const x = ma.get(k);
        const y = mb.get(k);
        if (!x.equals(y)) {
            let o = 0;
            while (o < x.length && o < y.length && x[o] === y[o]) o++;
            notes.push(`↳ 成员 ${k} 不同：java=${x.length} B / cpp=${y.length} B @${o}`
                + `；npy 头（dtype/shape）${x.subarray(0, 64).equals(y.subarray(0, 64)) ? '相同' : '不同'}`);
        }
    }
    return { equal: false, notes };
}

let fails = 0;
const pairs = [];
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.aux.npz`;
    const a = join(dirJava, f);
    const b = join(dirCpp, f);
    if (!existsSync(a) || !existsSync(b)) {
        console.error(`  [FAIL] ${f}：${existsSync(a) ? '' : 'Java 侧缺失 '}${existsSync(b) ? '' : 'C++ 侧缺失'}`);
        fails++;
        continue;
    }
    const ba = readFileSync(a);
    const bb = readFileSync(b);
    const { equal, notes } = compareNpz(ba, bb);
    pairs.push([f, ba, bb]);
    if (equal) {
        console.log(`  [ok]   ${f}：${ba.length} B 逐字节一致`);
    } else {
        fails++;
        console.error(`  [FAIL] ${f}：`);
        for (const n of notes) {
            console.error(`         ${n}`);
        }
    }
}

// 负向对照：比较器**不是空转** —— 把 C++ 侧的副本翻一个字节，必须报"不同"且定位到成员
if (SELFCHECK) {
    if (pairs.length === 0) {
        console.error('  [FAIL] --selfcheck：没有可比的对，先把 GAMES 调 ≥ 1');
        fails++;
    } else {
        const [f, ba, bb] = pairs[0];
        const tampered = Buffer.from(bb);
        // 翻**中间**那个字节（一定落在某个成员的数据里；翻最后一个字节只会动到 zip 尾部的
        // 注释长度字段 —— 那样报的是"容器不同"，测不到"能不能定位到成员"）
        tampered[Math.floor(tampered.length / 2)] ^= 0x01;
        const r1 = compareNpz(ba, bb);
        const r2 = compareNpz(ba, tampered);
        if (r1.equal && !r2.equal && r2.notes.some((n) => n.includes('成员'))) {
            console.log(`  [ok]   --selfcheck：同一对报"一致"、翻一个字节后报"不同"并定位到成员（${f}）`);
        } else {
            fails++;
            console.error(`  [FAIL] --selfcheck：比较器空转（原对 equal=${r1.equal} / 篡改后 equal=${r2.equal}）`);
        }
    }
}

if (fails) {
    console.error(`[aux-parity] FAIL：${fails} 处不一致（策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局）`);
    process.exit(1);
}
console.log(`[aux-parity] PASS：策略 ${POLICY} / ${GAMES} 场 × ${HANDS} 小局`
    + `${EXTRA.length ? ' / ' + EXTRA.filter((x) => x !== '--selfcheck').join(' ') : ''}`
    + ' —— 标签侧逐字节一致');
