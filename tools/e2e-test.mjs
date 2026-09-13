#!/usr/bin/env node
/**
 * 端到端联调脚本：以真实 TCP 客户端身份连接服务端，开房 + 3 个机器人，打完一场东风战。
 * 用途：验证 NDJSON 协议、房间流程、对局推进、结算事件。
 *
 * 用法：node tools/e2e-test.mjs [host] [port]
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
// 注意：服务端机器人每步约 800ms、局间 1200ms，一整场东风战通常要 4~8 分钟，
// 所以默认给 10 分钟。想快点可设 E2E_TIMEOUT_MS，或临时调小服务端的
// Table.botDelayMs / roundDelayMs。
const TIMEOUT_MS = Number(process.env['E2E_TIMEOUT_MS'] || 10 * 60 * 1000);

let mySeat = -1;
let sock;
let buf = '';
const counts = { ask: 0, discard: 0, draw: 0, roundStart: 0, agari: 0, ryuukyoku: 0, roundEnd: 0 };
const seen = new Set();
let finished = false;

// —— 报文 ASCII 审计 ——
// 协议里所有**词汇性文本**（役种/打点/流局原因/错误/牌码/事件名/type）都必须是 ASCII 码，
// 中文只在客户端语言文件里（docs/PROTOCOL.md §0）。例外只有三类**用户数据**：
// 玩家名 `name`、房间名 `room.name`、聊天 `chat.text`（以及老服务端的 `msg`）。
// 这条审计的作用：以后谁不小心把中文文案塞回报文里，e2e 会直接报出来，
// 而不是等到某个非 UTF-8 环境上才发现乱码。
const ASCII_EXEMPT_KEYS = new Set(['name', 'text', 'msg']);
const nonAscii = [];

// —— 岭上/王牌账（杠后从岭上摸牌的那 4 张）——
// 只在**报文**里能验证：岭上摸牌不动牌山（livePos/liveEnd 都不动），所以 `tiles_left` 看不出它；
// 必须靠每次摸牌下发的 `dead_wall_left`。不变量见 onEvent 的 draw 分支。
const rinshanErrors = [];
let rinshanDraws = 0;          // 本局已摸走的岭上牌数（每局重置）
let rinshanTotal = 0;          // 全场累计（收尾报告用）
let lastDeadWallLeft = 4;

function auditAscii(node, path) {
  if (typeof node === 'string') {
    if (/[^\x00-\x7F]/.test(node))
      nonAscii.push(`${path} = ${JSON.stringify(node)}`);
    return;
  }
  if (Array.isArray(node)) {
    node.forEach((v, i) => auditAscii(v, `${path}[${i}]`));
    return;
  }
  if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) {
      if (ASCII_EXEMPT_KEYS.has(k)) continue;
      auditAscii(v, path ? `${path}.${k}` : k);
    }
  }
}

function send(obj) {
  sock.write(JSON.stringify(obj) + '\n');
}

// —— 简易客户端策略：能自摸就自摸、能开杠就开杠、能荣和就荣和、否则打第一张合法牌
//   （**主动开杠**是为了让 L3 也真的走到「杠 → 从岭上摸牌」那条路：服务端机器人从不
//    开杠，脚本客户端不吃杠的话，这条路径在真 socket 上一次都到不了）
function chooseAction(ev) {
  const opts = ev.options || [];
  const byType = (t) => opts.find((o) => o.type === t);
  if (ev.kind === 'turn') {
    if (byType('tsumo')) return { type: 'tsumo' };
    const kan = byType('kan');
    if (kan && kan.kans && kan.kans.length) {
      const k = kan.kans[0];
      return { type: 'kan', kind: k.kind, tile: k.tile };
    }
    const d = byType('discard');
    if (d && d.tiles && d.tiles.length) return { type: 'discard', tile: d.tiles[0] };
    return { type: 'discard', tile: '1m' };
  }
  if (byType('ron')) return { type: 'ron' };
  if (byType('kan')) return { type: 'kan' };     // 大明杠
  return { type: 'pass' };
}

function onEvent(ev) {
  seen.add(ev.ev);
  auditAscii(ev, ev.ev || '?');
  switch (ev.ev) {
    case 'hello_ok':
      console.log(`[e2e] 握手成功 pid=${ev.pid}`);
      send({ cmd: 'create_room', name: 'E2E 测试房', rules: { length: 'tonpuu' }, fill_bots: 3 });
      break;
    case 'room_joined':
      mySeat = ev.seat;
      console.log(`[e2e] 入座 seat=${ev.seat} room=${ev.room}`);
      send({ cmd: 'ready', ready: true });
      break;
    case 'room':
      if (!ev.playing) {
        const ready = (ev.seats || []).filter((s) => s && (s.ready || s.bot)).length;
        if (ready === 4 && ev.seats[mySeat] && !ev.seats[mySeat].ready) {
          send({ cmd: 'ready', ready: true });
        }
      }
      break;
    case 'game_start':
      console.log('[e2e] 开局');
      break;
    case 'round_start':
      counts.roundStart++;
      // 每局重置岭上账：`dead_wall_left` 回到 4（服务端在 round_start 里报的就是它）
      rinshanDraws = 0;
      lastDeadWallLeft = 4;
      if (ev.dead_wall_left !== undefined && ev.dead_wall_left !== 4) {
        rinshanErrors.push(`第 ${counts.roundStart} 局开局岭上应为 4，实际 ${ev.dead_wall_left}`);
      }
      console.log(`[e2e] 第 ${counts.roundStart} 局 ${ev.round.bakaze}${ev.round.kyoku}局 ${ev.round.honba}本场  手牌=${ev.hand.join(' ')}`);
      break;
    case 'draw':
      counts.draw++;
      // —— 岭上/王牌账（报障：「杠后从岭上摸牌，但岭上牌并没有减少」）——
      // 岭上摸牌**不动** `tiles_left`（那张牌来自王牌，不是牌山），所以这个账只能看
      // `dead_wall_left`：它必须**随每次摸牌**下发，且小局内只减不增；
      // 每次 `rinshan=true` 的摸牌要正好减 1（4→3→2→1）。
      if (ev.dead_wall_left === undefined) {
        rinshanErrors.push(`draw 事件缺 dead_wall_left（tiles_left=${ev.tiles_left} rinshan=${ev.rinshan}）`);
      } else {
        if (ev.rinshan) {
          rinshanDraws++;
          rinshanTotal++;
        }
        const want = 4 - rinshanDraws;
        if (ev.rinshan && ev.dead_wall_left !== want) {
          rinshanErrors.push(`第 ${counts.roundStart} 局第 ${rinshanDraws} 次岭上摸牌报 ${ev.dead_wall_left}，应为 ${want}`);
        }
        if (ev.dead_wall_left > lastDeadWallLeft) {
          rinshanErrors.push(`dead_wall_left 变大了：${lastDeadWallLeft} → ${ev.dead_wall_left}`);
        }
        lastDeadWallLeft = ev.dead_wall_left;
      }
      break;
    case 'discard':
      counts.discard++;
      break;
    case 'ask':
      counts.ask++;
      if (ev.seat === mySeat) {
        const act = chooseAction(ev);
        send({ cmd: 'action', ask_id: ev.ask_id, ...act });
      }
      break;
    case 'agari': {
      counts.agari++;
      const y = (ev.yaku || []).map((x) => (x.yakuman ? `${x.code}(${x.yakuman}倍役满)` : `${x.code}${x.han}`)).join(' ');
      console.log(`[e2e] 和了 seat=${ev.winner} ${ev.tsumo ? '自摸' : '荣和'} ${y} ${ev.yakuman ? '' : ev.han + '番'}${ev.fu}符 → ${JSON.stringify(ev.score_delta)}`);
      break;
    }
    case 'ryuukyoku':
      counts.ryuukyoku++;
      console.log(`[e2e] 流局 ${ev.reason} 听牌=${JSON.stringify(ev.tenpai)}`);
      break;
    case 'round_end':
      counts.roundEnd++;
      console.log(`[e2e] 局终 分数=${JSON.stringify(ev.scores)}`);
      break;
    case 'game_end': {
      console.log('[e2e] ===== 终局 =====');
      for (const f of ev.final) {
        console.log(`  ${f.rank}位 ${f.name} ${f.score}点 精算${f.point}`);
      }
      const sum = ev.scores.reduce((a, b) => a + b, 0);
      if (sum !== 100000) {
        console.error(`[e2e] 失败：点数和 ${sum} != 100000`);
        finish(1);
        return;
      }
      const need = ['hello_ok', 'room', 'game_start', 'round_start', 'draw', 'discard', 'ask', 'round_end', 'game_end'];
      const missing = need.filter((n) => !seen.has(n));
      if (missing.length) {
        console.error(`[e2e] 失败：缺少事件 ${missing.join(',')}`);
        finish(1);
        return;
      }
      if (nonAscii.length) {
        console.error(`[e2e] 失败：报文里出现了非 ASCII 的词汇性文本 ${nonAscii.length} 处`
                      + `（中文只应出现在 name/text/msg 这三类用户数据里）`);
        for (const v of nonAscii.slice(0, 20)) console.error(`        ${v}`);
        finish(1);
        return;
      }
      console.log(`[e2e] 报文 ASCII 审计通过（用户数据 name/text 除外）`);
      if (rinshanErrors.length) {
        console.error(`[e2e] 失败：岭上/王牌账不对（${rinshanErrors.length} 处）`);
        for (const v of rinshanErrors.slice(0, 20)) console.error(`        ${v}`);
        finish(1);
        return;
      }
      console.log(`[e2e] 岭上账通过：全场共 ${rinshanTotal} 次岭上摸牌（无杠时为 0，属正常）`);
      console.log(`[e2e] 统计: ${JSON.stringify(counts)}`);
      console.log('[e2e] E2E PASS');
      finish(0);
      break;
    }
    case 'error':
      console.error(`[e2e] 服务端错误 ${ev.code}${ev.arg ? ' ' + ev.arg : ''}`
                    + `${ev.msg ? ' ' + ev.msg : ''}`);
      break;
    default:
      break;
  }
}

function finish(code) {
  if (finished) return;
  finished = true;
  try { sock.end(); } catch { /* ignore */ }
  setTimeout(() => process.exit(code), 100);
}

sock = net.createConnection({ host: HOST, port: PORT }, () => {
  console.log(`[e2e] 已连接 ${HOST}:${PORT}`);
  send({ cmd: 'hello', name: 'E2E-人类', ver: 1 });
});

sock.setEncoding('utf8');
sock.on('data', (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let ev;
    try {
      ev = JSON.parse(line);
    } catch (e) {
      console.error('[e2e] JSON 解析失败:', line.slice(0, 200));
      finish(1);
      return;
    }
    onEvent(ev);
  }
});

sock.on('error', (e) => {
  console.error('[e2e] 连接错误:', e.message);
  finish(1);
});

setTimeout(() => {
  console.error(`[e2e] 超时（${TIMEOUT_MS}ms），统计 ${JSON.stringify(counts)}`);
  finish(1);
}, TIMEOUT_MS);
