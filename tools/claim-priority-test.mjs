#!/usr/bin/env node
/**
 * 鸣牌优先级 / 队列回归：「胡 > 杠 = 碰 > 吃」——高优先级一旦成立，
 * **不必再等低优先级的人**；而且被取消询问的迟到回包必须从队列里摘掉。
 *
 * 两个测量（都要求真实出现一次「同一张舍张上，有人能碰、有人只能吃」）：
 *
 *   A) **不必再等**：能吃的那家故意拖到最后才回，能碰的那家立刻回。
 *      正确行为：碰**立刻**成立（Δ ≈ 0）；未修时会一直等到吃那家的 deadline（Δ ≈ 3s）。
 *
 *   B) **消息队列**：拖到最后才回的那家，其回包是**不带 ask_id 的动作**（模拟老客户端；
 *      这类回包无法被 ask_id 识别，只能靠 Table.dropReplies 摘掉）。
 *      正确行为：它下一巡**仍然等满** deadline（说明那条废包没被当成本巡答复）；
 *      未修时那条 `chi` 会漏到下一巡、被 awaitAction 当作出牌答复 →
 *      服务端立刻替他摸切（Δ ≈ 0，症状同 §2.3-9 的"没动就被代打"）。
 *
 * 本脚本用 4 个脚本客户端（不用机器人：机器人是在询问阶段同步决策的，测不到"等"）：
 *   碰/杠/荣和 → 立刻回；吃 → 拖 LATE_MS 且**不带 ask_id**。
 *
 *   node tools/claim-priority-test.mjs <host> <port> [每巡毫秒]
 *
 * 期望：A、B 各取到至少一个样本且都通过；取不到样本按"无法判定"退出（码 2），不算绿。
 */
import net from 'node:net';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);
const THINK_MS = Number(process.argv[4] || 3000);
const LATE_MS = 1500;          // 低优先级家的回包延迟：> PROMPT_LIMIT_MS 才能把 A 分开，且远小于 deadline
const PROMPT_LIMIT_MS = 1200;  // A：高优先级鸣牌成立超过这个时间就算"还在等"（deadline 是 3000）
const HARD_LIMIT_MS = 180000;

const NAME = ['甲', '乙', '丙', '丁'];
const clients = [];
const samplesA = [];   // { me, from, tile, deltaMs, staller }
const samplesB = [];   // { seat, declaredMs, actualMs }
// 同一张舍张上的鸣牌询问是"一轮"：key = from:tile
// 记下这一轮**谁在拖**（stalls）以及每个被问者最高能做什么（best）。
// A 的样本必须来自"有人拖着"的那一轮，否则测的只是"唯一被问者的正常往返"（毫无意义）。
const claimRounds = new Map();
let gameOver = false;
let asked = 0, claims = 0;

class Client {
  constructor(i) {
    this.i = i;
    this.seat = -1;
    this.drawn = '';
    this.pendingPon = null;   // { from, tile, t }
    this.measureTurn = false; // 回包作废之后的那一巡要"故意不答"来量等待
    this.turnAskAt = 0;
    this.turnDeadline = 0;
    this.buf = '';
    this.sock = net.createConnection({ host: HOST, port: PORT });
    this.sock.setEncoding('utf8');
    this.sock.on('connect', () => this.send({ cmd: 'hello', name: NAME[i], ver: 1 }));
    this.sock.on('error', (e) => { console.error(`[claim] 连接错误 ${e.code}`); process.exit(1); });
    this.sock.on('data', (c) => this.onData(c));
    clients.push(this);
  }

  send(o) { this.sock.write(JSON.stringify(o) + '\n'); }

  onData(chunk) {
    this.buf += chunk;
    let i;
    while ((i = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, i).trim();
      this.buf = this.buf.slice(i + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      try { this.onEvent(ev); } catch (e) { console.error('[claim] 事件处理异常', e); }
    }
  }

  opt(t) { return (this.pendingOptions || []).find((o) => o.type === t); }

  onEvent(ev) {
    switch (ev.ev) {
      case 'hello_ok':
        if (this.i === 0) {
          this.send({ cmd: 'create_room', name: '鸣牌优先级房',
                      rules: { length: 'tonpuu', thinking_ms: THINK_MS }, fill_bots: 0 });
        } else {
          // 其余三家：轮询房间列表，看到房主的房就进（4 个真人，不用机器人）
          this.send({ cmd: 'list_rooms' });
          this.retry = setInterval(() => {
            if (!this.joined) this.send({ cmd: 'list_rooms' });
          }, 400);
        }
        break;
      case 'rooms': {
        if (this.joined) break;
        const r = (ev.rooms || []).find((x) => !x.playing && x.players < x.seats);
        if (r) this.send({ cmd: 'join_room', room: r.id });
        break;
      }
      case 'room_joined':
        this.joined = true;
        this.seat = ev.seat;
        if (this.retry) { clearInterval(this.retry); this.retry = null; }
        this.send({ cmd: 'ready', ready: true });
        if (this.i === 0) {
          // 四家都 ready 前 start_game 会被拒；反复尝试直到真的开局
          this.startTimer = setInterval(() => this.send({ cmd: 'start_game' }), 500);
        }
        break;
      case 'round_start':
        if (this.startTimer) { clearInterval(this.startTimer); this.startTimer = null; }
        this.drawn = '';
        claimRounds.clear();     // 新的一局：上一局的轮次登记作废（避免同一张牌被误关联）
        this.pendingChi = null;
        break;

      case 'game_end':
        gameOver = true;
        finish();
        break;

      case 'draw':
        if (ev.seat === this.seat && ev.tile) this.drawn = ev.tile;
        break;

      case 'ask_cancel':
        if (ev.seat === this.seat && this.pendingChi) {
          // 这次"吃"的询问已被取消 —— 但我们的回包还在定时器里没发出去。
          // 记下来：等它发出去时，它就是一条货真价实的"作废后上路的废包"。
          this.pendingChi.cancelled = true;
        }
        break;

      case 'ask': {
        if (ev.seat !== this.seat) break;
        this.pendingOptions = ev.options || [];
        if (ev.kind !== 'turn') {
          claims++;
          this.answerClaim(ev);
          break;
        }
        asked++;
        if (this.measureTurn) {
          // 故意不答：等服务端代打，量真实等待
          this.turnAskAt = Date.now();
          this.turnDeadline = Number(ev.deadline_ms) || 0;
          this.measureTurn = false;
          console.log(`[claim] seat${this.seat} 下一巡：声明 ${this.turnDeadline}ms —— 不答，看会不会被废包顶掉`);
          break;
        }
        this.send({ cmd: 'action', ask_id: ev.ask_id, ...this.turnAction() });
        break;
      }

      case 'meld':
        if (ev.seat === this.seat && this.pendingPon && ev.kind === this.pendingPon.kind) {
          const delta = Date.now() - this.pendingPon.t;
          // 只有"同一轮里另有人拖着不回"才算 A 的有效样本（否则只是正常往返）
          const key = `${this.pendingPon.from}:${ev.called_tile}`;
          const round = claimRounds.get(key);
          const staller = round ? [...round.stalls].find((s) => s !== this.seat) : undefined;
          if (staller !== undefined) {
            samplesA.push({ me: this.seat, from: this.pendingPon.from, tile: ev.called_tile,
                            deltaMs: delta, staller });
            console.log(`[claim] A：seat${this.seat} 的 ${ev.kind} 在 ${delta}ms 后成立` +
                        `（同张舍张 ${ev.called_tile}，seat${staller} 能吃的低优先级家一直没回）`);
            claimRounds.delete(key);
          } else {
            console.log(`[claim] （忽略）seat${this.seat} 的 ${ev.kind} ${delta}ms：本轮没有拖着的低优先级家`);
          }
          this.pendingPon = null;
          maybeFinish();
        }
        break;

      case 'discard':
        if (ev.seat === this.seat) {
          this.drawn = '';
          if (this.turnAskAt > 0 && ev.tsumogiri === true) {
            const actual = Date.now() - this.turnAskAt;
            samplesB.push({ declaredMs: this.turnDeadline, actualMs: actual });
            console.log(`[claim] B：seat${this.seat} 下一巡实测等待 ${actual}ms（声明 ${this.turnDeadline}ms）`);
            this.turnAskAt = 0;
            maybeFinish();
          }
        }
        break;
      default:
        break;
    }
  }

  turnAction() {
    if (this.opt('tsumo')) return { type: 'tsumo' };
    const d = this.opt('discard');
    if (d && d.tiles && d.tiles.length) return { type: 'discard', tile: d.tiles[0] };
    return { type: 'pass' };
  }

  answerClaim(ev) {
    const byType = (t) => this.opt(t);
    // 登记本轮：谁被问了、最高能做什么、谁在拖（A 的样本有效性全靠它）
    const key = `${ev.from}:${ev.tile}`;
    let round = claimRounds.get(key);
    if (!round) { round = { stalls: new Set(), best: new Map() }; claimRounds.set(key, round); }
    const bestType = ['ron', 'kan', 'pon', 'chi'].find((t) => byType(t)) || 'pass';
    round.best.set(this.seat, bestType);

    // 高优先级：立刻回（带 ask_id）
    for (const [t, kind] of [['ron', null], ['kan', null], ['pon', 'pon']]) {
      const o = byType(t);
      if (!o) continue;
      const act = { cmd: 'action', ask_id: ev.ask_id, type: t };
      if (t === 'kan') {
        const k = (o.kans || [])[0];
        if (!k) continue;
        act.kind = k.kind;
        act.tile = k.tile;
      }
      if (t !== 'ron') {
        this.pendingPon = { from: ev.from, kind: t === 'kan' ? 'daiminkan' : t, t: Date.now() };
      }
      this.send(act);
      return;
    }
    // 低优先级（吃）：**拖 LATE_MS 再回，而且不带 ask_id**。
    // 这一个模式同时产出两个样本（不用交替，样本不再看运气）：
    //   A) 拖的这段时间就是"旧行为白等"的量：修好前高优先级鸣牌要等到 LATE_MS 才成立，
    //      修好后 ≈ 0（LATE_MS 明显大于 PROMPT_LIMIT_MS，一眼能分开）；
    //   B) 这条回包发出时那次询问**早已被取消**，它落到队列里只能靠
    //      "type 不属于本次询问"挡下 —— 挡不住就会把这一家的下一巡顶掉（自动出牌）。
    if (byType('chi')) {
      const sets = byType('chi').sets || [];
      if (sets.length) {
        round.stalls.add(this.seat);
        this.pendingChi = { tiles: sets[0], cancelled: false };
        setTimeout(() => {
          const p = this.pendingChi;
          if (!p) return;
          this.pendingChi = null;
          this.send({ cmd: 'action', type: 'chi', tiles: p.tiles });   // ⚠ 故意不带 ask_id
          if (p.cancelled) {
            // 询问确实被取消过 → 这就是那条"作废但已上路"的废包，量它有没有漏到下一巡
            this.measureTurn = true;
            console.log(`[claim] seat${this.seat}：废包已发（chi, 无 ask_id，发出前已收到 ask_cancel），下一巡故意不答`);
          }
        }, LATE_MS);
        console.log(`[claim] seat${this.seat}：能吃的低优先级家**拖 ${LATE_MS}ms 再回**（测 A：高优先级不该等他）`);
        return;
      }
    }
    this.send({ cmd: 'action', ask_id: ev.ask_id, type: 'pass' });
  }
}

for (let i = 0; i < 4; i++) new Client(i);

let done = false;
function maybeFinish() {
  if (samplesA.length > 0 && samplesB.length > 0) finish();
}

function finish() {
  if (done) return;
  done = true;
  console.log('');
  console.log('[claim] ===== 汇总 =====');
  let bad = 0;

  if (samplesA.length === 0) {
    console.log('  A  未取到样本（本局没出现「有人能碰、同一张舍张上另有人只能吃且拖着不回」）');
  }
  for (const s of samplesA) {
    const slow = s.deltaMs > PROMPT_LIMIT_MS;
    if (slow) bad++;
    console.log(`  A  seat${s.me} 的高优先级鸣牌成立耗时 ${s.deltaMs}ms` +
                `（seat${s.staller} 一直在拖）${slow ? '  ← FAIL：还在等低优先级的人' : '  ok'}`);
  }

  if (samplesB.length === 0) {
    console.log('  B  未取到样本（没走到"废包之后那一巡"）');
  }
  for (const s of samplesB) {
    const short = s.declaredMs > 0 && s.actualMs < s.declaredMs * 0.8;
    if (short) bad++;
    console.log(`  B  废包之后那一巡：声明 ${s.declaredMs}ms，实测 ${s.actualMs}ms` +
                `${short ? '  ← FAIL：废包被当成了本巡答复（自动出牌）' : '  ok'}`);
  }

  console.log(`[claim] 出牌询问 ${asked} 次，鸣牌询问 ${claims} 次`);
  if (samplesA.length === 0 || samplesB.length === 0) {
    console.log('[claim] 无法判定：样本不足（不是通过）');
    for (const c of clients) c.sock.destroy();
    process.exit(2);
  }
  console.log(bad === 0
    ? '[claim] PASS：高优先级不必等低优先级，作废回包也没漏到下一巡'
    : `[claim] FAIL：${bad} 项不达标`);
  for (const c of clients) c.sock.destroy();
  process.exit(bad === 0 ? 0 : 1);
}

setTimeout(() => {
  console.error(`[claim] 达到时间上限（${HARD_LIMIT_MS}ms）`);
  finish();
}, HARD_LIMIT_MS);
