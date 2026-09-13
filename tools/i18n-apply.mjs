#!/usr/bin/env node
/**
 * 把界面文案从 C++ 源码**搬进语言文件**（一次性/可重复执行的搬运器）。
 *
 *   node tools/i18n-apply.mjs --dry     # 只报告会改哪些行（不写文件）
 *   node tools/i18n-apply.mjs           # 执行替换 + 报告没搬掉的残留
 *
 * 数据源是 `tools/i18n-map.mjs`（`C++ 字面量 → key`）。只替换**带包装的字面量**：
 * `QStringLiteral("…")` / `QLatin1String("…")` / `QString::fromUtf8("…")` / `QString("…")`
 * —— 用包装形式匹配就**不会**出现「准备」误伤「取消准备」这类子串问题。
 *
 * 裸字面量（例如 `const char* const kWinds[] = { "東", … }`）与需要拼接的串**不动**：
 * 脚本会把它们报出来，由人工按 `// i18n-keep` 或结构化改写处理。这样"漏搬"永远可见。
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';
import { MAP, HANDLED, keyToText } from './i18n-map.mjs';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const dry = process.argv.includes('--dry');

const WRAPPERS = [
  (lit) => `QStringLiteral("${lit}")`,
  (lit) => `QLatin1String("${lit}")`,
  (lit) => `QString::fromUtf8("${lit}")`,
  (lit) => `QString("${lit}")`,
];

let total = 0;
for (const [rel, table] of Object.entries(MAP)) {
  const abs = path.join(root, rel);
  const before = fs.readFileSync(abs, 'utf8');
  let after = before;
  const done = [];
  const missing = [];
  const already = [];

  for (const [lit, key] of Object.entries(table)) {
    let n = 0;
    for (const wrap of WRAPPERS) {
      const from = wrap(lit);
      let idx;
      while ((idx = after.indexOf(from)) >= 0) {
        after = after.slice(0, idx) + `lang::t("${key}")` + after.slice(idx + from.length);
        n++;
      }
    }
    if (n > 0) done.push({ lit, key, n });
    else if (after.includes(`lang::t("${key}")`)) already.push({ lit, key });
    else if (!(HANDLED[rel] || []).includes(lit)) missing.push({ lit, key });
  }

  total += done.reduce((a, d) => a + d.n, 0);
  const nDone = done.reduce((a, d) => a + d.n, 0);
  console.log(`\n=== ${rel}  替换 ${nDone} 处`
              + (already.length ? `，已搬过 ${already.length} 处` : ''));
  for (const d of done) console.log(`   ${d.n}× ${JSON.stringify(d.lit)} → ${d.key}`);
  if (missing.length) {
    console.log('   ⚠ 未命中（字面量可能不是带包装的形式，或写法与源码不一致）：');
    for (const m of missing) console.log(`      ${JSON.stringify(m.lit)} → ${m.key}`);
  }
  if (!dry && after !== before) fs.writeFileSync(abs, after);
}

console.log(`\n[i18n-apply] 共替换 ${total} 处${dry ? '（--dry：未写文件）' : ''}`);
console.log(`[i18n-apply] 语言文件将新增/更新 ${Object.keys(keyToText()).length} 条 ui.* 文案`);
