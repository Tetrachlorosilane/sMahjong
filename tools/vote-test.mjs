#!/usr/bin/env node
/**
 * 「结束对局」投票回归 —— 真 socket，对着真服务端跑。
 *
 * 钉住 `docs/PROTOCOL.md` §2.5 / §3.12 的口径（需求原文：
 * 「如果有多于半数（不包括半数，不计数机器人或未在场的玩家）玩家同意结束对局，则对局结束，
 *   否则对局继续，投票功能全员冷却5分钟（由服务端记时）」）：
 *
 *   ① **分母不数机器人**：2 真人 + 2 机器人 → `total=2`、`need=2`（不是 3、也不是 4）；
 *   ② 发起人**算同意**（他点「结束对局」就是在表态），所以 2 人房里另一个人同意即通过；
 *   ③ 通过 → `vote_result{result:passed, reason:enough}` 紧跟 `game_end{reason:"vote"}`，
 *      并且点数仍然守恒（投票结束不能把分吃掉）；
 *   ④ 不够票 → **当场否决**（`reason:impossible`，不干等满窗口），牌局**继续**；
 *   ⑤ 否决之后**全员冷却**：再发 `vote_end` 回 `vote_denied{reason:"cooldown", wait_ms>0}`。
 *
 * 用法：node tools/vote-test.mjs <host> <port>
 */
import { connectClient, sleep } from './test-client.mjs';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);

let failed = 0;
const check = (ok, what) => {
    if (ok) {
        console.log(`  [vote-test] OK   ${what}`);
    } else {
        failed++;
        console.error(`  [vote-test] FAIL ${what}`);
    }
};

const clients = [];

/** 自动应答：能打牌就打第一张，否则 pass（让牌局正常流动，别把时间耗在超时上）。 */
function autoAnswer(c) {
    c.on('ask', (ev) => {
        const opts = ev.options || [];
        const d = opts.find((o) => o.type === 'discard');
        const cmd = { cmd: 'action', ask_id: ev.ask_id };
        if (d && (d.tiles || []).length) {
            cmd.type = 'discard';
            cmd.tile = d.tiles[0];
            cmd.tsumogiri = false;
        } else {
            cmd.type = 'pass';
        }
        c.send(cmd);
    });
}

async function main() {
    // ================= ① 通过：2 真人 + 2 机器人 =================
    const a = await connectClient(HOST, PORT, { name: '投票甲' });
    const b = await connectClient(HOST, PORT, { name: '投票乙' });
    clients.push(a, b);
    autoAnswer(a);
    autoAnswer(b);

    const rj = a.mark();
    a.send({ cmd: 'create_room', name: '投票房', rules: { length: 'tonpuu', thinking_ms: 1000 }, fill_bots: 2 });
    const joined = await a.waitAfter(rj, (e) => e.ev === 'room_joined', 5000, 'room_joined');
    b.send({ cmd: 'join_room', room: joined.room });
    await b.waitAfter(b.mark(), (e) => e.ev === 'room_joined' && e.seat >= 0, 5000, 'room_joined');
    a.send({ cmd: 'ready', ready: true });
    b.send({ cmd: 'ready', ready: true });
    await a.waitAfter(0, (e) => e.ev === 'game_start', 10000, 'game_start');
    await a.waitAfter(0, (e) => e.ev === 'round_start', 10000, 'round_start');
    check(a.seat >= 0 && b.seat >= 0 && a.seat !== b.seat,
          `开局：两个真人分别在座位 ${a.seat} / ${b.seat}（另外两格是机器人）`);

    let mark = a.mark();
    a.send({ cmd: 'vote_end' });
    const vs = await a.waitAfter(mark, (e) => e.ev === 'vote_start', 5000, 'vote_start');
    check(vs.by === a.seat, `vote_start.by = 发起人座位 ${a.seat}`);
    check(vs.total === 2, `vote_start.total = 2（**不数机器人**，实际 ${vs.total}）`);
    check(vs.need === 2, `vote_start.need = 2（2 人的严格过半，实际 ${vs.need}）`);
    check(typeof vs.deadline_ms === 'number' && vs.deadline_ms > 0,
          `vote_start.deadline_ms = ${vs.deadline_ms}`);

    mark = b.mark();
    b.send({ cmd: 'vote', agree: true });
    const vu = await b.waitAfter(mark, (e) => e.ev === 'vote_update', 5000, 'vote_update');
    check(vu.agree === 2,
          `发起人**自动算同意**：乙同意后 agree=2（实际 ${vu.agree}）`);
    check(Array.isArray(vu.agreed) && vu.agreed.length === 2,
          `vote_update.agreed = ${JSON.stringify(vu.agreed)}`);
    const vr = await b.waitAfter(mark, (e) => e.ev === 'vote_result', 5000, 'vote_result');
    check(vr.result === 'passed' && vr.reason === 'enough',
          `票够 → passed/enough（实际 ${vr.result}/${vr.reason}）`);
    const ge = await b.waitAfter(mark, (e) => e.ev === 'game_end', 8000, 'game_end');
    check(ge.reason === 'vote',
          `game_end.reason = "vote"（实际 ${JSON.stringify(ge.reason)}）`);
    const sum = (ge.scores || []).reduce((x, y) => x + y, 0);
    check(sum === 100000,
          `投票结束也要**点数守恒**：四家合计 ${sum}（应为 100000）`);
    check(Array.isArray(ge.final) && ge.final.length === 4,
          'game_end.final 仍然是四家精算');
    a.close();
    b.close();

    // ================= ② 否决 + 冷却：3 真人 + 1 机器人 =================
    const c1 = await connectClient(HOST, PORT, { name: '否决甲' });
    const c2 = await connectClient(HOST, PORT, { name: '否决乙' });
    const c3 = await connectClient(HOST, PORT, { name: '否决丙' });
    clients.push(c1, c2, c3);
    for (const c of [c1, c2, c3]) autoAnswer(c);

    const rj2 = c1.mark();
    c1.send({ cmd: 'create_room', name: '否决房', rules: { length: 'tonpuu', thinking_ms: 1000 }, fill_bots: 1 });
    const joined2 = await c1.waitAfter(rj2, (e) => e.ev === 'room_joined', 5000, 'room_joined');
    for (const c of [c2, c3]) {
        c.send({ cmd: 'join_room', room: joined2.room });
        await c.waitAfter(c.mark(), (e) => e.ev === 'room_joined' && e.seat >= 0, 5000, 'room_joined');
    }
    for (const c of [c1, c2, c3]) c.send({ cmd: 'ready', ready: true });
    await c1.waitAfter(0, (e) => e.ev === 'game_start', 10000, 'game_start');
    await c1.waitAfter(0, (e) => e.ev === 'round_start', 10000, 'round_start');

    mark = c1.mark();
    c1.send({ cmd: 'vote_end' });
    const vs2 = await c1.waitAfter(mark, (e) => e.ev === 'vote_start', 5000, 'vote_start');
    check(vs2.total === 3, `3 真人 + 1 机器人 → total=3（实际 ${vs2.total}）`);
    check(vs2.need === 2, `3 人的严格过半 = 2（实际 ${vs2.need}）`);
    // 另外两家都不同意 → 剩下的人数全同意也不够 → 当场否决
    c2.send({ cmd: 'vote', agree: false });
    c3.send({ cmd: 'vote', agree: false });
    const vr2 = await c1.waitAfter(mark, (e) => e.ev === 'vote_result', 6000, 'vote_result');
    check(vr2.result === 'rejected' && vr2.reason === 'impossible',
          `不够票 → 当场否决 rejected/impossible（实际 ${vr2.result}/${vr2.reason}）`);
    check(typeof vr2.cooldown_ms === 'number' && vr2.cooldown_ms >= 300000,
          `否决后进入全员冷却：cooldown_ms=${vr2.cooldown_ms}（应 ≥ 300000）`);
    check(vr2.agree === 1 && vr2.need === 2,
          `否决时的票数：${vr2.agree}/${vr2.need}`);

    // 否决**不等于**结束对局：牌局要继续
    const ended = await c1.sawWithin(2500, (e) => e.ev === 'game_end');
    check(!ended, '投票被否决后**不能**冒出 game_end（对局继续）');
    const alive = c1.all((e) => ['discard', 'meld', 'draw', 'round_end', 'ask'].includes(e.ev)).length;
    check(alive > 0, `否决之后牌局照常推进（收到 ${alive} 条对局事件）`);

    // 冷却期内再发起 → vote_denied{reason:cooldown, wait_ms}
    mark = c1.mark();
    c1.send({ cmd: 'vote_end' });
    const vd = await c1.waitAfter(mark, (e) => e.ev === 'vote_denied', 5000, 'vote_denied');
    check(vd.reason === 'cooldown', `冷却期内发起 → reason=cooldown（实际 ${vd.reason}）`);
    check(typeof vd.wait_ms === 'number' && vd.wait_ms > 0,
          `vote_denied.wait_ms = ${vd.wait_ms}（服务端记的剩余时间）`);
    const running = await c1.sawWithin(1500, (e) => e.ev === 'vote_start');
    check(!running, '被拒之后**没有**进入新的投票');
}

try {
    await main();
} catch (e) {
    failed++;
    console.error(`[vote-test] 异常：${e.message}`);
} finally {
    for (const c of clients) c.close();
}

console.log(failed === 0 ? '[vote-test] VOTE PASS' : `[vote-test] VOTE FAIL（${failed} 条）`);
process.exit(failed === 0 ? 0 : 1);
