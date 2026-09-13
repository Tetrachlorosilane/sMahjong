#!/usr/bin/env node
/**
 * 计时/局间回归：两件事一起测（都是玩家实测报障过的）：
 *
 *   A) **点掉结算确认键后，下一局第一巡会被自动出牌**
 *      —— 局间确认 `{cmd:"confirm"}` 是**没有 ask_id** 的消息。若它没被局间等待消费掉，
 *         就会被下一巡的 `awaitAction()` 当成「本巡的答复」返回；出牌循环再把它
 *         （没有 `type`）默认成 `"discard"` → 玩家没动就被摸切。
 *      本脚本模拟「玩家点确认」：收到 `round_wait` 后 200ms 发 confirm，然后检查
 *      ① 下一局是否**很快**开始（说明 confirm 被正确消费，而不是干等满 5 秒）
 *      ② 下一局第一巡是否**仍然等满**声明的 deadline（说明它没被那条 confirm 顶掉）
 *
 *   B) 每局第一巡的实际等待 = 声明的 deadline_ms（只读代码定不了性，只能实测）
 *
 *   node tools/firstturn-test.mjs <host> <port> [期限毫秒]
 *
 * 期望：A①② 与 B 全部成立。任何一项不成立都会 FAIL 并打印实测值。
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const THINK_MS = Number(process.argv[4] || 3000);   // 固定每巡时长（base=THINK_MS, bank=0）
const CONFIRM_DELAY_MS = 200;   // 模拟玩家看完结算后点「确定」
const WANT_ROUNDS = 3;          // 量够 3 局就收工
const HARD_LIMIT_MS = 180000;   // 兜底上限（一局可能打很久）

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
let mySeat = -1;
let roundNo = 0;
let turnIdxInRound = 0;      // 本局第几次 turn 询问（1 = 第一巡）
let pendingFirst = null;     // 正在测量的「本局第一手」
const measurements = [];     // { round, declaredMs, actualMs }  ← B
const transitions = [];      // { round, waitedMs }              ← A①
let waitAt = 0;              // 收到 round_wait 的时刻
let confirmedAt = 0;
let answered = 0;

const send = (o) => sock.write(JSON.stringify(o) + '\n');

function chooseAction(ev) {
  const opts = ev.options || [];
  const byType = (t) => opts.find((o) => o.type === t);
  if (ev.kind === 'turn') {
    if (byType('tsumo')) return { type: 'tsumo' };
    const d = byType('discard');
    if (d && d.tiles && d.tiles.length) return { type: 'discard', tile: d.tiles[0] };
    return { type: 'discard', tile: '1m' };
  }
  if (byType('ron')) return { type: 'ron' };
  return { type: 'pass' };
}

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: '首巡计时', ver: 1 }));
sock.on('error', (e) => { console.error('[firstturn] 连接错误', e.code); process.exit(1); });
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
      send({ cmd: 'create_room', name: '首巡计时房',
             rules: { length: 'tonpuu', thinking_ms: THINK_MS }, fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      send({ cmd: 'ready', ready: true });
      break;
    case 'round_wait':
      // 模拟玩家点「确定」：局间等待里发一条**没有 ask_id** 的 confirm
      waitAt = Date.now();
      setTimeout(() => {
        confirmedAt = Date.now();
        send({ cmd: 'confirm' });
        console.log(`[firstturn] 已发 confirm（模拟点确认键）`);
      }, CONFIRM_DELAY_MS);
      break;
    case 'round_start':
      roundNo++;
      turnIdxInRound = 0;
      pendingFirst = null;
      if (waitAt > 0 && confirmedAt > 0) {
        const waited = Date.now() - confirmedAt;
        transitions.push({ round: roundNo, waitedMs: waited });
        console.log(`[firstturn] 第 ${roundNo} 局在 confirm 后 ${waited}ms 开始`);
        waitAt = 0;
        confirmedAt = 0;
      }
      break;
    case 'game_end':
      finish();   // 牌局可能先于 3 局结束（有人被飞/东风战打完）
      break;
    case 'ask': {
      if (ev.seat !== mySeat) break;
      if (ev.kind !== 'turn') {                       // 鸣牌询问：立刻放过，不参与测量
        send({ cmd: 'action', ask_id: ev.ask_id, type: 'pass' });
        break;
      }
      turnIdxInRound++;
      if (turnIdxInRound === 1 && pendingFirst === null) {
        // 本局第一手：故意不答，等服务端代打
        pendingFirst = { round: roundNo, declaredMs: Number(ev.deadline_ms) || 0, t: Date.now() };
        console.log(`[firstturn] 第 ${roundNo} 局第一巡：声明 ${pendingFirst.declaredMs}ms —— 故意不答，实测服务端等多久…`);
      } else {
        answered++;
        send({ cmd: 'action', ask_id: ev.ask_id, ...chooseAction(ev) });
      }
      break;
    }
    case 'discard':
      if (pendingFirst && ev.seat === mySeat && ev.tsumogiri === true) {
        const actualMs = Date.now() - pendingFirst.t;
        measurements.push({ round: pendingFirst.round, declaredMs: pendingFirst.declaredMs, actualMs });
        console.log(`[firstturn] 第 ${pendingFirst.round} 局第一巡：实测等待 ${actualMs}ms（声明 ${pendingFirst.declaredMs}ms）`);
        pendingFirst = null;
        if (measurements.length >= WANT_ROUNDS) finish();
      }
      break;
    default:
      break;
  }
}

let done = false;
function finish() {
  if (done) return;
  done = true;
  console.log('');
  console.log('[firstturn] ===== 汇总 =====');
  let bad = 0;

  // A①：confirm 是否被局间等待正确消费（下一局应**很快**开始，而不是干等满 5 秒）
  for (const t of transitions) {
    const slow = t.waitedMs > 2500;
    if (slow) bad++;
    console.log(`  A① 第 ${t.round} 局：confirm 后 ${t.waitedMs}ms 开始 ${slow ? '  ← 太慢：confirm 没被消费' : '  ok'}`);
  }

  // B：每局第一巡的实际等待必须等于声明值（绝不能被那条 confirm 顶掉）
  for (const m of measurements) {
    const tooShort = m.actualMs < m.declaredMs * 0.8;
    if (tooShort) bad++;
    console.log(`  B  第 ${m.round} 局第一巡：声明 ${m.declaredMs}ms，实测 ${m.actualMs}ms ${tooShort ? '  ← 偏短：被残留消息顶掉（自动出牌）' : '  ok'}`);
  }

  console.log(`[firstturn] 已应答 ${answered} 次询问`);
  console.log(bad === 0
    ? '[firstturn] PASS：confirm 被正确消费，且每局第一巡都拿到了完整时长'
    : `[firstturn] FAIL：${bad} 项不达标`);
  sock.destroy();
  process.exit(bad === 0 ? 0 : 1);
}

setTimeout(() => {
  // 牌局没打完就收工：不算失败（只要已经量到的都达标），但明确提示样本数
  console.error(`[firstturn] 达到时间上限（${HARD_LIMIT_MS}ms）：量到 ${measurements.length} 局`);
  if (measurements.length > 0) finish();
  else process.exit(2);
}, HARD_LIMIT_MS);
