#!/usr/bin/env node
/**
 * **训练端 C++ 神经网络前向 ↔ Java `NeuralPolicy` 对拍闸门**。
 *
 *   node tools/trainer-net-parity.mjs <net.bin> <corpus.jsonl|轨迹目录> [corpus2 …] [--cpp <exe>] [--tol 1e-4] [--cpp-args "…"]
 *   node tools/trainer-net-parity.mjs --golden        # 用 python/tests/golden/forward.bin 夹具
 *   node tools/trainer-net-parity.mjs <net.bin> <corpus> --selfcheck   # 负向对照（不需要 C++）
 *
 * 两侧（**输出形状完全相同**，见 `tools/NetProbe.java`）：
 *   · Java 参考 = `tools/NetProbe.java`（脚本自己按需 `javac`），逐行 `NeuralPolicy.logits(obs, legal)`；
 *   · C++      = `trainer.exe net <net.bin> <corpus…>`（`--cpp` 可覆盖路径）。
 *
 * 逐行比较：`n` 与 `argmax` 必须**逐字符相同**，`logits` 逐元素 `|Δ| ≤ tol`（缺省 1e-4）；
 * 报告全局 maxΔ 与**每条不一致的行号 / 该行最大 Δ**；末尾 `[net-parity] PASS …` / `[net-parity] FAIL …`。
 *
 * ⚠ 两侧的 `#` 注释行（文件划分）**不作为判据**（两侧口径不同：探针只在"该文件 0 条决策"时打一行，
 *   C++ 侧每个文件都打）—— 只在**行数/step 序列对不上**时作为诊断打印，避免"数值全同却判 FAIL"。
 *
 * ⚠ 语料按**命令行长度**自动分批（Windows 上限 32767 字符：`bc-001` 整个目录 800 个路径 = 33,490
 *   字符，不分批进程根本起不来）。判据与统计跨批聚合；真实成本在**决策数**上（实测 ~5.9 ms/条）。
 *
 * 退出码：0 = PASS；1 = FAIL（有差异 / 负向对照没被抓住）；2 = 用法或环境问题（jar/语料/夹具）；
 *        3 = **C++ 侧尚未就绪**（exe 不在，或 `net` 子命令不可用）—— 既不是 PASS 也不是 FAIL。
 *
 * ⚠ `n` / `argmax` 逐字符比，但 **logits 的文本不逐字符比**（按数值 + 容差）：Java 的 `%.9g`
 *   **保留尾随零**（`-554.013000`），C 的 `%g` 去掉（`-554.013`）且 `NaN`/`Infinity` 拼写不同
 *   （`nan`/`inf`）—— 那是格式差异不是前向差异（实测 1772 条里 1485 个 token 只差尾随零，
 *   15 个是 float32 舍入）。文本差异只在提示行里统计。
 *
 * ⚠ `--selfcheck` 是本闸门的**负向对照**（本项目的老规矩）：跑一次 Java 参考 → 人为改坏一处
 *   （① 某行某个 logit +Δ、② 某行 argmax 改掉）→ 走**同一个比较器**，必须报 FAIL；未改坏的那份
 *   必须报 PASS。两种情形都打印出来。**它不需要 C++**（可以在 C++ 就绪前先验闸门本身不是空转）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, statSync, writeFileSync }
    from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE_JAVA = join(ROOT, 'tools', 'NetProbe.java');
const CLASS_DIR = join(ROOT, 'tools', 'build');
const PROBE_CLASS = join(CLASS_DIR, 'tools', 'NetProbe.class');
// ⚠ 临时产物放 tools/build/（已 gitignore）——**绝不写 trainer/**（那是 C++ 侧的地盘）
const WORK = join(CLASS_DIR, 'net-parity');
const GOLDEN_CANDIDATES = [
    join(ROOT, 'python', 'tests', 'golden', 'forward.bin'),
    join(ROOT, '..', 'python', 'tests', 'golden', 'forward.bin'),
    join(ROOT, '..', '..', 'python', 'tests', 'golden', 'forward.bin'),
];
const GOLDEN_MAGIC = 0x4D4A4746;                 // "MJGF"，与 export.py / SelfTest 同值
const WEIGHT_MAGIC = 0x4D4A4E4E;                 // "MJNN"
const CP_SEP = process.platform === 'win32' ? ';' : ':';
/** 本次进程的临时文件名前缀：并发跑两份闸门时不互相覆盖。 */
const RUN = String(process.pid);
/** 每批 corpus 参数的字符预算（Windows 整条命令行上限 32767，留足 classpath/开关的余量）。
 *  `NET_PARITY_MAX_ARG_CHARS` 是**测试钩子**：设小值可强制多批，用来验分批路径本身。 */
const MAX_ARG_CHARS = Number(process.env.NET_PARITY_MAX_ARG_CHARS) > 0
    ? Number(process.env.NET_PARITY_MAX_ARG_CHARS)
    : (process.platform === 'win32' ? 24000 : 120000);

const EXIT_PASS = 0;
const EXIT_FAIL = 1;
const EXIT_ENV = 2;
const EXIT_CPP_NOT_READY = 3;

function die(msg, code = EXIT_ENV) {
    console.error(`[net-parity] ${msg}`);
    process.exit(code);
}

function usage(code) {
    const out = code === 0 ? console.log : console.error;
    out(`用法：node tools/trainer-net-parity.mjs <net.bin> <corpus.jsonl|轨迹目录> [corpus2 …]`
        + ` [--cpp <exe路径>] [--tol 1e-4] [--cpp-args "…"]\n`
        + `      node tools/trainer-net-parity.mjs --golden [--selfcheck]\n`
        + `      node tools/trainer-net-parity.mjs <net.bin> <corpus> --selfcheck\n`
        + `  每个 <corpus> 是目录时取目录里的 g*.jsonl（按文件名排序，与 export.py 同一口径）；`
        + `语料按命令行长度自动分批\n`
        + `  退出码：0 PASS / 1 FAIL / 2 用法或环境 / 3 C++ 侧尚未就绪`);
    process.exit(code);
}

// ------------------------------------------------------------------ 参数

const argv = process.argv.slice(2);
let golden = false;
let selfcheck = false;
let cppArg = null;
let tol = 1e-4;
let cppExtra = [];
const pos = [];
for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--golden') {
        golden = true;
    } else if (a === '--selfcheck') {
        selfcheck = true;
    } else if (a === '--help' || a === '-h') {
        usage(0);
    } else if (a === '--cpp') {
        cppArg = argv[++i];
        if (!cppArg) die('--cpp 需要一个可执行文件路径');
    } else if (a === '--tol') {
        tol = Number(argv[++i]);
        if (!Number.isFinite(tol) || tol <= 0) die(`--tol 需要一个正数（收到 ${argv[i]}）`);
    } else if (a === '--cpp-args') {
        const v = argv[++i];
        if (v === undefined) die('--cpp-args 需要一个字符串');
        cppExtra = (v.match(/"[^"]*"|\S+/g) ?? []).map((t) => t.replace(/^"|"$/g, ''));
    } else if (a.startsWith('--')) {
        die(`未知开关：${a}`);
    } else {
        pos.push(a);
    }
}
if (!golden && pos.length < 2) {
    usage(EXIT_ENV);
}
if (golden && pos.length > 0) {
    console.log(`[net-parity] --golden：忽略位置参数 ${pos.join(' ')}（夹具自己带权重与语料）`);
}

function expandCorpus(arg) {
    if (!existsSync(arg)) {
        die(`找不到 corpus：${arg}`);
    }
    if (statSync(arg).isFile()) {
        return [arg];
    }
    const files = readdirSync(arg).filter((f) => /^g.*\.jsonl$/.test(f)).sort()
        .map((f) => join(arg, f));
    if (files.length === 0) {
        die(`目录里没有 g*.jsonl：${arg}`);
    }
    return files;
}

// ------------------------------------------------------------------ 子进程

/** ⚠ 管道被禁（受限沙箱下 Node 走管道会 `spawnSync … EPERM`）—— 一律让子进程直接写 fd/文件。 */
function runToFile(cmd, args, outFile, errFile = null) {
    const fd = openSync(outFile, 'w');
    const efd = errFile ? openSync(errFile, 'w') : 'inherit';
    try {
        execFileSync(cmd, args, { stdio: ['ignore', fd, efd] });
        return 0;
    } catch (e) {
        const noStart = !e || ((e.status === undefined || e.status === null) && !e.signal);
        if (noStart) {
            // 起不来（ENOENT / E2BIG=命令行太长 之类）：die 直接退进程，finally 不会跑
            die(`起不来：${cmd}（${e?.code ?? e?.message ?? '未知'}）`
                + `${e?.code === 'E2BIG' || e?.code === 'ENAMETOOLONG' ? '—— 命令行太长，语料要分批' : ''}`);
        }
        return e.status ?? -1;
    } finally {
        try { closeSync(fd); } catch { /* 已关 */ }
        if (efd !== 'inherit') {
            try { closeSync(efd); } catch { /* 已关 */ }
        }
    }
}

function readIfExists(f) {
    return f && existsSync(f) ? readFileSync(f, 'utf8') : '';
}

function ensureProbe() {
    if (!existsSync(JAR)) {
        die(`找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    }
    if (!existsSync(PROBE_JAVA)) {
        die(`找不到探针源码 ${PROBE_JAVA}`);
    }
    const stale = !existsSync(PROBE_CLASS)
        || statSync(PROBE_JAVA).mtimeMs > statSync(PROBE_CLASS).mtimeMs
        || statSync(JAR).mtimeMs > statSync(PROBE_CLASS).mtimeMs;
    if (!stale) {
        return;
    }
    mkdirSync(CLASS_DIR, { recursive: true });
    console.log(`[net-parity] 编译探针：javac -encoding UTF-8 -cp ${JAR} -d ${CLASS_DIR} ${PROBE_JAVA}`);
    try {
        execFileSync('javac', ['-encoding', 'UTF-8', '-cp', JAR, '-d', CLASS_DIR, PROBE_JAVA],
            { stdio: ['ignore', 'inherit', 'inherit'] });
    } catch (e) {
        die(`javac 编译探针失败（退出码 ${e.status ?? '?'}）—— 检查 JDK 21 与 ${JAR}`);
    }
}

/** Java 参考侧：跑探针，返回 stdout 文本。 */
function runJava(netFile, corpora, tag) {
    ensureProbe();
    mkdirSync(WORK, { recursive: true });
    const outFile = join(WORK, `java-${RUN}-${tag}.txt`);
    const code = runToFile('java', ['-cp', JAR + CP_SEP + CLASS_DIR, 'tools.NetProbe', netFile, ...corpora],
        outFile);
    const text = readIfExists(outFile);
    if (code !== 0) {
        console.error(text.slice(0, 2000));
        die(`Java 参考侧探针失败（退出码 ${code}）；完整输出 ${outFile}`);
    }
    console.log(`[net-parity] Java 参考：${corpora.length} 个 corpus → ${outFile}`);
    return { text, outFile };
}

/**
 * 把语料按**命令行长度**分批。
 *
 * ⚠ 为什么必须分：Windows 的整条命令行上限 ~32,767 字符，`bc-001` 整个目录（800 个 `g*.jsonl`）
 *   光路径就有 33,490 字符 → 进程**根本起不来**（Node 报 `status null` + `E2BIG`，看着像"被信号杀掉"）。
 *   分批后每批 ~24000 字符，输出按批顺序拼接/逐批比对，判据不变。
 */
function batchCorpora(corpora) {
    const batches = [];
    let cur = [];
    let len = 0;
    for (const f of corpora) {
        const add = f.length + 1;
        if (cur.length > 0 && len + add > MAX_ARG_CHARS) {
            batches.push(cur);
            cur = [];
            len = 0;
        }
        cur.push(f);
        len += add;
    }
    if (cur.length) {
        batches.push(cur);
    }
    return batches;
}

/**
 * C++ 侧：`<exe> net <net.bin> <corpus…> [--cpp-args …]`。
 * 区分三件事：**尚未就绪**（没有一行载荷 → exit 3）、跑出来**不一样**（FAIL）、一致（PASS）。
 */
function runCpp(exe, netFile, corpora, tag) {
    if (!existsSync(exe)) {
        console.error(`[net-parity] C++ 侧尚未就绪：找不到可执行 ${exe}`);
        console.error('[net-parity]   （先 pwsh -File trainer\\build.ps1，或用 --cpp 指定路径）');
        process.exit(EXIT_CPP_NOT_READY);
    }
    mkdirSync(WORK, { recursive: true });
    const outFile = join(WORK, `cpp-${RUN}-${tag}.txt`);
    const errFile = join(WORK, `cpp-err-${RUN}-${tag}.txt`);
    const args = ['net', netFile, ...corpora, ...cppExtra];
    console.log(`[net-parity] C++ 侧：${exe} ${args.join(' ')}`);
    const code = runToFile(exe, args, outFile, errFile);
    const text = readIfExists(outFile);
    const errText = readIfExists(errFile);
    const rows = text.split(/\r?\n/).filter((l) => l.startsWith('step=')).length;
    if (rows === 0) {
        // 一行载荷都没有 → 子命令还不存在（或起手就崩了）。拿**无参调用**的用法文本当证据。
        const usageFile = join(WORK, `cpp-usage-${RUN}.txt`);
        const usageErrFile = join(WORK, `cpp-usage-err-${RUN}.txt`);
        runToFile(exe, [], usageFile, usageErrFile);
        console.error(`[net-parity] C++ 侧尚未就绪：\`net\` 子命令没有输出任何 step= 行`
            + `（退出码 ${code}）`);
        const all = [text, errText, readIfExists(usageFile), readIfExists(usageErrFile)];
        for (const chunk of all) {
            for (const l of chunk.split(/\r?\n/).filter((x) => x.trim() !== '').slice(0, 6)) {
                console.error(`[net-parity]   | ${l}`);
            }
        }
        console.error(`[net-parity]   （stdout ${outFile} / stderr ${errFile}）`);
        process.exit(EXIT_CPP_NOT_READY);
    }
    const errTail = errText.split(/\r?\n/).filter((x) => x.trim() !== '');
    if (errTail.length) {
        console.log(`[net-parity] C++ 侧 stderr（末 ${Math.min(5, errTail.length)} 行，全文 ${errFile}）：`);
        for (const l of errTail.slice(-5)) console.log(`[net-parity]   | ${l}`);
    }
    if (code !== 0) {
        console.error(`[net-parity] 注意：C++ 侧退出码 ${code}（但有 ${rows} 行载荷，按差异处理）`);
    }
    console.log(`[net-parity] C++ 侧：${rows} 行载荷 → ${outFile}`);
    return text;
}

// ------------------------------------------------------------------ 解析 + 比较

const LINE_RE = /^step=(-?\d+) n=(\d+) argmax=(-?\d+) logits=(.*)$/;
const FINITE_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;
const SPECIAL_RE = /^[+-]?(inf(inity)?|nan)$/i;

/**
 * 一个 logit 文本 token → 数值。**格式按两侧的并集**收：
 * Java 的 `%.9g` 会打 `NaN` / `Infinity`，C 的 `%g` 打 `nan` / `inf`（大小写、拼写都不同），
 * 尾随零也一个保留一个去掉 —— 这些是格式差异，按数值比即可。收不下的 token 直接报错。
 */
function token(v) {
    if (SPECIAL_RE.test(v)) {
        if (/nan/i.test(v)) return NaN;
        return /^-/.test(v) ? -Infinity : Infinity;
    }
    if (!FINITE_RE.test(v)) return undefined;
    return Number(v);
}

/** 把一侧的输出解析成 `{comments, rows}`；形状不符直接返回 error（不是"跳过这一行"）。 */
function parseSide(text, label) {
    const comments = [];
    const rows = [];
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
        const l = lines[i];
        if (l.length === 0) continue;
        if (l.startsWith('#')) {
            comments.push(l);
            continue;
        }
        const m = LINE_RE.exec(l);
        if (!m) {
            return { error: `${label} 第 ${i + 1} 行不符合固定格式：${l.slice(0, 160)}` };
        }
        const n = Number(m[2]);
        const raw = m[4];
        const toks = raw === '' ? [] : raw.split(',');
        if (toks.length !== n) {
            return { error: `${label} 第 ${i + 1} 行：n=${n} 但有 ${toks.length} 个 logits` };
        }
        const vals = [];
        for (const t of toks) {
            const v = token(t);
            if (v === undefined) {
                return { error: `${label} 第 ${i + 1} 行：logits 里有不可解析的数值 "${t}"` };
            }
            vals.push(v);
        }
        rows.push({ line: i + 1, step: Number(m[1]), n, argmax: Number(m[3]), vals, toks, raw });
    }
    return { comments, rows };
}

/** `|Δ|`；两边都是 NaN 视为相等（Δ=0），一边 NaN 一边有限视为不一致（Δ=Infinity）。 */
function delta(a, b) {
    if (Number.isNaN(a) && Number.isNaN(b)) return 0;
    if (Number.isNaN(a) || Number.isNaN(b)) return Infinity;
    if (!Number.isFinite(a) || !Number.isFinite(b)) return a === b ? 0 : Infinity;
    return Math.abs(a - b);
}

/**
 * 比较两侧输出。**同一份比较器**同时服务正向（Java↔C++）与负向（Java↔改坏）。
 * 返回 `{ok, n, maxDelta, textDiff, stepDiff, diag, problems}`；`problems` 每项带行号与该行最大 Δ。
 *
 * ⚠ **`#` 注释行（文件划分）不作为判据**：两侧对它的口径有差异（探针只在"该文件 0 条决策"时打，
 *   C++ 侧每个文件都打），拿它判 FAIL 会把"数值完全一致"的跑批误报成失败。它只在
 *   **行数/step 序列对不上**时作为诊断打印（那时候它正好是定位"哪个文件少了几条"的线索）。
 */
function compare(refText, gotText, tol) {
    const A = parseSide(refText, '参考侧');
    const B = parseSide(gotText, '被测侧');
    if (A.error) return { ok: false, n: 0, maxDelta: NaN, textDiff: 0, stepDiff: 0, diag: [], problems: [{ msg: A.error }] };
    if (B.error) return { ok: false, n: 0, maxDelta: NaN, textDiff: 0, stepDiff: 0, diag: [], problems: [{ msg: B.error }] };

    const problems = [];
    const diag = [];
    const comments = () => `注释行 参考[${A.comments.slice(0, 3).join(' | ') || '无'}]`
        + ` 被测[${B.comments.slice(0, 3).join(' | ') || '无'}]`;
    if (A.comments.join('\n') !== B.comments.join('\n')) {
        diag.push(`注释行（# 文件划分）不同：参考 ${A.comments.length} 条 / 被测 ${B.comments.length} 条`
            + ` —— **不作为判据**（两侧口径不同）`);
    }
    if (A.rows.length !== B.rows.length) {
        problems.push({
            msg: `决策条数不同：参考 ${A.rows.length} / 被测 ${B.rows.length}`,
            detail: (A.rows.length > B.rows.length
                ? `被测侧首个缺行：参考第 ${B.rows.length + 1} 条（step=${A.rows[B.rows.length].step}）`
                : `参考侧首个缺行：被测第 ${A.rows.length + 1} 条（step=${B.rows[A.rows.length].step}）`)
                + `；${comments()}`,
        });
    }

    let maxDelta = 0;
    let textDiff = 0;
    let stepDiff = 0;
    const n = Math.min(A.rows.length, B.rows.length);
    for (let i = 0; i < n; i++) {
        const a = A.rows[i];
        const b = B.rows[i];
        let bad = [];
        let rowMax = 0;
        if (a.step !== b.step) {
            stepDiff++;                                // step 是元数据：只诊断，不判 FAIL
        }
        if (a.n !== b.n) {
            bad.push(`n 参考=${a.n} 被测=${b.n}`);
        }
        if (a.argmax !== b.argmax) {
            bad.push(`argmax 参考=${a.argmax} 被测=${b.argmax}`);
        }
        if (a.n === b.n) {
            for (let j = 0; j < a.vals.length; j++) {
                const d = delta(a.vals[j], b.vals[j]);
                if (d > rowMax) rowMax = d;
                if (d > maxDelta) maxDelta = d;
                if (!(d <= tol)) {
                    bad.push(`logit[${j}] Δ=${fmt(d)}（参考=${a.toks[j]} 被测=${b.toks[j]}）`);
                }
            }
            if (a.raw !== b.raw) textDiff++;
        }
        if (bad.length) {
            problems.push({
                msg: `第 ${i + 1} 条不等（参考第 ${a.line} 行 / step=${a.step}）：`
                    + `该行最大 Δ=${fmt(rowMax)}`,
                detail: bad.slice(0, 3).join('；') + (bad.length > 3 ? `；…共 ${bad.length} 处` : ''),
                line: a.line,
                rowMax,
            });
        }
    }
    return { ok: problems.length === 0, n, maxDelta, textDiff, stepDiff, diag, problems };
}

function fmt(x) {
    if (Number.isNaN(x)) return 'NaN';
    if (!Number.isFinite(x)) return String(x);
    return x === 0 ? '0' : x.toExponential(3);
}

function report(tag, r, tol) {
    const maxOut = 20;
    for (const d of r.diag ?? []) {
        console.log(`[net-parity] [info] ${tag} ${d}`);
    }
    for (const p of r.problems.slice(0, maxOut)) {
        console.error(`  [FAIL] ${tag} ${p.msg}`);
        if (p.detail) console.error(`         ${p.detail}`);
    }
    if (r.problems.length > maxOut) {
        console.error(`  [FAIL] ${tag} …还有 ${r.problems.length - maxOut} 条不一致`);
    }
    console.log(`[net-parity] ${tag} maxΔ=${fmt(r.maxDelta)}（tol ${tol}）`
        + `${r.textDiff ? `；文本不同但数值在容差内的行 = ${r.textDiff}（%.9g 的尾随零/NaN 拼写差异，仅提示）` : ''}`
        + `${r.stepDiff ? `；step 不同的行 = ${r.stepDiff}（元数据，不作为判据）` : ''}`);
}

// ------------------------------------------------------------------ golden 夹具

/** 抽 golden 夹具：权重块 → 临时 net.bin；每行 obs+legal → 临时 corpus；返回 Python 侧 logits。 */
function loadGolden() {
    const fx = GOLDEN_CANDIDATES.find((p) => existsSync(p));
    if (!fx) {
        die(`找不到 golden 夹具（试过 ${GOLDEN_CANDIDATES.join(' / ')}）`);
    }
    const buf = readFileSync(fx);
    if (buf.length < 20) die(`golden 夹具太短：${fx}`);
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const magic = dv.getUint32(0, true);
    if (magic !== GOLDEN_MAGIC) {
        die(`golden 夹具魔数不对：${magic.toString(16)}（期望 ${GOLDEN_MAGIC.toString(16)} "MJGF"）`);
    }
    const version = dv.getUint32(4, true);
    const nCases = dv.getUint32(8, true);
    const wLen = dv.getUint32(12, true);
    // 16 = 保留位（export.py 里写 0）
    let off = 20;
    if (off + wLen > buf.length) die(`golden 夹具权重块越界：len=${wLen}`);
    const weights = buf.subarray(off, off + wLen);
    off += wLen;
    const wdv = new DataView(weights.buffer, weights.byteOffset, weights.byteLength);
    if (weights.length < 28 || wdv.getUint32(0, true) !== WEIGHT_MAGIC) {
        die('golden 夹具里的权重块不是 MJNN 权重（magic 不对）');
    }
    const stateDim = wdv.getUint32(8, true);
    const candDim = wdv.getUint32(12, true);

    mkdirSync(WORK, { recursive: true });
    const netFile = join(WORK, 'golden-net.bin');
    writeFileSync(netFile, weights);

    const cases = [];
    for (let c = 0; c < nCases; c++) {
        const obsLen = dv.getUint32(off, true);
        off += 4;
        const obs = buf.toString('utf8', off, off + obsLen);
        off += obsLen;
        const nLegal = dv.getUint16(off, true);
        off += 2;
        const keys = [];
        for (let k = 0; k < nLegal; k++) {
            const kl = dv.getUint16(off, true);
            off += 2;
            keys.push(buf.toString('utf8', off, off + kl));
            off += kl;
        }
        // state / cand 是 Python 侧的中间量（Java 自检里已逐元素核过）；这里跳过它们，
        // 只取夹具自己带的 logits —— 用来做"golden 夹具自校验"。
        off += stateDim * 4 + nLegal * candDim * 4;
        const logits = [];
        for (let i = 0; i < nLegal; i++) {
            logits.push(dv.getFloat32(off, true));
            off += 4;
        }
        cases.push({ obs, keys, logits });
    }
    const corpusFile = join(WORK, 'golden-corpus.jsonl');
    const lines = cases.map((c, i) => JSON.stringify({
        type: 'decision',
        step: i,
        obs: JSON.parse(c.obs),
        legal: c.keys,
    }));
    writeFileSync(corpusFile, lines.join('\n') + '\n', 'utf8');
    console.log(`[net-parity] golden 夹具 ${fx}（version ${version}，${nCases} 个用例，`
        + `state ${stateDim} / cand ${candDim}）`);
    console.log(`[net-parity]   抽出权重 → ${netFile}（${weights.length} B）`);
    console.log(`[net-parity]   抽出语料 → ${corpusFile}（${lines.length} 行）`);
    return { netFile, corpora: [corpusFile], cases, fx };
}

/** golden 夹具自校验：Java 参考的 logits 必须复现夹具里 Python 算出的 logits。 */
function goldenSelfCheck(javaText, cases, tol) {
    const parsed = parseSide(javaText, 'Java 参考');
    if (parsed.error) {
        console.error(`  [FAIL] golden 夹具自校验：${parsed.error}`);
        return false;
    }
    let maxDelta = 0;
    let bad = 0;
    for (let i = 0; i < Math.min(parsed.rows.length, cases.length); i++) {
        const a = parsed.rows[i].vals;
        const b = cases[i].logits;
        if (a.length !== b.length) {
            console.error(`  [FAIL] golden 用例 ${i}：logits 长度 ${a.length} != 夹具 ${b.length}`);
            bad++;
            continue;
        }
        for (let j = 0; j < a.length; j++) {
            const d = delta(a[j], b[j]);
            if (d > maxDelta) maxDelta = d;
            if (!(d <= tol)) bad++;
        }
    }
    if (parsed.rows.length !== cases.length) {
        console.error(`  [FAIL] golden 夹具自校验：跑了 ${parsed.rows.length} 条 / 夹具 ${cases.length} 条`);
        return false;
    }
    console.log(`[net-parity] golden 夹具自校验（Java 参考 vs 夹具里的 Python logits）：`
        + `maxΔ=${fmt(maxDelta)}${bad === 0 ? ' ✓' : `，${bad} 处超容差 ✗`}`);
    return bad === 0;
}

// ------------------------------------------------------------------ 负向对照

/** 找第一个"有限数值"的 logit 位置（NaN 上加常数是空操作，会让负向对照假绿）。 */
function corruptLogit(text, mag) {
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const m = LINE_RE.exec(lines[i]);
        if (!m) continue;
        const toks = m[4] === '' ? [] : m[4].split(',');
        const j = toks.findIndex((t) => Number.isFinite(Number(t)));
        if (j < 0) continue;
        const before = lines[i];
        toks[j] = String(Number(toks[j]) + mag);
        lines[i] = `step=${m[1]} n=${m[2]} argmax=${m[3]} logits=${toks.join(',')}`;
        return { text: lines.join('\n'), before, after: lines[i], where: `第 ${i + 1} 行 logit[${j}] +${mag}` };
    }
    return null;
}

/** 把某行的 argmax 改成另一个下标（只有在 n ≥ 2 的行上才构造得出来）。 */
function corruptArgmax(text) {
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const m = LINE_RE.exec(lines[i]);
        if (!m) continue;
        const n = Number(m[2]);
        if (n < 2) continue;
        const a = Number(m[3]);
        const flip = a === 0 ? 1 : 0;                      // 保证与原来不同
        const before = lines[i];
        lines[i] = before.replace(` argmax=${a} `, ` argmax=${flip} `);
        return { text: lines.join('\n'), before, after: lines[i], where: `第 ${i + 1} 行 argmax ${a} → ${flip}` };
    }
    return null;
}

function runSelfcheck(javaText, tol) {
    console.log('[net-parity] --selfcheck：只跑 Java 参考侧，用"改坏一处"的负向对照证明比较器不是空转');
    let ok = true;
    const cases = [];
    cases.push({ name: '未改坏的 Java 输出（正向判据）', expect: true, text: javaText });
    const mag = Math.max(tol * 10, 1e-3);
    const c1 = corruptLogit(javaText, mag);
    if (c1) {
        cases.push({ name: `人为改坏：${c1.where}`, expect: false, text: c1.text, evidence: c1 });
    } else {
        console.error('  [FAIL] 构造不出"logit 加一点"的负向对照（输出里没有有限数值？）');
        ok = false;
    }
    const c2 = corruptArgmax(javaText);
    if (c2) {
        cases.push({ name: `人为改坏：${c2.where}`, expect: false, text: c2.text, evidence: c2 });
    } else {
        console.log('  [warn] 构造不出"argmax 改掉"的负向对照（没有 n ≥ 2 的行）—— 跳过这一条');
    }

    mkdirSync(WORK, { recursive: true });
    for (let i = 0; i < cases.length; i++) {
        const c = cases[i];
        const r = compare(javaText, c.text, tol);
        const verdict = r.ok;
        const pass = verdict === c.expect;
        if (!pass) ok = false;
        const why = c.expect ? '漏判（改坏了却报 PASS）' : '空转（改坏了没抓住）';
        console.log(`[net-parity] selfcheck ${i + 1}/${cases.length} ${c.name}`);
        console.log(`             → 比较器报 ${verdict ? 'PASS' : 'FAIL'}（期望 ${c.expect ? 'PASS' : 'FAIL'}）`
            + ` ${pass ? '✓' : `✗ 自检失败：闸门${why}`}`);
        if (c.evidence) {
            const file = join(WORK, `selfcheck-${RUN}-${i}-broken.txt`);
            writeFileSync(file, c.text, 'utf8');
            console.log(`             证据：${c.evidence.before.slice(0, 110)}`);
            console.log(`             改坏：${c.evidence.after.slice(0, 110)}`);
            console.log(`             改坏后的输出：${file}`);
        }
        if (!c.expect) {
            report(`selfcheck ${i + 1}`, r, tol);
        }
    }
    if (ok) {
        console.log(`[net-parity] PASS：自检 ${cases.length}/${cases.length}`
            + `（未改坏 → PASS，改坏 → FAIL；比较器不是空转）`);
    } else {
        console.error('[net-parity] FAIL：自检未按预期（见上）');
    }
    return ok;
}

// ------------------------------------------------------------------ 主流程

let netFile;
let corpora;
let goldenCases = null;
if (golden) {
    const g = loadGolden();
    netFile = g.netFile;
    corpora = g.corpora;
    goldenCases = g.cases;
} else {
    netFile = pos[0];
    if (!existsSync(netFile)) die(`找不到权重文件：${netFile}`);
    corpora = pos.slice(1).flatMap(expandCorpus);
}
const exe = cppArg ?? (existsSync(join(ROOT, 'trainer', 'build', 'trainer.exe'))
    ? join(ROOT, 'trainer', 'build', 'trainer.exe')
    : join(ROOT, 'trainer', 'build', 'trainer'));

console.log(`[net-parity] 权重 ${netFile}；语料 ${corpora.length} 个；tol ${tol}`
    + `${golden ? '；--golden' : ''}${selfcheck ? '；--selfcheck' : ''}`);

const batches = batchCorpora(corpora);
const multi = batches.length > 1;
if (multi) {
    console.log(`[net-parity] 语料分 ${batches.length} 批（Windows 命令行上限 ~32767 字符；`
        + `每批 corpus 参数 ≤ ${MAX_ARG_CHARS} 字符）：`
        + batches.map((b, i) => `#${i + 1}=${b.length} 个`).join(' '));
}
const tagOf = (i) => (multi ? `b${i + 1}` : 'all');
const labelOf = (i) => (multi ? `批 ${i + 1}/${batches.length}` : 'Java↔C++');

// ① Java 参考侧（分批复用同一份比较路径；selfcheck 用拼接后的全文）
const javaTexts = [];
for (let i = 0; i < batches.length; i++) {
    javaTexts.push(runJava(netFile, batches[i], `ref-${tagOf(i)}`).text);
}
const javaAll = javaTexts.join('');
writeFileSync(join(WORK, 'java-ref-latest.txt'), javaAll, 'utf8');
const javaParsed = parseSide(javaAll, 'Java 参考');
if (javaParsed.error) {
    die(javaParsed.error);
}
if (javaParsed.rows.length === 0) {
    die('Java 参考侧 0 条决策 —— 语料里没有 decision 行？比较器拒绝"空转的 PASS"');
}
console.log(`[net-parity] Java 参考合计 ${javaParsed.rows.length} 条决策`
    + `（参考输出留档 ${join(WORK, 'java-ref-latest.txt')}）`);

let ok = true;
if (goldenCases) {
    ok = goldenSelfCheck(javaAll, goldenCases, tol) && ok;
}

if (selfcheck) {
    ok = runSelfcheck(javaAll, tol) && ok;
    process.exit(ok ? EXIT_PASS : EXIT_FAIL);
}

// ② C++ 侧：逐批比对（每批独立一个 exe 进程；判据与统计跨批聚合）
const agg = { n: 0, maxDelta: 0, textDiff: 0, stepDiff: 0, problems: [] };
const cppAll = [];
for (let i = 0; i < batches.length; i++) {
    const cpp = runCpp(exe, netFile, batches[i], `all-${tagOf(i)}`);
    cppAll.push(cpp);
    const r = compare(javaTexts[i], cpp, tol);
    report(labelOf(i), r, tol);
    agg.n += r.n;
    agg.maxDelta = Math.max(agg.maxDelta, r.maxDelta);
    agg.textDiff += r.textDiff;
    agg.stepDiff += r.stepDiff;
    for (const p of r.problems) {
        agg.problems.push(multi ? { ...p, msg: `批 ${i + 1}/${batches.length} ${p.msg}` } : p);
    }
}
writeFileSync(join(WORK, 'cpp-latest.txt'), cppAll.join(''), 'utf8');

if (ok && agg.problems.length === 0) {
    console.log(`[net-parity] PASS：${agg.n} 条决策逐元素一致（maxΔ=${fmt(agg.maxDelta)} ≤ tol ${tol}，`
        + `n 与 argmax 全部相同）`);
    process.exit(EXIT_PASS);
}
console.error(`[net-parity] FAIL：${agg.problems.length} 处不一致 / ${agg.n} 条决策；`
    + `maxΔ=${fmt(agg.maxDelta)}（tol ${tol}）`);
process.exit(EXIT_FAIL);
