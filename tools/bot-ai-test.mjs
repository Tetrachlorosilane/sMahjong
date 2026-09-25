#!/usr/bin/env node
/**
 * 「机器人用哪一代 AI」的端到端回归（真 socket）。
 *
 * 服务端允许房主选**机器人用哪一代 AI**（内置牌效 / 各代训练网络，见 `mahjong.ai.BotAis`）。
 * 客户端只发**名字**，路径只由服务端起参数决定 —— 这条边界必须用真报文验，因为它的反面是
 * "客户端能指定服务器上的任意文件路径"（`net:/etc/passwd` 之类）。
 *
 * 验五件事：
 *   A) `hello_ok.bot_ais` 给出清单（至少含 `teacher`，且每项有 name/spec/default）；
 *   B) 建房带 `bot_ai` → `room.bot_ai` 回显成它；不带 → 回显成服务端默认；
 *   C) 房主 `set_bot_ai` 换名字 → 广播的 `room.bot_ai` 跟着变；
 *   D) **未知名字 / 路径串**（`net:/x/net.bin`）→ `error{bad_bot_ai}`，且当前值**不变**；
 *   E) 非房主 `set_bot_ai` → `error{not_host}`；牌局进行中改动被忽略（当前值不变）。
 *
 *   node tools/bot-ai-test.mjs <host> <port>
 *
 * 期望：`BOT-AI PASS`。
 * ⚠ 服务端至少要注册两个名字才有得选：默认清单里就有 `teacher`/`first`/`pass`/`random`，
 *   所以本脚本在**默认启动参数**下也能跑（"换一代"用 `first` 当替身）。
 */
import net from 'node:net'

const HOST = process.argv[2] || '127.0.0.1'
const PORT = Number(process.argv[3] || 10086)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const errors = []
const notes = []
const ok = (cond, what) => {
  if (cond) notes.push(`  [ok]   ${what}`)
  else errors.push(`  [FAIL] ${what}`)
}

function makeClient(name) {
  const c = { name, buf: '', pid: 0, seat: -1, room: null, botAis: null, errs: [] }
  c.sock = net.createConnection({ host: HOST, port: PORT })
  c.sock.setEncoding('utf8')
  c.sock.on('data', (chunk) => {
    c.buf += chunk
    let i
    while ((i = c.buf.indexOf('\n')) >= 0) {
      const line = c.buf.slice(0, i)
      c.buf = c.buf.slice(i + 1)
      if (!line.trim()) continue
      let ev
      try { ev = JSON.parse(line) } catch { continue }
      if (ev.ev === 'hello_ok') { c.pid = ev.pid; c.botAis = ev.bot_ais || [] }
      if (ev.ev === 'room_joined') c.seat = ev.seat
      if (ev.ev === 'room') c.room = ev
      if (ev.ev === 'error') c.errs.push(ev)
    }
  })
  c.send = (o) => c.sock.write(JSON.stringify(o) + '\n')
  c.close = () => { try { c.sock.destroy() } catch { /* 忽略 */ } }
  return c
}

async function waitFor(fn, ms = 2000) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    if (fn()) return true
    await sleep(20)
  }
  return false
}

const a = makeClient('甲')
const b = makeClient('乙')
try {
  await waitFor(() => a.pid && b.pid)
  a.send({ cmd: 'hello', name: '甲', ver: 1 })
  b.send({ cmd: 'hello', name: '乙', ver: 1 })
  await waitFor(() => a.pid > 0 && b.pid > 0, 3000)
  ok(a.pid > 0 && b.pid > 0, '两个客户端都完成握手')

  // ---- A) 清单 ----
  ok(Array.isArray(a.botAis) && a.botAis.length >= 1,
     `hello_ok.bot_ais 是数组且非空（${a.botAis ? a.botAis.length : 0} 项）`)
  const names = (a.botAis || []).map((x) => x.name)
  ok(names.includes('teacher'), `清单里含 teacher（${names.join('/')}）`)
  ok((a.botAis || []).every((x) => typeof x.name === 'string' && typeof x.default === 'boolean'),
     '每项都有 name 与 default 字段')
  ok((a.botAis || []).every((x) => !('spec' in x)),
     '清单里**不发**策略串（那是服务器本机路径；报文里也不该出现非 ASCII）')
  ok((a.botAis || []).filter((x) => x.default).length === 1,
     '恰好一项被标成 default')
  // 备选：挑一个不是 default 的名字来换
  const alt = names.find((n) => n !== 'teacher') || 'teacher'

  // ---- B) 建房带 bot_ai ----
  a.send({ cmd: 'create_room', name: '机器人AI测试', rules: { length: 'tonpuu' }, bot_ai: 'teacher' })
  ok(await waitFor(() => a.room, 3000), '建房成功并收到 room')
  ok(a.room?.bot_ai === 'teacher', `room.bot_ai 回显 = teacher（实际 ${a.room?.bot_ai}）`)

  b.send({ cmd: 'join_room', room: a.room.id })
  await waitFor(() => b.room, 3000)
  ok(!!b.room, '第二个客户端加入房间')

  // ---- D) 非房主 ----
  const before = a.room?.bot_ai
  b.errs.length = 0
  b.send({ cmd: 'set_bot_ai', ai: alt })
  await waitFor(() => b.errs.length > 0, 2000)
  ok(b.errs.some((e) => e.code === 'not_host'),
     `非房主 set_bot_ai → not_host（实际 ${b.errs.map((e) => e.code).join('/') || '无错误'}）`)
  await sleep(120)
  ok(a.room?.bot_ai === before, '非房主那条被拒后，当前值没变')

  // ---- C) 房主换一代 ----
  a.send({ cmd: 'set_bot_ai', ai: alt })
  const changed = await waitFor(() => a.room?.bot_ai === alt, 2000)
  ok(changed, `房主 set_bot_ai(${alt}) → room.bot_ai 跟着变（实际 ${a.room?.bot_ai}）`)
  ok(b.room?.bot_ai === alt, '广播是发给全房间的（另一个客户端也收到）')

  // ---- D2) 未知名字 + 路径串都要被拒 ----
  for (const bad of ['没这个AI', 'net:/etc/passwd', 'net:C:\\Windows\\win.ini']) {
    a.errs.length = 0
    a.send({ cmd: 'set_bot_ai', ai: bad })
    await waitFor(() => a.errs.length > 0, 2000)
    ok(a.errs.some((e) => e.code === 'bad_bot_ai'),
       `未知/路径串 \`${bad}\` → bad_bot_ai（实际 ${a.errs.map((e) => e.code).join('/') || '无错误'}）`)
    await sleep(80)
  }
  ok(a.room?.bot_ai === alt, '被拒的几次都没改动当前值')

  // ---- E) 牌局进行中 ----
  // 补齐四家（房主 + 乙 + 两个机器人）并开局
  a.send({ cmd: 'add_bot' })
  a.send({ cmd: 'add_bot' })
  await sleep(200)
  // 座位名要带上这一代（玩家得看得出在跟谁打）；默认 teacher 时不带后缀
  const botNames = (a.room?.seats || []).filter((s) => s && s.bot).map((s) => s.name)
  if (alt !== 'teacher') {
    ok(botNames.length === 2 && botNames.every((n) => n.includes(`·${alt}`)),
       `机器人座位名带上了这一代（${botNames.join(' / ')}）`)
  }
  a.send({ cmd: 'ready', ready: true })
  b.send({ cmd: 'ready', ready: true })
  await sleep(200)
  a.send({ cmd: 'start_game' })
  const playing = await waitFor(() => a.room?.playing === true, 5000)
  ok(playing, '牌局开始（room.playing = true）')
  if (playing) {
    const aiNow = a.room.bot_ai
    a.send({ cmd: 'set_bot_ai', ai: alt === 'teacher' ? (names[0] || 'teacher') : 'teacher' })
    await sleep(300)
    ok(a.room?.bot_ai === aiNow, '牌局进行中 set_bot_ai 被忽略（当前值不变）')
  } else {
    errors.push('  [FAIL] 没能开局，最后一条检查（进行中不可改）没验到')
  }
} catch (e) {
  errors.push(`  [FAIL] 异常：${e && e.stack ? e.stack : e}`)
} finally {
  a.close()
  b.close()
}

for (const n of notes) console.log(n)
for (const e of errors) console.log(e)
console.log(`\n检查项 ${notes.length + errors.length}，失败 ${errors.length}`)
console.log(errors.length ? 'BOT-AI FAIL' : 'BOT-AI PASS')
process.exit(errors.length ? 1 : 0)
