#!/usr/bin/env node
/**
 * 翻译表交叉核对：**服务端词表 ↔ 客户端语言文件**。
 *
 * 背景（docs/PROTOCOL.md §0）：报文里不带中文，服务端只发 ASCII 码
 * （役种 `yaku[].code`、参数化役种的 `tile`、打点档位 `limit`、流局原因 `reason`、
 * 错误 `error.code`），中文文案全在 `client/assets/i18n/<locale>.json` 里。
 *
 * 这带来一个新的失效模式：**服务端加了一个码，客户端没加条目** ——
 * 编译能过、自检能过，只有真打出来那一刻界面才显示成裸码。
 * 本工具就是这条链路的静态哨兵：以服务端为权威，逐个码核对客户端有没有译文。
 *
 * 用法：node tools/check-i18n.mjs [locale]
 * 退出码：0 = 通过；1 = 有缺口（缺译文 / 有失效条目）。
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const locale = process.argv[2] || 'zh_CN';

const YAKU_CODES_JAVA = path.join(root, 'server/src/main/java/mahjong/rules/YakuCodes.java');
const SESSION_JAVA = path.join(root, 'server/src/main/java/mahjong/net/Session.java');
const LANG_JSON = path.join(root, `client/assets/i18n/${locale}.json`);

const problems = [];
const warnings = [];

function read(file) {
  if (!fs.existsSync(file)) {
    problems.push(`找不到文件：${path.relative(root, file)}`);
    return '';
  }
  return fs.readFileSync(file, 'utf8');
}

function matchAll(text, re, group = 1) {
  const out = [];
  for (const m of text.matchAll(re)) out.push(m[group]);
  return out;
}

/** 码必须是 ASCII：`[a-z0-9_]`。否则没法保证报文里不出现中文。 */
function checkCodeShape(code, where) {
  if (!/^[a-z0-9_]+$/.test(code))
    problems.push(`${where} 的码不是 ASCII 小写标识符：${JSON.stringify(code)}`);
}

// ---------- 1. 服务端词汇表 ----------
const yakuSrc = read(YAKU_CODES_JAVA);

// 役种：`put("立直", "riichi");`（只认缩进 8 空格的那批，避开 `YAKU.put(cn, code)` 这类实现代码）
const yakuPairs = [...yakuSrc.matchAll(/^ {8}put\("([^"]+)",\s*"([^"]+)"\);$/gm)]
  .map((m) => ({ cn: m[1], code: m[2] }));
const yakuCodes = new Set(yakuPairs.map((p) => p.code));

// 参数化役种 + 兜底码（不是 put 进去的，是常量）
const paramCodes = ['YAKUHAI', 'ROUND_WIND', 'SEAT_WIND', 'UNKNOWN']
  .map((name) => {
    const m = yakuSrc.match(new RegExp(`String ${name} = "([^"]+)"`));
    if (!m) problems.push(`YakuCodes.java 里找不到常量 ${name}`);
    return m ? m[1] : null;
  })
  .filter(Boolean);

const limitCodes = new Set(matchAll(yakuSrc, /LIMIT\.put\("[^"]*",\s*"([^"]+)"\);/g));
const reasonCodes = new Set(matchAll(yakuSrc, /REASON\.put\("[^"]+",\s*"([^"]+)"\);/g));

// 错误码：Session.java 的 sendError("...") / sendError("...", arg)
const sessionSrc = read(SESSION_JAVA);
const errorCodes = new Set(matchAll(sessionSrc, /sendError\("([^"]+)"/g));

for (const p of yakuPairs) checkCodeShape(p.code, `役种「${p.cn}」`);
for (const c of paramCodes) checkCodeShape(c, '参数化役种常量');
for (const c of limitCodes) checkCodeShape(c, '打点档位');
for (const c of reasonCodes) checkCodeShape(c, '流局原因');
for (const c of errorCodes) checkCodeShape(c, '错误码');

// ---------- 2. 客户端语言文件 ----------
const langRaw = read(LANG_JSON);
let lang = {};
if (langRaw) {
  try {
    lang = JSON.parse(langRaw);
  } catch (e) {
    problems.push(`语言文件不是合法 JSON：${e.message}`);
  }
}
const keys = new Set(Object.keys(lang));

// 译文自身不能等于 key（那说明写表时把 key 抄成了文案）
for (const [k, v] of Object.entries(lang)) {
  if (typeof v !== 'string' || v.trim() === '')
    problems.push(`条目 ${k} 的文案是空串`);
  else if (v === k)
    problems.push(`条目 ${k} 的文案等于 key 本身（写表时抄错了？）`);
}

// ---------- 3. 逐族核对：服务端的每个码都必须有译文 ----------
// 牌码：`<1-9><m|p|s>` + 赤五 `0m/0p/0s` + `<1-7>z` = 37 种（与客户端 Tile.cpp 的编码一致）
const tileCodes = [];
for (const suit of ['m', 'p', 's']) {
  for (let n = 1; n <= 9; n++) tileCodes.push(`${n}${suit}`);
  tileCodes.push(`0${suit}`);
}
for (let n = 1; n <= 7; n++) tileCodes.push(`${n}z`);

const families = [
  { name: 'yaku', codes: [...yakuCodes, ...paramCodes], where: 'YakuCodes.java' },
  { name: 'limit', codes: [...limitCodes], where: 'YakuCodes.java' },
  { name: 'reason', codes: [...reasonCodes], where: 'YakuCodes.java' },
  { name: 'error', codes: [...errorCodes], where: 'Session.java' },
  { name: 'tile', codes: tileCodes, where: '协议牌码' },
];

const referenced = new Set();
for (const fam of families) {
  for (const code of fam.codes) {
    const key = `${fam.name}.${code}`;
    referenced.add(key);
    if (!keys.has(key))
      problems.push(`${fam.where} 里有码 ${code}，但语言文件缺 ${key}`);
  }
}

// 参数化役种的模板必须带 %1（否则牌码没地方填）
for (const code of ['yakuhai', 'round_wind', 'seat_wind']) {
  const v = lang[`yaku.${code}`];
  if (typeof v === 'string' && !v.includes('%1'))
    problems.push(`参数化役种模板 yaku.${code} 必须含 %1 占位，实际：${JSON.stringify(v)}`);
}

// ---------- 4. 客户端源码里的字面量 key ----------
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(cpp|h)$/.test(e.name)) out.push(p);
  }
  return out;
}

const clientSrc = walk(path.join(root, 'client/src'));
// lang::t("x.y") / lang::t(QStringLiteral("x.y")) / lang::code(QStringLiteral("prefix."), ...)
const literalKey = /lang::t\(\s*(?:QStringLiteral\()?"([A-Za-z0-9_.]+)"/g;
const literalPrefix = /lang::code\(\s*(?:QStringLiteral\()?"([A-Za-z0-9_.]+)"/g;

// SelfTest 里**故意**引用不存在的 key（验"缺 key 原样返回 + 计 miss"这条路径），
// 不能拿来当"表格缺口"，所以按文件跳过字面量核对（它仍然参与 tile/yaku 的动态核对）。
const SKIP_LITERAL_FILES = ['SelfTest.cpp'];

let literalCount = 0;
for (const f of clientSrc) {
  const src = fs.readFileSync(f, 'utf8');
  const rel = path.relative(root, f);
  if (SKIP_LITERAL_FILES.some((n) => rel.endsWith(n))) continue;
  for (const m of src.matchAll(literalKey)) {
    literalCount++;
    if (!keys.has(m[1]))
      problems.push(`${rel} 里引用了语言文件没有的 key：${m[1]}`);
  }
  for (const m of src.matchAll(literalPrefix)) {
    // 前缀本身不是 key（真实 key = 前缀 + 运行期码），只登记，交给上面的逐码核对
    if (!m[1].endsWith('.'))
      warnings.push(`${rel} 里 lang::code 的前缀没有以 '.' 结尾：${m[1]}`);
  }
}

// ---------- 5. 失效条目（语言文件里有、服务端与源码都不引用）----------
// ⚠ 动态族：key 由**运行期拿到的码**拼出来（`lang::code("limit.", code)`），
//   静态扫不到调用点，所以靠"服务端词表"来解释，多出来的就算失效。
const dynamic = new Set(['yaku', 'limit', 'reason', 'error', 'tile']);
const sourceText = clientSrc.map((f) => fs.readFileSync(f, 'utf8')).join('\n');
for (const k of keys) {
  const fam = k.includes('.') ? k.slice(0, k.indexOf('.')) : '';
  if (dynamic.has(fam)) {
    if (!referenced.has(k))
      problems.push(`语言文件里的 ${k} 在服务端词表里找不到对应码（词表改名了？）`);
  } else if (!sourceText.includes(`"${k}"`)) {
    warnings.push(`语言文件里的 ${k} 没有任何源码引用`);
  }
}

// ---------- 汇总 ----------
console.log(`[check-i18n] 服务端词表：役种 ${yakuPairs.length} + 参数化/兜底 ${paramCodes.length}`
            + ` + 打点 ${limitCodes.size} + 流局 ${reasonCodes.size} + 错误 ${errorCodes.size}`
            + ` + 牌码 ${tileCodes.length}   （YakuCodes.java / Session.java）`);
console.log(`[check-i18n] 客户端语言文件：${keys.size} 条（${path.relative(root, LANG_JSON)}）`);
console.log(`[check-i18n] 客户端源码字面量 key：${literalCount} 处（${clientSrc.length} 个文件）`);
for (const w of warnings) console.warn(`[check-i18n] 提示：${w}`);
if (problems.length) {
  for (const p of problems) console.error(`[check-i18n] 失败：${p}`);
  console.error(`[check-i18n] CHECK-I18N FAIL（${problems.length} 项）`);
  process.exit(1);
}
console.log('[check-i18n] CHECK-I18N PASS');
