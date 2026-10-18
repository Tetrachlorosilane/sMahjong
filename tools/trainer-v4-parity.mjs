#!/usr/bin/env node
// v4 前向的**三端对拍闸门**（P5）：Python 夹具 ↔ Java（服务端手写前向）↔ C++（训练端镜像）。
//
//   node tools/trainer-v4-parity.mjs --golden [--fixture-semantics c2|c2b3|c1|c3|b3|current]
//       跑 golden 夹具：Java `tools/V4Probe --golden` 与 C++ `trainer v4golden` **各自**必须
//       PASS（特征 + 前向 + 红证），也就是说两边都与 Python 的 `blocks.assemble` + `model.forward`
//       逐元素一致（容差 1e-4）。
//
//       ⚠ **语义档**（丙，第一百零五轮）：夹具必须按**指定**语义重生成，不许"生成时恰好是缺省"
//       当对拍通过。仓库夹具 `407BBE21…` 是 `c2`（部署端 `fusion.out` 仍带 ReLU），而当前 Python
//       缺省是 `current`（C1 去 ReLU + C3 非零初始化 + B3），Java/C++ 仍是 C2 ⇒
//       `--fixture-semantics c2` ⇒ **PASS 11/11**；`--fixture-semantics current` ⇒ **FAIL**（如实报，
//       证明工具不假绿）。默认（不给该开关）**直接用仓库夹具**，一个字节都不改。
//       给了开关时：先把仓库夹具读进内存，重生成 → 跑三端 → **finally 按原字节还原**并核对 sha。
//       `--fixture-out <p>` 改写落点（不碰仓库夹具，用于保留证据）。
//
//   node tools/trainer-v4-parity.mjs <net.bin> <corpus1.jsonl> [corpus2.jsonl …] [--tol 1e-6]
//       同一份真实权重、同一批真实轨迹，Java 与 C++ 各打一遍逐决策 logits，**逐行比**：
//       `n` 与 `argmax` 必须完全相同、`logits` 逐元素 |Δ| ≤ tol（缺省 1e-6；实测 0）。
//
//   node tools/trainer-v4-parity.mjs --selfcheck
//       比较器自身的负向对照：把某一行的某一格扰动一下，必须**报 FAIL**（否则"全绿"可能是因为
//       比较器根本没在比）。不需要 C++ 与权重。
//
// 退出码：0 PASS / 1 FAIL / 2 用法或环境 / 3 C++ 侧未就绪（exe 不存在或没有一行载荷）。
//
// ⚠ 判据是"两侧对同一份权重、同一批输入给出同一批 logits"，**不是**"都能跑"。
//   Java 侧参考实现是 `tools/V4Probe.java`（只调 `V4Features.assemble` / `V4Policy.forwardAll`，
//   不在探针里重写前向）；C++ 侧是 `trainer v4net`。两侧都由 `docs/FEATURES-V4.md` §6 的
//   `net.bin` 格式 2 喂权重。

import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE_SRC = join(HERE, 'V4Probe.java');
const PROBE_OUT = join(HERE, 'build');
const GOLDEN = join(ROOT, 'python', 'tests', 'golden', 'forward-v4.bin');
const WORK = join(HERE, 'build', 'v4-parity');
const RUN = String(Date.now());
const CP_SEP = process.platform === 'win32' ? ';' : ':';
const JAVA_CP = `${JAR}${CP_SEP}${PROBE_OUT}`;

const EXIT_FAIL = 1;
const EXIT_USAGE = 2;
const EXIT_CPP_NOT_READY = 3;

function fail(code, msg) {
  console.error(msg);
  process.exit(code);
}

/**
 * ⚠ 管道被禁（受限沙箱下 Node 走管道会 `spawnSync … EPERM`）—— 子进程一律**直接写文件**，
 * 再把文件读回来（与 `tools/trainer-net-parity.mjs` 同一条纪律）。
 */
function run(cmd, args, tag) {
  mkdirSync(WORK, { recursive: true });
  const outFile = join(WORK, `${tag}-${RUN}.out.txt`);
  const errFile = join(WORK, `${tag}-${RUN}.err.txt`);
  const ofd = openSync(outFile, 'w');
  const efd = openSync(errFile, 'w');
  let status = 0;
  try {
    execFileSync(cmd, args, { stdio: ['ignore', ofd, efd] });
  } catch (e) {
    const noStart = !e || ((e.status === undefined || e.status === null) && !e.signal);
    if (noStart) {
      fail(EXIT_USAGE, `[v4-parity] 起不来：${cmd}（${e?.code ?? e?.message ?? '未知'}）`);
    }
    status = e.status ?? -1;
  } finally {
    try { closeSync(ofd); } catch { /* 已关 */ }
    try { closeSync(efd); } catch { /* 已关 */ }
  }
  const read = (f) => (existsSync(f) ? readFileSync(f, 'utf8') : '');
  return { status, out: read(outFile), err: read(errFile) };
}

/** Java 探针按需编译（与 `tools/trainer-net-parity.mjs` 同一套路）。 */
function ensureProbe() {
  if (!existsSync(JAR)) fail(EXIT_USAGE, `[v4-parity] 找不到服务端 jar：${JAR}（先 pwsh -File server/build.ps1）`);
  mkdirSync(PROBE_OUT, { recursive: true });
  const cls = join(PROBE_OUT, 'tools', 'V4Probe.class');
  const fresh = existsSync(cls) && statSync(cls).mtimeMs >= statSync(PROBE_SRC).mtimeMs
    && statSync(cls).mtimeMs >= statSync(JAR).mtimeMs;
  if (fresh) return;
  try {
    execFileSync('javac', ['-encoding', 'UTF-8', '-cp', JAR, '-d', PROBE_OUT, PROBE_SRC],
      { stdio: ['ignore', 'inherit', 'inherit'] });
  } catch (e) {
    fail(EXIT_USAGE, `[v4-parity] 编译 V4Probe 失败（退出码 ${e.status ?? '?'}）`);
  }
}

function findCpp(explicit) {
  const cands = explicit ? [explicit] : [
    join(ROOT, 'trainer', 'build', 'trainer.exe'),
    join(ROOT, 'trainer', 'build', 'trainer'),
  ];
  for (const c of cands) {
    if (existsSync(c)) return c;
  }
  return null;
}

/** 语料参数：文件直接用；目录展开成 `g*.jsonl`（与 v3 闸门同一口径）。 */
function expandCorpus(paths) {
  const out = [];
  for (const p of paths) {
    if (!existsSync(p)) fail(EXIT_USAGE, `[v4-parity] 找不到语料：${p}`);
    if (statSync(p).isFile()) {
      out.push(p);
      continue;
    }
    const files = readdirSync(p).filter((f) => /^g.*\.jsonl$/.test(f)).sort().map((f) => join(p, f));
    if (files.length === 0) fail(EXIT_USAGE, `[v4-parity] 目录里没有 g*.jsonl：${p}`);
    out.push(...files);
  }
  return out;
}

/** 解析 `step=… n=… argmax=… logits=…` 载荷（`#` 注释行不算判据）。 */
function parseRows(text) {
  const rows = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const m = /^step=(-?\d+)\s+n=(\d+)\s+argmax=(-?\d+)\s+logits=(.*)$/.exec(t);
    if (!m) continue;
    rows.push({ step: Number(m[1]), n: Number(m[2]), argmax: Number(m[3]),
                logits: m[4].length ? m[4].split(',').map(Number) : [] });
  }
  return rows;
}

function compare(aRows, bRows, tol) {
  const n = Math.min(aRows.length, bRows.length);
  let worst = 0;
  let worstRel = 0;
  let argmaxSame = 0;
  const bad = [];
  for (let i = 0; i < n; i++) {
    const a = aRows[i], b = bRows[i];
    if (a.n !== b.n || a.argmax !== b.argmax) {
      bad.push(`第 ${i} 行：n/argmax 不同（java n=${a.n} argmax=${a.argmax} / cpp n=${b.n} argmax=${b.argmax}）`);
      continue;
    }
    argmaxSame++;
    if (a.logits.length !== b.logits.length) {
      bad.push(`第 ${i} 行：logits 个数不同（${a.logits.length} vs ${b.logits.length}）`);
      continue;
    }
    let rowWorst = 0;
    for (let k = 0; k < a.logits.length; k++) {
      const d = Math.abs(a.logits[k] - b.logits[k]);
      rowWorst = Math.max(rowWorst, d);
      const scale = Math.max(1e-9, Math.abs(a.logits[k]), Math.abs(b.logits[k]));
      worstRel = Math.max(worstRel, d / scale);
    }
    worst = Math.max(worst, rowWorst);
    if (rowWorst > tol) {
      bad.push(`第 ${i} 行：logits 最大差 ${rowWorst.toExponential(2)} > ${tol}`);
    }
  }
  if (aRows.length !== bRows.length) {
    bad.push(`行数不同：java ${aRows.length} / cpp ${bRows.length}`);
  }
  return { worst, worstRel, argmaxSame, bad, n };
}

function golden(cpp, tol, fixture = GOLDEN) {
  ensureProbe();
  const jr = run('java', ['-Dstdout.encoding=UTF-8', '-cp', JAVA_CP, 'tools.V4Probe',
    '--golden', fixture, '--tol', String(tol)], 'java-golden');
  const javaLine = (jr.out + jr.err).split(/\r?\n/).filter((l) => l.includes('golden cases=')).pop();
  console.log(`  Java : ${javaLine || '(没有输出)'}  → exit ${jr.status}`);
  if (jr.status !== 0) {
    console.error('[v4-parity] Java golden 未通过');
    return EXIT_FAIL;
  }
  if (!cpp) {
    console.error('[v4-parity] C++ 侧未就绪（trainer 可执行文件不在）：只跑了 Java 一半');
    return EXIT_CPP_NOT_READY;
  }
  const cr = run(cpp, ['v4golden', fixture, '--tol', String(tol)], 'cpp-golden');
  const cppLine = (cr.out + cr.err).split(/\r?\n/).filter((l) => l.includes('golden cases=')).pop();
  console.log(`  C++  : ${cppLine || '(没有输出)'}  → exit ${cr.status}`);
  if (cr.status !== 0) {
    console.error('[v4-parity] C++ golden 未通过');
    return EXIT_FAIL;
  }
  console.log(`PASS：Java 与 C++ 分别与 Python 夹具一致（容差 ${tol}）`);
  return 0;
}

function sha256(buf) {
  return createHash('sha256').update(buf).digest('hex').toUpperCase();
}

function findPython() {
  const cands = [process.env.MAHJONG_PYTHON,
    join(ROOT, 'python', '.venv', 'Scripts', 'python.exe'),
    join(ROOT, 'python', '.venv', 'bin', 'python')].filter(Boolean);
  for (const c of cands) if (existsSync(c)) return c;
  return null;
}

/** 夹具的 `.json` 边车（trace / cases / 语义档）；缺了就返回空对象。 */
function fixtureMeta(binPath) {
  const j = binPath.replace(/\.bin$/i, '.json');
  if (!existsSync(j)) return {};
  try { return JSON.parse(readFileSync(j, 'utf8')); } catch { return {}; }
}

/** 按**指定语义**重生成夹具（调 `python -m mahjong_ml.v4.export golden`；只读轨迹、写 out）。 */
function regenerateFixture(semantics, out, trace, cases) {
  const py = findPython();
  if (!py) fail(EXIT_USAGE, '[v4-parity] 找不到 python（设 MAHJONG_PYTHON，或建 python/.venv）');
  mkdirSync(WORK, { recursive: true });
  const outFile = join(WORK, `regen-${semantics}-${RUN}.out.txt`);
  const errFile = join(WORK, `regen-${semantics}-${RUN}.err.txt`);
  const ofd = openSync(outFile, 'w');
  const efd = openSync(errFile, 'w');
  let status = 0;
  try {
    execFileSync(py, ['-m', 'mahjong_ml.v4.export', 'golden', '--trace', String(trace),
      '--out', out, '--cases', String(cases), '--semantics', semantics], {
      cwd: join(ROOT, 'python'),
      stdio: ['ignore', ofd, efd],
      env: { ...process.env, PYTHONPATH: join(ROOT, 'python') },
    });
  } catch (e) {
    status = e.status ?? -1;
  } finally {
    try { closeSync(ofd); } catch { /* 已关 */ }
    try { closeSync(efd); } catch { /* 已关 */ }
  }
  const log = `${existsSync(outFile) ? readFileSync(outFile, 'utf8') : ''}`
    + `${existsSync(errFile) ? readFileSync(errFile, 'utf8') : ''}`;
  if (status !== 0) {
    fail(EXIT_FAIL, `[v4-parity] 夹具重生成失败（semantics=${semantics}，exit ${status}）：\n${log.trim().slice(-3000)}`);
  }
  console.log(`  夹具 : semantics=${semantics} → ${out}（${statSync(out).size} B，`
    + `sha256 ${sha256(readFileSync(out)).slice(0, 8)}…）`);
  return out;
}

/** `--golden --fixture-semantics <档>`：重生成 → 三端跑 → **finally 还原仓库夹具**。 */
function goldenWithSemantics(cpp, semantics, opts) {
  const meta = fixtureMeta(GOLDEN);
  const trace = opts.trace || meta.trace;
  const cases = opts.cases || meta.cases || 11;
  if (!trace) {
    fail(EXIT_USAGE, '[v4-parity] 不知道轨迹目录：给 --fixture-trace（夹具边车里没有 trace）');
  }
  const out = opts.out ? resolve(opts.out) : GOLDEN;
  const replacesRepo = out === GOLDEN;
  const origBin = replacesRepo ? readFileSync(GOLDEN) : null;
  const origJson = replacesRepo && existsSync(GOLDEN.replace(/\.bin$/i, '.json'))
    ? readFileSync(GOLDEN.replace(/\.bin$/i, '.json')) : null;
  const origSha = origBin ? sha256(origBin) : null;
  if (replacesRepo) {
    console.log(`  仓库夹具原件：sha256 ${origSha.slice(0, 8)}…（跑完按原字节还原）`);
  }
  try {
    regenerateFixture(semantics, out, trace, cases);
    return golden(cpp, 1e-4, out);
  } finally {
    if (replacesRepo) {
      writeFileSync(GOLDEN, origBin);
      const jsonPath = GOLDEN.replace(/\.bin$/i, '.json');
      if (origJson) writeFileSync(jsonPath, origJson);
      const backSha = sha256(readFileSync(GOLDEN));
      const same = backSha === origSha;
      console.log(`  还原 : ${GOLDEN} ← 原字节（sha256 ${backSha.slice(0, 8)}… `
        + `${same ? '== 原件' : '≠ 原件 ⛔'}）`);
      if (!same) {
        console.error('[v4-parity] ⛔ 夹具还原失败：sha256 与原件不同');
        process.exitCode = EXIT_FAIL;
      }
    }
  }
}

function parity(net, corpus, cpp, tol) {
  ensureProbe();
  if (!existsSync(net)) fail(EXIT_USAGE, `[v4-parity] 找不到权重：${net}`);
  if (!cpp) fail(EXIT_CPP_NOT_READY, '[v4-parity] C++ 侧未就绪（先 pwsh -File trainer/build.ps1）');
  const CHUNK = 8;                               // Windows 命令行长度限制：分批喂
  let rowsJava = 0, rowsCpp = 0, worst = 0, worstRel = 0, argmaxSame = 0, badLines = 0;
  for (let i = 0; i < corpus.length; i += CHUNK) {
    const part = corpus.slice(i, i + CHUNK);
    const jr = run('java', ['-Dstdout.encoding=UTF-8', '-cp', JAVA_CP, 'tools.V4Probe', net, ...part],
      `java-part${i}`);
    if (jr.status !== 0) fail(EXIT_FAIL, `[v4-parity] Java 侧失败（exit ${jr.status}）：${jr.err.trim()}`);
    const cr = run(cpp, ['v4net', net, ...part], `cpp-part${i}`);
    if (cr.status !== 0) fail(EXIT_FAIL, `[v4-parity] C++ 侧失败（exit ${cr.status}）：${cr.err.trim()}`);
    const a = parseRows(jr.out), b = parseRows(cr.out);
    if (b.length === 0) fail(EXIT_CPP_NOT_READY, '[v4-parity] C++ 侧一行载荷都没有（子命令没接上？）');
    const res = compare(a, b, tol);
    rowsJava += a.length; rowsCpp += b.length;
    worst = Math.max(worst, res.worst);
    worstRel = Math.max(worstRel, res.worstRel);
    argmaxSame += res.argmaxSame;
    for (const m of res.bad) {
      if (badLines < 5) console.error(`  ✗ ${m}`);
      badLines++;
    }
  }
  if (badLines > 0) {
    console.error(`FAIL：${badLines} 处不一致（逐元素 maxΔ=${worst.toExponential(3)}，tol=${tol}）`);
    return EXIT_FAIL;
  }
  console.log(`PASS：${rowsJava} 条决策 / argmax 全同 ${argmaxSame} 条 / Java↔C++ maxΔ=`
    + `${worst.toExponential(3)}（相对 ${worstRel.toExponential(2)}）≤ tol ${tol}`);
  console.log('      注：v4 前向里有 exp/tanh（softmax、GRU、attention），两侧 libm 的末位差理论上会随'
    + '权重尺度放大 —— 本机实测（1.36M 参数真权重 / 1,885 条决策）**maxΔ = 0**，'
    + '但判据仍写成"绝对差 ≤ tol **且 argmax 逐条相同**"，免得换 libm/编译器就假红（v3 那条前向'
    + '没有超越函数，恒为 0）');
  return 0;
}

function selfcheck() {
  const a = parseRows('step=0 n=3 argmax=1 logits=0.5,-1.25,2\nstep=1 n=2 argmax=0 logits=1e-3,2e-3\n');
  const same = compare(a, a, 1e-6);
  const bumped = a.map((r) => ({ ...r, logits: r.logits.map((v, i) => (i === 1 ? v + 1e-3 : v)) }));
  const diff = compare(a, bumped, 1e-6);
  const argmaxDiff = compare(a, a.map((r, i) => (i === 0 ? { ...r, argmax: 2 } : r)), 1e-6);
  const shortRows = compare(a, a.slice(0, 1), 1e-6);
  const ok = same.bad.length === 0 && diff.bad.length > 0 && argmaxDiff.bad.length > 0
    && shortRows.bad.length > 0;
  console.log(`  相同输入 → ${same.bad.length === 0 ? 'PASS（无差异）' : 'FAIL（不该有差异）'}`);
  console.log(`  扰动 logits 1e-3 → ${diff.bad.length > 0 ? 'FAIL（判据生效）' : 'PASS（判据失效！）'}`);
  console.log(`  扰动 argmax → ${argmaxDiff.bad.length > 0 ? 'FAIL（判据生效）' : 'PASS（判据失效！）'}`);
  console.log(`  行数不齐 → ${shortRows.bad.length > 0 ? 'FAIL（判据生效）' : 'PASS（判据失效！）'}`);
  console.log(ok ? 'SELFCHECK PASS：比较器不是空转' : 'SELFCHECK FAIL');
  return ok ? 0 : EXIT_FAIL;
}

function main(argv) {
  const args = argv.slice(2);
  let tol = 1e-5;
  let cppArg = null;
  let fixtureSem = null;
  const fixtureOpts = {};
  const rest = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--tol') tol = Number(args[++i]);
    else if (args[i] === '--cpp') cppArg = args[++i];
    else if (args[i] === '--fixture-semantics') fixtureSem = args[++i];
    else if (args[i] === '--fixture-out') fixtureOpts.out = args[++i];
    else if (args[i] === '--fixture-trace') fixtureOpts.trace = args[++i];
    else if (args[i] === '--fixture-cases') fixtureOpts.cases = Number(args[++i]);
    else rest.push(args[i]);
  }
  const cpp = findCpp(cppArg);
  if (rest.includes('--selfcheck') || (rest.length === 0 && args.length === 0)) return selfcheck();
  if (rest[0] === '--golden') {
    if (!fixtureSem) return golden(cpp, 1e-4);
    const valid = ['c2', 'c2b3', 'c1', 'c3', 'b3', 'current'];
    if (!valid.includes(fixtureSem)) {
      console.error(`[v4-parity] 未知语义档 ${fixtureSem}（可选：${valid.join('|')}）`);
      return EXIT_USAGE;
    }
    console.log(`== golden 夹具语义档：${fixtureSem}`);
    return goldenWithSemantics(cpp, fixtureSem, fixtureOpts);
  }
  if (rest.length < 2) {
    console.error('用法：node tools/trainer-v4-parity.mjs --golden [--fixture-semantics c2|c2b3|c1|c3|b3|current]'
      + ' [--fixture-out <p>] | --selfcheck'
      + ' | <net.bin> <corpus.jsonl> … [--cpp <exe>] [--tol 1e-6]');
    return EXIT_USAGE;
  }
  const net = resolve(rest[0]);
  const corpus = expandCorpus(rest.slice(1).map((p) => resolve(p)));
  console.log(`== v4 三端对拍：${net}`);
  console.log(`   语料 ${corpus.length} 个文件；C++ = ${cpp || '(未找到)'}；tol=${tol}`);
  return parity(net, corpus, cpp, tol);
}

process.exit(main(process.argv));
