#!/usr/bin/env node
/**
 * 训练数据集校验：核对 `--selfplay --out <dir>` 产出的 JSONL 是否自洽。
 *
 * 为什么需要它：训练数据是**离线**消费的，服务端的自检碰不到它。一旦格式悄悄变了
 * （少一个字段、backfill 没补上、观测里混进隐藏信息、动作不在合法集里），
 * 后果不是报错而是"训练出一个莫名其妙的策略"，而且极难回溯。
 * 所以这里用**独立实现**（不是把 Java 的断言翻译一遍）逐行核对，
 * 顺便充当 `docs/PROTOCOL.md` 「训练接口」一节的**可执行契约**。
 *
 * 用法：node tools/selfplay-check.mjs <dir> [--quiet]
 *   <dir> 里应有 g*.jsonl（每场一个）与可选的 summary.json。
 * 退出码：0 = 通过；1 = 有不一致。
 */
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
const quiet = process.argv.includes('--quiet');
if (!dir) {
  console.error('用法: node tools/selfplay-check.mjs <轨迹目录> [--quiet]');
  process.exit(2);
}
if (!fs.existsSync(dir)) {
  console.error(`目录不存在: ${dir}`);
  process.exit(2);
}

/** 观测的字段白名单 —— 与 mahjong/ai/Observation.java 的 toJson 必须一致。 */
const OBS_KEYS = [
  'v', 'seat', 'kind', 'hand', 'hand_red', 'drawn', 'player_draws', 'menzen', 'self_riichi',
  'furiten', 'melds', 'discards', 'dora_indicators', 'riichi', 'ippatsu', 'scores', 'round',
  'tiles_left', 'dead_wall_left', 'total_discards', 'kan_count', 'any_call', 'visible',
  'haitei', 'houtei', 'rinshan', 'from', 'called_tile', 'win_note', 'legal',
].sort();

const DECISION_KEYS = new Set(['type', 'game', 'hand_no', 'hand', 'step', 'seat', 'policy',
  'kind', 'legal', 'chosen', 'chosen_index', 'obs', 'hand_delta', 'hand_winner', 'hand_loser',
  'hand_agari', 'final_scores', 'placement']);

const problems = [];
const stats = { games: 0, hands: 0, decisions: 0, turn: 0, claim: 0, byKind: {} };
const add = (file, line, msg) => problems.push(`${path.basename(file)}:${line} ${msg}`);

const files = fs.readdirSync(dir).filter((f) => /^g\d+\.jsonl$/.test(f))
  .sort((a, b) => parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10));
if (files.length === 0) {
  console.error(`目录里没有 g*.jsonl：${dir}`);
  process.exit(2);
}

const placementOf = (scores) => {
  const idx = [0, 1, 2, 3].sort((a, b) => (scores[a] !== scores[b]
    ? scores[b] - scores[a] : a - b));
  const place = new Array(4);
  idx.forEach((seat, rank) => { place[seat] = rank + 1; });
  return place;
};

for (const f of files) {
  const file = path.join(dir, f);
  const raw = fs.readFileSync(file, 'utf8');
  const lines = raw.split('\n').filter((l) => l.trim() !== '');
  let decisions = [];
  let hands = [];
  let game = null;
  let lastHandNo = -1;
  let lastStep = -1;
  const stepsByHand = new Map();

  lines.forEach((line, i) => {
    const ln = i + 1;
    let row;
    try {
      row = JSON.parse(line);
    } catch (e) {
      add(file, ln, `不是合法 JSON：${e.message}`);
      return;
    }
    if (row.type === 'decision') decisions.push({ row, ln });
    else if (row.type === 'hand') hands.push({ row, ln });
    else if (row.type === 'game') {
      if (game) add(file, ln, '出现了第二条 game 行');
      game = { row, ln };
    } else add(file, ln, `未知行类型：${row.type}`);
  });

  // ---- 决策行
  for (const { row, ln } of decisions) {
    const extra = Object.keys(row).filter((k) => !DECISION_KEYS.has(k));
    if (extra.length) add(file, ln, `决策行有未登记字段：${extra.join(',')}`);
    const obs = row.obs;
    if (!obs) { add(file, ln, '缺少 obs'); continue; }
    const badKeys = Object.keys(obs).filter((k) => !OBS_KEYS.includes(k));
    if (badKeys.length) add(file, ln, `观测里有未登记字段（防泄漏白名单）：${badKeys.join(',')}`);
    const missKeys = OBS_KEYS.filter((k) => !(k in obs));
    if (missKeys.length) add(file, ln, `观测缺少字段：${missKeys.join(',')}`);
    if (obs.seat !== row.seat) add(file, ln, `obs.seat=${obs.seat} 与 seat=${row.seat} 不一致`);
    if (obs.kind !== row.kind) add(file, ln, `obs.kind=${obs.kind} 与 kind=${row.kind} 不一致`);
    if (JSON.stringify(obs.legal) !== JSON.stringify(row.legal)) {
      add(file, ln, 'obs.legal 与行上的 legal 不一致');
    }
    // 合法动作集：非空、无重复、chosen 在其中且下标对得上
    if (!Array.isArray(row.legal) || row.legal.length === 0) add(file, ln, 'legal 为空');
    else {
      if (new Set(row.legal).size !== row.legal.length) add(file, ln, 'legal 里有重复动作');
      const at = row.legal.indexOf(row.chosen);
      if (at < 0) add(file, ln, `chosen=${row.chosen} 不在合法动作集里`);
      else if (at !== row.chosen_index) add(file, ln, `chosen_index=${row.chosen_index} 实际应为 ${at}`);
    }
    // 动作键形状
    if (!/^(discard|riichi):[0-9][mpsz](\/tsumogiri)?$|^kan:(ankan|kakan|daiminkan):[0-9][mpsz]$|^chi:[0-9][mpsz]\+[0-9][mpsz]$|^(tsumo|ron|pon|pass|kyuushu)$/.test(row.chosen)) {
      add(file, ln, `chosen 键形状可疑：${row.chosen}`);
    }
    // 手牌张数：13 - 3×副露 + (自家回合 ? 1 : 0)
    const myMelds = obs.melds[row.seat] || [];
    const handSum = obs.hand.reduce((a, b) => a + b, 0);
    const wantSum = 13 - 3 * myMelds.length + (row.kind === 'turn' ? 1 : 0);
    if (handSum !== wantSum) {
      add(file, ln, `暗牌张数=${handSum}，应为 ${wantSum}（副露 ${myMelds.length}，kind=${row.kind}）`);
    }
    // visible 必须等于"四家牌河 + 四家副露 + 宝牌指示牌"的计数
    const vis = new Array(34).fill(0);
    const kindOf = (code) => {
      const n = parseInt(code[0] === '0' ? '5' : code[0], 10);
      const s = code[1];
      if (s === 'z') return 27 + n - 1;
      return (s === 'm' ? 0 : s === 'p' ? 9 : 18) + n - 1;
    };
    for (const ds of obs.discards) for (const c of ds) vis[kindOf(c)]++;
    for (const ms of obs.melds) for (const m of ms) for (const c of m.tiles) vis[kindOf(c)]++;
    for (const c of obs.dora_indicators) vis[kindOf(c)]++;
    if (JSON.stringify(vis) !== JSON.stringify(obs.visible)) {
      add(file, ln, 'visible 与"牌河+副露+宝牌"的计数对不上');
    }
    // hand_red 必须与 hand 不矛盾（赤五只在 5m/5p/5s 上，且该牌种必须有牌）
    for (let k = 0; k < 34; k++) {
      if (obs.hand_red[k] && obs.hand[k] === 0) add(file, ln, `hand_red[${k}] 为真但手里没有该牌种`);
      if (obs.hand_red[k] && ![4, 13, 22].includes(k)) add(file, ln, `hand_red[${k}] 不在赤五牌种上`);
    }
    // 记号单调性
    if (row.hand_no < lastHandNo) add(file, ln, `hand_no 回退：${row.hand_no} < ${lastHandNo}`);
    if (row.hand_no !== lastHandNo) { lastStep = -1; lastHandNo = row.hand_no; }
    if (row.step <= lastStep) add(file, ln, `step 未严格递增：${row.step} <= ${lastStep}`);
    lastStep = row.step;
    // backfill
    for (const k of ['hand_delta', 'final_scores', 'placement']) {
      if (!(k in row)) add(file, ln, `缺少回填字段 ${k}`);
    }
    stats.decisions++;
    if (row.kind === 'turn') stats.turn++; else stats.claim++;
    stats.byKind[row.chosen.split(':')[0]] = (stats.byKind[row.chosen.split(':')[0]] || 0) + 1;
    stepsByHand.set(row.hand_no, (stepsByHand.get(row.hand_no) || 0) + 1);
  }

  // ---- 小局行
  let prev = null;
  if (hands.length === 0) add(file, hands[0]?.ln || 1, '没有任何小局结算行');
  for (const { row, ln } of hands) {
    stats.hands++;
    if (row.delta.length !== 4 || row.scores_after.length !== 4) add(file, ln, 'delta/scores_after 长度不为 4');
    const sum = row.delta.reduce((a, b) => a + b, 0);
    if (sum % 1000 !== 0) add(file, ln, `delta 之和 ${sum} 不是 1000 的整数倍（立直棒/点数账不对）`);
    if (prev) {
      const want = prev.scores_after.map((v, i) => v + row.delta[i]);
      if (JSON.stringify(want) !== JSON.stringify(row.scores_after)) {
        add(file, ln, `scores_after 与上一局 + delta 不一致`);
      }
    }
    if (row.winner >= 0 && row.delta[row.winner] <= 0) add(file, ln, '和了者的收支不是正的');
    if (row.loser >= 0 && row.delta[row.loser] >= 0) add(file, ln, '放铳者的收支不是负的');
    if (row.agari && row.winner < 0) add(file, ln, 'agari=true 但没有 winner');
    if (!row.agari && row.winner >= 0) add(file, ln, 'agari=false 却有 winner');
    if (row.tsumo && row.loser >= 0) add(file, ln, '自摸却有 loser');
    prev = row;
  }
  if (game) {
    stats.games++;
    if (game.row.decisions !== decisions.length) {
      add(file, game.ln, `game.decisions=${game.row.decisions}，实际决策行 ${decisions.length}`);
    }
    if (game.row.hands !== hands.length) {
      add(file, game.ln, `game.hands=${game.row.hands}，实际小局行 ${hands.length}`);
    }
    if (prev && JSON.stringify(prev.scores_after) !== JSON.stringify(game.row.final_scores)) {
      add(file, game.ln, '最后一局的 scores_after 与终局分数不一致');
    }
    const place = placementOf(game.row.final_scores);
    if (JSON.stringify(place) !== JSON.stringify(game.row.placement)) {
      add(file, game.ln, `placement=${game.row.placement} 与按分数推出的 ${place} 不一致`);
    }
    if (new Set(game.row.placement).size !== 4) add(file, game.ln, 'placement 不是 1..4 的排列');
  } else add(file, lines.length, '没有 game 结算行');

  // ---- 决策数与小局数对得上（每小局至少有 4 次决策）
  for (const [handNo, n] of stepsByHand) {
    if (n < 4) add(file, 1, `第 ${handNo} 小局只有 ${n} 次决策（不可能少于 4）`);
  }
}

// ---- summary.json 与逐场核对
const summaryPath = path.join(dir, 'summary.json');
if (fs.existsSync(summaryPath)) {
  const sum = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
  if (sum.games !== stats.games) problems.push(`summary.games=${sum.games}，实际 ${stats.games}`);
  if (sum.hands !== stats.hands) problems.push(`summary.hands=${sum.hands}，实际 ${stats.hands}`);
  if (sum.per_game && sum.per_game.length !== stats.games) {
    problems.push(`summary.per_game 有 ${sum.per_game.length} 条，实际 ${stats.games} 场`);
  }
  for (const g of sum.per_game || []) {
    const p = g.placement;
    if (p.reduce((a, b) => a + b, 0) !== 10) problems.push(`summary.per_game[${g.game}] 顺位之和 != 10`);
  }
} else if (!quiet) {
  console.log('（没有 summary.json，跳过汇总核对）');
}

if (!quiet) {
  console.log(`轨迹目录：${dir}`);
  console.log(`  场数 ${stats.games} / 小局 ${stats.hands} / 决策 ${stats.decisions}`
    + `（自家回合 ${stats.turn}，鸣牌 ${stats.claim}）`);
  console.log('  动作分布：' + Object.entries(stats.byKind)
    .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}=${v}`).join(' '));
}
if (problems.length) {
  console.error(`\n✗ 发现 ${problems.length} 处不一致（只显示前 20 条）：`);
  for (const p of problems.slice(0, 20)) console.error('  ' + p);
  console.error('DATASET FAIL');
  process.exit(1);
}
console.log('DATASET PASS');
