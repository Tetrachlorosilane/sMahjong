#!/usr/bin/env node
/**
 * `jvm-guard` —— 跑「起 JVM（服务端）+ 打若干局」这类**验证性**命令时的崩溃包装器。
 *
 * 为什么要有它：本机有一类**与代码无关**的偶发 JVM 崩溃（`NOTES.md` §4 L1 注解已登记，
 * 仓库根现存 6 份 `hs_err_pid*.log`）：`EXCEPTION_ACCESS_VIOLATION (0xc0000005)`，
 * 崩溃帧恒为 `J <n> c2 mahjong.rules.Shanten.dfs([IIIII[I)V`。2026-10-07 凌晨 **20 分钟内撞 2 次**。
 * 手搓的应对是「看到 `hs_err` 横幅 + 退出码 1 就先重跑」—— 这句话在会话里反复出现，却**没有判据**：
 * ① 重跑几次、② 因为什么重跑、③ 原始失败到底几次，全靠人记；而「重跑后绿了」最容易被顺手读成
 * 「这次验证通过」，**把一次真实的进程级崩溃洗掉**。
 *
 * 所以这里把那条口头纪律变成一条命令 + 一条判据：
 *   · **检测**：退出码非 0 **或** 本次尝试新落下一份 `hs_err_pid*.log`（后者更重要 —— 它才是
 *     "JVM 崩了"的直接证据，退出码可能被外层 shell 吞掉）⇒ 判为一次失败；
 *   · **签名**：从 `hs_err` 里抽 `Problematic frame` / 访问地址 / JVM 报的 elapsed，再从子进程输出里
 *     抽「局号」（`--hand-regex`）⇒ 报告里能说清"崩在哪一帧、第几局、什么时候"；
 *   · **重试**：缺省重试 1 次（与 `NOTES.md` §4 的口径一致），每次尝试的 stdout/stderr 都单独落盘；
 *   · ⛔ **重试不算通过**：状态只有 `PASS`（第一次就干净）与 `RETRIED-PASS`（靠重试才绿）两种，
 *     后者**退出码 3**，并且汇总里**永远**打印原始失败次数与全部签名。要把它当通过用，
 *     必须显式加 `--allow-retry-pass`（写进调用处 = 留下"我知道这次崩过"的痕迹）。
 *
 * 用法（一行可复制）：
 *   node tools/jvm-guard.mjs --out python/.tmp/jvm-guard --port 10086 -- \
 *        java -jar server/build/mahjong-server.jar --port 10086
 *   node tools/jvm-guard.mjs --selftest          # 自检 + 负向自证（不动仓库、不出网）
 *
 * ⛔ 只读/只包装：不改服务端代码、不改任何被包装命令的行为；规避实验（`-XX:CompileCommand=…`）
 * 只能写在**被包装命令**那一侧，属于本工具的用法而不是它的实现。
 */
import { execFileSync, spawn } from 'node:child_process';
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// ---------------------------------------------------------------- 参数
const argv = process.argv.slice(2);
/**
 * ⚠ **本工具的开关只在 `--` 之前解析**。踩过一次真的：包的是
 * `java -jar server/build/mahjong-server.jar --selftest`，而被包装命令自己就带 `--selftest`
 * ⇒ 用 `argv.includes('--selftest')` 会让 guard **去跑自己的自检而不是那条命令**，
 * 输出看着还挺正常（"SELFTEST PASS"）—— 这就是典型的"包装器把被测物吞了"。
 */
const sep = argv.indexOf('--');
const OWN = sep >= 0 ? argv.slice(0, sep) : argv;
const SELFTEST = OWN.includes('--selftest');
const ALLOW_RETRY_PASS = OWN.includes('--allow-retry-pass');

/**
 * 切开「本工具的开关」与「被包装命令」。⚠ 抽成纯函数是为了**能被自检**：bug 就出在这一刀上
 * （见上面那段注释），只有让自检直接打这个函数，才算真的钉住了它。
 */
function splitOwnArgs(list) {
    const s = list.indexOf('--');
    if (s < 0) return { own: list, cmd: undefined, rest: [] };
    return { own: list.slice(0, s), cmd: list[s + 1], rest: list.slice(s + 2) };
}

function opt(name, dflt) {
    const i = OWN.indexOf(name);
    if (i < 0) return dflt;
    const v = OWN[i + 1];
    return v === undefined || v.startsWith('--') ? dflt : v;
}
const RETRIES = Number(opt('--retries', '1'));
const HAND_RE = new RegExp(opt('--hand-regex', String.raw`(?:第\s*(\d+)\s*(?:局|小局)|hand\s*#?\s*(\d+))`), 'i');
const OUT = resolve(ROOT, opt('--out', 'python/.tmp/jvm-guard'));
const CWD = resolve(ROOT, opt('--cwd', '.'));
const PORT = opt('--port', '');            // 给了就负责收尾（杀监听 + 复查残留）
const JSON_OUT = opt('--json', '');
/**
 * `--mark <正则>`：要求子进程输出里出现这个标记，**否则这一遍不算干净**。
 * 为什么需要它：被包装的那些命令（`--selftest` / 自检类）的契约是"**输出里出现 PASS**"，
 * 而退出码为 0 却不含标记 = 典型的静默降级（`AGENTS.md` §4 与 `all-checks.ps1` 都写了这一条）。
 * 把这条判据放在 guard 里而不是每个调用处，是为了只有**一处**定义"干净"。
 */
const MARK = opt('--mark', '');

/** `--` 后面的才是被包装命令（`--` 之前全是本工具的开关）。 */
const CMD = sep >= 0 ? argv[sep + 1] : undefined;
const CMD_ARGS = sep >= 0 ? argv.slice(sep + 2) : [];

/**
 * 自检用的额外扫描目录。⚠ 这个口子是**被自检的第 ③ 例逼出来的**：最初的实现只扫 cwd 与仓库根，
 * 于是"退出码 0 但新落了 hs_err"那一例被判成 PASS —— 负向自证当场把这个假绿抓了出来。
 * 同理，真机上 `-XX:ErrorFile` 指到别处时要显式 `--error-file-dir`，⛔ 不许假装扫过了。
 */
let EXTRA_ERROR_DIR = '';

/** 扫 `hs_err` 的目录：缺省 = 子进程 cwd（JVM 的 `-XX:ErrorFile` 缺省落在那里）+ 仓库根。 */
function errorDirs() {
    const extra = opt('--error-file-dir', '');
    const dirs = [CWD, ROOT, EXTRA_ERROR_DIR];
    if (extra) dirs.push(resolve(ROOT, extra));
    return [...new Set(dirs.filter(Boolean))];
}

/** 目录里现存的 `hs_err_pid*.log` → `{路径: mtimeMs}`（用于"这次尝试**新**落下的"判定）。 */
function snapshotHsErr() {
    const map = new Map();
    for (const d of errorDirs()) {
        let names = [];
        try { names = readdirSync(d); } catch { continue; }
        for (const n of names) {
            if (!/^hs_err_pid\d+\.log$/.test(n)) continue;
            const p = join(d, n);
            try { map.set(p, statSync(p).mtimeMs); } catch { /* 竞争：文件刚被删 */ }
        }
    }
    return map;
}

/**
 * 从 `hs_err` 里抽签名。⛔ 缺字段不编造：抽不到就写 `(未识别)` —— 报告里必须能看出"没读到"，
 * 而不是让一个空串看起来像"没有崩溃帧"。
 */
function parseHsErr(path) {
    let text = '';
    try { text = readFileSync(path, 'utf8'); } catch { return { file: path, frame: '(读不到)' }; }
    // ⚠ 行尾必须是 `\r?\n`：Windows 上 JVM 写的 `hs_err` 是 **CRLF**，只写 `\n` 会让
    //   「Problematic frame」那一条**恒抽不到**（实测第一版就是这样：原始失败报出来了，
    //   但签名里 `frame: (未识别)` 而日志里明明有 —— 报告看不出崩在哪一帧）。
    const line = (re) => {
        const m = text.match(re);
        return m ? m[1].trim() : '';
    };
    return {
        file: path,
        error: line(/#\s+(EXCEPTION_[A-Z_]+[^\r\n]*)/),
        frame: line(/# Problematic frame:\r?\n# ([^\r\n]+)/) || '(未识别)',
        commandLine: line(/Command Line: ([^\r\n]+)/),
        elapsed: line(/elapsed time: ([^\r\n]+)/),
        host: line(/Host: ([^\r\n]+)/),
    };
}

/** 从子进程输出里抽"局号"（缺省匹配「第 N 局 / hand N」）；抽不到返回 null（不瞎填 0）。 */
function handOf(text) {
    const m = HAND_RE.exec(text || '');
    if (!m) return null;
    const v = m[1] ?? m[2];
    return v === undefined ? null : Number(v);
}

/** 跑一次命令，返回 `{code, out, err, hsErr: [...], sec}`；不抛（失败也是一种结果）。 */
function runOnce(cmd, args, logBase) {
    return new Promise((res) => {
        const before = snapshotHsErr();
        const t0 = Date.now();
        const child = spawn(cmd, args, { cwd: CWD, stdio: ['ignore', 'pipe', 'pipe'] });
        let out = '';
        let err = '';
        child.stdout.on('data', (d) => { out += d.toString('utf8'); });
        child.stderr.on('data', (d) => { err += d.toString('utf8'); });
        child.on('error', (e) => {
            res({ code: 127, out, err: err + `\n[spawn 失败] ${e.message}`, hsErr: [], sec: 0 });
        });
        child.on('close', (code, signal) => {
            const after = snapshotHsErr();
            // "新落下的" = 这次之前没有，或 mtime 变新了（同名文件被覆盖）
            const fresh = [...after.entries()]
                .filter(([p, m]) => !before.has(p) || m > (before.get(p) ?? 0))
                .map(([p]) => parseHsErr(p));
            writeFileSync(`${logBase}.out.txt`, out, 'utf8');
            writeFileSync(`${logBase}.err.txt`, err, 'utf8');
            res({
                code: code === null ? 1 : code,
                signal: signal || '',
                out,
                err,
                hsErr: fresh,
                sec: Math.round((Date.now() - t0) / 100) / 10,
            });
        });
    });
}

// ---------------------------------------------------------------- 收尾：按端口杀 + 复查残留
function listenersOn(port) {
    try {
        const txt = execFileSync('powershell', ['-NoProfile', '-Command',
            `(Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen -ErrorAction SilentlyContinue `
            + '| Select-Object -ExpandProperty OwningProcess -Unique) -join ","'], { encoding: 'utf8' });
        return txt.trim().split(',').map((s) => s.trim()).filter((s) => /^\d+$/.test(s));
    } catch {
        return [];
    }
}

function killPort(port) {
    const pids = listenersOn(port);
    for (const pid of pids) {
        try {
            execFileSync('powershell', ['-NoProfile', '-Command', `Stop-Process -Id ${pid} -Force`], { stdio: 'ignore' });
        } catch { /* 已经退了 */ }
    }
    const left = listenersOn(port);
    return { pids, left };
}

// ---------------------------------------------------------------- 汇总
/**
 * 判定一次运行。⛔ 这里就是"重试不算通过"的**唯一实现处**：
 *   · `state` 只有 `PASS` / `RETRIED-PASS` / `FAIL`；
 *   · 退出码：`PASS` → 0；`RETRIED-PASS` → **3**（要显式 `--allow-retry-pass` 才是 0）；`FAIL` → 1。
 */
function decide(attempts, hsErrKind) {
    const n = attempts.length;
    const firstClean = attempts[0].clean;
    const lastClean = attempts[n - 1].clean;
    let state;
    if (firstClean) state = 'PASS';
    else if (lastClean) state = 'RETRIED-PASS';
    else state = 'FAIL';
    const originalFailures = firstClean ? 0 : 1;
    const retryFailures = attempts.slice(1).filter((a) => !a.clean).length;
    const exit = state === 'PASS' ? 0 : state === 'RETRIED-PASS' ? (ALLOW_RETRY_PASS ? 0 : 3) : 1;
    return { state, exit, attempts: n, originalFailures, retries: n - 1, retryFailures };
}

/** 一次尝试"干净"的唯一判据：退出码 0 **且** 没新落 hs_err **且**（若给了 `--mark`）输出里有标记。 */
function isClean(a) {
    if (a.code !== 0 || a.hsErr.length > 0) return false;
    if (MARK && !new RegExp(MARK).test(a.out + a.err)) return false;
    return true;
}

function attemptReport(a, i) {
    if (a.clean) return `  尝试 #${i + 1}：干净（退出码 0，无新 hs_err${MARK ? `，含标记 /${MARK}/` : ''}，${a.sec}s）`;
    const why = [];
    if (a.hsErr.length) why.push(`新增 ${a.hsErr.length} 份 hs_err`);
    if (a.code !== 0) why.push(`退出码 ${a.code}${a.signal ? ` (signal ${a.signal})` : ''}`);
    if (MARK && a.code === 0 && a.hsErr.length === 0 && !new RegExp(MARK).test(a.out + a.err)) {
        why.push(`退出码 0 但输出里没有标记 /${MARK}/ ⇒ 判失败（退出码 0 不算证据）`);
    }
    return `  尝试 #${i + 1}：失败（${why.join(' + ')}，${a.sec}s）`;
}

function signatureLines(attempts) {
    const lines = [];
    attempts.forEach((a, i) => {
        if (a.clean) return;
        if (!a.hsErr.length) {
            lines.push(`  #${i + 1} exit-only：退出码 ${a.code}，没有 hs_err ⇒ 这不是"JVM 崩了"，是命令自己失败`
                + `（局号 ${handOf(a.out + a.err) ?? '(输出里没有局号)'}）`);
            return;
        }
        for (const h of a.hsErr) {
            lines.push(`  #${i + 1} hs_err=${h.file}`);
            lines.push(`      fatal: ${h.error || '(未识别)'}`);
            lines.push(`      frame: ${h.frame}`);
            lines.push(`      elapsed: ${h.elapsed || '(未识别)'}`);
            lines.push(`      hand: ${handOf(a.out + a.err) ?? '(输出里没有局号)'}`);
        }
    });
    return lines;
}

// ---------------------------------------------------------------- --selftest
/**
 * 自检 + **负向自证**：自检的判据不是"能跑"，而是"**该红的必须红**"。
 * 四个桩命令都由本脚本自己造（不动仓库、不出网），其中两个是**故意应当失败**的情形：
 *   ① 崩一次就好 → 必须 `RETRIED-PASS` 且**退出码 3**（⛔ 不许静默当 PASS）；
 *   ② 一直崩      → 必须 `FAIL`、退出码 1，且**不许**出现 PASS 字样；
 *   ③ 退出码 0 但**新落了 hs_err**（JVM 崩了却被外层吞掉退出码）→ 必须判为失败（不静默降级）；
 *   ④ 一直干净    → 必须 `PASS`、退出码 0。
 */
async function selftest() {
    // ⚠ 每次自检用**全新**的子目录：桩里的计数器（`_a.count`）与假 hs_err 都是文件，
    //   沿用上一次的目录会让"第一次尝试"读到旧计数 ⇒ 该例静默变成假绿（实测踩过一次）。
    const sb = join(OUT, 'selftest', `run-${Date.now()}`);
    mkdirSync(sb, { recursive: true });
    EXTRA_ERROR_DIR = sb;      // 桩把假 hs_err 写在这里 ⇒ 扫描目录必须覆盖它（见 errorDirs 的注释）
    const stub = (body) => [process.execPath, ['-e', body]];
    const cases = [
        {
            name: '① 崩一次就好（应 RETRIED-PASS / 退出码 3）',
            mk: () => stub(`const fs=require('fs');const p=${JSON.stringify(join(sb, '_a.count'))};`
                + `let n=0;try{n=+fs.readFileSync(p,'utf8')}catch{};fs.writeFileSync(p,String(n+1));`
                + `if(n===0){fs.writeFileSync(${JSON.stringify(join(sb, 'hs_err_pid11111.log'))},`
                + `'#\\n# A fatal error has been detected by the Java Runtime Environment:\\n#\\n`
                + `#  EXCEPTION_ACCESS_VIOLATION (0xc0000005) at pc=0xdeadbeef, pid=1, tid=2\\n#\\n`
                + `# Problematic frame:\\n# J 271 c2 mahjong.rules.Shanten.dfs([IIIII[I)V (472 bytes)\\n#\\n`
                + `---------------  S U M M A R Y ------------\\n\\nCommand Line: fake --selftest\\n`
                + `Time: x elapsed time: 9.5 seconds\\n');console.log('第 3 局');process.exit(1);}`
                + `console.log('ok 第 3 局');`),
            want: { state: 'RETRIED-PASS', exit: 3, originalFailures: 1, retries: 1 },
        },
        {
            name: '② 一直崩（应 FAIL / 退出码 1 / 不出现 PASS）',
            mk: () => stub('console.error("boom 第 7 局");process.exit(1);'),
            want: { state: 'FAIL', exit: 1 },
            forbid: 'PASS',
        },
        {
            name: '③ 退出码 0 但新落 hs_err（应 FAIL —— 不静默降级）',
            // ⚠ 每次尝试写**不同文件名**：同名覆盖在 NTFS 上可能撞到 mtime 精度，那样第二次会被
            //   当成"没崩溃" ⇒ 这条负向自证自己会变成假绿。文件名带序号就没这个风险。
            mk: () => stub(`const fs=require('fs');const p=${JSON.stringify(join(sb, '_c.count'))};`
                + `let n=0;try{n=+fs.readFileSync(p,'utf8')}catch{};fs.writeFileSync(p,String(n+1));`
                + `fs.writeFileSync(${JSON.stringify(sb)}+'/hs_err_pid3333'+n+'.log',`
                + `'# Problematic frame:\\n# J 9 c2 mahjong.rules.Shanten.dfs([IIIII[I)V\\n');`
                + `console.log('看起来成功了 第 1 局');`),
            want: { state: 'FAIL', exit: 1 },
        },
        {
            name: '④ 一直干净（应 PASS / 退出码 0）',
            mk: () => stub('console.log("全绿 第 2 局");'),
            want: { state: 'PASS', exit: 0, originalFailures: 0 },
        },
    ];
    let bad = 0;
    // ⑤ 开关作用域（这一条针对的是**真实踩过的** bug：被包装命令带 `--selftest` 时，
    //    guard 必须仍然去跑那条命令，而不是转头跑自己的自检）
    {
        const sp = splitOwnArgs(['--out', 'x', '--', 'java', '-jar', 's.jar', '--selftest']);
        const okScope = sp.own.includes('--selftest') === false
            && sp.cmd === 'java' && sp.rest.join(' ') === '-jar s.jar --selftest';
        if (!okScope) bad++;
        console.log(`${okScope ? '  [ok]  ' : '  [FAIL]'} ⑤ 开关作用域：` + '--selftest 只认 `--` 之前那一份');
    }
    for (const [i, c] of cases.entries()) {
        const [cmd, args] = c.mk();
        const base = join(sb, `case${i}`);
        const attempts = [];
        for (let k = 0; k <= 1; k++) {
            const r = await runOnce(cmd, args, `${base}.try${k}`);
            r.clean = isClean(r);
            attempts.push(r);
        }
        const d = decide(attempts, 'selftest');
        // 自检里把"原始失败"那一遍也算进来：只有第 ① 例的第一遍会失败，且它必须靠重试才好
        const passState = d.state === c.want.state;
        const passExit = d.exit === c.want.exit;
        const passOrig = c.want.originalFailures === undefined || d.originalFailures === c.want.originalFailures;
        const passRetries = c.want.retries === undefined || d.retries === c.want.retries;
        const passForbid = !c.forbid || !`${d.state}`.includes(c.forbid);
        const ok = passState && passExit && passOrig && passRetries && passForbid;
        if (!ok) bad++;
        console.log(`${ok ? '  [ok]  ' : '  [FAIL]'} ${c.name}`
            + ` ⇒ state=${d.state} exit=${d.exit} 原始失败=${d.originalFailures} 重试=${d.retries}`);
        if (!ok) {
            console.log(`         期望 state=${c.want.state} exit=${c.want.exit}`
                + ` 原始失败=${c.want.originalFailures ?? '-'} 重试=${c.want.retries ?? '-'}`
                + `${c.forbid ? ` 且不含「${c.forbid}」` : ''}`);
        }
        for (const l of signatureLines(attempts)) console.log(l);
    }
    console.log(bad === 0 ? '[jvm-guard] SELFTEST PASS（5/5，含 3 条负向自证 + 1 条开关作用域）'
        : `[jvm-guard] SELFTEST FAIL（${bad} 例）`);
    process.exit(bad === 0 ? 0 : 1);
}

// ---------------------------------------------------------------- main
/** `--show-signature <hs_err>`：只解析一份既有日志（用于"事后补签名"与事后复核，不重跑）。 */
const SIGNATURE_OF = opt('--show-signature', '');
if (SIGNATURE_OF) {
    const h = parseHsErr(resolve(ROOT, SIGNATURE_OF));
    console.log(JSON.stringify(h, null, 2));
    process.exit(0);
}
if (SELFTEST) {
    await selftest();
} else if (!CMD) {
    console.error('用法：node tools/jvm-guard.mjs [选项] -- <命令> [参数…]\n'
        + '      node tools/jvm-guard.mjs --selftest\n'
        + '选项：--out <dir> --cwd <dir> --retries <N> --hand-regex <re> --error-file-dir <dir>\n'
        + '      --mark <正则>（子进程输出里必须出现它，否则这一遍不算干净）\n'
        + '      --port <端口>（跑完杀该端口的监听并复查）--json <file> --allow-retry-pass');
    process.exit(2);
}

mkdirSync(OUT, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const base = join(OUT, `run-${stamp}`);
console.log(`[jvm-guard] 命令：${CMD} ${CMD_ARGS.join(' ')}`);
console.log(`[jvm-guard] cwd=${CWD} 日志前缀=${base} 最多重试=${RETRIES}`);

const attempts = [];
for (let k = 0; k <= RETRIES; k++) {
    const r = await runOnce(CMD, CMD_ARGS, `${base}.try${k}`);
    r.clean = isClean(r);
    attempts.push(r);
    console.log(attemptReport(r, k));
    if (r.clean) break;
    if (k < RETRIES) console.log(`  ⇒ 判为一次**原始失败**，按 NOTES §4 的口径重试（这不是"通过"）`);
}

const d = decide(attempts);
console.log('');
console.log('=== 汇总（⛔ 重试不算通过）===');
console.log(`  状态          ${d.state}`);
console.log(`  尝试次数      ${d.attempts}（原始失败 **${d.originalFailures}** 次 · 重试 ${d.retries} 次，其中又失败 ${d.retryFailures} 次）`);
console.log(`  退出码        ${d.exit}`);
if (d.originalFailures > 0) {
    console.log('  ⛔ 这次跑**发生过原始失败**：上面那 N 次不是"抖动"，签名如下（要按 NOTES §4 的判据处置）');
    for (const l of signatureLines(attempts)) console.log(l);
}
if (d.state === 'RETRIED-PASS' && !ALLOW_RETRY_PASS) {
    console.log('  ⛔ 退出码 3 = 「带重试的通过」，**不是**干净通过。要当通过用请显式加 --allow-retry-pass。');
}

if (PORT) {
    const { pids, left } = killPort(PORT);
    console.log(`  端口收尾      :${PORT} 杀 ${pids.length ? pids.join(',') : '(无监听)'}`
        + ` ⇒ 复查残留监听 ${left.length === 0 ? '0（干净）' : left.join(',')}`);
}

if (JSON_OUT) {
    const payload = {
        cmd: [CMD, ...CMD_ARGS], cwd: CWD, port: PORT || null, mark: MARK || null,
        ...d,
        attempts: attempts.map((a) => ({ clean: a.clean, code: a.code, signal: a.signal, sec: a.sec,
            hand: handOf(a.out + a.err), hsErr: a.hsErr })),
    };
    writeFileSync(resolve(ROOT, JSON_OUT), JSON.stringify(payload, null, 2), 'utf8');
    console.log(`  汇总 JSON     ${resolve(ROOT, JSON_OUT)}`);
}
process.exit(d.exit);
