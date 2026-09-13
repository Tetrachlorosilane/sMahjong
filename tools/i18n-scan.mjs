#!/usr/bin/env node
/**
 * 扫描客户端源码里**还没搬进语言文件**的用户可见中文。
 *
 * 背景：协议里只有 ASCII 码，界面文案全在 `client/assets/i18n/<locale>.json`（见 docs/PROTOCOL.md §0）。
 * 那些"留在 C++ 里的中文"分两类，**必须区分开**：
 *
 *   ① **界面文案** —— 窗口标题、按钮、标签、状态栏、结算 HTML。这些要搬进语言文件。
 *   ② **不是文案** —— 控制台/日志输出（自检、autoplay 日志、--lobbytest 的打印）、
 *      以及**牌面字形**（程序化绘制里的「萬」「筒」「索」「東」）。这些**不能**翻译：
 *      日志是给开发者看的（项目约定：日志用中文、不进语言文件），
 *      牌面字形是图形（翻了就画错牌）。见 §"排除项"。
 *
 * 所以本工具用**文件/行内标记**做白名单，而不是"见到中文就报"。
 *
 * 用法：
 *   node tools/i18n-scan.mjs            # 列出全部「未搬运」的界面文案（按文件分组）
 *   node tools/i18n-scan.mjs --check    # CI 用：有未搬运的就 exit 1
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const SRC = path.join(root, 'client/src');

const CJK = /[\u3000-\u303F\u4E00-\u9FFF\uFF00-\uFFEF\u2018\u2019\u201C\u201D\u2026]/;

/**
 * 排除项（**不是界面文案**，故意不进语言文件）：
 *   - 控制台/日志：`printf`/`fprintf`/`std::printf`/`log(`/`QTextStream(stdout)` 之类的行；
 *   - 牌面字形：TileRenderer 的程序化绘制（「萬」「筒」「索」与字牌名是**图形**）；
 *   - 自检消息：SelfTest.cpp 的断言名（测试报告，不是界面）；
 *   - 联调机器人：AutoPlay.cpp（输出全是 `autoplay.log` 的日志，不是界面）。
 * 另外**行内标记 `// i18n-keep`** 可以就地豁免：用户数据（默认玩家名）与成形字形用它标注。
 */
const EXCLUDE_FILES = new Set([
  'SelfTest.cpp',        // 自检报告：断言名（开发者看的中文）
  'FontProbe.cpp',       // 字体探针：调试出图
  'GenTiles.cpp',        // 素材生成：离线工具
  'AutoPlay.cpp',        // 联调自走：只写 autoplay.log（日志不进语言文件）
]);

/** 行内标记：出现即视为"非界面文案"（日志/控制台/就地豁免）。 */
const EXCLUDE_LINE = /printf|QTextStream\(stdout\)|QTextStream\(stderr\)|fputs|fflush|<<\s*"\\n"|\blog\(|logLine|qDebug|qWarning|LOG_|i18n-keep/;

/** TileRenderer 里这些字形是**画牌**用的，不是文案。 */
const GLYPH_FILES = new Set(['TileRenderer.cpp']);

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(cpp|h)$/.test(e.name)) out.push(p);
  }
  return out;
}

/**
 * 逐字符扫描，返回所有普通字符串字面量：{ value, line, raw }。
 * 要正确处理：行注释 / 块注释 / 字符字面量 / 原始字符串 R"(...)" —— 否则会把注释里的
 * 中文当成文案（这个项目注释里全是中文，误报会淹没真信号）。
 */
function scanLiterals(src) {
  const out = [];
  let i = 0;
  let line = 1;
  const n = src.length;

  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];

    if (c === '\n') { line++; i++; continue; }

    // 行注释
    if (c === '/' && c2 === '/') {
      while (i < n && src[i] !== '\n') i++;
      continue;
    }
    // 块注释
    if (c === '/' && c2 === '*') {
      i += 2;
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) {
        if (src[i] === '\n') line++;
        i++;
      }
      i += 2;
      continue;
    }
    // 原始字符串 R"delim( ... )delim"
    if (c === 'R' && c2 === '"') {
      const open = src.indexOf('(', i + 2);
      const delim = src.slice(i + 2, open);
      const close = `)${delim}"`;
      const end = src.indexOf(close, open + 1);
      const body = src.slice(open + 1, end < 0 ? n : end);
      const bodyLine = line;
      if (CJK.test(body)) out.push({ value: body, line: bodyLine, raw: 'R""' });
      for (const ch of src.slice(i, end < 0 ? n : end + close.length)) if (ch === '\n') line++;
      i = end < 0 ? n : end + close.length;
      continue;
    }
    // 字符字面量
    if (c === "'") {
      i++;
      while (i < n && src[i] !== "'") { if (src[i] === '\\') i++; i++; }
      i++;
      continue;
    }
    // 普通字符串字面量
    if (c === '"') {
      const startLine = line;
      i++;
      let value = '';
      while (i < n && src[i] !== '"') {
        if (src[i] === '\\') { value += src[i] + src[i + 1]; i += 2; continue; }
        if (src[i] === '\n') { line++; break; }   // 未闭合（不该发生）
        value += src[i];
        i++;
      }
      i++;
      if (CJK.test(value)) out.push({ value, line: startLine, raw: '""' });
      continue;
    }
    i++;
  }
  return out;
}

const files = walk(SRC);
const perFile = [];
for (const f of files) {
  const rel = path.relative(root, f);
  const base = path.basename(f);
  if (EXCLUDE_FILES.has(base)) continue;
  const src = fs.readFileSync(f, 'utf8');
  const lines = src.split('\n');
  const lits = scanLiterals(src).filter((l) => {
    const text = lines[l.line - 1] || '';
    if (EXCLUDE_LINE.test(text)) return false;
    if (GLYPH_FILES.has(base)) return false;   // 画牌字形
    return true;
  });
  if (lits.length) perFile.push({ rel, lits });
}

let total = 0;
for (const { rel, lits } of perFile.sort((a, b) => b.lits.length - a.lits.length)) {
  console.log(`\n=== ${rel}  (${lits.length})`);
  for (const l of lits) {
    total++;
    console.log(`  ${String(l.line).padStart(4)}  ${JSON.stringify(l.value)}`);
  }
}
console.log(`\n[i18n-scan] 未搬进语言文件的界面文案：${total} 处（${perFile.length} 个文件）`);
if (process.argv.includes('--check') && total > 0) {
  console.error('[i18n-scan] FAIL：界面文案必须写进 client/assets/i18n/<locale>.json，'
                + '代码里只留 lang::t("key")');
  process.exit(1);
}
