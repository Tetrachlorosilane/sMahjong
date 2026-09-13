#!/usr/bin/env node
/**
 * 列出 OTF/TTF 字体 GSUB 表里声明的 OpenType feature tag。
 *
 * 用途：想知道某字体靠哪个特性实现"输入 3m 变成牌面图形"这类连字，
 * 才能在 Qt 里用 QFont::setFeature(QFont::Tag("xxxx"), 1) 打开它。
 *
 *   node tools/dump-otf-features.mjs <字体路径> [--lookups]
 *
 * 只解析表目录 + GSUB 的 FeatureList，不需要完整解析脚本与查找表。
 */
import fs from 'node:fs';

const file = process.argv[2];
const showLookups = process.argv.includes('--lookups');
if (!file) {
  console.error('用法: node tools/dump-otf-features.mjs <字体路径> [--lookups]');
  process.exit(2);
}

const buf = fs.readFileSync(file);
const numTables = buf.readUInt16BE(4);
console.log(`字体: ${file}`);
console.log(`sfnt 版本: 0x${buf.readUInt32BE(0).toString(16)}，表数: ${numTables}`);

const tables = {};
for (let i = 0; i < numTables; i++) {
  const p = 12 + i * 16;
  const tag = buf.toString('latin1', p, p + 4);
  tables[tag] = { offset: buf.readUInt32BE(p + 8), length: buf.readUInt32BE(p + 12) };
}
console.log('包含表: ' + Object.keys(tables).sort().join(' '));

/** GSUB / GPOS 的 FeatureList 里所有 feature tag */
function featureTags(tableTag) {
  const t = tables[tableTag];
  if (!t) return null;
  const base = t.offset;
  const featureListOff = buf.readUInt16BE(base + 6);
  const fl = base + featureListOff;
  const count = buf.readUInt16BE(fl);
  const out = [];
  for (let i = 0; i < count; i++) {
    const rec = fl + 2 + i * 6;
    const tag = buf.toString('latin1', rec, rec + 4);
    out.push(tag);
  }
  return out;
}

for (const t of ['GSUB', 'GPOS']) {
  const tags = featureTags(t);
  if (!tags) { console.log(`\n${t}: 无`); continue; }
  const uniq = [...new Set(tags)];
  console.log(`\n${t}: ${tags.length} 条 feature 记录，去重 ${uniq.length} 个 tag`);
  console.log('  ' + uniq.sort().join(' '));
  // 标注常见含义
  const known = {
    liga: '标准连字（多数引擎默认开启）',
    rlig: '必需连字（默认开启）',
    dlig: '自由连字（默认关闭，需显式开启）',
    clig: '上下文连字',
    calt: '上下文替换（默认开启）',
    ccmp: '字形组合/分解（默认开启）',
    vert: '竖排替换',
    vrt2: '竖排替换 v2',
    salt: '样式替代',
    aalt: '所有替代字形',
  };
  for (const u of uniq.sort()) {
    if (u.startsWith('ss')) console.log(`  ss* : ${u} = 第 ${parseInt(u.slice(2), 10)} 个样式集（默认关闭，需显式开启）`);
    else if (known[u]) console.log(`  ${u}: ${known[u]}`);
  }
}

if (showLookups && tables.GSUB) {
  const base = tables.GSUB.offset;
  const lookupListOff = buf.readUInt16BE(base + 8);
  const ll = base + lookupListOff;
  const n = buf.readUInt16BE(ll);
  console.log(`\nGSUB Lookup 数: ${n}`);
}
