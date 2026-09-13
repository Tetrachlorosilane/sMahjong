#!/usr/bin/env node
/**
 * 报文编码回归：**报文明明会带中文**（役种名、limit、流局 reason、玩家名、聊天都是原样文本），
 * 所以这条链路必须钉死 UTF-8，而且截断不能把字符切坏。
 *
 * 本脚本走**真 socket**，把中文 / 繁体 / 日文 / BMP 之外的字符（代理对）发过去再要回来：
 *
 *   A) `hello.name` → 服务端在 `hello_ok` 与 `room` 的座位上原样回显；
 *   B) `create_room.name` → `room.name` 原样回显；
 *   C) `chat.text` → 服务端广播回来的 `chat.text` 逐字相同（含 𠮷 U+20BB7、🀄 U+1F004）；
 *   D) **截断边界**：220 个码点、第 200 个正好是 emoji —— 服务端应按"字符"截到 200，
 *      且**绝不能**把代理对切成半个（切了 UTF-8 编码会变成 '?' / U+FFFD）。
 *      旧实现用 substring 按 UTF-16 码元切，正好切在这里。
 *
 *   node tools/utf8-test.mjs [host] [port]
 *
 * 期望：A–D 全部通过。
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const HARD_LIMIT_MS = 20000;

// 各种"难缠"的字符：简体 / 繁体 / 日文 / 全角符号 / BMP 外（代理对）
const NAME = '立直麻将・𠮷🀄';
const ROOM = '東風戦・测试房🀄';
const TEXT = '日本語・简体・繁體・𠮷・🀄・①②③';

const results = [];
let done = false;

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
const send = (o) => sock.write(JSON.stringify(o) + '\n');

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: NAME, ver: 1 }));
sock.on('error', (e) => { console.error('[utf8] 连接错误', e.code); process.exit(1); });
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

/** 有没有被编码坏掉：U+FFFD（替换字符）或孤立代理 */
function broken(s) {
  if (s.includes('\uFFFD')) return 'U+FFFD（替换字符）';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF) {          // 高代理：后面必须跟低代理
      const n = s.charCodeAt(i + 1);
      if (!(n >= 0xDC00 && n <= 0xDFFF)) return '孤立的高代理';
      i++;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return '孤立的低代理';
    }
  }
  return null;
}

function onEvent(ev) {
  switch (ev.ev) {
    case 'hello_ok':
      // A1：hello_ok 回显名字
      check('A1 hello_ok.name 原样回显', ev.name === NAME, `${JSON.stringify(ev.name)}`);
      send({ cmd: 'create_room', name: ROOM, rules: { length: 'tonpuu' }, fill_bots: 3 });
      break;

    case 'room_joined':
      mySeat = ev.seat;
      break;

    case 'room': {
      // A2：房间里自己的座位名
      const me = (ev.seats || []).find((s) => s.seat === mySeat);
      check('A2 room.seats[我].name 原样回显', me && me.name === NAME,
            me ? JSON.stringify(me.name) : '没找到自己的座位');
      // B：房间名
      check('B room.name 原样回显', ev.name === ROOM, JSON.stringify(ev.name));
      // C：聊天（中文 + 代理对）
      send({ cmd: 'chat', text: TEXT });
      // D：截断边界（第 200 个码点正好是 emoji）
      longText = 'a'.repeat(199) + '🀄' + 'b'.repeat(20);
      send({ cmd: 'chat', text: longText });
      break;
    }

    case 'chat': {
      if (ev.text === TEXT) {
        check('C chat.text 原样往返（含 𠮷/🀄）', true, '');
      } else if (ev.text.startsWith('a')) {
        const cps = [...ev.text].length;
        check('D1 截断按**字符**算：应为 200 个码点', cps === 200, `实际 ${cps}`);
        check('D2 结尾仍是完整 emoji（代理对没被切半）', ev.text.endsWith('🀄'),
              `实际结尾 ${JSON.stringify(ev.text.slice(-4))}`);
        check('D3 截断结果没有被编码坏掉', broken(ev.text) === null, broken(ev.text) || '');
        finish();
      }
      break;
    }
    default:
      break;
  }
}

let mySeat = -1;
let longText = '';

function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`[utf8] ${ok ? 'ok  ' : 'FAIL'} ${name}${ok || !detail ? '' : '  ← ' + detail}`);
}

function finish() {
  if (done) return;
  done = true;
  console.log('');
  const bad = results.filter((r) => !r.ok);
  // 全链路没有被编码坏掉（对所有回显做个总检查）
  console.log(`[utf8] 检查 ${results.length} 项，失败 ${bad.length} 项`);
  console.log(bad.length === 0
    ? '[utf8] PASS：报文全程 UTF-8，中文/代理对原样往返，截断按字符且不切坏'
    : `[utf8] FAIL：${bad.map((b) => b.name).join(' / ')}`);
  sock.destroy();
  process.exit(bad.length === 0 ? 0 : 1);
}

setTimeout(() => {
  console.error(`[utf8] 达到时间上限（${HARD_LIMIT_MS}ms），已收 ${results.length} 项`);
  finish();
}, HARD_LIMIT_MS);
