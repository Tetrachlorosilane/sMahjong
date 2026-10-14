#!/usr/bin/env node
// update.sh 选择规则的回归测试 —— **不联网**，全部跑在录下来的真实 JSON 夹具上。
//
//   node tools\update-pick-test.mjs
//
// 为什么要有它：服务端包的自更新脚本 `server/pack/update.sh` 原来用
// `releases/latest` + "按 tag 拼资产名" 选包，而本仓库近几个发布（v1.15.0 风格包、
// v1.16.0 机器人包）**都不含服务端包** ⇒ 它去下载一个不存在的
// `sMahjong-server-v1.16.0.zip`（HTTP 404）。脚本本身没法用单测覆盖（要 POSIX shell +
// 真网络），所以这里用**录下来的真实 API 响应**当夹具，驱动**真正的 update.sh**
// （`--list --json-file`，干跑、不下载），对"选中了哪个资产"做机械判据。
//
// ⚠ 需要 POSIX shell（优先 Git Bash，其次 WSL Ubuntu）。找不到 ⇒ 退出码 **2** 并说明原因
//   （⛔ 不静默跳过 —— 静默跳过就是"假绿"）。
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const FIXDIR = path.join(HERE, 'release-fixtures');
const UPDATE_SH = path.join(ROOT, 'server', 'pack', 'update.sh');

// ---------------------------------------------------------------- 找 shell
function detectShell() {
  const win = process.platform === 'win32';
  const gitBash = ['C:\\Program Files\\Git\\bin\\bash.exe', 'C:\\Program Files (x86)\\Git\\bin\\bash.exe'].find((p) => existsSync(p));
  if (gitBash && tryShell([gitBash])) return { name: `Git Bash (${gitBash})`, kind: 'gitbash', exe: gitBash };
  if (win && spawnSync('wsl.exe', ['-d', 'Ubuntu', '--', 'bash', '-c', 'exit 0'], { encoding: 'utf8' }).status === 0) {
    return { name: 'WSL Ubuntu', kind: 'wsl', exe: 'wsl.exe' };
  }
  // System32\bash.exe 是 WSL 存根（会去跑默认登录 shell），所以最后才试
  if (tryShell(['bash'])) return { name: 'PATH 上的 bash', kind: 'native', exe: 'bash' };
  return null;
}
function tryShell(exe) {
  try {
    return spawnSync(exe[0], [...exe.slice(1), '-c', 'awk "BEGIN{exit 0}" && echo shell_ok'], { encoding: 'utf8' }).stdout?.includes('shell_ok');
  } catch { return false; }
}

function toWsl(p) {
  const m = /^([A-Za-z]):[\\/](.*)$/.exec(p);
  return m ? `/mnt/${m[1].toLowerCase()}/${m[2].replace(/\\/g, '/')}` : p.replace(/\\/g, '/');
}

function runIn(shell, cwd, argv) {
  if (shell.kind === 'wsl') {
    const r = spawnSync(shell.exe, ['-d', 'Ubuntu', '--cd', toWsl(cwd), '--', 'bash', ...argv], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    return { status: r.status, stdout: r.stdout || '', stderr: r.stderr || '' };
  }
  const r = spawnSync(shell.exe, argv, { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return { status: r.status, stdout: r.stdout || '', stderr: r.stderr || '' };
}

// ---------------------------------------------------------------- 夹具里的真摘要
const REAL_FIXTURE = 'releases-real-20261015.json';
function digestOf(fixture, assetName) {
  const rs = JSON.parse(readFileSync(path.join(FIXDIR, fixture), 'utf8'));
  for (const r of rs) for (const a of r.assets) if (a.name === assetName) return (a.digest || '').replace(/^sha256:/, '');
  throw new Error(`夹具 ${fixture} 里找不到资产 ${assetName}`);
}

// ---------------------------------------------------------------- 用例
const CASES = [
  {
    id: 'a-real',
    desc: '(a) 真实 release 列表 ⇒ 选中**最新含服务端包的非 prerelease 发布** v1.14.0',
    fixture: REAL_FIXTURE, args: ['--list'],
    expect: { rc: 0, tag: 'v1.14.0', asset: 'sMahjong-server-v1.14.0.zip' },
  },
  {
    id: 'b-prerelease-newest',
    desc: '(b) 最新是 prerelease（且**带**服务端 zip）⇒ 不选它，退回上一个正式发布',
    fixture: 'releases-prerelease-newest.json', args: ['--list'],
    expect: { rc: 0, tag: 'v8.0.0', asset: 'sMahjong-server-v8.0.0.zip' },
    forbid: ['本次会安装 v9.0.0-weights'],
  },
  {
    id: 'b2-prerelease-optin',
    desc: '(b2) 只有 --include-prerelease 才允许选中 prerelease',
    fixture: 'releases-prerelease-newest.json', args: ['--list', '--include-prerelease'],
    expect: { rc: 0, tag: 'v9.0.0-weights', asset: 'sMahjong-server-v9.0.0-weights.zip' },
  },
  {
    id: 'c-bot-only-newest',
    desc: '(c) 最新非 prerelease **只有 bot/风格包**⇒ 不选它，继续往前找到 v7.0.0',
    fixture: 'releases-bot-only-newest.json', args: ['--list'],
    expect: { rc: 0, tag: 'v7.0.0', asset: 'sMahjong-server-v7.0.0.zip' },
    forbid: ['本次会安装 v7.2.0', '本次会安装 v7.1.0'],
  },
  {
    id: 'd-semver-order',
    desc: '(d) tag 排序反例：v1.9.0 排在列表第 1（字符串序也说它大）⇒ 必须选 v1.16.0',
    fixture: 'releases-semver-order.json', args: ['--list'],
    expect: { rc: 0, tag: 'v1.16.0', asset: 'sMahjong-server-v1.16.0.zip' },
    forbid: ['本次会安装 v1.9.0'],
  },
  {
    id: 'e-body-decoy',
    desc: '(e) release body 里写着伪造的资产名与伪造摘要 ⇒ 只认 assets[]，不读 body',
    fixture: 'releases-body-decoy.json', args: ['--list'],
    expect: { rc: 0, tag: 'v5.0.0', asset: 'sMahjong-server-v5.0.0.zip' },
    forbid: ['本次会安装 v9.9.9'],
  },
  {
    id: 'f-draft-newest',
    desc: '(f) 最新是 **draft**（也带服务端 zip）⇒ 跳过，选 v6.0.0',
    fixture: 'releases-draft-newest.json', args: ['--list'],
    expect: { rc: 0, tag: 'v6.0.0', asset: 'sMahjong-server-v6.0.0.zip' },
  },
  {
    id: 'g-none',
    desc: '(g) 一个服务端包都没有 ⇒ **非零退出**（⛔ 不是"取不到就跳过"）',
    fixture: 'releases-no-server-asset.json', args: ['--list'],
    expect: { rc: 1 }, stderrHas: '挑不出可安装的服务端包',
  },
  {
    id: 'h-tag-missing-asset',
    desc: '(h) --tag 指到只有 bot 包的发布 ⇒ 非零退出并点名该 tag',
    fixture: REAL_FIXTURE, args: ['--list', '--tag', 'v1.16.0'],
    expect: { rc: 1 }, stderrHas: 'v1.16.0',
  },
  {
    id: 'i-tag-pin',
    desc: '(i) --tag 固定到确实有服务端包的发布 v1.13.0',
    fixture: REAL_FIXTURE, args: ['--list', '--tag', 'v1.13.0'],
    expect: { rc: 0, tag: 'v1.13.0', asset: 'sMahjong-server-v1.13.0.zip' },
  },
  {
    id: 'j-compact-json',
    desc: '(j) 同一份真实 JSON 压成**一行**（无缩进）⇒ 结果不变（证明是结构解析，不是按行/缩进猜）',
    compact: REAL_FIXTURE, args: ['--list'],
    expect: { rc: 0, tag: 'v1.14.0', asset: 'sMahjong-server-v1.14.0.zip' },
  },
  {
    id: 'k-already-latest',
    desc: '(k) 本地 VERSION 写 1.14.0（**不带 v**）+ 摘要一致 ⇒ --check 必须说"已是最新"',
    fixture: REAL_FIXTURE, args: ['--check'],
    localVersion: '1.14.0', localShaOf: 'sMahjong-server-v1.14.0.zip',
    expect: { rc: 0 }, stdoutHas: '已是最新',
  },
  {
    id: 'l-digest-missing-sidecar',
    desc: '(l) 该发布没有 digest，但有 <资产>.sha256 边车 ⇒ 明说用边车校验',
    fixture: 'releases-no-digest.json', args: ['--list'],
    expect: { rc: 0, tag: 'v3.1.0' }, stdoutHas: '边车资产',
  },
  {
    id: 'm-no-checksum',
    desc: '(m) 既没 digest 也没有边车 ⇒ 明说**无校验可用**（⛔ 不假装校过）',
    fixture: 'releases-no-digest.json', args: ['--list', '--tag', 'v3.0.0'],
    expect: { rc: 0, tag: 'v3.0.0' }, stdoutHas: '无校验可用',
  },
  {
    id: 'n-offline-install',
    // 回归"自覆盖半装"：包里那份 update.sh 会盖到**正在运行的自己**身上。
    // 用 `cp`（就地截断 inode）时 bash 会读到错位命令 ⇒ 装到一半**静默退出、退出码 0**，
    // 既没有「已安装」也没有 VERSION / installed.sha256。改成 `cp` 到临时名再 `mv` 才跑得完。
    // ⚠ 包夹具**当场用 tools/make-zip.mjs 打**（`*.zip` 被 .gitignore 挡着，⛔ 不进仓库）；
    //   里面的 update.sh 故意比正在跑的短，正是真实场景（包里那份是旧版）。
    desc: '(n) 离线跑打包好的**整包**安装 ⇒ 必须打印「已安装」并写全 VERSION/摘要（回归"自覆盖半装静默退出 0"）',
    buildPackage: true, args: ['--no-restart'],
    expect: { rc: 0 },
    stdoutHas: ['已安装 v1.14.0', 'sha256 校验通过', '完整性校验'],
    expectFiles: ['VERSION', 'mahjong-server.jar', 'run/installed.sha256', 'run/installed.tag'],
    expectFileText: { VERSION: 'v1.14.0', 'run/installed.tag': 'v1.14.0' },
  },
];

// 当场打一个"服务端发布包"夹具：一层版本目录 + jar + VERSION(不带 v) + 几个 .sh。
// 返回给 update.sh 用的 releases JSON 文件名（大小/摘要按**真打出来的 zip** 算，所以摘要校验是真跑）。
function buildPackageFixture(cwd) {
  const TAG = 'v1.14.0';
  const ROOTNAME = `sMahjong-server-${TAG}`;
  const stage = path.join(cwd, 'stage', ROOTNAME);
  mkdirSync(stage, { recursive: true });
  writeFileSync(path.join(stage, 'mahjong-server.jar'), 'PLACEHOLDER-JAR-NOT-A-REAL-JAR\n'.repeat(40), 'utf8');
  writeFileSync(path.join(stage, 'VERSION'), '1.14.0', 'utf8'); // ⚠ 真实包就是不带 v
  // 包里那份"旧版" update.sh：**故意比正在跑的那份短**，这样裸 cp 覆盖必然让 bash 读到 EOF/错位
  writeFileSync(
    path.join(stage, 'update.sh'),
    '#!/usr/bin/env bash\n# 包里那份旧版 update.sh\necho "old update.sh"\n',
    'utf8',
  );
  for (const s of ['start.sh', 'stop.sh', 'restart.sh', 'status.sh']) {
    writeFileSync(path.join(stage, s), `#!/usr/bin/env bash\necho ${s}\n`, 'utf8');
  }
  const zip = path.join(cwd, 'pkg.zip');
  const z = spawnSync(process.execPath, [path.join(HERE, 'make-zip.mjs'), zip, ROOTNAME, stage], { encoding: 'utf8' });
  if (z.status !== 0) throw new Error(`make-zip.mjs 失败（${z.status}）：${z.stdout}${z.stderr}`);
  const buf = readFileSync(zip);
  const digest = 'sha256:' + createHash('sha256').update(buf).digest('hex');
  const jsonName = 'local-package.json';
  writeFileSync(
    path.join(cwd, jsonName),
    JSON.stringify(
      [
        {
          url: 'https://api.github.com/repos/o/r/releases/1',
          id: 1,
          tag_name: TAG,
          draft: false,
          prerelease: false,
          published_at: '2026-09-29T06:10:19Z',
          assets: [
            {
              url: 'https://api.github.com/repos/o/r/releases/assets/1',
              id: 1,
              name: `${ROOTNAME}.zip`,
              size: buf.length,
              digest,
              content_type: 'application/zip',
              state: 'uploaded',
            },
          ],
          body: '当场打的包夹具',
        },
      ],
      null,
      2,
    ) + '\n',
    'utf8',
  );
  return jsonName;
}

// ---------------------------------------------------------------- 跑
const shell = detectShell();
if (!shell) {
  console.error('[SKIP] 找不到可用的 POSIX shell（Git Bash / WSL Ubuntu / PATH 上的 bash）。');
  console.error('       update-pick-test 需要真跑 update.sh，⛔ 不静默跳过（那就是假绿）。');
  process.exit(2);
}
if (!existsSync(UPDATE_SH)) {
  console.error(`[FAIL] 找不到 ${UPDATE_SH}`);
  process.exit(1);
}

console.log(`update.sh 选择规则回归（shell = ${shell.name}）`);
console.log(`脚本 = ${path.relative(ROOT, UPDATE_SH)}  夹具目录 = tools/release-fixtures\n`);

const tmp = mkdtempSync(path.join(tmpdir(), 'smj-upd-'));
let pass = 0;
const failures = [];

try {
  for (const c of CASES) {
    const cwd = mkdtempSync(path.join(tmp, 'case-'));
    copyFileSync(UPDATE_SH, path.join(cwd, 'update.sh'));
    let jsonName;
    let useZip = false;
    if (c.buildPackage) {
      jsonName = buildPackageFixture(cwd);
      useZip = true;
    } else if (c.compact) {
      jsonName = 'compact.json';
      const parsed = JSON.parse(readFileSync(path.join(FIXDIR, c.compact), 'utf8'));
      writeFileSync(path.join(cwd, jsonName), JSON.stringify(parsed), 'utf8');
    } else {
      jsonName = c.fixture;
      copyFileSync(path.join(FIXDIR, c.fixture), path.join(cwd, jsonName));
    }
    if (c.localVersion !== undefined) {
      writeFileSync(path.join(cwd, 'VERSION'), c.localVersion, 'utf8');
      mkdirSync(path.join(cwd, 'run'), { recursive: true });
      writeFileSync(path.join(cwd, 'run', 'installed.sha256'), digestOf(REAL_FIXTURE, c.localShaOf) + '\n', 'utf8');
    }

    const r = runIn(shell, cwd, ['update.sh', ...c.args, '--json-file', jsonName, ...(useZip ? ['--zip-file', 'pkg.zip'] : [])]);
    const out = r.stdout + r.stderr;
    const problems = [];
    if (r.status !== c.expect.rc) problems.push(`退出码 ${r.status} ≠ 期望 ${c.expect.rc}`);
    if (c.expect.tag) {
      // ⚠ 资产名后面紧跟中文全角括号，不能用 \S+ 贪婪吃到括号里
      const m = /本次会安装\s+(\S+)\s*\/\s*([^\s（]+\.zip)/.exec(r.stdout);
      if (!m) problems.push('输出里找不到「本次会安装 <tag> / <资产>」');
      else {
        if (m[1] !== c.expect.tag) problems.push(`选中 tag = ${m[1]} ≠ 期望 ${c.expect.tag}`);
        if (c.expect.asset && m[2] !== c.expect.asset) problems.push(`选中资产 = ${m[2]} ≠ 期望 ${c.expect.asset}`);
      }
    }
    for (const s of [].concat(c.stdoutHas || [])) if (!r.stdout.includes(s)) problems.push(`stdout 里没有 ${JSON.stringify(s)}`);
    if (c.stderrHas && !r.stderr.includes(c.stderrHas)) problems.push(`stderr 里没有 ${JSON.stringify(c.stderrHas)}`);
    for (const f of c.forbid || []) if (out.includes(f)) problems.push(`输出里出现了不该有的 ${JSON.stringify(f)}`);
    for (const f of c.expectFiles || []) if (!existsSync(path.join(cwd, f))) problems.push(`安装后缺少文件 ${f}`);
    for (const [f, want] of Object.entries(c.expectFileText || {})) {
      const got = existsSync(path.join(cwd, f)) ? readFileSync(path.join(cwd, f), 'utf8').trim() : '(文件不存在)';
      if (got !== want) problems.push(`${f} 内容 = ${JSON.stringify(got)} ≠ 期望 ${JSON.stringify(want)}`);
    }

    if (problems.length === 0) {
      pass++;
      console.log(`  PASS  ${c.id.padEnd(24)} ${c.desc}`);
    } else {
      console.log(`  FAIL  ${c.id.padEnd(24)} ${c.desc}`);
      for (const p of problems) console.log(`        - ${p}`);
      console.log(`        ---- update.sh 原始输出 ----`);
      for (const line of out.trimEnd().split('\n')) console.log(`        | ${line}`);
      failures.push(c.id);
    }
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n通过 ${pass} / ${CASES.length} 项`);
if (failures.length) {
  console.error(`失败：${failures.join(', ')}`);
  process.exit(1);
}
console.log('UPDATE-PICK PASS');
