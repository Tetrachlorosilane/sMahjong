#!/usr/bin/env node
/**
 * 假服务端：用脚本化事件把 Qt 客户端推进到「可以和牌」的状态，
 * 用来验证 UI 是否正确显示 自摸 / 荣和 按钮（不跑真实牌局）。
 *
 *   node tools/mock-server.mjs <port> [turn|claim|kan|...]
 *
 *   turn  → 发一个 kind=turn 的 ask，options 含 discard + tsumo + riichi
 *   claim → 发一个 kind=claim 的 ask，options 含 ron + pon + pass
 *   kan   → 暗杠/加杠后从岭上摸牌：`dead_wall_left` 4 → 3 → 2（验「岭上 N 会不会减」）
 *   其余模式（river / agari / yakuman / twoturn / note / allmeld / hand*）见 AGENTS.md §4 L4。
 */
import net from 'node:net';

const PORT = Number(process.argv[2] || 10999);
const MODE = process.argv[3] || 'turn';

const HAND14 = ['1m', '1m', '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m', '9m', '9m', '9m'];

const srv = net.createServer((sock) => {
  let buf = '';
  const send = (o) => sock.write(JSON.stringify(o) + '\n');

  sock.setEncoding('utf8');
  sock.on('data', (chunk) => {
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      let cmd;
      try { cmd = JSON.parse(line); } catch { continue; }
      console.log('[mock] recv', ((Date.now() % 1000000) / 1000).toFixed(1), JSON.stringify(cmd).slice(0, 320));
      onCmd(cmd);
    }
  });

  function onCmd(cmd) {
    switch (cmd.cmd) {
      case 'hello':
        send({ ev: 'hello_ok', pid: 1, token: 'mock', name: cmd.name || 'mock', ver: 1 });
        break;
      case 'create_room':
        send({ ev: 'room_joined', room: 'MOCK', seat: 0 });
        send({ ev: 'room', id: 'MOCK', name: '假服务端', host: 1, playing: false,
               rules: { length: 'hanpu' },
               seats: [ { seat: 0, pid: 1, name: '测试玩家', ready: true, bot: false, score: 25000 },
                        { seat: 1, pid: 0, name: 'CPU-1', ready: true, bot: true, score: 25000 },
                        { seat: 2, pid: 0, name: 'CPU-2', ready: true, bot: true, score: 25000 },
                        { seat: 3, pid: 0, name: 'CPU-3', ready: true, bot: true, score: 25000 } ] });
        break;
      case 'ready':
        if (MODE === 'river') setTimeout(pushRiverScenario, 400);
        else if (MODE === 'kan') setTimeout(pushKanScenario, 400);
        else if (MODE === 'sticks') setTimeout(pushSticksScenario, 400);
        else if (MODE.startsWith('hand')) setTimeout(() => pushHandScenario(MODE), 400);
        else if (MODE === 'allmeld') setTimeout(pushAllMeldScenario, 400);
        else if (MODE === 'agari') setTimeout(pushAgariScenario, 400);
        else if (MODE === 'yakuman') setTimeout(pushYakumanScenario, 400);
        else if (MODE === 'twoturn') setTimeout(pushTwoTurnScenario, 400);
        else setTimeout(pushRound, 400);
        break;
      case 'action':
        console.log('[mock] 客户端动作:', JSON.stringify(cmd));
        break;
      default:
        break;
    }
  }

  // 模式 kan：**杠后从岭上摸牌**的账（报障：「杠后岭上牌并没有减少」）。
  // 关键在两处报文字段：
  //   - `dead_wall_left` 必须随**每次摸牌**下发（只在开局发一次的话，界面上的「岭上 N」整局不动）；
  //   - 岭上摸牌**不动** `tiles_left`（那张牌来自王牌，不是牌山）——
  //     所以光看「余牌」是发现不了岭上被摸走的。
  // 序列：开局 4 → 暗杠 + 新宝牌指示牌 → 岭上摸牌 3 → 再杠 → 岭上摸牌 2。
  function pushKanScenario() {
    const seatsInfo = [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                        { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ];
    send({ ev: 'game_start', rules: { length: 'hanpu' }, seats: seatsInfo,
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','1m','1m','1m','2m','3m','4m','5p','6p','7p','2s','3s','4s'],
           dora_indicators: ['5p'], tiles_left: 70, dead_wall_left: 4 });
    // 第一次暗杠（1m）+ 翻新宝牌指示牌 → 岭上摸牌（剩 3）
    setTimeout(() => send({ ev: 'meld', seat: 0, kind: 'ankan',
                            tiles: ['1m','1m','1m','1m'], from: 0, called_tile: '1m',
                            called_index: -1 }), 300);
    setTimeout(() => send({ ev: 'dora_reveal', dora_indicators: ['5p', '2z'] }), 500);
    // ⚠ 开杠会让服务端把牌山末尾的一张移进王牌（`liveEnd--`），所以 `tiles_left` 在**开杠时**减 1；
    //   而这张岭上牌本身来自王牌，**不再**消耗牌山 —— 于是"余牌"看不出岭上被摸走，"岭上"才看得出。
    setTimeout(() => send({ ev: 'draw', seat: 0, tiles_left: 69, rinshan: true, tile: '9s',
                            dead_wall_left: 3 }), 700);
    setTimeout(() => send({ ev: 'ask', ask_id: 11, seat: 0, kind: 'turn', deadline_ms: 60000,
                            tiles_left: 69,
                            options: [ { type: 'discard', tiles: ['2m','3m','9s'] },
                                       { type: 'kan', kans: [ { kind: 'kakan', tile: '5p' } ] } ] }), 900);
    // 第二次开杠（加杠）→ 岭上摸牌（剩 2）
    setTimeout(() => send({ ev: 'meld', seat: 0, kind: 'kakan',
                            tiles: ['5p','5p','5p','5p'], from: 1, called_tile: '5p',
                            called_index: -1 }), 1200);
    setTimeout(() => send({ ev: 'dora_reveal', dora_indicators: ['5p', '2z', '7s'] }), 1400);
    setTimeout(() => send({ ev: 'draw', seat: 0, tiles_left: 68, rinshan: true, tile: '1z',
                            dead_wall_left: 2 }), 1600);
    setTimeout(() => send({ ev: 'ask', ask_id: 12, seat: 0, kind: 'turn', deadline_ms: 60000,
                            tiles_left: 68,
                            options: [ { type: 'discard', tiles: ['2m','3m','9s','1z'] } ] }), 1800);
    console.log('[mock] kan 场景已下发（岭上 4 → 3 → 2）');
  }

  // 模式 sticks：**立直棒摆位**（报障：四家的棒全堆在盘底，看着像都是自家的）。
  // 开局就带 2 根**供託**（上一局留下的，不属于任何一家）→ 该摆在盘中央；
  // 随后四家依次立直 → 每家的棒要落在**各家自己那一侧**（上/下/左/右各一根）。
  function pushSticksScenario() {
    const seatsInfo = [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                        { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ];
    send({ ev: 'game_start', rules: { length: 'hanpu' }, seats: seatsInfo,
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 2 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','2m','3m','4m','5m','6m','7m','8m','9m','1p','2p','3p','4p'],
           dora_indicators: ['5p'], tiles_left: 60, dead_wall_left: 4 });
    const riichiAt = (seat, sticks, delay) => setTimeout(() => send({
      ev: 'riichi', seat, stick_index: sticks - 1, sticks,
      scores: [25000, 25000, 25000, 25000],
    }), delay);
    riichiAt(2, 3, 600);      // 対面
    riichiAt(1, 4, 900);      // 下家
    riichiAt(3, 5, 1200);     // 上家
    riichiAt(0, 6, 1500);     // 自家
    setTimeout(() => send({ ev: 'ask', ask_id: 21, seat: 0, kind: 'turn', deadline_ms: 60000,
                            tiles_left: 60,
                            options: [ { type: 'discard', tiles: ['1m','2m'] } ] }), 1800);
    console.log('[mock] sticks 场景已下发（供託 2 + 四家各立直一根）');
  }

  // 模式 twoturn：**跨小局的「第一巡」计时**复现。
  // 顺序严格照服务端：第1局首巡询问 → 和牌 → round_end → round_wait(5000) →
  //（客户端倒计时到点自动关闭结算并发 confirm）→ 第2局 round_start + 首巡询问。
  // 两次询问都声明 15000ms：第2局首巡若显示成 ~1 秒，就是客户端串了计时基准。
  function pushTwoTurnScenario() {
    const seatsInfo = [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                        { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ];
    send({ ev: 'game_start', rules: { length: 'hanpu' }, seats: seatsInfo,
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','2m','3m','4m','5m','6m','7m','8m','9m','1p','2p','3p','4p'],
           dora_indicators: ['5m'], tiles_left: 69, dead_wall_left: 4 });
    setTimeout(() => send({ ev: 'ask', ask_id: 1, seat: 0, kind: 'turn', deadline_ms: 15000,
                            base_ms: 5000, bank_ms: 10000, tiles_left: 69,
                            options: [ { type: 'discard', tiles: ['1m','2m'] } ] }), 300);
    setTimeout(() => send({ ev: 'agari', winner: 0, from: -1, tsumo: true,
                            hand: ['1m','2m','3m','5p','6p','7p','2s','3s','4s','5z','5z'],
                            winning_tile: '3m',
                            yaku: [ { code: 'riichi', han: 1 }, { code: 'pinfu', han: 1 },
                                    { code: 'tanyao', han: 1 } ],
                            dora_indicators: ['5p'], ura_indicators: ['9m'],
                            // 3 番 70 符 = 基本点 2240 → 封顶满贯，用来顺带验 `limit` 的
                            // ASCII 码（mangan）在客户端能翻成「满贯」。
                            han: 3, fu: 70, points: 2000, limit: 'mangan',
                            scores_after: [27000, 24400, 24400, 24200],
                            score_delta: [2000, -600, -600, -800] }), 2600);
    setTimeout(() => send({ ev: 'round_end', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
                            scores: [27000, 24400, 24400, 24200], agari: true,
                            abortive: false, reason: '', renchan: false, game_over: false }), 2900);
    setTimeout(() => {
      send({ ev: 'round_wait', ms: 5000 });
      console.log('[mock] round_wait(5000) —— 5 秒后下发下一局的首巡询问');
    }, 3100);
    setTimeout(() => {
      send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 2, honba: 0, riichi_sticks: 0 },
             seat: 0, dealer: 1, scores: [27000, 24400, 24400, 24200],
             hand: ['1m','1m','2m','3m','4m','5m','6m','7m','8m','9m','1p','2p','3p'],
             dora_indicators: ['5m','2p'], tiles_left: 60, dead_wall_left: 4 });
      send({ ev: 'ask', ask_id: 1, seat: 0, kind: 'turn', deadline_ms: 15000,
             base_ms: 5000, bank_ms: 10000, tiles_left: 60,
             options: [ { type: 'discard', tiles: ['1m','2m'] } ] });
      console.log('[mock] 第2局 round_start + 首巡 ask(15000ms) 已下发');
    }, 8300);
  }

  // 模式 hand-<N>：先做 N 副露，再摸一张。用来验证
  // 「摸牌/打牌不位移」「摸牌有独立槽位」「副露钉在右下角且把整块往左顶」。
  function pushHandScenario(mode) {
    const nMeld = (mode.match(/^hand-([0-9])$/) || [])[1];
    const meldCount = nMeld !== undefined ? Number(nMeld) : (mode.includes('meld') ? 1 : 0);
    const withDraw = mode.includes('draw') || nMeld !== undefined;
    // 每副露（碰）扣 3 张暗牌
    const pool = ['1m','2m','3m','4m','5m','6m','7m','8m','9m','1p','2p','3p','4p',
                  '5p','6p','7p','8p','9p','1s','2s','3s','4s','5s','6s','7s','8s','9s'];
    const hand = pool.slice(0, 13 - 3 * meldCount);
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand, dora_indicators: ['5p','1s'], tiles_left: 60, dead_wall_left: 4 });
    let delay = 400;
    const meldTiles = ['9s', '8s', '7s', '6s'];
    for (let i = 0; i < meldCount; i++) {
      const t = meldTiles[i];
      setTimeout(() => {
        send({ ev: 'discard', seat: 1, tile: t, tsumogiri: false, riichi: false, sideways: false });
        send({ ev: 'meld', seat: 0, kind: 'pon', tiles: [t, t, t], from: 1,
               called_tile: t, aka: [false, false, false], called_index: 0 });
      }, delay);
      delay += 450;
    }
    if (withDraw) {
      setTimeout(() => {
        send({ ev: 'draw', seat: 0, tiles_left: 59, rinshan: false, tile: '7z' });
      }, delay);
    }
    console.log('[mock] hand 场景已下发 mode=' + mode + ' 副露数=' + meldCount);
  }


  // 模式 allmeld：四家都有 1~2 副露 + 自己有摸牌，用来核对四角的重叠
  // 和牌结算：验证结算界面里的**牌面示意**（内嵌字体 + liga）。
  // 带一个吃副露，用来检查被鸣的那张是否渲染成横置。
  function pushAgariScenario() {
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','2m','3m','5p','6p','7p','2s','3s','4s','5z','5z'],
           dora_indicators: ['5p', '1s'], tiles_left: 40, dead_wall_left: 4 });
    setTimeout(() => send({ ev: 'meld', seat: 0, kind: 'chi',
                            tiles: ['3m','4m','5m'], from: 3, called_tile: '3m',
                            called_index: 0 }), 300);
    setTimeout(() => send({ ev: 'agari', winner: 0, from: -1, tsumo: true,
                            hand: ['1m','2m','3m','5p','6p','7p','2s','3s','4s','5z','5z'],
                            winning_tile: '3m',
                            yaku: [ { code: 'riichi', han: 1 },
                                    { code: 'menzen_tsumo', han: 1 } ],
                            dora_indicators: ['5p', '1s'], ura_indicators: ['9m', '3s'],
                            han: 2, fu: 40, points: 2000,
                            scores_after: [27000, 24400, 24400, 24200],
                            score_delta: [2000, -600, -600, -800] }), 1000);
    // 局间等待：服务端最多等 5 秒（或所有人确认）**然后**才开下一局。
    // 客户端应显示倒计时，并在到点时**自动关闭结算弹窗**（关闭即视为确认）。
    setTimeout(() => send({ ev: 'round_end', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
                            scores: [27000, 24400, 24400, 24200], agari: true,
                            abortive: false, reason: '', renchan: false, game_over: false }), 1300);
    setTimeout(() => {
      send({ ev: 'round_wait', ms: 5000 });
      console.log('[mock] round_wait(5000) 已下发，等待客户端 confirm');
    }, 1500);
  }

  // 模式 yakuman：**役满结算界面**（两倍役满）。
  // 用来定点确认「役满不写 0 番、写 n倍役满」这条：
  // 服务端对役满役上报的 han 是 13×倍数（老服务端曾是 0），界面必须按 yakuman 字段显示倍数。
  function pushYakumanScenario() {
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','1m','1m','1p','1p','1p','4p','4p','4p','3s','3s','3s','1z'],
           dora_indicators: ['5p'], tiles_left: 40, dead_wall_left: 4 });
    setTimeout(() => send({ ev: 'agari', winner: 0, from: -1, tsumo: true,
                            hand: ['1m','1m','1m','1p','1p','1p','4p','4p','4p','3s','3s','3s','1z','1z'],
                            winning_tile: '1z',
                            // 四暗刻单骑 = 两倍役满；逐役 han 是「13×倍数」的等价番数
                            yaku: [ { code: 'suuankou_tanki', han: 26, yakuman: 2 } ],
                            dora_indicators: ['5p'], ura_indicators: [],
                            han: 26, fu: 0, yakuman: 2, limit: 'yakuman',
                            base_points: 16000, dora: 0, aka: 0, ura: 0,
                            scores_after: [57000, 9000, 9000, 5000],
                            score_delta: [32000, -16000, -16000, -20000],
                            pao: { seat: -1 } }), 1000);
    setTimeout(() => send({ ev: 'round_end', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
                            scores: [57000, 9000, 9000, 5000], agari: true,
                            abortive: false, reason: '', renchan: true, game_over: false }), 1300);
    setTimeout(() => {
      send({ ev: 'round_wait', ms: 5000 });
      console.log('[mock] round_wait(5000) 已下发，等待客户端 confirm');
    }, 1500);
  }

  function pushAllMeldScenario() {
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','2m','3m','4m','5m','6m','7m'],
           dora_indicators: ['5p','1s'], tiles_left: 40, dead_wall_left: 4 });
    let d = 400;
    const meld = (seat, kind, tiles, from, called, aka) => {
      send({ ev: 'meld', seat, kind, tiles, from, called_tile: called, aka, called_index: 0 });
    };
    setTimeout(() => { meld(0, 'pon', ['9s','9s','9s'], 1, '9s', [false,false,false]); }, d); d += 350;
    setTimeout(() => { meld(0, 'pon', ['8s','8s','8s'], 3, '8s', [false,false,false]); }, d); d += 350;
    setTimeout(() => { meld(1, 'chi', ['3p','4p','5p'], 0, '4p', [false,false,false]); }, d); d += 350;
    setTimeout(() => { meld(2, 'pon', ['7z','7z','7z'], 1, '7z', [false,false,false]); }, d); d += 350;
    setTimeout(() => { meld(3, 'pon', ['5z','5z','5z'], 2, '5z', [false,false,false]); }, d); d += 350;
    setTimeout(() => { send({ ev: 'draw', seat: 0, tiles_left: 39, rinshan: false, tile: '7z' }); }, d);
    // 再让**别家**（下家 CPU-1）也刚摸一张：验证别家的牌背同样「手牌 + 单独一格摸牌」
    setTimeout(() => { send({ ev: 'draw', seat: 1, tiles_left: 38, rinshan: false, tile: '3m' }); }, d + 200);
    console.log('[mock] allmeld 场景已下发');
  }
  // 模式 river：脚本化「立直宣言牌被鸣走 → 横置顺延」的完整序列
  function pushRiverScenario() {
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 }, { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 }, { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: ['1m','2m','3m','4m','5m','6m','7m','8m','9m','1p','2p','3p','4p'],
           // 一局最多 5 张宝牌指示牌（宝牌 1 + 四个杠），这里直接拉到上限，
           // 用来验证「宝牌指示牌区容得下 5 张、且第 5 张不被对家的牌挡住」。
           dora_indicators: ['5m','2p','8s','3z','6m'], tiles_left: 60, dead_wall_left: 4 });
    const d = (tile, sideways) => ({ ev: 'discard', seat: 0, tile, tsumogiri: false,
                                     riichi: !!sideways, riichi_stick: !!sideways, sideways: !!sideways });
    setTimeout(() => {
      send(d('1m', false));
      send(d('2m', false));
      send(d('3m', true));            // 立直宣言牌（横置）
      send(d('4m', false));
      send({ ev: 'riichi', seat: 0, stick_index: 0, sticks: 1 });
      // 宣言牌（牌河下标 2）被下家碰走
      setTimeout(() => {
        send({ ev: 'meld', seat: 1, kind: 'pon', tiles: ['3m','3m','3m'], from: 0,
               called_tile: '3m', aka: [false,false,false], called_index: 2 });
        setTimeout(() => {
          send(d('5m', true));        // 顺延牌（横置）
          send(d('6m', false));
          // 对家（seat 2）刚摸一张 → 对家手牌块变成最宽的 13+1 格，
          // 这是「第 5 张宝牌指示牌会不会被对家的摸牌遮住」的最坏情况。
          send({ ev: 'draw', seat: 2, tiles_left: 55, rinshan: false, tile: '1z' });
          console.log('[mock] river 场景已下发');
        }, 500);
      }, 500);
    }, 500);
  }
  function pushRound() {
    send({ ev: 'game_start', rules: { length: 'hanpu' },
           seats: [ { seat: 0, name: '测试玩家', score: 25000 },
                    { seat: 1, name: 'CPU-1', score: 25000 },
                    { seat: 2, name: 'CPU-2', score: 25000 },
                    { seat: 3, name: 'CPU-3', score: 25000 } ],
           round: { bakaze: 'E', kyoku: 1, honba: 0 } });
    send({ ev: 'round_start', round: { bakaze: 'E', kyoku: 1, honba: 0, riichi_sticks: 0 },
           seat: 0, dealer: 0, scores: [25000, 25000, 25000, 25000],
           hand: HAND14, dora_indicators: ['5m'], tiles_left: 60, dead_wall_left: 4 });
    setTimeout(() => {
      send({ ev: 'draw', seat: 0, tiles_left: 59, rinshan: false, tile: '9m' });
      setTimeout(() => {
        if (MODE === 'claim') {
          send({ ev: 'discard', seat: 1, tile: '9m', tsumogiri: false, riichi: false });
          send({ ev: 'ask', ask_id: 2, seat: 0, kind: 'claim', deadline_ms: 60000,
                 from: 1, tile: '9m',
                 options: [ { type: 'ron' }, { type: 'pon' }, { type: 'pass' } ] });
        } else if (MODE === 'note') {
          send({ ev: 'ask', ask_id: 3, seat: 0, kind: 'turn', deadline_ms: 60000,
                 tiles_left: 59, win_note: 'no_yaku',
                 options: [ { type: 'discard', tiles: ['1m', '2m', '9m'] },
                            { type: 'riichi', tiles: ['1m'] } ] });
        } else {
          send({ ev: 'ask', ask_id: 1, seat: 0, kind: 'turn', deadline_ms: 60000,
                 tiles_left: 59, base_ms: 20000, bank_ms: 5000,
                 options: [ { type: 'discard', tiles: ['1m', '2m', '9m'] },
                            { type: 'riichi', tiles: ['1m'] },
                            { type: 'tsumo' },
                            { type: 'kan', kans: [ { kind: 'ankan', tile: '9m' } ] } ] });
        }
        console.log('[mock] 已下发 ask（mode=' + MODE + '）');
      }, 600);
    }, 600);
  }
});

srv.listen(PORT, '0.0.0.0', () => console.log(`[mock] 监听 0.0.0.0:${PORT}，模式 ${MODE}`));
