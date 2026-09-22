#!/usr/bin/env node
/**
 * 无用代码扫描（Java + C++）：找出"只在声明/定义处出现"的函数。
 *
 *   node tools/deadcode-scan.mjs            # 两边都扫
 *   node tools/deadcode-scan.mjs java       # 只扫服务端
 *   node tools/deadcode-scan.mjs cpp        # 只扫客户端
 *
 * ⚠ 这是**候选**清单，不是判决书：静态扫描看不出反射、宏、Qt 元对象（信号槽）、
 *   接口/虚函数实现、以及"故意留着给外部使用者"的公开 API。删之前必须：
 *   ① 读一眼那段代码 ② 确认没有 `override`/`signals:`/`slots:`/`Q_INVOKABLE`
 *   ③ 查文档里有没有把它写成契约（有的话算"在用"，别删）
 *   ④ 删完编译 + 跑对应层自检（L1 / L2）。
 *
 * 为什么留在仓库里：这个仓库的纪律是"文档里不写幻觉、代码里不留垃圾"，
 * 而"某个 helper 早就没人调了"只有靠一遍遍扫才能发现 —— 把判据固化成工具，
 * 下次加完功能顺手跑一次即可（见 AGENTS §6.7）。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, basename } from 'node:path';

const ROOT = process.cwd();
const SKIP_DIRS = new Set(['.git', 'build', 'dist', 'node_modules', '.qt', 'release', 'replays', '.tools']);
const mode = process.argv[2] || 'all';

function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    if (SKIP_DIRS.has(e)) continue;
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const all = walk(ROOT);
const read = (f) => readFileSync(f, 'utf8');

function countAll(files, name) {
  let n = 0;
  const re = new RegExp(`\\b${name}\\b`, 'g');
  for (const f of files) n += (read(f).match(re) || []).length;
  return n;
}

// ---------------------------------------------------------------- Java
function scanJava() {
  const files = all.filter((f) => f.endsWith('.java'));
  const corpus = files.map((f) => ({ path: relative(ROOT, f), text: read(f) }));
  const decl = /^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*(?:public|protected|private|static|final|synchronized|abstract|native|default|strictfp)[\w<>\[\],.\s?]*?\s(\w+)\s*\(/gm;
  const out = [];
  const names = new Set();
  for (const { path, text } of corpus) {
    text.split('\n').forEach((line, i) => {
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
      if (/\b(if|for|while|switch|catch|return|new|assert|throw)\b/.test(line)) return;
      if (!line.includes('(')) return;
      decl.lastIndex = 0;
      const m = decl.exec(line);
      if (!m) return;
      const name = m[1];
      if (name === 'main' || name === basename(path, '.java')) return;
      names.add(name);
      if (countAll(files, name) <= 1) out.push(`  ${path}:${i + 1}  ${name}`);
    });
  }
  console.log(`Java：${files.length} 个文件，候选无用方法 ${out.length} 条`);
  out.forEach((l) => console.log(l));
}

// ---------------------------------------------------------------- C++
function scanCpp() {
  const hdrs = all.filter((f) => f.endsWith('.h'));
  const srcs = all.filter((f) => f.endsWith('.cpp'));
  const files = [...hdrs, ...srcs];
  const textOf = new Map(files.map((f) => [f, read(f)]));
  // 头文件里的成员函数声明（跳过 signals:/slots: 区段与 override/virtual/operator/构造析构）
  const out = [];
  for (const h of hdrs) {
    const text = textOf.get(h);
    const lines = text.split('\n');
    const cls = basename(h, '.h');
    lines.forEach((line, i) => {
      const t = line.trim();
      if (/^\s*(\/\/|\*|\/\*)/.test(t)) return;
      if (/\b(signals|slots|public slots|private slots|protected slots)\s*:/.test(t)) return;
      if (/\b(override|virtual|operator|Q_OBJECT|Q_SLOT|Q_INVOKABLE|emit)\b/.test(t)) return;
      if (t.includes('=') && !t.includes('(')) return;
      const m = /^[\w:<>,\s*&]+?\b(\w+)\s*\([^;{)]*\)\s*(?:const)?\s*;/.exec(t);
      if (!m) return;
      const name = m[1];
      if (name === cls || name.startsWith('~')) return;
      // 排除内联（有函数体）
      if (t.includes('{')) return;
      // 定义/调用都要分清楚：定义是**顶格**的 `Ret Cls::name(`（缩进的调用不算）
      let defs = 0;
      for (const s of srcs) {
        const st = textOf.get(s);
        if (!st.includes(`${cls}::`)) continue;
        for (const l of st.split('\n')) {
          if (!l.includes(`${cls}::${name}(`)) continue;
          if (/^\S/.test(l)) defs++;      // 顶格 → 定义
        }
      }
      if (countAll(files, name) - 1 - defs <= 0) out.push(`  ${relative(ROOT, h)}:${i + 1}  ${name}`);
    });
  }
  console.log(`\nC++：${hdrs.length} 个头 / ${srcs.length} 个实现，候选无用成员函数 ${out.length} 条`);
  out.forEach((l) => console.log(l));
}

if (mode === 'all' || mode === 'java') scanJava();
if (mode === 'all' || mode === 'cpp') scanCpp();
