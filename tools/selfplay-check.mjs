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
  'furiten', 'melds', 'discards', 'dora_indicators', 'riichi', 'ippatsu', 'scores', 'kuitan',
  'round', 'tiles_left', 'dead_wall_left', 'total_discards', 'kan_count', 'any_call', 'visible',
  // obs v3（见 PROTOCOL §8.2）：事件流 + 立直巡数。`v < 3` 的老轨迹没有它们，只对 v≥3 强制要求。
  'events', 'riichi_turn',
  'haitei', 'houtei', 'rinshan', 'from', 'called_tile', 'win_note', 'legal',
].sort();

/** obs v3 才有的字段（老轨迹不许拿它们当"缺字段"）。 */
const OBS_KEYS_V3 = ['events', 'riichi_turn'];

/** 事件流的类型白名单与"哪些字段必带"（契约见 PROTOCOL §8.2，两边一起改）。 */
const EVT_TYPES = ['discard', 'meld', 'kan', 'riichi', 'dora_flip'];
const EVT_MELD_KINDS = ['chi', 'pon', 'ankan', 'kakan', 'daiminkan'];
const EVT_DISCARD_KEYS = ['actor', 'tile', 'tsumogiri', 'sideways', 'turn'];
const EVT_MELD_KEYS = ['actor', 'tile', 'called_tile', 'tiles', 'meld_kind', 'from', 'turn'];

const DECISION_KEYS = new Set(['type', 'game', 'hand_no', 'hand', 'step', 'seat', 'policy',
  'kind', 'legal', 'chosen', 'chosen_index', 'obs', 'hand_delta', 'hand_winner', 'hand_loser',
  'hand_agari', 'reward_to_go', 'final_scores', 'placement', 'teacher', 'teacher_index']);

/** 小局行（`hand`）的字段白名单 —— 与 `TraceRecorder.onEvent` / PROTOCOL §8.4 必须一致。 */
const HAND_KEYS = new Set(['type', 'game', 'hand_no', 'hand', 'round', 'scores_after', 'delta',
  'agari', 'abortive', 'reason', 'renchan', 'winner', 'loser', 'tsumo', 'nagashi', 'tenpai',
  // 役种轴（2026-10 起；见 PROTOCOL §8.4 的字段表）
  'yaku', 'han', 'fu', 'yakuman', 'limit']);

/**
 * **役种轴五列**（PROTOCOL §8.4）：**要么整块都在，要么整块都不在**。
 *
 * 为什么钉这条：流局 / 途中流局真的没有和了者，而 2026-10 之前的轨迹也没有这五列 ——
 * 两者都必须是"缺席"而不是 0（画像工具据此画 `—` 而不是 `0.00`）。
 */
const YAKU_KEYS = ['yaku', 'han', 'fu', 'yakuman', 'limit'];
/** 参数化役种（带 ASCII 牌码 `tile` 的那三个角色码，= `YakuCodes.YAKUHAI/ROUND_WIND/SEAT_WIND`）。 */
const PARAM_CODES = new Set(['yakuhai', 'round_wind', 'seat_wind']);
/** 单个役对象的字段白名单（与 `agari` 报文的 `yaku[]` **同一套键序**）。 */
const YAKU_ITEM_KEYS = ['code', 'tile', 'han', 'yakuman'];
/** 打点档位码（= `YakuCodes` 的 `LIMIT` 值域；`""` = 未达満貫）。 */
const LIMIT_CODES = new Set(['', 'mangan', 'haneman', 'baiman', 'sanbaiman', 'kazoe_yakuman',
  'yakuman']);

const problems = [];
const warnings = [];
const stats = { games: 0, hands: 0, decisions: 0, turn: 0, claim: 0, byKind: {} };
const add = (file, line, msg) => problems.push(`${path.basename(file)}:${line} ${msg}`);
// ⚠ 提示**不算失败**：区分"训练数据本身不可用"（problems，退出码 1）与"评测元数据是旧版"
//   （warnings，照样 PASS）—— 例如旧 `summary.json` 没有 `rank_points`：轨迹训练不受影响，
//   但**配对评测**要用新版重跑自对弈。
const warn = (msg) => warnings.push(msg);

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
    // ⚠ `events` / `riichi_turn` 是 **obs v3** 才有的：拿老轨迹（v2）当输入时不该要求它们
    //   （判据是 `obs.v`，不是"文件里有没有" —— 老数据集照样要能过这份校验）。
    const obsV = Number(obs.v ?? 2);
    const required = obsV >= 3 ? OBS_KEYS : OBS_KEYS.filter((k) => !OBS_KEYS_V3.includes(k));
    const missKeys = required.filter((k) => !(k in obs));
    if (missKeys.length) add(file, ln, `观测缺少字段：${missKeys.join(',')}`);
    if (obsV < 3) {
      const stray = OBS_KEYS_V3.filter((k) => k in obs);
      if (stray.length) add(file, ln, `obs v${obsV} 不该出现 obs v3 的字段：${stray.join(',')}`);
    }
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
    // 动作键形状：**只认真实键文法**（碰 / 大明杠必须带取法 —— 赤五与普通五是两个不同的合法动作，
    // 见 PROTOCOL §8.3）。裸 `pon` / 单码 `kan:daiminkan:<码>` 是**老数据集**的形态（已作废），
    // 这里故意不放行：旧轨迹必须重采，而不是让校验器替它兜底。
    if (!/^discard:[0-9][mpsz](\/tsumogiri)?$|^riichi:[0-9][mpsz]$|^pon:[0-9][mpsz]\+[0-9][mpsz]$|^kan:(ankan|kakan):[0-9][mpsz]$|^kan:daiminkan:[0-9][mpsz]\+[0-9][mpsz]\+[0-9][mpsz]$|^chi:[0-9][mpsz]\+[0-9][mpsz]$|^(tsumo|ron|pass|kyuushu)$/.test(row.chosen)) {
      add(file, ln, `chosen 键形状可疑（旧格式或写错）：${row.chosen}`);
    }
    // 取法必须"同牌种"（碰的两张 / 大明杠的三张只能是同一个 kind）—— 独立于服务端实现判一次
    for (const key of [row.chosen, ...(row.legal || [])]) {
      const m = /^(?:pon|kan:daiminkan):(.+)$/.exec(key);
      if (!m) continue;
      const kinds = m[1].split('+').map((c) => (c[0] === '0' ? '5' : c[0]) + c[1]);
      if (new Set(kinds).size !== 1) add(file, ln, `取法里的牌不是同一牌种：${key}`);
    }
    // DAgger 标注（可选，`--teacher-label`）：学生座位上"老师会怎么打"
    if (('teacher' in row) !== ('teacher_index' in row)) {
      add(file, ln, 'teacher / teacher_index 必须成对出现');
    } else if ('teacher' in row) {
      const tAt = (row.legal || []).indexOf(row.teacher);
      if (tAt < 0) add(file, ln, `teacher=${row.teacher} 不在 legal 里`);
      else if (tAt !== row.teacher_index) add(file, ln, `teacher_index=${row.teacher_index} 实际应为 ${tAt}`);
      if (row.policy === 'teacher' || row.policy === 'bot') {
        add(file, ln, `座位策略是 ${row.policy} 时不该重复标 teacher（它的 chosen 就是老师动作）`);
      }
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
    // ---- obs v3 事件流（PROTOCOL §8.2）：形状 + **用事件流独立重建牌河**（不翻译 Java 的断言）
    if (obsV >= 3) {
      if (!Array.isArray(obs.events)) {
        add(file, ln, 'events 不是数组');
      } else {
        const evs = obs.events;
        const riverFromEvents = [[], [], [], []];
        const riichiEvTurn = [null, null, null, null];
        evs.forEach((e, k) => {
          const at = `events[${k}]`;
          if (!e || typeof e !== 'object') { add(file, ln, `${at} 不是对象`); return; }
          if (!EVT_TYPES.includes(e.type)) { add(file, ln, `${at} 未知 type=${e.type}`); return; }
          if (!Number.isInteger(e.actor) || e.actor < 0 || e.actor > 3) {
            add(file, ln, `${at} actor 越界：${e.actor}`);
          }
          if (!Number.isInteger(e.turn) || e.turn < 0 || e.turn > 30) {
            add(file, ln, `${at} turn 越界：${e.turn}`);
          }
          const tileOk = (c) => /^[0-9][mpsz]$/.test(String(c));
          if (e.type === 'discard') {
            for (const key of EVT_DISCARD_KEYS) if (!(key in e)) add(file, ln, `${at} 缺字段 ${key}`);
            if (typeof e.tsumogiri !== 'boolean' || typeof e.sideways !== 'boolean') {
              add(file, ln, `${at} tsumogiri/sideways 必须是布尔`);
            }
            if (!tileOk(e.tile)) add(file, ln, `${at} tile 牌码可疑：${e.tile}`);
            if (riverFromEvents[e.actor]) riverFromEvents[e.actor].push(e.tile);
          } else if (e.type === 'meld' || e.type === 'kan') {
            for (const key of EVT_MELD_KEYS) if (!(key in e)) add(file, ln, `${at} 缺字段 ${key}`);
            if (!EVT_MELD_KINDS.includes(e.meld_kind)) {
              add(file, ln, `${at} meld_kind 未知：${e.meld_kind}`);
            } else {
              const isKan = ['ankan', 'kakan', 'daiminkan'].includes(e.meld_kind);
              if (isKan !== (e.type === 'kan')) {
                add(file, ln, `${at} type=${e.type} 与 meld_kind=${e.meld_kind} 不一致`);
              }
              // 吃/碰 3 张；三种杠 4 张（大明杠 = 手里 3 张 + 被鸣那张）
              const wantLen = ['ankan', 'kakan', 'daiminkan'].includes(e.meld_kind) ? 4 : 3;
              if (!Array.isArray(e.tiles) || e.tiles.length !== wantLen
                  || !e.tiles.every(tileOk)) {
                add(file, ln, `${at} tiles 形状可疑：${JSON.stringify(e.tiles)}`);
              }
              if (!tileOk(e.called_tile)) add(file, ln, `${at} called_tile 可疑：${e.called_tile}`);
              // 只有吃/碰/大明杠会把牌河里那张**挪走**；加杠拿的是手里的第 4 张、暗杠自摸四张
              if (e.from !== e.actor && ['chi', 'pon', 'daiminkan'].includes(e.meld_kind)) {
                const owner = riverFromEvents[e.from];
                const got = owner ? owner.pop() : undefined;
                if (got !== e.called_tile) {
                  add(file, ln, `${at} 被鸣走的不是座位 ${e.from} 牌河最后一张（${got} != ${e.called_tile}）`);
                }
              }
            }
          } else if (e.type === 'riichi') {
            if ('tile' in e) add(file, ln, `${at} riichi 事件不该带 tile`);
            if (e.actor >= 0 && e.actor < 4) riichiEvTurn[e.actor] = e.turn;
          } else if (!tileOk(e.tile)) {                       // dora_flip
            add(file, ln, `${at} dora_flip tile 可疑：${e.tile}`);
          }
        });
        // ① 事件流重建出的牌河必须逐张等于 obs.discards（被鸣走的不在牌河里，但**在事件流里**）
        for (let s = 0; s < 4; s++) {
          if (JSON.stringify(riverFromEvents[s]) !== JSON.stringify(obs.discards[s] || [])) {
            add(file, ln, `座位 ${s} 的牌河与事件流重建不一致`);
          }
        }
        // ② riichi_turn 与事件里的 riichi 逐座位对齐（未立直 = 0，见 PROTOCOL §8.2）
        if (!Array.isArray(obs.riichi_turn) || obs.riichi_turn.length !== 4) {
          add(file, ln, `riichi_turn 形状可疑：${JSON.stringify(obs.riichi_turn)}`);
        } else {
          for (let s = 0; s < 4; s++) {
            const want = (obs.riichi || [])[s] ? riichiEvTurn[s] : 0;
            if (obs.riichi_turn[s] !== want) {
              add(file, ln, `riichi_turn[${s}]=${obs.riichi_turn[s]}，按立直状态与事件应为 ${want}`);
            }
          }
        }
      }
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
    for (const k of ['hand_delta', 'reward_to_go', 'final_scores', 'placement']) {
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
  // 这个文件是不是**新格式**：只要有一条小局行带 `yaku`，这一份轨迹就是 2026-10 之后采的
  // ⇒ 所有"有和了者"的行都必须带齐五列。老轨迹（整份缺席）照旧放行（不许当 0 读）。
  const yakuFormat = hands.some(({ row }) => 'yaku' in row);
  for (const { row, ln } of hands) {
    stats.hands++;
    const extra = Object.keys(row).filter((k) => !HAND_KEYS.has(k));
    if (extra.length) add(file, ln, `小局行有未登记字段：${extra.join(',')}`);
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

    // ---- 役种轴（2026-10；PROTOCOL §8.4）------------------------------------------------
    // 契约：和了者那一手的役/番/符/役满/打点档；**没有和了者 ⇒ 五列整块缺席**（⛔ 不写 0）。
    // 这里独立重算三件事（不照抄生产者的实现）：
    //   ① 形状：`yaku` 是对象数组，键序 ∈ {code[,tile],han[,yakuman]}、参数化役种才带 `tile`；
    //   ② **逐役 `han` 之和 == 合计 `han`**（役满按 13 × 倍数折算，PROTOCOL §3.7）；
    //   ③ `yakuman > 0 ⇒ han == 13 × yakuman`，且合计 `yakuman` == 逐役倍数之和。
    const haveYaku = YAKU_KEYS.filter((k) => k in row);
    if (haveYaku.length && haveYaku.length !== YAKU_KEYS.length) {
      add(file, ln, `役种轴五列必须整块出现或整块缺席，实际只有：${haveYaku.join(',')}`);
    }
    if (yakuFormat) {
      // 「有和了者」与「有役种数据」在引擎里是同一处发生的（`Round.Result.winScore`）⇒ 双向都要对
      if (row.agari && haveYaku.length === 0) add(file, ln, `agari=true 却缺役种轴（${YAKU_KEYS.join('/')}）`);
      if (!row.agari && haveYaku.length) add(file, ln, '不是和了局（agari=false）却有役种轴');
    }
    if (haveYaku.length === YAKU_KEYS.length) {
      stats.yakuHands = (stats.yakuHands || 0) + 1;
      const yk = row.yaku;
      if (!Array.isArray(yk) || yk.length === 0) {
        add(file, ln, `yaku 形状可疑（应是非空数组）：${JSON.stringify(yk)}`);
      } else {
        let hanSum = 0;
        let ykSum = 0;
        yk.forEach((y, i) => {
          const at = `yaku[${i}]`;
          if (!y || typeof y !== 'object' || Array.isArray(y)) {
            add(file, ln, `${at} 不是对象：${JSON.stringify(y)}`);
            return;
          }
          const bad = Object.keys(y).filter((k) => !YAKU_ITEM_KEYS.includes(k));
          if (bad.length) add(file, ln, `${at} 有未登记字段：${bad.join(',')}`);
          // 码是 ASCII 小写蛇形（`YakuCodes` 的值域形状；具体码表由服务端 `misses()` 守）
          if (typeof y.code !== 'string' || !/^[a-z][a-z0-9_]*$/.test(y.code)) {
            add(file, ln, `${at}.code 不是 ASCII 码：${JSON.stringify(y.code)}`);
          }
          // 参数化役种（役牌/场风/自风）**必须**带 `tile`（`1z..7z`），其余**不许**带
          const wantTile = PARAM_CODES.has(y.code);
          if (wantTile && !/^[1-7]z$/.test(y.tile)) add(file, ln, `${at}（${y.code}）缺合法 tile：${JSON.stringify(y.tile)}`);
          if (!wantTile && 'tile' in y) add(file, ln, `${at}（${y.code}）不是参数化役种却带 tile：${y.tile}`);
          if (!Number.isInteger(y.han) || y.han <= 0) add(file, ln, `${at}.han 应为正整数：${JSON.stringify(y.han)}`);
          if ('yakuman' in y && (!Number.isInteger(y.yakuman) || y.yakuman <= 0)) {
            add(file, ln, `${at}.yakuman 应为正整数：${JSON.stringify(y.yakuman)}`);
          }
          if ('yakuman' in y && y.han !== 13 * y.yakuman) {
            add(file, ln, `${at} 役满的 han 必须是 13 × 倍数（han=${y.han} / yakuman=${y.yakuman}）`);
          }
          hanSum += Number.isInteger(y.han) ? y.han : 0;
          ykSum += Number.isInteger(y.yakuman) ? y.yakuman : 0;
        });
        if (hanSum !== row.han) add(file, ln, `逐役 han 之和 ${hanSum} != 合计 han ${row.han}`);
        if (ykSum !== row.yakuman) add(file, ln, `逐役 yakuman 之和 ${ykSum} != 合计 yakuman ${row.yakuman}`);
      }
      if (!Number.isInteger(row.han) || row.han <= 0) add(file, ln, `han 应为正整数：${JSON.stringify(row.han)}`);
      // 符数：役满**没有符这个量纲**（引擎里 `s.fu = 0`），普通和了恒 ≥ 20（平和自摸 / 七对子也满足）
      if (!Number.isInteger(row.fu) || row.fu < 0) add(file, ln, `fu 形状可疑：${JSON.stringify(row.fu)}`);
      else if (row.yakuman > 0 ? row.fu !== 0 : row.fu < 20) {
        add(file, ln, `fu 与役满不匹配（yakuman=${row.yakuman} / fu=${row.fu}）：役满恒 0，普通和了 ≥ 20`);
      }
      if (!Number.isInteger(row.yakuman) || row.yakuman < 0) add(file, ln, `yakuman 形状可疑：${JSON.stringify(row.yakuman)}`);
      if (!LIMIT_CODES.has(row.limit)) add(file, ln, `limit 不是登记的档位码：${JSON.stringify(row.limit)}`);
      if (row.yakuman > 0 && row.han !== 13 * row.yakuman) {
        add(file, ln, `yakuman>0 ⇒ han 必须 == 13 × yakuman（han=${row.han} / yakuman=${row.yakuman}）`);
      }
      if (row.yakuman > 0 && row.limit !== 'yakuman') {
        add(file, ln, `役满（yakuman=${row.yakuman}）的 limit 应为 yakuman，实际 ${JSON.stringify(row.limit)}`);
      }
      if (row.yakuman === 0 && row.limit === 'yakuman') {
        add(file, ln, 'limit=yakuman 却不是役满（yakuman=0）—— 界面判据看 `yakuman` 字段，不看番数');
      }
    }
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
    if (prev) {
      // 终局账：末局结算**之后**，供託里的立直棒按规则归末局第 1 位（并列则由相关者均分、
      // 以 100 点为单位向下取整、尾数归更接近起家 = 座次小的一方，见 DESIGN「终局与精算」）。
      // 所以 final_scores 与末局 scores_after 的差额**正是这批余棒**，不是账不平。
      const sticks = Number((prev.round && prev.round.riichi_sticks) || 0);
      const total = sticks * 1000;
      const diff = game.row.final_scores.map((v, i) => v - prev.scores_after[i]);
      const sum = diff.reduce((a, b) => a + b, 0);
      if (diff.some((d) => d < 0) || sum !== total) {
        add(file, game.ln, `终局分数 − 末局 scores_after 的差额之和应为余棒 ${total}（末局 riichi_sticks=${sticks}），实际 ${sum}`);
      } else {
        const best = Math.max(...prev.scores_after);
        const group = prev.scores_after.map((v, i) => (v === best ? i : -1))
          .filter((i) => i >= 0).sort((a, b) => a - b);          // 座次小的在前 = 更接近起家
        for (let i = 0; i < 4; i++) {
          if (diff[i] !== 0 && !group.includes(i)) {
            add(file, game.ln, `座位${i} 分到余棒，但它不是末局 1 位（末局：${prev.scores_after.join(',')}）`);
          }
        }
        const unit = 100;
        const q = Math.floor(total / group.length / unit) * unit;
        const need = (total - q * group.length) / unit;           // 有几个座位多拿一份
        group.forEach((seat, k) => {
          const want = q + (k < need ? unit : 0);
          if (diff[seat] !== want) {
            add(file, game.ln, `座位${seat} 的余棒 ${diff[seat]} 应为 ${want}（共 ${total} 点、${group.length} 人并列 1 位）`);
          }
        });
      }
    }
    const place = placementOf(game.row.final_scores);
    if (JSON.stringify(place) !== JSON.stringify(game.row.placement)) {
      add(file, game.ln, `placement=${game.row.placement} 与按分数推出的 ${place} 不一致`);
    }
    if (new Set(game.row.placement).size !== 4) add(file, game.ln, 'placement 不是 1..4 的排列');
  } else add(file, lines.length, '没有 game 结算行');

  // ---- 逐决策 reward-to-go（离线 PPO 的 λ=1 目标；契约见 PROTOCOL §8.4 与 AGENTS §6.5）
  // 独立重算一遍：`R(h,s) = Σ_{h' ≥ h} delta[h'][s] + 终局余棒[s]`，逐行比对 —— ⛔ 不照抄 Java 的实现。
  // 余棒 = 末局结算后供託里那批立直棒按规则归末局 1 位（见上面 game 段），所以它必须加进来，
  // 否则第 0 小局的 reward_to_go 会比值头的旧口径（final_scores − 起点）少一个 0~3000 点的常数。
  if (hands.length > 0 && game) {
    const maxNo = Math.max(...hands.map((h) => h.row.hand_no));
    const byNo = new Map(hands.map((h) => [h.row.hand_no, h.row.delta]));
    const lastRow = hands[hands.length - 1].row;
    const bonus = game.row.final_scores.map((v, i) => v - lastRow.scores_after[i]);
    const want = new Array(maxNo + 1);
    const acc = [0, 0, 0, 0];
    for (let h = maxNo; h >= 0; h--) {
      const d = byNo.get(h) || [0, 0, 0, 0];
      for (let s = 0; s < 4; s++) acc[s] += d[s];
      want[h] = acc.map((v, s) => v + bonus[s]);
    }
    let midGameDiffers = 0;
    for (const { row, ln } of decisions) {
      const w = want[row.hand_no];
      if (!w || row.seat < 0 || row.seat > 3) {
        add(file, ln, `reward_to_go：hand_no=${row.hand_no} / seat=${row.seat} 越界`);
        continue;
      }
      if (row.reward_to_go !== w[row.seat]) {
        add(file, ln, `reward_to_go=${row.reward_to_go} 与独立重算的 ${w[row.seat]} 不一致`
          + `（本局及其后收支之和 + 余棒 ${bonus[row.seat]}）`);
      }
      const whole = game.row.final_scores[row.seat] - game.row.start_score;
      if (row.hand_no === 0 && row.reward_to_go !== whole) {
        add(file, ln, `第 0 小局 reward_to_go=${row.reward_to_go} != final_scores[${row.seat}] − 起点 ${whole}`);
      }
      if (row.hand_no > 0 && row.reward_to_go !== whole) midGameDiffers++;
    }
    // 红证：它必须**真的逐决策**。整场只有 1 小局时"每行都等于整场结果"是对的，所以只在多局时查。
    if (maxNo > 0 && midGameDiffers === 0) {
      add(file, 1, 'reward_to_go 在所有非首局行上都等于整场结果 —— 这个字段没有逐决策（等于把 value 抄了一遍）');
    }
  }

  // ---- 决策数与小局数对得上
  // ⚠ **采样过的轨迹不能查"每小局至少 4 条"**：`--sample k` 只记每 k 次决策里的 1 条，
  //   一局 60 次决策在 k=64 时就只剩 0~1 条 —— 那不是脏数据，是采样（`game.sampled_every` 记着）。
  //   没有这一条判断时，文档推荐的"`--sample 4` 起步"与"每次采集后必须跑本校验"会**互相打架**
  //   （实测 k=64 的轨迹会报满屏「第 N 小局只有 1 次决策」）。
  const sampledEvery = Number((game && game.row && game.row.sampled_every) || 1);
  if (sampledEvery > 1) {
    if (decisions.length === 0) {
      add(file, 1, `采样轨迹（k=${sampledEvery}）里一条决策都没有 —— 采样不该把整场采空`);
    }
  } else {
    for (const [handNo, n] of stepsByHand) {
      // ⚠ 判据是「有小局就必须有决策」，不是「每小局至少 4 次」：原来的 n < 4 会把**合法**轨迹判红 ——
      //   一小局完全可以在 1~2 次决策内结束（庄家九种九牌中途流局、第一张舍牌就被荣和）。
      //   2026-09 实测：300 场 × 2 小局的 first 轨迹里 g63 第 2 小局只有 2 次决策，而**同一份文件
      //   Java 自己产出的也一模一样**（逐字节相同）→ 是这条规则过严，不是数据有问题。
      if (n < 1) add(file, 1, `第 ${handNo} 小局一次决策都没有（有小局就该有决策）`);
    }
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
    // 顺位点（精算点数）：**独立复算它满足的不变式**（不照抄 Java 的公式 —— 那正是这个脚本的纪律）
    const rp = g.rank_points;
    if (!Array.isArray(rp) || rp.length !== 4) {
      warn(`summary.per_game[${g.game}] 没有 rank_points（旧版 summary —— 评价要用新版自对弈重跑）`);
      continue;
    }
    if (rp.some((v) => typeof v !== 'number' || !Number.isFinite(v))) {
      problems.push(`summary.per_game[${g.game}] rank_points 里有非数字`);
      continue;
    }
    // ① 马点之和为 0、头名赏刚好抵消 (返点−配给原点)×4/1000 → 顺位点总额恒为 0
    if (Math.abs(rp.reduce((a, b) => a + b, 0)) > 1e-6) {
      problems.push(`summary.per_game[${g.game}] rank_points 之和应为 0，实际 ${rp.reduce((a, b) => a + b, 0)}`);
    }
    // ② 顺位点必须与**终局分数严格同序**：`scores[i] > scores[j] ⟹ rp[i] > rp[j]`。
    //    ⚠ 不能写成"最高顺位点 ⟺ 1 位"：并列 1 位时两家顺位点**相同**（`settle` 均分马点+头名赏），
    //    而 `placement` 会按座次把它拆成 1 与 2 —— 实测 800 场里就有 2 场是这样（game 4: 39900,39900）。
    //    严格同序这条更强也更正确（它蕴含"只有最高分那几家可能拿最高顺位点"）。
    let ordered = true;
    for (let i = 0; i < 4 && ordered; i++) {
      for (let j = 0; j < 4; j++) {
        if (i === j) continue;
        const si = g.final_scores?.[i], sj = g.final_scores?.[j];
        if (typeof si === 'number' && typeof sj === 'number'
            && si > sj && !(rp[i] > rp[j])) {
          problems.push(`summary.per_game[${g.game}] 座位${i} 分数更高(${si}>${sj})但顺位点没更高(${rp[i]} vs ${rp[j]})`);
          ordered = false;
          break;
        }
      }
    }
  }
  // ③ by_policy.avg_rank_points 必须等于按 per_game 重算的均值（四舍五入到 0.1 分，容差 0.05）
  const acc = new Map();
  for (const g of sum.per_game || []) {
    (g.policies || []).forEach((label, i) => {
      const cur = acc.get(label) || { n: 0, sum: 0 };
      cur.n += 1;
      cur.sum += (g.rank_points || [])[i] || 0;
      acc.set(label, cur);
    });
  }
  for (const [label, st] of Object.entries(sum.by_policy || {})) {
    const a = acc.get(label);
    if (!a || a.n === 0) continue;
    const want = a.sum / a.n;
    const got = st.avg_rank_points;
    if (typeof got !== 'number') {
      warn(`summary.by_policy.${label} 没有 avg_rank_points（旧版 summary）`);
      continue;
    }
    if (Math.abs(got - want) > 0.05) {
      problems.push(`summary.by_policy.${label}.avg_rank_points=${got}，按 per_game 重算应为 ${want.toFixed(3)}`);
    }
  }
} else if (!quiet) {
  console.log('（没有 summary.json，跳过汇总核对）');
}

if (!quiet) {
  console.log(`轨迹目录：${dir}`);
  console.log(`  场数 ${stats.games} / 小局 ${stats.hands} / 决策 ${stats.decisions}`
    + `（自家回合 ${stats.turn}，鸣牌 ${stats.claim}）`);
  // 役种轴（2026-10）：0 = 老轨迹（整份没有这五列，画像工具会画 `—`）；老轨迹**照样 PASS**。
  console.log(`  带役种数据的小局 ${stats.yakuHands || 0} / ${stats.hands}`
    + `（0 = 2026-10 之前采的老轨迹，读侧按"取不到"处理，不是 0 番）`);
  console.log('  动作分布：' + Object.entries(stats.byKind)
    .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}=${v}`).join(' '));
}
if (warnings.length && !quiet) {
  console.log(`\n（提示 ${warnings.length} 条 —— 不影响训练数据本身）：`);
  for (const w of warnings.slice(0, 5)) console.log('  · ' + w);
}
if (problems.length) {
  console.error(`\n✗ 发现 ${problems.length} 处不一致（只显示前 20 条）：`);
  for (const p of problems.slice(0, 20)) console.error('  ' + p);
  console.error('DATASET FAIL');
  process.exit(1);
}
console.log('DATASET PASS');
