#!/usr/bin/env node
/**
 * `paired-eval` —— 「给 N 个策略串 + 一个公共 seed_base + 场数」→ **采集 + 逐场配对表 + 方向敏感判定**
 * 一条命令收口，产物落 `paired/*.json` 与一份 md。
 *
 * 为什么要有它：这一整条路（`style-vector.py --run-out … --policy a,b,c,d --games N --seed S`
 * → 用 `w-ladder.py` 的逐场配对算 Δ/SE/t/CI → 再套伴随量的**方向**判据 → 落盘 + 写报告）
 * 在会话里是**五六条手敲命令 + 一次人工抄表**，而且最容易错的一步肉眼看不出来：
 * 两条采集的 `seed_base` / 学生席对不上时，"配对"就不是配对（牌子换了，不是策略换了）。
 *
 * ⛔ **本工具不自己算任何统计量、也不抄方向表**：全部借 `tools/paired-eval-bridge.py`，
 * 后者 `importlib` 加载**同一个** `tools/w-ladder.py`（`PAIR_AXES` / `_paired_delta` /
 * `_check_pairing` / `_student_per_game` / `companion_verdict`）。判据只有一份 ——
 * 这是 `AGENTS.md` §6.5「⛔ 不要复制出第二套规则」的直接落地。
 *
 * 用法（一行可复制）：
 *   # ① 采集（多策略 = 一个 `--policy` 串，复用现成的 style-vector.py 生产路径）
 *   node tools/paired-eval.mjs collect --policy teacher,net:ckpt/a.bin,first,pass \
 *        --games 200 --seed 40970001 --run-out <数据根>/paired/src/<标签>
 *   # ② 配对评测（同 seed 的两条采集；产物落 --out 目录 + md）
 *   node tools/paired-eval.mjs --base <数据根>/paired/src/a --cand <数据根>/paired/src/b \
 *        --out <数据根>/paired --md <数据根>/paired/a-vs-b.md
 *   node tools/paired-eval.mjs --selftest      # 自检 + 负向自证（不采集、不出网）
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const SELFTEST = argv.includes('--selftest');
const MODE = argv[0] && !argv[0].startsWith('--') ? argv[0] : 'analyze';

function opt(name, dflt) {
    const i = argv.indexOf(name);
    if (i < 0) return dflt;
    const v = argv[i + 1];
    return v === undefined || v.startsWith('--') ? dflt : v;
}
function optAll(name) {
    const out = [];
    argv.forEach((a, i) => { if (a === name && argv[i + 1] && !argv[i + 1].startsWith('--')) out.push(argv[i + 1]); });
    return out;
}
const OUT = opt('--out', 'python/.tmp/paired');
const MD = opt('--md', '');
const BASE = opt('--base', '');
const CANDS = optAll('--cand');
const METRICS = opt('--metric', '');

/** python 解释器：优先仓库自带的 venv（`w-ladder.py` 会 import `mahjong_ml`，见 NOTES §4 的三道坑）。 */
function pythonExe() {
    const venv = join(ROOT, 'python', '.venv', 'Scripts', 'python.exe');
    return existsSync(venv) ? venv : 'python';
}
/** 数据根：⛔ 不静默回落到 `S:\`（那可能不存在/不可写）—— 显式给，或者用仓库内的 gitignore 目录。 */
function dataRoot() {
    return process.env.MAHJONG_DATA_ROOT || join(ROOT, 'python', '.tmp', 'paired-root');
}
function bridgeEnv() {
    return { ...process.env, MAHJONG_DATA_ROOT: dataRoot() };
}
const BRIDGE = join(ROOT, 'tools', 'paired-eval-bridge.py');
const MK = '__PAIRED_JSON__';

/** 调桥；返回 `{code, json, raw}`（解析失败不编造：`json = null` 并保留原始输出）。 */
function callBridge(args) {
    const r = spawnSync(pythonExe(), [BRIDGE, ...args], { cwd: ROOT, env: bridgeEnv(), encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 });
    const raw = `${r.stdout || ''}${r.stderr || ''}`;
    const line = (r.stdout || '').split(/\r?\n/).find((l) => l.startsWith(MK));
    let json = null;
    if (line) { try { json = JSON.parse(line.slice(MK.length)); } catch { json = null; } }
    return { code: r.status === null ? 1 : r.status, json, raw };
}

/** 纯净性检查：md/json 的落点不许是仓库工作区里的受管目录（⛔ 别把数据产物写进 docs/ 或仓库根）。 */
function assertWritableTarget(p) {
    const abs = isAbsolute(p) ? p : resolve(ROOT, p);
    const rel = relative(ROOT, abs).replace(/\\/g, '/');
    const banned = ['docs', 'server', 'client', 'tools', 'trainer', 'python/mahjong_ml'];
    if (rel === '' || banned.some((b) => rel === b || rel.startsWith(b + '/'))) {
        throw new Error(`⛔ 落点 ${rel || '.'} 在仓库受管目录里 ⇒ 拒绝写（数据产物请给数据根或 python/.tmp/…）`);
    }
    return abs;
}

// ---------------------------------------------------------------- md 渲染
function renderMd(payload, label) {
    const L = [];
    const rows = payload.rows || [];
    L.push(`# 配对评测：${label}`);
    L.push('');
    L.push(`- 基准（pre）：\`${payload.base}\``);
    L.push(`- 对照（post）：\`${payload.cand}\``);
    L.push(`- 公共 seed_base：\`${payload.seed_base}\` · 逐场配对的场数：**${payload.pairing?.games}**`
        + `（seed 不符 ${payload.pairing?.seed_mismatch} · 学生席不符 ${payload.pairing?.student_seat_mismatch}）`);
    L.push('- 口径：全部借 `tools/w-ladder.py`（`PAIR_AXES` / `_paired_delta` / `companion_verdict`）；'
        + '⛔ 本文件由 `tools/paired-eval.mjs` 生成，不重复定义任何统计量或方向。');
    L.push('');
    L.push('| 量 | 单位 | pre | post | Δ | SE | t | 95% CI | n | 坏方向 | 判定 |');
    L.push('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |');
    for (const r of rows) {
        const f = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? '—' : Number(x).toFixed(d));
        L.push(`| \`${r.metric}\` | ${r.unit_cn} | ${f(r.pre)} | ${f(r.post)} | **${f(r.delta_pp)}** | ${f(r.se_pp)} `
            + `| ${f(r.t)} | [${f(r.lo_pp)}, ${f(r.hi_pp)}] | ${r.n_games} | ${r.bad_when} `
            + `| ${r.ok ? '未越界' : '**越界**'} |`);
    }
    L.push('');
    L.push('判据（逐字来自 `companion_verdict`）：`down` ⇒ 越界 ⇔ `CI 上界 < 0` 或 `Δ < −tol`；'
        + '`up` ⇒ 越界 ⇔ `CI 下界 > 0` 或 `Δ > +tol`。宽容带随单位走（打点 150 点，pp 类 2pp）。');
    L.push('');
    L.push('<details><summary>逐场配对差 Δ（前 20 场）</summary>');
    L.push('');
    for (const r of rows) {
        L.push(`- \`${r.metric}\`（n=${r.n_games}）：`
            + (r.per_game || []).slice(0, 20).map((g) => `g${g.game}=${g.delta.toFixed(2)}`).join(' '));
    }
    L.push('');
    L.push('</details>');
    L.push('');
    return L.join('\n');
}

// ---------------------------------------------------------------- --selftest
function selftest() {
    let bad = 0;
    const say = (ok, name, detail = '') => {
        if (!ok) bad++;
        console.log(`${ok ? '  [ok]  ' : '  [FAIL]'} ${name}${detail ? ` —— ${detail}` : ''}`);
    };
    console.log(`  python = ${pythonExe()}`);
    // ① 桥的自检（它**真的**加载了 w-ladder.py：方向表覆盖全部 PAIR_AXES、四条实测回归、未知量报错）
    const r = callBridge(['--synthetic']);
    say(r.code === 0 && r.json && r.json.pass === true,
        `桥 --synthetic ⇒ ${r.json ? `${r.json.n_checks} 项检查 / ${r.json.n_bad} 红` : '没解析出 JSON'}`,
        r.code === 0 ? '' : `退出码 ${r.code}`);
    if (r.json) {
        for (const c of r.json.checks.filter((c) => c.name.includes('负向'))) say(c.ok, `桥内负向自证：${c.name}`);
        say(r.json.pair_axes.length >= 8, `方向表借用成功（PAIR_AXES ${r.json.pair_axes.length} 个量）`);
    }
    // ② 负向自证：两条采集不存在 ⇒ 必须非 0 退出（⛔ 不许"没数据也算通过"）
    const miss = callBridge(['--base', join(ROOT, 'python/.tmp/__no_such_dir_a__'), '--cand', join(ROOT, 'python/.tmp/__no_such_dir_b__')]);
    say(miss.code !== 0, '负向：采集目录不存在 ⇒ 非 0 退出', `退出码 ${miss.code}`);
    // ③ 负向自证：往仓库受管目录写产物 ⇒ 必须拒绝
    let refused = false;
    try { assertWritableTarget('docs'); } catch { refused = true; }
    say(refused, '负向：落点选在 docs/ ⇒ 拒绝写');
    console.log(bad === 0 ? '[paired-eval] SELFTEST PASS（含桥内 23 项 + 2 条负向自证）'
        : `[paired-eval] SELFTEST FAIL（${bad} 例）`);
    process.exitCode = bad === 0 ? 0 : 1;
}

// ---------------------------------------------------------------- collect
function collect() {
    const policy = opt('--policy', 'teacher,teacher,first,first');
    const games = opt('--games', '');
    const seed = opt('--seed', '');
    const runOut = opt('--run-out', '');
    const workers = opt('--workers', '8');
    const dry = argv.includes('--dry-run');
    if (!games || !seed || !runOut) {
        console.error('⛔ collect 需要 --games <场数> --seed <seed_base> --run-out <目录>（--policy 缺省 teacher×2+first+pass）');
        process.exitCode = 2;
        return;
    }
    const args = [join(ROOT, 'tools', 'style-vector.py'), '--run-out', runOut, '--games', games,
        '--seed', seed, '--policy', policy, '--workers', workers];
    console.log(`[paired-eval] 采集（复用 style-vector.py 的生产路径）：\n  ${pythonExe()} ${args.join(' ')}`);
    if (dry) { console.log('  --dry-run：只打印，不跑（⛔ 采集 = 自对弈，按本任务的墙钟/口径默认不跑）'); process.exitCode = 0; return; }
    const r = spawnSync(pythonExe(), args, { cwd: join(ROOT, 'python'), env: bridgeEnv(), stdio: 'inherit' });
    process.exitCode = r.status === null ? 1 : r.status;
}

// ---------------------------------------------------------------- analyze
function analyze() {
    if (!BASE || CANDS.length === 0) {
        console.error('用法：node tools/paired-eval.mjs --base <目录> --cand <目录> [--cand <目录>…] '
            + '[--out <目录>] [--md <文件>] [--metric a,b]\n'
            + '      node tools/paired-eval.mjs collect --policy … --games N --seed S --run-out <目录>\n'
            + '      node tools/paired-eval.mjs --selftest');
        process.exitCode = 2;
        return;
    }
    const outDir = assertWritableTarget(OUT);
    mkdirSync(outDir, { recursive: true });
    let anyBad = 0;
    let anyFail = 0;
    for (const cand of CANDS) {
        const label = `${relative(ROOT, resolve(ROOT, BASE)).replace(/\\/g, '/').split('/').pop()}-vs-`
            + `${relative(ROOT, resolve(ROOT, cand)).replace(/\\/g, '/').split('/').pop()}`;
        const args = ['--base', BASE, '--cand', cand];
        if (METRICS) args.push('--metric', METRICS);
        const r = callBridge(args);
        if (!r.json) {
            console.error(`[paired-eval] ⛔ 桥没有返回可解析的 JSON（退出码 ${r.code}）\n${r.raw.slice(-2000)}`);
            process.exitCode = 1;
            return;
        }
        if (r.json.error) { console.error(`[paired-eval] ⛔ ${r.json.error}`); process.exitCode = 1; return; }
        const jsonPath = join(outDir, `${label}.json`);
        writeFileSync(jsonPath, JSON.stringify(r.json, null, 2), 'utf8');
        const over = r.json.rows.filter((x) => !x.ok);
        anyBad += over.length;
        anyFail += r.code !== 0 ? 1 : 0;
        console.log(`[paired-eval] ${label}：配对 ${r.json.pairing?.games} 场`
            + `（seed 不符 ${r.json.pairing?.seed_mismatch} / 学生席不符 ${r.json.pairing?.student_seat_mismatch}）`
            + ` ⇒ 越界 ${over.length} 个${over.length ? '：' + over.map((x) => x.metric).join(',') : ''}`);
        console.log(`  账：${jsonPath}`);
        const md = renderMd(r.json, label);
        const mdPath = MD ? resolve(ROOT, MD) : join(outDir, `${label}.md`);
        writeFileSync(mdPath, md, 'utf8');
        console.log(`  报告：${mdPath}`);
    }
    // 退出码：0 = 全部未越界；3 = 有伴随量越界（与 `w-ladder.py quality` 的 3 同义）
    process.exitCode = anyFail ? 1 : anyBad > 0 ? 3 : 0;
}

if (SELFTEST) selftest();
else if (MODE === 'collect') collect();
else analyze();
