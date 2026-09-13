#!/usr/bin/env node
/**
 * 生成牌面 SVG **占位符**（37 种牌 + 牌背）。
 *
 * 这些是给美术替换用的骨架：每张都是一个合法 SVG，牌面底色/描边与程序化绘制一致，
 * 中间写着该牌的代号（如 `1m`、`0p`、`5z`），方便一眼认出是哪一张。
 * 替换时保持 **viewBox="0 0 300 400"** 与文件同名即可，代码侧无需改动。
 *
 *   node tools/gen-tile-placeholders.mjs [输出目录]
 *
 * 默认输出到 client/assets/tiles/。已存在的文件**不会**被覆盖（避免盖掉美术的成果）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.argv[2] || path.join(here, '..', 'client', 'assets', 'tiles');

const W = 300;
const H = 400;
const IVORY = '#FAF6E7';
const OUTLINE = '#8C8672';
const INK = '#142A54';
const RED = '#C0392B';
const BACK = '#1F5C4A';
const BACK_EDGE = '#164236';

/** 牌代号：与客户端 mj::tileString() 的输出一致 */
const codes = [];
for (const s of ['m', 'p', 's']) {
  for (let n = 1; n <= 9; n++) codes.push(`${n}${s}`);
  codes.push(`0${s}`);            // 赤五（0 表示赤宝牌）
}
for (let n = 1; n <= 7; n++) codes.push(`${n}z`);   // 東南西北白發中

const isRed = (c) => c.startsWith('0');

function tileSvg(code) {
  const fill = isRed(code) ? RED : INK;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
  <!-- 占位符：请替换为正式牌面。保持 viewBox 与文件名不变即可。 -->
  <rect x="6" y="6" width="${W - 12}" height="${H - 12}" rx="34" fill="${IVORY}" stroke="${OUTLINE}" stroke-width="6"/>
  <text x="${W / 2}" y="${H / 2 + 52}" font-size="140" text-anchor="middle"
        font-family="sans-serif" font-weight="bold" fill="${fill}">${code}</text>
</svg>
`;
}

function backSvg() {
  const lines = [];
  for (let x = -H; x < W; x += 34) {
    lines.push(`    <line x1="${x}" y1="${H}" x2="${x + H}" y2="0"/>`);
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
  <!-- 占位符：牌背。请替换为正式牌背。 -->
  <rect x="6" y="6" width="${W - 12}" height="${H - 12}" rx="34" fill="${BACK}" stroke="${BACK_EDGE}" stroke-width="6"/>
  <g stroke="#FFFFFF" stroke-opacity="0.26" stroke-width="9">
${lines.join('\n')}
  </g>
</svg>
`;
}

fs.mkdirSync(outDir, { recursive: true });
let created = 0;
let skipped = 0;
for (const c of [...codes, 'back']) {
  const file = path.join(outDir, `${c}.svg`);
  if (fs.existsSync(file)) {
    skipped++;
    continue;
  }
  fs.writeFileSync(file, c === 'back' ? backSvg() : tileSvg(c), 'utf8');
  created++;
}

// qrc：把 tiles/ 整个前缀挂到 :/tiles 下
// prefix="/"：<file> 里已经带了 tiles/ 目录，资源路径才是 :/tiles/<码>.svg。
// （若写成 prefix="/tiles"，资源会变成 :/tiles/tiles/<码>.svg，代码找不到 → 静默回退。）
const qrc = `<!DOCTYPE RCC>
<RCC version="1.0">
  <qresource prefix="/">
${[...codes, 'back'].map((c) => `    <file>tiles/${c}.svg</file>`).join('\n')}
  </qresource>
</RCC>
`;
fs.writeFileSync(path.join(outDir, '..', 'tiles.qrc'), qrc, 'utf8');

console.log(`[tiles] 目录 ${outDir}`);
console.log(`[tiles] 新建 ${created} 个，跳过已存在 ${skipped} 个（共 ${codes.length + 1} 个）`);
console.log('[tiles] 已写入 ' + path.join(outDir, '..', 'tiles.qrc'));
