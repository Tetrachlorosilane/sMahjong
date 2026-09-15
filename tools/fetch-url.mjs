#!/usr/bin/env node
// 通用「下载 URL 到文件」小工具 —— 构建脚本的**兜底传输层**。
//
// 为什么需要它：PowerShell 的 Invoke-WebRequest / curl.exe 走的是 Windows 的 schannel，
// 在受限沙箱、企业 TLS 中间人、或凭据库不可用的环境下会直接失败
// （实测：`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`）。
// Node 自带 OpenSSL 与 CA 包，走的是自己的 TLS 栈，这种环境下反而能通。
// 所以 tools/qt-provision.ps1 的下载顺序是 Invoke-WebRequest → curl.exe → 本脚本。
//
// 用法：
//   node tools/fetch-url.mjs <url> <outFile> [--sha1 <hex>] [--sha256 <hex>]
//                            [--timeout <秒>] [--quiet] [--text]
// 成功：stdout 打印一行 `OK <sha256> <字节数> <耗时秒>`；退出码 0
// 失败：stderr 打印原因；退出码非 0
//
// 实现要点：
//   * 边读边写并**增量**算哈希 —— 不把整个文件读进内存（Qt 源码包有 48 MB+）
//   * 先写 `<out>.part`，校验通过后再改名 —— 中断的下载不会留下"看着像成功"的坏文件
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

function fail(msg) {
  process.stderr.write(`ERR ${msg}\n`);
  process.exit(1);
}

const argv = process.argv.slice(2);
const opts = { sha1: '', sha256: '', timeout: 0, quiet: false, text: false };
const positional = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--sha1') opts.sha1 = (argv[++i] || '').toLowerCase();
  else if (a === '--sha256') opts.sha256 = (argv[++i] || '').toLowerCase();
  else if (a === '--timeout') opts.timeout = Number(argv[++i] || 0);
  else if (a === '--quiet') opts.quiet = true;
  else if (a === '--text') opts.text = true;
  else if (a.startsWith('--')) fail(`未知参数 ${a}`);
  else positional.push(a);
}
const [url, outFile] = positional;
if (!url || !outFile) fail('用法：node tools/fetch-url.mjs <url> <outFile> [--sha1 <hex>] [--sha256 <hex>]');

const t0 = Date.now();
let res;
try {
  res = await fetch(url, {
    redirect: 'follow',
    signal: opts.timeout ? AbortSignal.timeout(opts.timeout * 1000) : undefined,
    headers: { 'user-agent': 'mahjong-build/1.0 (+qt-provision)' },
  });
} catch (e) {
  fail(`请求失败 ${url}：${e.message}`);
}
if (!res.ok) fail(`HTTP ${res.status} ${res.statusText} —— ${url}`);

fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true });
const part = `${outFile}.part`;
const sha1 = crypto.createHash('sha1');
const sha256 = crypto.createHash('sha256');
let bytes = 0;
let lastTick = 0;

const out = fs.createWriteStream(part);
try {
  for await (const chunk of res.body) {
    out.write(chunk);
    sha1.update(chunk);
    sha256.update(chunk);
    bytes += chunk.length;
    if (!opts.quiet && !opts.text) {
      const now = Date.now();
      if (now - lastTick > 700) {
        lastTick = now;
        const total = Number(res.headers.get('content-length') || 0);
        const pct = total ? ` (${((bytes / total) * 100).toFixed(0)}%)` : '';
        process.stderr.write(`\r[fetch] ${(bytes / 1048576).toFixed(1)} MB${pct}   `);
      }
    }
  }
} catch (e) {
  out.destroy();
  fs.rmSync(part, { force: true });
  fail(`传输中断：${e.message}`);
}
await new Promise((resolve, reject) => out.end((err) => (err ? reject(err) : resolve())));
if (!opts.quiet && !opts.text) process.stderr.write('\r');

const hex1 = sha1.digest('hex');
const hex256 = sha256.digest('hex');
if (opts.sha1 && opts.sha1 !== hex1) {
  fs.rmSync(part, { force: true });
  fail(`SHA1 不匹配\n  期望 ${opts.sha1}\n  实际 ${hex1}\n  ${url}`);
}
if (opts.sha256 && opts.sha256 !== hex256) {
  fs.rmSync(part, { force: true });
  fail(`SHA256 不匹配\n  期望 ${opts.sha256}\n  实际 ${hex256}\n  ${url}`);
}
// 校验通过才落成正式文件名
fs.rmSync(outFile, { force: true });
fs.renameSync(part, outFile);

if (opts.text) {
  process.stdout.write(fs.readFileSync(outFile, 'utf8'));
} else {
  process.stdout.write(`OK ${hex256} ${bytes} ${((Date.now() - t0) / 1000).toFixed(1)}\n`);
}
