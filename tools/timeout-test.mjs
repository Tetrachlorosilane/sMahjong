#!/usr/bin/env node
/**
 * 服务端行为回归：
 *   1) 超时不出牌 → 服务端应代打「摸切」（discard.tsumogiri 必须恒为 true）
 *   2) 连接断开后房间内无真人 → 应立即回收（不等整局打完）
 *
 * 用法：node tools/timeout-test.mjs <host> <port>
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const THINK_MS = 900;          // 让服务端很快超时
const WATCH_MS = 12000;        // 观察窗口

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
let mySeat = -1;
let rounds = 0;
let autoDiscards = 0;
let badTsumogiri = 0;
let askedMe = 0;
const seenTiles = [];

const send = (o) => sock.write(JSON.stringify(o) + '\n');

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: '超时测试', ver: 1 }));
sock.on('data', (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    onEvent(ev);
  }
});

function onEvent(ev) {
  switch (ev.ev) {
    case 'hello_ok':
      send({ cmd: 'create_room', name: '超时测试房',
             rules: { length: 'tonpuu', thinking_ms: THINK_MS }, fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      send({ cmd: 'ready', ready: true });
      break;
    case 'round_start':
      rounds++;
      console.log(`[timeout-test] 第 ${rounds} 局开始，余牌 ${ev.tiles_left}`);
      break;
    case 'ask':
      if (ev.seat === mySeat) askedMe++;   // 故意不回答，等服务端代打
      break;
    case 'discard':
      if (ev.seat === mySeat) {
        autoDiscards++;
        seenTiles.push(ev.tile);
        if (ev.tsumogiri !== true) badTsumogiri++;
        console.log(`[timeout-test] 服务端代打: tile=${ev.tile} tsumogiri=${ev.tsumogiri}`);
      }
      break;
    default:
      break;
  }
}

sock.on('error', (e) => { console.error('[timeout-test] 连接错误', e.code); process.exit(1); });

setTimeout(() => {
  console.log(`[timeout-test] 询问次数=${askedMe} 代打次数=${autoDiscards} 非摸切次数=${badTsumogiri}`);
  let ok = true;
  if (askedMe === 0) { console.error('[timeout-test] 失败：从未被询问'); ok = false; }
  if (autoDiscards === 0) { console.error('[timeout-test] 失败：服务端从未代打'); ok = false; }
  if (badTsumogiri > 0) { console.error(`[timeout-test] 失败：有 ${badTsumogiri} 次代打不是摸切`); ok = false; }
  console.log(ok ? '[timeout-test] 超时摸切 PASS' : '[timeout-test] 超时摸切 FAIL');
  console.log('[timeout-test] 现在断开连接，观察服务端是否立即回收房间…');
  sock.destroy();
  setTimeout(() => process.exit(ok ? 0 : 1), 2500);
}, WATCH_MS);
