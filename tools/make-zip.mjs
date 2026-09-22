#!/usr/bin/env node
/**
 * 手写 zip 打包器 —— **唯一目的是保住 Unix 权限位**。
 *
 *   node tools/make-zip.mjs <out.zip> <zipRootName> <srcDir> [--exec-ext .sh,.bash]
 *
 * 为什么不用现成的压缩 API：
 *   · PowerShell 的 `Compress-Archive`（.NET `ZipArchive`）写的条目 **versionMadeBy = 0**
 *     （FAT/DOS），于是 Linux 侧**忽略** ExternalAttributes 的高 16 位 —— 解压出来 `.sh`
 *     全是 0644，`./start.sh` 直接 Permission denied（实测：bsdtar 显示 `-rw-rw-r--`）。
 *   · 要让 Unix 权限生效，必须把条目的 versionMadeBy 标成 Unix(3) 并写 externalAttrs 高位。
 *     .NET 不暴露 versionMadeBy，所以这里按 ZIP 规范手写中央目录（无压缩库依赖，
 *     压缩用 Node 内置 zlib 的 deflateRaw）。
 *
 * 产物：`<zipRootName>/...` 一层版本目录，`*.sh` 为 0755（含 exec 位），其余 0644。
 * 兼容性：只用 store/deflate（method 8）与 UTF-8 名称（flag bit 11），单文件 < 4GB。
 */
import { createWriteStream, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { deflateRawSync } from 'node:zlib';

const [outZip, zipRoot, srcDir, ...rest] = process.argv.slice(2);
if (!outZip || !zipRoot || !srcDir) {
  console.error('用法：node tools/make-zip.mjs <out.zip> <zipRootName> <srcDir> [--exec-ext .sh]');
  process.exit(2);
}
let execExts = ['.sh', '.bash'];
const exIdx = rest.indexOf('--exec-ext');
if (exIdx >= 0 && rest[exIdx + 1]) execExts = rest[exIdx + 1].split(',');

// ---- CRC32 ----
const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function dosDateTime(d) {
  const time = ((d.getHours() & 31) << 11) | ((d.getMinutes() & 63) << 5) | ((d.getSeconds() / 2) & 31);
  const date = (((d.getFullYear() - 1980) & 127) << 9) | (((d.getMonth() + 1) & 15) << 5) | (d.getDate() & 31);
  return { time, date };
}

function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const files = walk(srcDir).sort();
const chunks = [];
const central = [];
let offset = 0;

for (const f of files) {
  const rel = relative(srcDir, f).split('\\').join('/');
  const name = Buffer.from(`${zipRoot}/${rel}`, 'utf8');
  const data = readFileSync(f);
  const isExec = execExts.includes(extname(f).toLowerCase());
  const mode = isExec ? 0o100755 : 0o100644;
  const deflated = deflateRawSync(data, { level: 9 });
  const useDeflate = deflated.length < data.length;
  const body = useDeflate ? deflated : data;
  const method = useDeflate ? 8 : 0;
  const crc = crc32(data);
  const { time, date } = dosDateTime(statSync(f).mtime);

  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4);            // version needed 2.0
  local.writeUInt16LE(0x0800, 6);        // flag: UTF-8 名称
  local.writeUInt16LE(method, 8);
  local.writeUInt16LE(time, 10);
  local.writeUInt16LE(date, 12);
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(body.length, 18);
  local.writeUInt32LE(data.length, 22);
  local.writeUInt16LE(name.length, 26);
  local.writeUInt16LE(0, 28);
  chunks.push(local, name, body);

  const cen = Buffer.alloc(46);
  cen.writeUInt32LE(0x02014b50, 0);
  cen.writeUInt16LE((3 << 8) | 20, 4);   // versionMadeBy = Unix(3) + 2.0 ← 关键
  cen.writeUInt16LE(20, 6);
  cen.writeUInt16LE(0x0800, 8);
  cen.writeUInt16LE(method, 10);
  cen.writeUInt16LE(time, 12);
  cen.writeUInt16LE(date, 14);
  cen.writeUInt32LE(crc, 16);
  cen.writeUInt32LE(body.length, 20);
  cen.writeUInt32LE(data.length, 24);
  cen.writeUInt16LE(name.length, 28);
  cen.writeUInt16LE(0, 30);              // extra
  cen.writeUInt16LE(0, 32);              // comment
  cen.writeUInt16LE(0, 34);              // disk
  cen.writeUInt16LE(0, 36);              // internal attrs
  cen.writeUInt32LE((mode << 16) >>> 0, 38);   // external attrs：Unux mode 在高位
  cen.writeUInt32LE(offset, 42);
  central.push(cen, name);

  offset += local.length + name.length + body.length;
}

const cd = Buffer.concat(central);
const eocd = Buffer.alloc(22);
eocd.writeUInt32LE(0x06054b50, 0);
eocd.writeUInt16LE(0, 4);
eocd.writeUInt16LE(0, 6);
eocd.writeUInt16LE(files.length, 8);
eocd.writeUInt16LE(files.length, 10);
eocd.writeUInt32LE(cd.length, 12);
eocd.writeUInt32LE(offset, 16);
eocd.writeUInt16LE(0, 20);

const out = createWriteStream(outZip);
out.write(Buffer.concat(chunks));
out.write(cd);
out.write(eocd);
await new Promise((res, rej) => out.end((e) => (e ? rej(e) : res())));

console.log(`==> ${outZip}：${files.length} 个文件，可执行 ${files.filter((f) => execExts.includes(extname(f).toLowerCase())).length} 个（${execExts.join('/')}）`);
