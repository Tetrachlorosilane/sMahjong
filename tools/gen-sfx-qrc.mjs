#!/usr/bin/env node
/**
 * 生成 `client/assets/sfx.qrc`（音效的 qrc 兜底清单）。
 *
 *   node tools/gen-sfx-qrc.mjs
 *
 * 与 `tools/gen-tile-placeholders.mjs` 同一个套路：**目录优先**（build.ps1 会把
 * `assets/sfx` 拷到 exe 同级），qrc 只是"目录被删掉时仍然有声音"的兜底。
 * `gen-sfx.mjs` 改了音效名之后要跑一次本脚本，否则编进 exe 的那份会漏文件。
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const dir = path.join(root, 'client', 'assets', 'sfx');
const out = path.join(root, 'client', 'assets', 'sfx.qrc');

const files = fs.existsSync(dir)
  ? fs.readdirSync(dir).filter((f) => f.toLowerCase().endsWith('.wav')).sort()
  : [];

// 前缀留空、条目带 `sfx/`：rcc 于是去 `assets/sfx/chi.wav` 取文件（存在），
// 而资源名正好是 `:/sfx/chi.wav` —— 与 `Sound.cpp` 里的查找路径逐字一致。
// ⚠ 这一对（前缀 / 条目）**必须**与源码里的 `:/sfx/...` 对齐，否则整条 qrc 兜底静默失效。
const lines = ['<RCC>', '    <qresource prefix="/">'];
for (const f of files) lines.push(`        <file>sfx/${f}</file>`);
lines.push('    </qresource>', '</RCC>', '');
fs.writeFileSync(out, lines.join('\n'));
console.log(`[gen-sfx-qrc] ${path.relative(root, out)}：${files.length} 个音效`);
for (const f of files) console.log(`   ${f}`);
