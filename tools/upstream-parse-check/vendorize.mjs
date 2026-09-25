// 把 convlog 的依赖（按上游 Cargo.lock 的精确版本 + 校验和）下载并解包成 vendor 目录。
//
// 为什么要这么绕：本机 cargo/curl 走 Windows schannel 取凭据失败（SEC_E_NO_CREDENTIALS，
// 沙箱下访问不了系统凭据库），而 node 自带 OpenSSL 能正常下载。所以变成
// 「node 下载 + 生成 .cargo-checksum.json + cargo 用 source replacement 离线编译」。
//
//   node release/rust/vendorize.mjs <Cargo.lock> <vendorDir> <根包名...>
import { mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { gunzipSync } from 'node:zlib';
import { join } from 'node:path';

const [lockPath, vendorDir, ...roots] = process.argv.slice(2);
if (!lockPath || !vendorDir || roots.length === 0) {
  console.error('用法：node vendorize.mjs <Cargo.lock> <vendorDir> <根包名...>');
  process.exit(2);
}

// ── 解析 Cargo.lock（只认 package 段）─────────────────────────────────────
const lock = readFileSync(lockPath, 'utf8');
const pkgs = new Map();          // "name@version" -> { name, version, checksum, deps: [name...] }
let cur = null;
for (const line of lock.split('\n')) {
  const t = line.trim();
  if (t === '[[package]]') {
    if (cur && cur.name && cur.version) pkgs.set(`${cur.name}@${cur.version}`, cur);   // 上一段没有 dependencies 时要在这里落袋
    cur = { deps: [] };
    continue;
  }
  if (!cur) continue;
  let m;
  if ((m = /^name = "(.*)"/.exec(t))) cur.name = m[1];
  else if ((m = /^version = "(.*)"/.exec(t))) cur.version = m[1];
  else if ((m = /^checksum = "(.*)"/.exec(t))) cur.checksum = m[1];
  else if (t.startsWith('dependencies = [')) { cur.inDeps = true; continue; }
  else if (cur.inDeps) {
    if (t === ']') { cur.inDeps = false; pkgs.set(`${cur.name}@${cur.version}`, cur); cur = null; continue; }
    if ((m = /^"(.*)",?$/.exec(t))) cur.deps.push(m[1]);
  } else if (t.startsWith('[') && t !== '[[package]]') {
    if (cur.name && cur.version) pkgs.set(`${cur.name}@${cur.version}`, cur);
    cur = null;
  }
}
if (cur && cur.name) pkgs.set(`${cur.name}@${cur.version}`, cur);

// 同一名字可能有多个版本 → 解析依赖时按名字找；多于一个就报错让人看见
const byName = new Map();
for (const [key, p] of pkgs) {
  if (!byName.has(p.name)) byName.set(p.name, []);
  byName.get(p.name).push({ key, ...p });
}

const wanted = new Map();
const queue = [...roots];
while (queue.length) {
  const spec = queue.pop();
  const [name, ver] = spec.split(' ');
  const cands = (byName.get(name) || []).filter((c) => !ver || c.version === ver);
  if (cands.length !== 1) {
    console.error(`解析 ${spec} 失败：候选 ${cands.length} 个（${cands.map((c) => c.version).join(',')}）`);
    process.exit(1);
  }
  const p = cands[0];
  if (wanted.has(p.key)) continue;
  wanted.set(p.key, p);
  for (const d of p.deps) queue.push(d);
}

console.log(`需要 ${wanted.size} 个包（含传递依赖）`);
mkdirSync(vendorDir, { recursive: true });

// ── 最小 tar 读取（crate 是 .tar.gz）──────────────────────────────────────
function untar(buf) {
  const files = [];
  let off = 0;
  while (off + 512 <= buf.length) {
    const header = buf.subarray(off, off + 512);
    if (header.every((b) => b === 0)) break;
    const str = (a, b) => header.subarray(a, b).toString('utf8').replace(/\0.*$/, '');
    const name = str(0, 100);
    const sizeStr = str(124, 136).trim();
    const size = parseInt(sizeStr || '0', 8);
    const type = String.fromCharCode(header[156]);
    const prefix = str(345, 500);
    const full = prefix ? `${prefix}/${name}` : name;
    const dataStart = off + 512;
    if (type === '0' || type === '\0' || type === '') files.push({ name: full, data: buf.subarray(dataStart, dataStart + size) });
    off = dataStart + Math.ceil(size / 512) * 512;
  }
  return files;
}

let done = 0;
for (const p of wanted.values()) {
  const dir = join(vendorDir, `${p.name}-${p.version}`);
  if (existsSync(join(dir, '.cargo-checksum.json'))) { done++; continue; }
  if (!p.checksum) { console.log(`跳过（无 checksum，多半是路径依赖）：${p.key}`); continue; }
  const url = `https://static.crates.io/crates/${p.name}/${p.name}-${p.version}.crate`;
  const res = await fetch(url);
  if (!res.ok) { console.error(`下载失败 ${res.status}: ${url}`); process.exit(1); }
  const bytes = Buffer.from(await res.arrayBuffer());
  const sha = createHash('sha256').update(bytes).digest('hex');
  if (sha !== p.checksum) { console.error(`校验和不符 ${p.key}: ${sha} != ${p.checksum}`); process.exit(1); }

  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const files = {};
  for (const f of untar(gunzipSync(bytes))) {
    const rel = f.name.split('/').slice(1).join('/');   // 去掉顶层 name-version/
    if (!rel) continue;
    // ⚠ Windows 建不出「以点或空格结尾」的文件名（如 rustls-webpki 的某个测试证书
    //   `…subtree.ee.`）。这类文件只被那些 crate 自己的测试引用，编译依赖时用不到 ——
    //   跳过它，也**不写进 checksum 清单**（写进去 cargo 就会去找它）。
    if (rel.split('/').some((seg) => /[. ]$/.test(seg))) {
      console.log(`  跳过 Windows 非法文件名：${p.key} / ${rel}`);
      continue;
    }
    const target = join(dir, rel);
    mkdirSync(join(target, '..'), { recursive: true });
    writeFileSync(target, f.data);
    files[rel] = createHash('sha256').update(f.data).digest('hex');
  }
  writeFileSync(join(dir, '.cargo-checksum.json'), JSON.stringify({ files, package: p.checksum }));
  done++;
  if (done % 10 === 0) console.log(`  已 vendor ${done}/${wanted.size}`);
}
console.log(`vendor 完成：${done} 个包 → ${vendorDir}`);
