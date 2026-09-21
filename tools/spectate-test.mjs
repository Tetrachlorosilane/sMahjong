#!/usr/bin/env node
/**
 * 观战回归（对局中入局）：**观战必须有明确语义**，不能是"被放行进一个未定义状态"。
 *
 * 服务端本来就有观战这条路（`Table.spectators`，人数上限 `MAX_SPECTATORS`），但**客户端**
 * 以前根本没有 `spectate` 分支：它被 `onEvent` 排除在模型之外，紧接着的 `state`（`seat:-1`）
 * 又被 `qBound(0, -1, 3)` 夹成 0 —— 观战者看到的是"坐在东家、手里一张牌都没有"
 * （报障：「进入未定义的所谓观战状态」）。客户端那一半由 `client --selftest` 的
 * 「观战」组钉住；这里验**服务端**那一半（真 socket）：
 *
 *   A) 对局中 `join_room` → `{ev:"spectate"}` + 一份**公开快照**（`seat = -1`、`spectate = true`）；
 *   B) 快照带 `dealer` / `drawn_seat`（半场进入要一次摆对牌桌），且**不带**任何人的暗牌与振听；
 *   C) 观战者**跟着牌局走**：持续收到 `discard`/`draw` 等公开事件，且 `draw` 不带 `tile`
 *      （per-seat 的 `round_start`/`draw` 到不了 seat = -1，服务端另有 `sendSpectators` 补公开版）；
 *   D) 观战者**不能操作**：发 `action` 被拒。
 *
 *   node tools/spectate-test.mjs <host> <port>
 *
 * 期望：`SPECTATE PASS`。⚠ 依赖"对局进行中"这个时机：拿不到样本时退出码 2（不是失败）。
 */
import net from 'node:net'

const HOST = process.argv[2] || '127.0.0.1'
const PORT = Number(process.argv[3] || 10086)
const ROOM_NAME = '观战测试房'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const errors = []
const checks = []
const check = (ok, what) => {
  checks.push({ ok, what })
  if (!ok) errors.push(what)
  console.log(`${ok ? '[ok]  ' : '[fail]'} ${what}`)
}

const PUB = ['discard', 'draw', 'meld', 'riichi', 'dora_reveal', 'agari', 'ryuukyoku', 'round_end']

function makeClient(name, autoAnswer = false) {
  const c = {
    name,
    buf: '',
    pid: 0,
    seat: -1,
    rooms: [],
    spectate: null,
    state: null,
    states: 0,
    public: [],
    errs: [],
    asks: 0,
  }
  c.sock = net.createConnection({ host: HOST, port: PORT })
  c.sock.setEncoding('utf8')
  // 握手：服务端要 `hello` 之后才认这条连接（`hello_ok` 回来才算接上）
  c.sock.on('connect', () => c.send({ cmd: 'hello', name, ver: 1 }))
  c.sock.on('data', (chunk) => {
    c.buf += chunk
    let i
    while ((i = c.buf.indexOf('\n')) >= 0) {
      const line = c.buf.slice(0, i)
      c.buf = c.buf.slice(i + 1)
      if (!line.trim()) continue
      let ev
      try {
        ev = JSON.parse(line)
      } catch {
        continue
      }
      if (ev.ev === 'hello_ok') c.pid = ev.pid
      if (ev.ev === 'rooms') c.rooms = ev.rooms || []
      if (ev.ev === 'room_joined') c.seat = ev.seat
      if (ev.ev === 'spectate') c.spectate = ev
      if (ev.ev === 'state') {
        c.state = ev
        c.states++
      }
      if (ev.ev === 'error') c.errs.push(ev.code || 'error')
      if (PUB.includes(ev.ev)) c.public.push(ev)
      if (ev.ev === 'ask' && autoAnswer) c.answer(ev)
    }
  })
  // 脚本客户端必须**自己应答**，否则它那一巡会一直等到思考时间结束（牌局停住，
  // 观战测试就永远等不到"对局进行中"）。口径与 e2e-test 的 chooser 一致。
  c.answer = (ask) => {
    c.asks++
    const opt = (t) => (ask.options || []).find((o) => o.type === t)
    const cmd = { cmd: 'action', ask_id: ask.ask_id }
    if (opt('tsumo')) return c.send({ ...cmd, type: 'tsumo' })
    if (opt('ron')) return c.send({ ...cmd, type: 'ron' })
    if (opt('kyuushu')) return c.send({ ...cmd, type: 'pass' })
    if (opt('discard')) {
      const tiles = opt('discard').tiles || []
      if (tiles.length) return c.send({ ...cmd, type: 'discard', tile: tiles[0] })
    }
    return c.send({ ...cmd, type: 'pass' })
  }
  c.send = (o) => c.sock.write(JSON.stringify(o) + '\n')
  c.close = () => {
    try {
      c.sock.destroy()
    } catch {
      /* 忽略 */
    }
  }
  return c
}

async function waitFor(fn, ms, step = 100) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    if (fn()) return true
    await sleep(step)
  }
  return fn()
}

async function main() {
  // 房主**要占一个座位**：只建房不入座的话它一条 per-seat 报文都收不到（`round_start` 是按座位发的），
  // 也就等不到"开局"。补 3 个机器人 ⇒ 四家齐、房主自己坐剩下那个座位。
  const host = makeClient('房主', true)
  await waitFor(() => host.pid > 0, 5000)
  host.send({ cmd: 'create_room', name: ROOM_NAME, rules: { length: 'tonpuu' }, fill_bots: 3 })
  await waitFor(() => host.seat >= 0, 5000)
  check(host.seat >= 0, `房主拿到了座位（seat=${host.seat}）`)
  host.send({ cmd: 'ready', ready: true })
  // 四家 ready 之前 `start_game` 会被拒 → 反复尝试（与 claim-priority-test 同一套做法）
  const starter = setInterval(() => host.send({ cmd: 'start_game' }), 500)
  const started = await waitFor(() => host.public.length > 0, 20000, 200)
  clearInterval(starter)
  if (!started) {
    console.log('[skip] 20 秒内没等到开局（服务端没起/太慢？）—— 不算失败')
    host.close()
    process.exit(2)
  }
  host.send = host.send.bind(host)
  // 让牌局先走两巡，确保是"对局进行中"入局
  await waitFor(() => host.public.filter((e) => e.ev === 'discard').length >= 2, 10000, 100)

  // ---- A) 对局中入局 → 观战 ----
  const viewer = makeClient('观战者')
  await sleep(300)
  viewer.send({ cmd: 'list_rooms' })
  await waitFor(() => viewer.rooms.length > 0, 5000)
  const room = viewer.rooms.find((r) => r.name === ROOM_NAME) || viewer.rooms[0]
  if (!room) {
    console.log('[skip] 房间列表里没有拿到房间（拿不到样本）')
    viewer.close()
    host.close()
    process.exit(2)
  }
  viewer.send({ cmd: 'join_room', room: room.id })

  check(await waitFor(() => viewer.spectate !== null, 6000),
    '对局中入局会收到 spectate 事件（明确语义，不是静默放行）')
  check(await waitFor(() => viewer.state !== null, 6000),
    'spectate 之后立刻补一份 state 快照（半场进入要能摆对牌桌）')
  // ⚠ 局间（`Table.currentRound == null`）入局时那一份是 `phase:"idle"` —— 这是**正常**的：
  //   服务端在**每小局开始**会再补一份 `playing` 快照（`sendRoundStart` → `sendSpectators`）。
  //   所以这里等"playing 的那一份"，而不是赌入局那一瞬间正好在小局中间。
  const gotPlaying = await waitFor(
    () => viewer.state !== null && viewer.state.phase === 'playing', 25000, 200)
  check(gotPlaying,
    '入局后能拿到 phase=playing 的公开快照（局间入局则等下一局开始补齐）')
  if (gotPlaying) {
    const s = viewer.state
    check(s.seat === -1, `快照 seat = -1（实际 ${s.seat}）`)
    check(s.spectate === true, '快照 spectate = true（客户端据此进"无座位"模式）')
    check(Array.isArray(s.hand) && s.hand.length === 0,
      '观战快照**不带**任何人的暗牌（hand 为空）')
    check(Array.isArray(s.furiten) && s.furiten.every((v) => v === false),
      '观战快照四家振听恒为 false（临时振听等价于"他听牌了"）')
    check(typeof s.dealer === 'number', '快照带 dealer（自风/亲家显示要用）')
    check(typeof s.drawn_seat === 'number',
      '快照带 drawn_seat（谁手里 14 张 —— 半场进入各家张数靠它）')
    check(s.phase === 'playing', `快照 phase = playing（实际 ${s.phase}）`)
    check(Array.isArray(s.scores) && s.scores.length === 4, '快照带四家点数')
    check(Array.isArray(s.discards) && s.discards.length === 4, '快照带四家牌河')
  }

  // ---- C) 观战者跟着牌局走 ----
  const before = viewer.public.length
  await waitFor(() => viewer.public.length > before, 20000, 200)
  check(viewer.public.length > before,
    `观战者持续收到公开事件（新增 ${viewer.public.length - before} 条）`)
  const kinds = [...new Set(viewer.public.map((e) => e.ev))].sort()
  check(kinds.includes('discard'), `观战者收到 discard（收到过：${kinds.join('/') || '无'}）`)
  const draws = viewer.public.filter((e) => e.ev === 'draw')
  check(draws.length > 0, '观战者收到 draw（否则牌桌整局不动）')
  check(draws.every((e) => e.tile === undefined),
    '观战者收到的 draw **不带** tile（看不到别人摸到什么牌）')
  check(viewer.public.every((e) => e.seat === undefined || e.seat >= 0),
    '公开事件里的 seat 都是真实座位（观战者不伪装成某一家）')
  // 新一局也要有公开快照（否则观战者的牌桌停在上一局的残局）
  const statesBefore = viewer.states
  const sawNewRound = await waitFor(
    () => viewer.public.some((e) => e.ev === 'round_end') && viewer.states > statesBefore,
    60000,
    200)
  if (viewer.public.some((e) => e.ev === 'round_end')) {
    check(sawNewRound, '小局结束后观战者收到新一局的公开快照（跨局不残局）')
  } else {
    console.log('[i] 60 秒内没走完一小局，跳过"跨局快照"这一条')
  }

  // ---- D) 观战者不能操作 ----
  const errsBefore = viewer.errs.length
  viewer.send({ cmd: 'action', type: 'discard', tile: '1m' })
  check(await waitFor(() => viewer.errs.length > errsBefore, 3000),
    `观战者的 action 被拒绝（错误码 ${viewer.errs.slice(-1)[0] || '无'}）`)

  viewer.close()
  host.close()
  console.log()
  console.log(`通过 ${checks.filter((c) => c.ok).length}/${checks.length} 项`)
  if (errors.length === 0) {
    console.log('SPECTATE PASS')
    process.exit(0)
  }
  console.log('SPECTATE FAIL')
  for (const e of errors) console.log('  ✗ ' + e)
  process.exit(1)
}

main().catch((e) => {
  console.error('探针异常：', e)
  process.exit(1)
})
