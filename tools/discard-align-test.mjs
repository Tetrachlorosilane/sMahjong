#!/usr/bin/env node
/**
 * 出牌对齐回归（**幽灵手牌**）：在真 socket 上验证「哪张是刚摸到的」与「按牌码取牌」。
 *
 * 背景（详见 AGENTS §2.3-11 / `docs/PROTOCOL.md` §2.2、§3.3）：
 *   庄家第一巡的 14 张配牌是**已排序**下发的。客户端若按"最后一张 = 刚摸到的"去认摸牌位，
 *   就会与服务端的第 14 张认成两张不同的牌 —— 玩家点摸牌位时 `tsumogiri` 的牌码对不上，
 *   而旧服务端会**退回默认摸切**（打出刚摸到的那张，恰恰是玩家没点的那张）。
 *   两端于是各留一张不同的牌：**张数相同、内容差一张**，且不会自愈。
 *
 * 这个脚本用**脚本客户端**把三种情况各走一遍（不依赖机器人是否恰好摸到某张牌）：
 *
 *   A) 庄家第一巡：`round_start` 必须带 `drawn`（且 `drawn` 就在 `hand` 里、`hand` 14 张）；
 *   B) 故意**错报摸切**：发 `{tile: <暗手里的某张>, tsumogiri: true}`（牌码 ≠ `drawn`），
 *      服务端必须按**牌码**取牌 —— 打出的就是报的那张，而不是"摸切那张"
 *      （旧行为会打 `drawn`）；这条是幽灵手牌的**直接复现**；
 *   C) 正常摸切：收到 `draw` 之后用**刚摸到的那张的牌码** + `tsumogiri: true`，
 *      服务端必须原样打它并把事件标成 `tsumogiri: true`。
 *   D) **同牌码手切**（报障的回归）：手里本来就有一张与刚摸到的**同码**的牌时，用那个牌码
 *      发 `tsumogiri: false` —— 服务端必须打**手里那张**并把事件标成 `tsumogiri: false`。
 *      ⚠ 这条必须反复采样（每次适用都打），因为老服务端的错法是"按 id 排序后先撞上摸牌位那张"，
 *      两张同码牌谁在前是**随机的** —— 单次采样大约只有一半概率抓到它。
 *
 * 另有一条贯穿全场的不变式：**我发出的每一张牌 = 服务端 `discard` 事件里的那一张**
 * （哪怕我错报了摸切）—— 一旦不成立，两端手牌就会各差一张。
 *
 *   node tools/discard-align-test.mjs <host> <port>
 *
 * 期望：`DISCARD-ALIGN PASS`（A/B/C/D 全过 + 不变式零违例）。
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const HARD_LIMIT_MS = 240000;   // D（同码手切）要等"摸到的牌手里正好也有"的机会，120s 常常不够
const MAX_MY_DISCARDS = 8;      // 自己打够这么多张就收工（够覆盖 A/B/C/D）

const errors = [];
const notes = [];
let mySeat = -1;
let sentDiscard = null;          // 我这一巡发出的 {tile, tsumogiri}
const cases = { A: false, B: false, C: false, D: false };
// 自己那份"暗牌（不含摸牌位）+ 摸牌位"的账：D 要判断"手里有没有同码的牌"，
// 而服务端下发的 `discard` 选项是**按牌码去重**的（看不出有没有两张），只能自己记。
let myHand = [];
let myDrawn = '';
let dSamples = 0;                // D 采样了几次（非空转证据）
let dViolations = 0;             // 其中几次被打成了摸切
let myDiscardCount = 0;
let ended = false;

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
const send = (o) => sock.write(JSON.stringify(o) + '\n');

function finish(code) {
  if (ended) return;
  ended = true;
  const allCases = cases.A && cases.B && cases.C && cases.D;
  if (errors.length === 0 && allCases) {
    console.log('DISCARD-ALIGN PASS');
    for (const n of notes) console.log('  ' + n);
    process.exit(0);
  }
  for (const n of notes) console.log('  ' + n);
  if (!cases.A) errors.push('A 未覆盖：庄家第一巡的 round_start 没有拿到 `drawn`');
  if (!cases.B) errors.push('B 未覆盖：没走到「错报摸切」那一手');
  if (!cases.C) errors.push('C 未覆盖：没走到「正常摸切」那一手');
  if (!cases.D) errors.push('D 未覆盖：没走到「手里有同码牌的手切」那一手'
                            + `（采样 ${dSamples} 次，违例 ${dViolations} 次）`);
  console.log('DISCARD-ALIGN FAIL（' + errors.length + ' 项）');
  for (const e of errors) console.log('  ✗ ' + e);
  process.exit(1);
}

setTimeout(() => { errors.push('超时未覆盖全 A/B/C'); finish(1); }, HARD_LIMIT_MS).unref();

/** 从这一巡的选项里挑一张**牌码不等于 drawn** 的牌，用来错报摸切。 */
function pickMismatch(ask, drawn) {
  const d = (ask.options || []).find((o) => o.type === 'discard');
  const list = (d && d.tiles) || [];
  for (const t of list) if (t !== drawn) return t;
  return null;
}

function chooseAction(ev) {
  const opts = ev.options || [];
  const byType = (t) => opts.find((o) => o.type === t);
  if (ev.kind === 'turn') {
    if (byType('tsumo')) return { type: 'tsumo' };
    const d = byType('discard');
    if (!d || !d.tiles || d.tiles.length === 0) return { type: 'discard', tile: '1m' };
    // 本巡"刚摸到的那张"：闲家来自 draw 事件，庄家第一巡来自 round_start.drawn
    const basis = drawnCode || expectDealerDrawn || '';
    // D) **同码手切**：手里还有一张与刚摸到的牌同码的 → 报那个码 + tsumogiri:false。
    //    服务端必须打**手里那张**（事件 tsumogiri 必须是 false）。
    //    每次都采样（不设 `!cases.D`），因为老服务端的错法是"排序后先撞上摸牌位那张"，
    //    单次只有约一半概率抓到。
    if (basis && myHand.includes(basis) && d.tiles.includes(basis)) {
      return { type: 'discard', tile: basis, tsumogiri: false, __caseD: true };
    }
    // C) 正常摸切：牌码就是刚摸到的那张
    if (!cases.C && basis && d.tiles.includes(basis)) {
      return { type: 'discard', tile: basis, tsumogiri: true };
    }
    // B) 错报摸切：牌码故意与刚摸到的那张不同
    if (!cases.B && basis) {
      const bad = pickMismatch(ev, basis);
      if (bad) return { type: 'discard', tile: bad, tsumogiri: true };
    }
    return { type: 'discard', tile: d.tiles[0], tsumogiri: false };
  }
  if (byType('ron')) return { type: 'ron' };
  return { type: 'pass' };
}

let drawnCode = '';              // 我这一巡刚摸到的牌（来自 draw 事件）
let expectDealerDrawn = null;    // 庄家 round_start 里点名的 drawn

function onEvent(ev) {
  switch (ev.ev) {
    case 'hello_ok':
      notes.push(`握手成功 pid=${ev.pid}`);
      send({ cmd: 'create_room', name: '出牌对齐', rules: { length: 'tonpuu' }, fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      notes.push(`我坐在座位 ${mySeat}（房间 ${ev.room}）`);
      send({ cmd: 'ready', ready: true });
      break;
    case 'room': {
      if (ev.playing) break;
      const ready = (ev.seats || []).filter((s) => s && (s.ready || s.bot)).length;
      if (ready === 4 && ev.seats[mySeat] && !ev.seats[mySeat].ready) send({ cmd: 'ready', ready: true });
      break;
    }
    case 'round_start':
      if (ev.seat === mySeat) {
        const hand = ev.hand || [];
        const isDealer = ev.dealer === mySeat;
        const want = isDealer ? 14 : 13;
        if (hand.length !== want) {
          errors.push(`round_start 的手牌应为 ${want} 张（${isDealer ? '庄家' : '闲家'}），实际 ${hand.length}`);
        }
        // 自己那份账：暗牌（不含摸牌位）+ 摸牌位
        myHand = hand.slice();
        myDrawn = '';
        if (isDealer) {
          // A) 庄家必须点名 drawn，且它真的在 hand 里
          if (typeof ev.drawn !== 'string' || ev.drawn === '') {
            errors.push('庄家 round_start 没有 `drawn` 字段（客户端只能瞎猜 → 幽灵手牌）');
          } else if (!hand.includes(ev.drawn)) {
            errors.push(`round_start.drawn=${ev.drawn} 不在 hand 里`);
          } else {
            cases.A = true;
            expectDealerDrawn = ev.drawn;
            myDrawn = ev.drawn;
            const i = myHand.indexOf(ev.drawn);
            if (i >= 0) myHand.splice(i, 1);      // 摸牌位那张不算在暗牌里
            notes.push(`A 通过：庄家 round_start.drawn=${ev.drawn}（hand 里确实有它，共 ${hand.length} 张）`);
          }
        } else {
          expectDealerDrawn = null;
        }
      }
      drawnCode = '';
      break;
    case 'draw':
      if (ev.seat === mySeat) {
        drawnCode = ev.tile || '';
        myDrawn = drawnCode;
      }
      break;
    case 'discard':
      if (ev.seat === mySeat && sentDiscard) {
        const basis = sentDiscard.drawn;   // 我发这一手时认定的"刚摸到的那张"
        // 不变式：我报哪张，服务端就打哪张
        if (ev.tile !== sentDiscard.tile) {
          errors.push(`出牌不一致：我报 ${sentDiscard.tile}（tsumogiri=${sentDiscard.tsumogiri}），服务端打的是 ${ev.tile}`);
        } else if (sentDiscard.caseD) {
          // D) 同码手切：服务端必须打**手里那张**，事件必须标成 tsumogiri=false
          dSamples++;
          if (ev.tsumogiri === true) {
            dViolations++;
            errors.push(`D 违例：手里有同码牌的手切（报 ${sentDiscard.tile}，tsumogiri=false）`
                        + `被打成了摸切（事件 tsumogiri=true）`);
          } else {
            cases.D = true;
            notes.push(`D 采样 ${dSamples}：同码手切 ${sentDiscard.tile} → 事件 tsumogiri=false`);
          }
        } else if (sentDiscard.tsumogiri && sentDiscard.tile !== basis) {
          // B) 错报摸切：服务端必须按牌码取牌（而不是打"摸到的那张"）
          if (ev.tsumogiri === true) {
            errors.push(`错报摸切却被打成摸切：报 ${sentDiscard.tile}（本巡摸到 ${basis || '无'}），事件 tsumogiri=true`);
          } else {
            cases.B = true;
            notes.push(`B 通过：错报摸切（报 ${sentDiscard.tile}，本巡摸到 ${basis || '无'}）→ 服务端按牌码打出 ${ev.tile}，tsumogiri=false`);
          }
        } else if (sentDiscard.tsumogiri && sentDiscard.tile === basis) {
          // C) 正常摸切
          if (ev.tsumogiri !== true) {
            errors.push(`正常摸切（${sentDiscard.tile}）却被打成手切：事件 tsumogiri=false`);
          } else {
            cases.C = true;
            notes.push(`C 通过：正常摸切 ${sentDiscard.tile} → 事件 tsumogiri=true`);
          }
        }
        // 维护自己那份账（按**事件**说的那一摞走）
        if (ev.tsumogiri === true && myDrawn === ev.tile) {
          myDrawn = '';
        } else {
          const i = myHand.indexOf(ev.tile);
          if (i >= 0) myHand.splice(i, 1);
          if (myDrawn) {
            myHand.push(myDrawn);
            myDrawn = '';
          }
        }
        myDiscardCount++;
        sentDiscard = null;
        if (myDiscardCount >= MAX_MY_DISCARDS && cases.A && cases.B && cases.C && cases.D) finish(0);
      }
      break;
    case 'ask': {
      const act = chooseAction(ev);
      if (act.type === 'discard') {
        sentDiscard = {
          tile: act.tile,
          tsumogiri: act.tsumogiri === true,
          drawn: drawnCode || expectDealerDrawn || '',
          caseD: act.__caseD === true,
        };
      }
      const { __caseD, ...wire } = act;      // 自用标记不下发
      send({ cmd: 'action', ask_id: ev.ask_id, ...wire });
      break;
    }
    case 'game_end':
      finish(0);
      break;
    default:
      break;
  }
}

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: '出牌对齐', ver: 1 }));
sock.on('error', (e) => { console.error('[discard-align] 连接错误', e.code); process.exit(1); });
sock.on('data', (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i);
    buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    onEvent(ev);
  }
});
