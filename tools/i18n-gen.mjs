#!/usr/bin/env node
/**
 * 把 `tools/i18n-map.mjs` 里的界面文案**写进语言文件**（`client/assets/i18n/zh_CN.json`）。
 *
 * 与 `tools/i18n-apply.mjs` 配对：那个负责改代码，这个负责生成文案表。
 * 两边共用同一份映射，所以 **key 与文案不会漂移**。
 *
 *   node tools/i18n-gen.mjs --check   # 只核对语言文件是否与映射一致（CI 用）
 *   node tools/i18n-gen.mjs           # 写入（保留已有条目的键序，新增的追加在后面）
 *
 * ⚠ 已有条目**不会被覆盖**：`zh_CN.json` 是给人编辑的（改文案不必碰代码），
 *   本工具只**补缺**，避免把人工润色过的文案覆盖回源码字面量。
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';
import { keyToText } from './i18n-map.mjs';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const JSON_PATH = path.join(root, 'client/assets/i18n/zh_CN.json');
const check = process.argv.includes('--check');

const wanted = keyToText();
const existing = JSON.parse(fs.readFileSync(JSON_PATH, 'utf8'));

const missing = Object.keys(wanted).filter((k) => !(k in existing));
if (check) {
  if (missing.length) {
    console.error(`[i18n-gen] FAIL：语言文件缺 ${missing.length} 条：`);
    for (const k of missing.slice(0, 40)) console.error(`   ${k} = ${JSON.stringify(wanted[k])}`);
    process.exit(1);
  }
  console.log(`[i18n-gen] OK：语言文件已覆盖映射表里的 ${Object.keys(wanted).length} 条 ui.* 文案`);
  process.exit(0);
}

// 追加缺失项（键序保持插入顺序，便于人工 diff）
const merged = { ...existing };
for (const k of Object.keys(wanted).sort()) merged[k] = wanted[k];

// 输出与现状一致的格式：两空格缩进 + 末尾换行（和仓库里其它 json 一样）
fs.writeFileSync(JSON_PATH, JSON.stringify(merged, null, 2) + '\n');
console.log(`[i18n-gen] 语言文件：原有 ${Object.keys(existing).length} 条，新增 ${missing.length} 条，`
            + `共 ${Object.keys(merged).length} 条`);
