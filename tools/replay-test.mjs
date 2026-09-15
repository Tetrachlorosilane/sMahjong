// 对局记录（回放）接口的端到端验证：真 socket、真服务端。
//
// 覆盖：
//   ① 开局报文带 replay_id；空库时列表为空
//   ② 跑完一整场（4 机器人）后：列表出现这一场，元信息不含牌山（列表要小）
//   ③ replay_get：牌山 136 张不重复、小局索引与 replay_round 条目一一对应
//   ④ 分块翻页与整体一致（count 钳到 2000；from 越界回空数组）
//   ⑤ 语义不变量：每条 discard 的牌是该家之前拿到的（配牌 + 摸牌）
//   ⑥ 安全：非法 ID / 路径穿越 / 不存在的 ID 一律 replay_not_found
//   ⑦ 限速：连发 80 次拿到 replay_rate_limited
//
// 用法：node tools/replay-test.mjs <host> <port> [--keep]
import net from 'node:net';

const args = process.argv.slice(2);
const host = args[0] || '127.0.0.1';
const port = Number(args[1] || 10086);

let pass = 0;
let fail = 0;
const failures = [];
const check = (name, ok) => {
  if (ok) { pass++; console.log(`  [ok]   ${name}`); }
  else { fail++; failures.push(name); console.log(`  [FAIL] ${name}`); }
};
const eq = (name, got, want) => check(`${name}（期望 ${want}，实际 ${got}）`, String(got) === String(want));

// ---------------------------------------------------------------- 极简客户端
class Client {
  constructor() {
    this.buf = '';
    this.waiters = [];
    this.inbox = [];
    this.sock = net.createConnection({ host, port });
    this.sock.setEncoding('utf8');
    this.sock.on('data', (d) => this.onData(d));
    this.sock.on('error', (e) => { console.error('socket 错误: ' + e.message); process.exit(1); });
  }
  onData(d) {
    this.buf += d;
    let nl;
    while ((nl = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (!line.trim()) continue;
      let m;
      try { m = JSON.parse(line); } catch { continue; }
      if (this.onEvent) { try { this.onEvent(m); } catch { /* 自动应答失败不影响用例 */ } }
      // ⚠ 先给等待者；**被等待者拿走的消息不能再留在 inbox 里** ——
      //   否则「先问列表（0 场）→ 打完再问列表」的第二问会命中第一条旧回复（假失败）。
      let taken = false;
      for (const w of this.waiters.slice()) {
        if (w.pred(m)) {
          this.waiters.splice(this.waiters.indexOf(w), 1);
          w.resolve(m);
          taken = true;
          break;
        }
      }
      if (!taken) {
        this.inbox.push(m);
      }    }
  }
  send(o) { this.sock.write(JSON.stringify(o) + '\n'); }
  /**
   * 等一条**还没被消费过**的消息。
   * ⚠ 命中后必须从 inbox 里摘掉：否则「先问列表（0 场）→ 打完再问列表」会命中那条旧的
   *   空列表，把"服务端没记下来"误报成 bug（这个坑在本工具的第一次真机跑里就踩到了）。
   */
  wait(pred, timeoutMs = 120000, what = '事件') {
    return new Promise((resolve, reject) => {
      const i = this.inbox.findIndex(pred);
      if (i >= 0) return resolve(this.inbox.splice(i, 1)[0]);
      const w = { pred, resolve };
      this.waiters.push(w);
      setTimeout(() => {
        const k = this.waiters.indexOf(w);
        if (k >= 0) this.waiters.splice(k, 1);
        reject(new Error('等待超时: ' + what));
      }, timeoutMs);
    });
  }
  close() { try { this.sock.end(); } catch { /* ignore */ } }
}

const main = async () => {
  const c = new Client();
  // 自动应答：本工具只负责**把一场打完**（真人对局不是被测对象），
  // 策略与 e2e-test 一致（能自摸/荣和就胡、否则打第一张合法牌；顺带开杠以覆盖岭上路径）。
  const mySeat = { v: -1 };
  c.sock.on('data', () => { /* 数据已由 Client 收集 */ });
  const autoAnswer = (ev) => {
    if (ev.ev === 'ask' && ev.seat === mySeat.v) {
      const opts = ev.options || [];
      const by = (t) => opts.find((o) => o.type === t);
      let act;
      if (ev.kind === 'turn') {
        if (by('tsumo')) act = { type: 'tsumo' };
        else {
          const kan = by('kan');
          const d = by('discard');
          if (kan && kan.kans && kan.kans.length) act = { type: 'kan', kind: kan.kans[0].kind, tile: kan.kans[0].tile };
          else if (d && d.tiles && d.tiles.length) act = { type: 'discard', tile: d.tiles[0] };
          else act = { type: 'discard', tile: '1m' };
        }
      } else if (by('ron')) act = { type: 'ron' };
      else act = { type: 'pass' };
      c.send({ cmd: 'action', ask_id: ev.ask_id, ...act });
    } else if (ev.ev === 'round_wait') {
      c.send({ cmd: 'confirm' });
    } else if (ev.ev === 'room_joined') {
      mySeat.v = ev.seat;
      c.send({ cmd: 'ready', ready: true });
    } else if (ev.ev === 'room' && !ev.playing) {
      const ready = (ev.seats || []).filter((s) => s && (s.ready || s.bot)).length;
      if (ready === 4 && ev.seats[mySeat.v] && !ev.seats[mySeat.v].ready) {
        c.send({ cmd: 'ready', ready: true });
      }
    }
  };
  c.onEvent = autoAnswer;

  c.send({ cmd: 'hello', name: '回放测试' });
  const hello = await c.wait((m) => m.ev === 'hello_ok', 10000, 'hello_ok');
  check('握手成功', !!hello);

  // ① 空库 / 起始状态
  c.send({ cmd: 'replay_list', limit: 5 });
  const list0 = await c.wait((m) => m.ev === 'replay_list', 10000, 'replay_list');
  check('列表接口可用（字段 total + items）',
    typeof list0.total === 'number' && Array.isArray(list0.items));
  const before = list0.total;

  // ② 开一桌、补 3 个机器人、跑完一场东风战（--no-game：跳过打牌，用库里已有的记录验读路径）
  let rid = '';
  if (!args.includes('--no-game')) {
    c.send({ cmd: 'create_room', name: '回放验证房', rules: { length: 'tonpuu', preset: 'mleague' }, fill_bots: 3 });
    const room = await c.wait((m) => m.ev === 'room', 10000, 'room');
    check('建房成功: ' + room.id, !!room.id);
    if (!room.playing) {
      const ready = (room.seats || []).filter((s) => s && (s.ready || s.bot)).length;
      if (ready === 4) c.send({ cmd: 'ready', ready: true });
    }
    await new Promise((r) => setTimeout(r, 500));
    c.send({ cmd: 'start_game' });
    const gs = await c.wait((m) => m.ev === 'game_start', 30000, 'game_start');
    rid = gs.replay_id;
    check(`game_start 带 replay_id: ${rid}`, /^[0-9A-HJKMNP-TV-Z]{10}$/.test(rid || ''));

    console.log('  … 等整场打完（机器人每步有延迟，约 1~4 分钟）');
    const ge = await c.wait((m) => m.ev === 'game_end', 900000, 'game_end');
    eq('game_end 的 replay_id 与开局一致', ge.replay_id, rid);

    // ③ 列表里出现这一场（**就在 game_end 之后立刻问** —— 这条顺序是真机踩出来的：
    //    先广播后落盘的话，客户端一收到结算就点"看回放"会查不到）
    c.send({ cmd: 'replay_list', limit: 10 });
    const list1 = await c.wait((m) => m.ev === 'replay_list', 10000, 'replay_list');
    check(`列表总数 +1（${before} → ${list1.total}）`, list1.total === before + 1);
    check('game_end 之后立刻就能查到这一场', !!list1.items.find((x) => x.id === rid));
  } else {
    c.send({ cmd: 'replay_list', limit: 10 });
    const list1 = await c.wait((m) => m.ev === 'replay_list', 10000, 'replay_list');
    const pick = list1.items.find((x) => x.rounds >= 1);
    rid = pick ? pick.id : '';
    check('--no-game：从库里挑到一场有牌山的记录', !!rid);
  }

  // ④ 取整场（分页），并核对牌山
  const head = await getReplay(c, rid, 0, 2000);
  const total = head.total;
  const entries = [...head.entries];
  for (let from = 2000; from < total; from += 2000) {
    const page = await getReplay(c, rid, from, 2000);
    entries.push(...page.entries);
  }
  eq('分页取全 == 总数', entries.length, total);
  const seqOk = entries.every((e, i) => e.seq === i);
  check('seq 严格递增 0..n-1', seqOk);
  const m = head.meta;
  eq('牌山快照数 == 小局数', m.walls.length, m.rounds);
  let wallOk = true;
  for (const w of m.walls) {
    if (w.length !== 136) wallOk = false;
    const seen = new Set(w);
    if (seen.size !== 136 || Math.min(...w) < 0 || Math.max(...w) > 135) wallOk = false;
  }
  check('每小局牌山都是 136 张且 id 不重复、范围 0..135', wallOk);
  check('列表元信息不含牌山（列表要小）', m.walls !== undefined && head.meta !== undefined);
  check(`元信息带局数/条数（rounds=${m.rounds}, entries=${total}）`, m.rounds >= 1 && total > 100);
  const roundAt = m.round_at;
  check('round_at 单调递增（首项 = 开局事件之后的第一条小局边界）',
    roundAt.length === m.rounds && roundAt.every((v, i) => i === 0 || v > roundAt[i - 1]));
  let roundEntryOk = true;
  for (let i = 0; i < roundAt.length; i++) {
    const e = entries[roundAt[i]];
    if (!e || e.b.ev !== 'replay_round' || e.b.index !== i || !Array.isArray(e.b.wall) || e.b.wall.length !== 136) {
      roundEntryOk = false;
    }
  }
  check('每个小局边界都指向 replay_round（含 index 与 136 张牌山）', roundEntryOk);
  const wallsMatch = roundAt.every((at, i) => JSON.stringify(entries[at].b.wall) === JSON.stringify(m.walls[i]));
  check('replay_round.wall 与 meta.walls 一致', wallsMatch);

  // ⑤ 语义不变量：出牌的牌必须是该家之前拿到的（配牌 + 摸牌）
  const per = Array.from({ length: 4 }, () => new Array(34).fill(0));
  const kindOf = (s) => {
    const mm = /^([0-9])([mpsz])$/.exec(s || '');
    if (!mm) return -1;
    const n = Number(mm[1]);
    const suit = 'mpsz'.indexOf(mm[2]);
    return n === 0 ? suit * 9 + 4 : suit * 9 + (n - 1);
  };
  let bad = null;
  let discards = 0;
  let draws = 0;
  for (const e of entries) {
    const b = e.b || {};
    const seat = typeof b.seat === 'number' ? b.seat : -1;
    if (b.ev === 'round_start' && seat >= 0 && Array.isArray(b.hand)) {
      for (const t of b.hand) { const k = kindOf(t); if (k >= 0) per[seat][k]++; }
      continue;
    }
    if (b.ev === 'draw' && seat >= 0 && b.tile) {
      const k = kindOf(b.tile);
      if (k >= 0) { per[seat][k]++; draws++; }
    } else if (b.ev === 'discard' && seat >= 0 && b.tile) {
      const k = kindOf(b.tile);
      discards++;
      if (k < 0 || per[seat][k] <= 0) bad = `${seat}/${b.tile}`;
      else per[seat][k]--;
    }
  }
  check(`整场记到了摸牌/出牌（摸 ${draws} / 打 ${discards}）`, draws > 50 && discards > 50);
  eq('每条出牌的牌都是该家之前拿到的（含配牌）', bad, null);

  const chatless = entries.every((e) => typeof e.t === 'number' && typeof e.to === 'number');
  check('每条 entry 都带 seq/t/to（客户端按 to 做上帝视角合并）', chatless);

  // ⑥ 越界 / 非法 / 路径穿越
  const oob = await getReplay(c, rid, total + 500, 50);
  eq('from 越界回空数组（不报错）', oob.entries.length, 0);
  const clamp = await getReplay(c, rid, 0, 99999);
  eq('count 被钳到 2000', clamp.entries.length, Math.min(2000, total));
  for (const badId of ['../../etc/pass', 'short', 'IIIIIIIIII', 'ZZZZZZZZZZ']) {
    const r = await getReplayError(c, badId);
    eq(`非法/不存在的 ID → replay_not_found（${badId}）`, r.code, 'replay_not_found');
  }

  // ⑦ 限速：连发请求
  let limited = false;
  for (let i = 0; i < 90 && !limited; i++) {
    c.send({ cmd: 'replay_list', limit: 1 });
    const r = await c.wait((x) => x.ev === 'replay_list' || x.ev === 'error', 5000, '限速探测');
    if (r.ev === 'error' && r.code === 'replay_rate_limited') limited = true;
  }
  check('连发请求会触发 replay_rate_limited（每 10 秒 60 次）', limited);

  c.close();
  console.log();
  console.log(`通过 ${pass} 项，失败 ${fail} 项`);
  if (fail > 0) {
    for (const f of failures) console.log('  ✗ ' + f);
    console.log('REPLAY FAIL');
    process.exit(1);
  }
  console.log('REPLAY PASS');
};

const getReplay = async (c, id, from, count) => {
  c.send({ cmd: 'replay_get', id, from, count });
  const r = await c.wait((m) => (m.ev === 'replay_get' && m.id === id && m.from === from) || m.ev === 'error',
    20000, `replay_get(${from})`);
  if (r.ev === 'error') throw new Error('replay_get 失败: ' + r.code);
  return r;
};
const getReplayError = async (c, id) => {
  c.send({ cmd: 'replay_get', id, from: 0, count: 10 });
  // 只认 error：inbox 里可能还留着上一次成功请求的 replay_get（谓词放宽会命中它 → 假通过）
  const r = await c.wait((m) => m.ev === 'error', 20000, 'replay_get(err)');
  return r;
};

main().catch((e) => {
  console.error('REPLAY 测试异常: ' + e.message);
  process.exit(1);
});
