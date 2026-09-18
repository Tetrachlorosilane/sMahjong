#!/usr/bin/env node
/**
 * 换座回归（等待室）：座位表是**房间状态**，而等待室命令跑在各连接自己的线程上 ——
 * 这类"判据 + 改座位 + 广播"必须是一个原子步，否则会有两类问题（见 `Table.roomLock` 的注释）：
 *
 *   A) **被换走的那一家，自己的座位号没跟着改** → 点「准备」会把**别人**设成已准备，
 *      自己一直不准备、牌局永远开不了。（这是真实报障，探针实测确认过。）
 *   B) **两条换座 / 换座与开局交错** → `swapFieldsOnly` 逐字段交换被切开，
 *      出现两个座位同一个 pid、或某人凭空消失；极端情况是发牌过程中座位被换
 *      （按座位号发的牌会落到别人连接上）。
 *
 * 本脚本在真 socket 上验四件事：
 *   A) 换座后，**被换走的那一家**点准备 → 必须落在**自己**的座位上；
 *   B) 洗座后同理（每家都能把"我已准备"写到自己身上）；
 *   C) 并发 churn（4 家 × 多次换座/洗座）期间，房间快照里的 pid 集合恒为四家的一个**排列**
 *      （不重、不漏 —— 逐字段交换被切开的直接症状）；
 *   D) `playing=true` 之后发 take_seat 必须被忽略（座位表不动）。
 *
 *   node tools/seat-swap-test.mjs <host> <port>
 *
 * 期望：`SEAT-SWAP PASS`。
 */
import net from 'node:net'

const HOST = process.argv[2] || '127.0.0.1'
const PORT = Number(process.argv[3] || 10086)
const CHURN_ROUNDS = 60
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const errors = []
const notes = []

function makeClient(name) {
  const c = { name, buf: '', pid: 0, seat: -1, room: null, joined: null, asks: [], errs: [] }
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
      if (ev.ev === 'hello_ok') c.pid = ev.pid
      if (ev.ev === 'room_joined') { c.seat = ev.seat; c.joined = ev }
      if (ev.ev === 'room') c.room = ev
      if (ev.ev === 'ask') c.asks.push(ev)
      if (ev.ev === 'error') c.errs.push(ev.code || 'error')
    }
  })
  c.send = (o) => c.sock.write(JSON.stringify(o) + '\n')
  return c
}

const pidAt = (c, i) => (c.room?.seats?.[i]?.pid ?? null)
const seatOfPid = (c, pid) => {
  const a = c.room?.seats || []
  for (let i = 0; i < 4; i++) if (a[i] && a[i].pid === pid) return i
  return -1
}
const readyAt = (c, i) => !!c.room?.seats?.[i]?.ready
/** 房间快照里四家的 pid 是否恰好是 {pids} 的一个排列 */
function isPermutation(c, pids) {
  const got = (c.room?.seats || []).map((s) => (s ? s.pid : null)).filter((p) => p !== null)
  const want = [...pids].sort((a, b) => a - b)
  const have = [...got].sort((a, b) => a - b)
  return have.length === want.length && have.every((v, i) => v === want[i])
}

const A = makeClient('A'); const B = makeClient('B')
const C = makeClient('C'); const D = makeClient('D')
for (const c of [A, B, C, D]) c.send({ cmd: 'hello', name: c.name, ver: 1 })
await sleep(400)

A.send({ cmd: 'create_room', name: '换座回归', rules: { length: 'tonpuu' }, fill_bots: 0 })
await sleep(400)
const roomId = A.joined?.room
for (const c of [B, C, D]) { c.send({ cmd: 'join_room', room: roomId }); await sleep(250) }
await sleep(300)
const pids = [A.pid, B.pid, C.pid, D.pid]
notes.push(`四家 pid=${pids.join(',')} 座位=${pids.map((p) => seatOfPid(A, p)).join(',')}`)

// ── A) 换座后，被换走的那一家点准备必须落到自己身上 ───────────────────────
{
  const mySeat = seatOfPid(A, A.pid)
  const target = seatOfPid(A, B.pid)          // A 去抢 B 的座位 → 服务端语义是互换
  A.send({ cmd: 'take_seat', seat: target })
  await sleep(400)
  const bSeatNow = seatOfPid(A, B.pid)
  B.send({ cmd: 'ready', ready: true })
  await sleep(400)
  const bSeatAfter = seatOfPid(A, B.pid)
  if (!readyAt(A, bSeatAfter)) {
    errors.push(`A：换座后 B 点准备没落在自己的座位（B 在 #${bSeatAfter}，ready=${readyAt(A, bSeatAfter)}）`
      + `；别的座位 ready: ${[0, 1, 2, 3].map((i) => `${i}=${readyAt(A, i)}`).join(' ')}`)
  } else {
    notes.push(`A 通过：A 抢 #${target}（原座位 #${mySeat}）后，B 的准备落在自己 #${bSeatAfter}`)
  }
  B.send({ cmd: 'ready', ready: false })
  await sleep(200)
}

// ── B) 洗座后每家都能把"我已准备"写到自己身上 ──────────────────────────────
{
  A.send({ cmd: 'shuffle_seats' })
  await sleep(400)
  for (const c of [A, B, C, D]) { c.send({ cmd: 'ready', ready: true }); await sleep(150) }
  await sleep(300)
  for (const c of [A, B, C, D]) {
    const at = seatOfPid(A, c.pid)
    if (!readyAt(A, at)) {
      errors.push(`B：洗座后 ${c.name}（pid=${c.pid}，座位 #${at}）的准备没落到自己身上`)
    }
  }
  if (!errors.length) notes.push('B 通过：洗座后四家的「准备」都落在各自座位上')
  for (const c of [A, B, C, D]) { c.send({ cmd: 'ready', ready: false }); await sleep(120) }
}

// ── C) 并发 churn：pid 集合必须恒为四家的一个排列 ──────────────────────────
{
  let bad = 0
  let samples = 0
  for (let r = 0; r < CHURN_ROUNDS; r++) {
    for (const c of [A, B, C, D]) {
      if (Math.random() < 0.5) c.send({ cmd: 'take_seat', seat: Math.floor(Math.random() * 4) })
    }
    if (r % 5 === 0) A.send({ cmd: 'shuffle_seats' })
    await sleep(8)                                  // 故意不排队：让命令在网线上交叉
    for (const c of [A, B, C, D]) {                 // 每家收到的最新快照都必须是合法排列
      if (!c.room) continue
      samples++
      if (!isPermutation(c, pids)) bad++
    }
  }
  await sleep(500)
  for (const c of [A, B, C, D]) {
    if (c.room && !isPermutation(c, pids)) bad++
  }
  if (bad > 0) {
    errors.push(`C：并发换座期间出现 ${bad}/${samples} 次非法座位表（pid 重复或丢失）`)
  } else {
    notes.push(`C 通过：并发 churn ${CHURN_ROUNDS} 轮 × ${samples} 次快照，pid 始终是四家的排列`)
  }
}

// ── D) playing=true 之后 take_seat 必须被忽略 ──────────────────────────────
{
  for (const c of [A, B, C, D]) { c.send({ cmd: 'ready', ready: true }); await sleep(150) }
  A.send({ cmd: 'start_game' })
  // 等 playing=true 出现在房间事件里（开局后 take_seat 就该被忽略）
  let playing = false
  for (let i = 0; i < 60 && !playing; i++) {
    await sleep(100)
    playing = !!A.room?.playing
  }
  if (!playing) {
    errors.push('D：没能进入对局（4 家已准备，但 room 事件里 playing 一直是 false）')
  } else {
    const before = [0, 1, 2, 3].map((i) => pidAt(A, i))
    B.send({ cmd: 'take_seat', seat: (seatOfPid(A, B.pid) + 1) % 4 })
    await sleep(400)
    const after = [0, 1, 2, 3].map((i) => pidAt(A, i))
    if (before.join(',') !== after.join(',')) {
      errors.push(`D：牌局进行中 take_seat 竟然改了座位表：${before.join(',')} → ${after.join(',')}`)
    } else {
      notes.push('D 通过：牌局进行中 take_seat 被忽略（座位表不动）')
    }
  }
}

for (const c of [A, B, C, D]) c.sock.end()
for (const n of notes) console.log('  ' + n)
if (errors.length === 0) {
  console.log('SEAT-SWAP PASS')
  process.exit(0)
}
console.log(`SEAT-SWAP FAIL（${errors.length} 项）`)
for (const e of errors) console.log('  ✗ ' + e)
process.exit(1)
