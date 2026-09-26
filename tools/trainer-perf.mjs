#!/usr/bin/env node
/**
 * **性能口径统一的小工具**（临时/可复用）：同一条 selfplay 命令跑 N 遍，报**墙钟**与
 * `summary.json` 里的 `seconds` / `decisions_per_second`，并给出中位数与极差。
 *
 *   node tools/trainer-perf.mjs <exe> <标签> [场数] [worker列表] [策略] [种子] [重复次数]
 *
 * 为什么要它：比较编译选项（`-flto` 等）时，"快 2%" 和"纯噪声"必须分得开 ——
 * 单次测量在本机（32 逻辑核、共享机器）极差可达 10%。这里固定：同一批场次、同一核数、
 * 每个配置跑同样的次数，并把三次的结果全列出来（**不挑最好的一次**）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, mkdirSync, openSync, rmSync, existsSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const BUILD = join(ROOT, 'trainer', 'build');
const [exe, label, gamesArg, workersArg, policyArg, seedArg, repsArg] = process.argv.slice(2);
if (!exe || !label) {
    console.error('用法：node tools/trainer-perf.mjs <exe> <标签> [场数] [worker列表] [策略] [种子] [重复次数]');
    process.exit(2);
}
const GAMES = Number(gamesArg ?? 100);
const WORKERS = String(workersArg ?? '24').split(',').map(Number);
const POLICY = policyArg ?? 'first';
const SEED = seedArg ?? '20260101';
const REPS = Number(repsArg ?? 3);
if (!existsSync(exe)) {
    console.error(`找不到 ${exe}`);
    process.exit(2);
}

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
for (const w of WORKERS) {
    const walls = [];
    const dps = [];
    let decisions = 0;
    for (let r = 0; r < REPS; r++) {
        const dir = join(BUILD, `perf-${label}-w${w}-r${r}`);
        rmSync(dir, { recursive: true, force: true });
        mkdirSync(dir, { recursive: true });
        const log = `${dir}.log`;
        const fd = openSync(log, 'w');
        const t0 = process.hrtime.bigint();
        try {
            execFileSync(exe, ['selfplay', String(GAMES), '--workers', String(w), '--policy', POLICY,
                '--seed', SEED, '--hands', '0', '--out', dir], { stdio: ['ignore', fd, 'inherit'] });
        } finally {
            closeSync(fd);
        }
        const wall = Number(process.hrtime.bigint() - t0) / 1e9;
        const s = JSON.parse(readFileSync(join(dir, 'summary.json'), 'utf8'));
        walls.push(wall);
        dps.push(s.decisions_per_second);
        decisions = s.decisions;
        rmSync(dir, { recursive: true, force: true });
        rmSync(log, { force: true });
    }
    console.log(`${label} · workers=${w} · ${GAMES} 场 × ${POLICY} · 决策 ${decisions}（${REPS} 次）`);
    console.log(`  墙钟 s: ${walls.map((x) => x.toFixed(3)).join(' / ')} → 中位 ${median(walls).toFixed(3)}`);
    console.log(`  决策/秒（自身计时）: ${dps.map((x) => Math.round(x)).join(' / ')} → 中位 ${Math.round(median(dps))}`);
}
