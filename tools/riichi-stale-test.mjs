#!/usr/bin/env node
/**
 * 「立直按钮连点」类回归：**作废的动作绝不能替玩家打牌**。
 *
 * 背景（玩家报障：「重复点击立直按钮，会出现未知的行为」）：
 *   客户端点了立直/出牌之后，询问栏直到服务端回事件为止都还是活的，
 *   于是连点两下会发出**两条**动作；而客户端组包时**没带 ask_id**，
 *   服务端就无法把第二条判成过期回包 —— 它会落到下一巡，被当成那一巡的答复。
 *   更糟的是 Round 里对 `type=="riichi"` 的处理：
 *       if (want >= 0 && canRiichi(turn, want)) { 宣言 }
 *       else { type = "discard"; }                  ← 立直不成立就**退化成普通打牌**
 *       if (!declareRiichi) { discardId = resolveTile(act, "tile", turn); ... }
 *   也就是：一条**不可能成立的立直宣言**（重复点击 / 过期回包）会把玩家指定的
 *   那张牌**直接打出去** —— 玩家没选过要打它。
 *
 * 本脚本用「不带 ask_id 的非法立直」精确复现这条路径：
 *   A) 在自己回合、**没有立直选项**时，发一条 {type:"riichi", tile:<非摸牌>}（不带 ask_id）。
 *      期望：服务端**不认**这张「宣言牌」，退回默认摸切（tsumogiri=true）。
 *      未修时：服务端把那张牌当普通打牌打掉（tsumogiri=false 且 tile == 我们发的牌）。
 *   C) 守卫：**带 ask_id** 的重复动作必须被服务端按过期回包丢弃 ——
 *      紧接着的那一巡若故意不答，实测等待必须仍等于声明的 deadline_ms
 *      （被顶掉的症状是 0ms 代打，同 firstturn-test 的 B 项）。
 *
 *   node tools/riichi-stale-test.mjs <host> <port> [每巡毫秒]
 *
 * 期望：A 与 C 全部通过。
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const THINK_MS = Number(process.argv[4] || 3000);
const HARD_LIMIT_MS = 240000;

const sock = net.createConnection({ host: HOST, port: PORT });
let buf = '';
let mySeat = -1;
let myDrawn = '';            // 最近一次 draw 事件里我摸到的牌
let turnIdx = 0;             // 我第几次被询问出牌
let phase = 'idle';          // idle | caseA | caseC-dup | caseC-wait
let caseA = null;            // { sentTile, ok }
let dupSentFor = -1;         // 已给第几次询问发过重复包
let waitStart = 0;
let waitDeclared = 0;
let caseC = null;            // { declaredMs, actualMs }
let answered = 0;

const send = (o) => sock.write(JSON.stringify(o) + '\n');

sock.setEncoding('utf8');
sock.on('connect', () => send({ cmd: 'hello', name: '立直连点', ver: 1 }));
sock.on('error', (e) => { console.error('[riichi-stale] 连接错误', e.code); process.exit(1); });
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

const optOf = (ev, t) => (ev.options || []).find((o) => o.type === t);

function onEvent(ev) {
  switch (ev.ev) {
    case 'hello_ok':
      send({ cmd: 'create_room', name: '立直连点房',
             rules: { length: 'tonpuu', thinking_ms: THINK_MS }, fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      send({ cmd: 'ready', ready: true });
      break;

    case 'round_start':
      myDrawn = '';                                    // 新的一局：旧摸牌作废
      break;

    case 'draw':
      // 只有摸牌那一家能拿到 tile（Round.broadcastDraw）；庄家第一巡不摸牌、拿不到，
      // 所以这时 myDrawn 为空 —— A 项必须等到「真的摸了一张」才取样，否则
      // 挑出的「宣言牌」可能正好就是摸到的那张，判定失去意义。
      if (ev.seat === mySeat && ev.tile) myDrawn = ev.tile;
      break;

    case 'game_end':
      finish();
      break;

    case 'ask': {
      if (ev.seat !== mySeat) break;
      if (ev.kind !== 'turn') {                       // 鸣牌询问：立刻跳过
        send({ cmd: 'action', ask_id: ev.ask_id, type: 'pass' });
        break;
      }
      turnIdx++;

      // ---- C：先应答正常的一手，再补一条**带 ask_id** 的重复动作 ----
      if (phase === 'idle' && turnIdx >= 2 && dupSentFor < 0) {
        const d = optOf(ev, 'discard');
        const tiles = (d && d.tiles) || [];
        if (tiles.length) {
          const answer = { cmd: 'action', ask_id: ev.ask_id, type: 'discard', tile: tiles[0] };
          send(answer);
          send(answer);                               // 连点第二下：同一 ask_id 的重复包
          dupSentFor = turnIdx;
          answered++;
          console.log(`[riichi-stale] 第 ${turnIdx} 次询问：发了一条重复动作（带 ask_id）`);
          break;
        }
      }

      // ---- C：下一巡故意不答，量真实等待 ----
      if (phase === 'idle' && dupSentFor > 0 && turnIdx === dupSentFor + 1) {
        phase = 'caseC-wait';
        waitDeclared = Number(ev.deadline_ms) || 0;
        waitStart = Date.now();
        console.log(`[riichi-stale] 第 ${turnIdx} 次询问：故意不答，声明 ${waitDeclared}ms —— 验证重复包有没有被顶掉…`);
        break;
      }

      // ---- A：没有立直选项时，发一条**不带 ask_id 的非法立直** ----
      // 必须知道本轮摸到的是哪张，才能挑一张「不是它」的牌当宣言牌；
      // 不知道就正常应答，等下一巡再取样。
      if (phase === 'idle' && !caseA && !optOf(ev, 'riichi') && myDrawn) {
        const d = optOf(ev, 'discard');
        const tiles = (d && d.tiles) || [];
        const fake = tiles.find((t) => t !== myDrawn);   // 挑一张「不是摸到的」牌
        if (fake) {
          caseA = { sentTile: fake, ok: null };
          phase = 'caseA';
          send({ cmd: 'action', type: 'riichi', tile: fake });   // ⚠ 故意不带 ask_id
          console.log(`[riichi-stale] 第 ${turnIdx} 次询问：无立直选项，发非法立直 tile=${fake}（摸到的是 ${myDrawn}），且不带 ask_id`);
          break;
        }
      }

      answered++;
      send({ cmd: 'action', ask_id: ev.ask_id, ...chooseAction(ev) });
      break;
    }

    case 'discard': {
      if (ev.seat !== mySeat) break;
      myDrawn = '';                                    // 本轮结束，摸牌信息作废

      // A：看服务端到底打了哪张
      if (phase === 'caseA' && caseA && caseA.ok === null) {
        const honored = (ev.tile === caseA.sentTile) && ev.tsumogiri !== true;
        caseA.ok = !honored;
        caseA.actualTile = ev.tile;
        caseA.tsumogiri = ev.tsumogiri === true;
        console.log(`[riichi-stale] A：服务端打出 ${ev.tile}（tsumogiri=${ev.tsumogiri}）` +
                    ` → ${honored ? '把非法宣言牌当普通打牌打掉了（FAIL）' : '退回了默认摸切（ok）'}`);
        phase = 'idle';
      }

      // C：量「没答却被代打」的间隔
      if (phase === 'caseC-wait') {
        const actual = Date.now() - waitStart;
        caseC = { declaredMs: waitDeclared, actualMs: actual };
        console.log(`[riichi-stale] C：第 ${turnIdx} 次询问实测等待 ${actual}ms（声明 ${waitDeclared}ms）`);
        phase = 'idle';
      }
      maybeFinish();
      break;
    }
    default:
      break;
  }
}

function chooseAction(ev) {
  if (ev.kind === 'turn') {
    if (optOf(ev, 'tsumo')) return { type: 'tsumo' };
    const d = optOf(ev, 'discard');
    if (d && d.tiles && d.tiles.length) return { type: 'discard', tile: d.tiles[0] };
    return { type: 'discard', tile: '1m' };
  }
  if (optOf(ev, 'ron')) return { type: 'ron' };
  return { type: 'pass' };
}

let done = false;

// 两个样本都到手就收工，不必打完整场（省时间，也避免机器人局间等待拖长）
function maybeFinish() {
  if (caseA && caseA.ok !== null && caseC) finish();
}

function finish() {
  if (done) return;
  done = true;
  console.log('');
  console.log('[riichi-stale] ===== 汇总 =====');
  let bad = 0;

  if (!caseA || caseA.ok === null) {
    console.log('  A  未取到样本（本局没碰上「自己回合且无立直选项」）—— 视为 FAIL');
    bad++;
  } else {
    if (!caseA.ok) bad++;
    console.log(`  A  非法立直宣言牌 ${caseA.sentTile}：服务端实际打出 ${caseA.actualTile}（tsumogiri=${caseA.tsumogiri}）` +
                `${caseA.ok ? '  ok' : '  ← FAIL：替玩家把那张牌打出去了'}`);
  }

  if (!caseC) {
    console.log('  C  未取到样本（没走到被重复包顶掉的那一巡）—— 视为 FAIL');
    bad++;
  } else {
    const short = caseC.actualMs < caseC.declaredMs * 0.8;
    if (short) bad++;
    console.log(`  C  重复包之后那一巡：声明 ${caseC.declaredMs}ms，实测 ${caseC.actualMs}ms` +
                `${short ? '  ← FAIL：重复包被当成了本巡答复（自动出牌）' : '  ok'}`);
  }

  console.log(`[riichi-stale] 已应答 ${answered} 次询问`);
  console.log(bad === 0
    ? '[riichi-stale] PASS：作废的立直没有替玩家打牌，重复包被服务端丢弃'
    : `[riichi-stale] FAIL：${bad} 项不达标`);
  sock.destroy();
  process.exit(bad === 0 ? 0 : 1);
}

setTimeout(() => {
  console.error(`[riichi-stale] 达到时间上限（${HARD_LIMIT_MS}ms）`);
  finish();
}, HARD_LIMIT_MS);
