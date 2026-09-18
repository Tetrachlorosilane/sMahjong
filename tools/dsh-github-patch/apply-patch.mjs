// 极简 unified diff 应用器 —— 只服务本目录这一份补丁。
//
// 为什么不用 git apply / GNU patch：
//   · `git apply` 在**非 git 仓库**目录里会打印 "Skipped patch 'src/index.ts'." 然后
//     **退出码 0**（静默什么都不做）——插件检出（~/.dsh/plugins/... 或源码 checkout）
//     并不是 git 仓库，用它等于没打补丁；
//   · Git for Windows 自带的 MSYS `patch.exe` 在受限沙箱里连自身都起不来
//     （"couldn't create signal pipe, Win32 error 5"）。
// 所以把「校验 + 应用」写进这 60 行：逐行严格比对，任何一处对不上就整体报错，
// 绝不"尽力而为"地猜。补丁只含单文件、普通 hunk（无 rename/binary/CRLF）。
//
// 用法：
//   node apply-patch.mjs <patch> <target>            应用
//   node apply-patch.mjs <patch> <target> --reverse  反向（还原上游原文）
//   node apply-patch.mjs <patch> <target> --check    只校验能否干净应用
import { readFileSync, writeFileSync } from 'node:fs'

const [patchPath, targetPath, ...flags] = process.argv.slice(2)
const reverse = flags.includes('--reverse')
const checkOnly = flags.includes('--check')
if (!patchPath || !targetPath) {
  console.error('用法: node apply-patch.mjs <patch> <target> [--reverse] [--check]')
  process.exit(2)
}
const die = (msg) => { console.error('apply-patch: ' + msg); process.exit(1) }

/** 解析补丁 → { file, hunks: [{ oldStart, oldCount, newStart, newCount, lines }] } */
function parsePatch(text) {
  const lines = text.split('\n')
  let file = null
  let hunk = null
  const hunks = []
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    if (line.startsWith('+++ ')) {
      const path = line.slice(4).trim()
      if (path === '/dev/null') die('不支持新建空文件的补丁')
      file = path.replace(/^[ab]\//, '')
      continue
    }
    if (line.startsWith('@@')) {
      const m = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line)
      if (!m) die(`看不懂的 hunk 头：${line}`)
      hunk = {
        oldStart: Number(m[1]),
        oldCount: m[2] === undefined ? 1 : Number(m[2]),
        newStart: Number(m[3]),
        newCount: m[4] === undefined ? 1 : Number(m[4]),
        lines: [],
      }
      hunks.push(hunk)
      continue
    }
    if (hunk === null) continue
    // 一个 hunk 的正文：以 ' ' / '-' / '+' 开头
    const tag = line[0]
    if (tag === ' ' || tag === '-' || tag === '+') {
      hunk.lines.push({ tag, text: line.slice(1) })
      continue
    }
    // 其它内容（空行、"\ No newline"、下一个 diff --git…）结束当前 hunk
    hunk = null
  }
  if (file === null || hunks.length === 0) die('补丁里没有可用的 hunk')
  return { file, hunks }
}

const patchText = readFileSync(patchPath, 'utf8')
const { file, hunks } = parsePatch(patchText)
const original = readFileSync(targetPath, 'utf8')
const hadFinalNewline = original.endsWith('\n')
const src = original.split('\n')
if (hadFinalNewline) src.pop()      // 末尾空串；输出时再加回来

// 应用方向：正向时 ' ' / '-' 命中原文、'+' 是新内容；反向时对调
const out = []
let cursor = 0                        // 已消费的原文行数（0-based）
let lastLine = 0
for (const [index, h] of hunks.entries()) {
  // 反向时"原文"是补丁 apply 之后的内容，所以位置要从新行号取
  const from = (reverse ? h.newStart : h.oldStart) - 1
  const to = (reverse ? h.oldStart : h.newStart) - 1
  if (from < cursor) die(`hunk ${index + 1} 与前一个重叠`)
  if (from > src.length) die(`hunk ${index + 1} 起点超出文件长度`)
  out.push(...src.slice(cursor, from))
  cursor = from

  let consumedOld = 0
  let producedNew = 0
  for (const { tag, text } of h.lines) {
    const isContext = tag === ' '
    const isRemove = tag === '-'
    const isAdd = tag === '+'
    // 反向模式：'+' 变成要命中原文的行，'-' 变成要写回的新行
    const matchesOld = reverse ? (isContext || isAdd) : (isContext || isRemove)
    const producesNew = reverse ? (isContext || isRemove) : (isContext || isAdd)
    if (matchesOld) {
      const actual = src[cursor]
      if (actual === undefined) die(`hunk ${index + 1} 第 ${consumedOld + 1} 行：原文已到文件末尾（期望 "${text}"）`)
      if (actual !== text) {
        die(`hunk ${index + 1} 上下文不匹配（文件第 ${cursor + 1} 行）\n  期望: ${JSON.stringify(text)}\n  实际: ${JSON.stringify(actual)}`)
      }
      cursor += 1
      consumedOld += 1
      // 上下文行两边都算：既消费一行原文，也产出一行（只消费不产出的是被删掉的行）
      if (producesNew) {
        out.push(actual)
        producedNew += 1
      }
    } else {
      out.push(text)
      producedNew += 1
    }
  }
  const expectOld = reverse ? h.newCount : h.oldCount
  const expectNew = reverse ? h.oldCount : h.newCount
  if (consumedOld !== expectOld) die(`hunk ${index + 1} 消费了 ${consumedOld} 行原文，头里写的是 ${expectOld}`)
  if (producedNew !== expectNew) die(`hunk ${index + 1} 产出了 ${producedNew} 行，头里写的是 ${expectNew}`)
  if (to < lastLine) die(`hunk ${index + 1} 产出位置回退`)
  lastLine = to
}
out.push(...src.slice(cursor))

const result = out.join('\n') + (hadFinalNewline ? '\n' : '')
if (checkOnly) {
  console.log(`OK 可干净应用：${file}（${hunks.length} 个 hunk，${reverse ? '反向' : '正向'}）`)
  process.exit(0)
}
if (result === original) die('应用后内容没有变化 —— 补丁可能已经在位（用 --reverse 还原）')
writeFileSync(targetPath, result)
console.log(`OK 已${reverse ? '反向' : ''}应用 ${hunks.length} 个 hunk → ${targetPath}（${Buffer.byteLength(original)} → ${Buffer.byteLength(result)} 字节）`)
