#!/usr/bin/env node
/**
 * **v4 增量事件缓存：同种子「开缓存 vs 关缓存」逐字节对拍**（缓存正确性的最外层判据）。
 *
 *   node tools/v4-cache-check.mjs <net.bin> [场数] [每场小局数] [种子]
 *
 * 为什么要有这一条（而不是只靠 `V4Policy` 的自检）：
 * 自检比的是"同一个进程里两条前向路径的七个头"；这一条比的是**整场轨迹**——
 * 缓存一旦在某个环节陈旧（跨小局、跨座位、窗口平移），动作就会变，`g*.jsonl` 立刻不一致。
 * 它是"缓存能上线"的最外层证据：**同种子 + 只改缓存开关 ⇒ 轨迹逐字节相同**。
 *
 * 判据：
 *   · `g<g>.jsonl` 逐字节相同（含 CRLF 与键序）；
 *   · `summary.json` 除计时/核数（`seconds` / `games_per_second` / `decisions_per_second` /
 *     `workers`）之外逐字段相同 —— 那四项是墙钟，本来就复现不了。
 *
 * ⚠ 策略串里必须**真的有 v4 网络**（`net:<net.bin>`）：全 teacher 的轨迹两次必然相同，
 * 那种 PASS 是空转（脚本会检查策略串里至少有 `net:`）。
 *
 * 退出码：0 = 一致；1 = 有差异；2 = 环境/用法问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const BUILD = join(ROOT, 'tools', 'build');

const net = process.argv[2];
if (!net) {
    console.error('用法：node tools/v4-cache-check.mjs <net.bin> [场数] [小局数] [种子] [策略串]');
    process.exit(2);
}
if (!existsSync(net)) {
    console.error(`[v4-cache] 找不到权重：${net}`);
    process.exit(2);
}
const GAMES = Number(process.argv[3] ?? 2);
const HANDS = Number(process.argv[4] ?? 4);
const SEED = process.argv[5] ?? '424242';
/** 缺省：两个学生席用这个网络、另两席 teacher（四席全网络会让这一跑慢一倍）。 */
const POLICY = process.argv[6] ?? `net:${net},net:${net},teacher,teacher`;
if (!POLICY.includes('net:')) {
    console.error('[v4-cache] 策略串里必须至少有一个 `net:` —— 全是 teacher 的对拍是空转');
    process.exit(2);
}
if (!existsSync(JAR)) {
    console.error(`[v4-cache] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
mkdirSync(BUILD, { recursive: true });
const TAG = process.env.SP_TAG || `cache${process.pid}`;
const dirOn = join(BUILD, `v4cache-${TAG}-on`);
const dirOff = join(BUILD, `v4cache-${TAG}-off`);

function run(extra, outDir, log) {
    const fd = openSync(log, 'w');
    try {
        execFileSync('java', ['-jar', JAR, ...extra, '--selfplay', String(GAMES), '--workers', '1',
            '--hands', String(HANDS), '--policy', POLICY, '--seed', String(SEED), '--out', outDir],
        { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

console.log(`[v4-cache] ${GAMES} 场 × ${HANDS} 小局 / seed ${SEED} / ${POLICY}`);
const t0 = Date.now();
run([], dirOn, join(BUILD, `v4cache-${TAG}-on.log`));
const tOn = Date.now() - t0;
const t1 = Date.now();
run(['--no-v4-cache'], dirOff, join(BUILD, `v4cache-${TAG}-off.log`));
const tOff = Date.now() - t1;
console.log(`  墙钟：开缓存 ${(tOn / 1000).toFixed(1)}s / 关缓存 ${(tOff / 1000).toFixed(1)}s`
    + `（小样本，仅作对照；准数看 tools.V4Probe --cache）`);

let fails = 0;
for (let g = 0; g < GAMES; g++) {
    const f = `g${g}.jsonl`;
    const a = join(dirOn, f);
    const b = join(dirOff, f);
    if (!existsSync(a) || !existsSync(b)) {
        console.error(`  [FAIL] ${f}：开缓存侧${existsSync(a) ? '' : '缺失'} 关缓存侧${existsSync(b) ? '' : '缺失'}`);
        fails++;
        continue;
    }
    const ba = readFileSync(a);
    const bb = readFileSync(b);
    if (ba.equals(bb)) {
        console.log(`  [ok]   ${f}：${ba.length} B 逐字节一致`);
    } else {
        fails++;
        console.error(`  [FAIL] ${f}：开缓存 ${ba.length} B / 关缓存 ${bb.length} B`);
        let off = 0;
        while (off < ba.length && off < bb.length && ba[off] === bb[off]) off++;
        console.error(`         第一个不同的字节：偏移 ${off}`);
    }
}
// summary.json：只看非计时字段
const sa = join(dirOn, 'summary.json');
const sb = join(dirOff, 'summary.json');
if (existsSync(sa) && existsSync(sb)) {
    const drop = (o) => {
        const c = { ...o };
        for (const k of ['seconds', 'games_per_second', 'decisions_per_second', 'workers']) delete c[k];
        return c;
    };
    const a = drop(JSON.parse(readFileSync(sa, 'utf8')));
    const b = drop(JSON.parse(readFileSync(sb, 'utf8')));
    if (JSON.stringify(a) === JSON.stringify(b)) {
        console.log('  [ok]   summary.json：除计时/核数外逐字段一致');
    } else {
        fails++;
        console.error('  [FAIL] summary.json：非计时字段不一致');
    }
}
if (fails === 0) {
    console.log('[v4-cache] PASS：增量事件缓存不改变任何一条轨迹');
    process.exit(0);
}
console.error(`[v4-cache] FAIL：${fails} 处不一致（缓存陈旧会让动作变 —— 别上线）`);
process.exit(1);
