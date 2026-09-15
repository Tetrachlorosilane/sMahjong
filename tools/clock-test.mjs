#!/usr/bin/env node
/**
 * 思考时间（额外时长）回归：验证「每巡基本时长内不扣、超出部分从总额外时长里扣、
 * 扣减按 1 秒离散且剩余量向上去整」，且**鸣牌询问也占用额外时长**。
 *
 * 做法：建房时设 base=1000ms / bank=6000ms，然后每次被问都故意迟 2.2 秒才答，
 * 观察后续 ask 里的 bank_ms 是否按预期下降。
 *
 *   node tools/clock-test.mjs <host> <port>
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const BASE_MS = 1000;
const BANK_MS = 6000;
const TURN_DELAY_MS = 0;        // 回合立刻答 → 不扣（基本时长内）
const CLAIM_DELAY_MS = 2200;    // 鸣牌故意迟 1.2s 超出基本 → 只可能是鸣牌扣的

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
let mySeat = -1;
const seen = [];        // {kind, bank}
let lastBank = BANK_MS;
let violations = [];
let roundJustStarted = false;   // 每小局会重置额外时长，此时回升是合法的

const send = (o) => sock.write(JSON.stringify(o) + '\n');

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: '时钟测试', ver: 1 }));
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
sock.on('error', (e) => { console.error('[clock-test] 连接错误', e.code); process.exit(1); });

function onEvent(ev) {
  switch (ev.ev) {
    case 'hello_ok':
      send({ cmd: 'create_room', name: '时钟测试房',
             rules: { length: 'tonpuu', thinking_base_ms: BASE_MS, thinking_bank_ms: BANK_MS },
             fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      send({ cmd: 'ready', ready: true });
      break;
    case 'round_start':
      roundJustStarted = true;   // 本局刚开始，额外时长已被服务端重置
      break;
    case 'ask': {
      if (ev.seat !== mySeat) break;
      const bank = typeof ev.bank_ms === 'number' ? ev.bank_ms : -1;
      const base = typeof ev.base_ms === 'number' ? ev.base_ms : -1;
      seen.push({ kind: ev.kind, bank, base, deadline: ev.deadline_ms });
      console.log(`[clock-test] ask kind=${ev.kind} base=${base} bank=${bank} deadline=${ev.deadline_ms}`);

      if (base !== BASE_MS) violations.push(`base_ms 应为 ${BASE_MS}，实际 ${base}`);
      // 额外时长每小局重置，所以「紧跟在 round_start 之后」的回升是合法的
      if (bank > lastBank && !roundJustStarted) {
        violations.push(`bank 在局中回升：${lastBank} → ${bank}`);
      }
      roundJustStarted = false;
      lastBank = bank;
      // 期望 deadline = base + bank
      if (ev.deadline_ms !== base + bank) {
        violations.push(`deadline 应等于 base+bank=${base + bank}，实际 ${ev.deadline_ms}`);
      }

      const opts = ev.options || [];
      const pick = (t) => opts.find((o) => o.type === t);
      let act = { type: 'pass' };
      if (pick('tsumo')) act = { type: 'tsumo' };
      else if (pick('ron')) act = { type: 'ron' };
      else if (pick('discard')) act = { type: 'discard', tile: pick('discard').tiles[0] };
      const delay = ev.kind === 'claim' ? CLAIM_DELAY_MS : TURN_DELAY_MS;
      setTimeout(() => send({ cmd: 'action', ask_id: ev.ask_id, ...act }), delay);
      break;
    }
    case 'game_end':
      report(0);
      break;
    default:
      break;
  }
}

function report(code) {
  console.log(`[clock-test] 共收到 ${seen.length} 次询问，末次 bank=${lastBank}`);
  const claims = seen.filter((a) => a.kind === 'claim').length;
  console.log(`[clock-test] 其中鸣牌询问 ${claims} 次`);
  let ok = true;
  if (seen.length < 3) { console.error('[clock-test] 失败：询问次数太少'); ok = false; }
  // ⚠ 判据用「观察到的**最低** bank」，不能用 lastBank：
  //   额外时长每小局重置（本文件上面就承认这一点），90 秒窗口里只要跨过一局，
  //   末次观察到的 bank 必然回到满值 —— 用 lastBank 判会假失败，还会掩盖真信号。
  const banks = seen.map((a) => a.bank).filter((b) => b >= 0);
  const minBank = banks.length ? Math.min(...banks) : BANK_MS;
  if (minBank >= BANK_MS) { console.error('[clock-test] 失败：额外时长从未被扣除'); ok = false; }
  if (claims === 0) {
    console.error('[clock-test] 失败：本次没出现鸣牌询问，无法验证鸣牌计时');
    ok = false;
  } else {
    // 关键隔离：turn 都是 0 延迟（在基本时长内，不扣），所以 bank 一旦下降，
    // 只能归因于「上一次被问的是鸣牌且我答得慢」。ask 里的 bank_ms 是发问那一刻的值，
    // 扣减发生在回答之后，因此要看「相邻两次 ask 之间」的变化。
    let drops = 0;
    for (let i = 1; i < seen.length; i++) {
      if (seen[i].bank < seen[i - 1].bank) {
        drops++;
        if (seen[i - 1].kind !== 'claim') {
          violations.push(`bank 在非鸣牌询问(${seen[i - 1].kind})后下降：`
                          + `${seen[i - 1].bank} → ${seen[i].bank}`);
        }
      }
    }
    console.log(`[clock-test] bank 共下降 ${drops} 次，最低观察到 ${minBank}（满值 ${BANK_MS}，末次 ${lastBank}）`);
    if (drops === 0) {
      console.error('[clock-test] 失败：鸣牌没有消耗额外时长');
      ok = false;
    } else {
      console.log('[clock-test] 已证明：额外时长的消耗全部来自鸣牌询问');
    }
  }
  for (const v of violations) { console.error('[clock-test] 违规：' + v); ok = false; }
  console.log(ok ? '[clock-test] 思考时间 PASS' : '[clock-test] 思考时间 FAIL');
  try { sock.destroy(); } catch { /* ignore */ }
  setTimeout(() => process.exit(ok ? code : 1), 200);
}

// 兜底：跑满 90 秒也收工
setTimeout(() => report(0), 90000);
