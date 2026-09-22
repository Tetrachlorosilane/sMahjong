#!/usr/bin/env node
/**
 * 掉线托管回归 —— 真 socket，对着真服务端跑。
 *
 * 需求原文：「如果有人掉线或退出，牌局不应该立即结束，而是掉线者转为自动摸切，
 *            并不吃碰杠（不要用机器人代打），直到如果四人都掉线，依旧回收房间」。
 *
 * 所以这里钉住四条（`docs/PROTOCOL.md` §3.13）：
 *   ① 掉线后那一格是 **`away=true` / `bot=false`** —— 人不在，但座位还是他的，
 *      **绝不**换成会吃碰杠甚至和牌的机器人（那是替玩家做决定）；
 *   ② 牌局**不结束**：其他家继续收到 discard / draw / round_end，点数账照走；
 *   ③ 托管者的每一张牌都是**摸切**（`tsumogiri=true`），而且**从不鸣牌**；
 *   ④ 真人**全掉线**之后房间**依旧被回收**（不等整局打完）。
 *
 * 用法：node tools/away-test.mjs <host> <port>
 *
 * ⚠ 需要至少 2 个真人：只有一个真人时他一掉线房间就该回收（那是 ④ 的行为），
 *    也就没得观察 ①②③ 了。
 */
import { connectClient, sleep } from './test-client.mjs';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);

let failed = 0;
const check = (ok, what) => {
    if (ok) {
        console.log(`  [away-test] OK   ${what}`);
    } else {
        failed++;
        console.error(`  [away-test] FAIL ${what}`);
    }
};

const clients = [];

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
    const a = await connectClient(HOST, PORT, { name: '掉线甲' });
    const b = await connectClient(HOST, PORT, { name: '留守乙' });
    clients.push(a, b);
    autoAnswer(a);
    autoAnswer(b);

    const rj = a.mark();
    a.send({ cmd: 'create_room', name: '托管房', rules: { length: 'tonpuu', thinking_ms: 1000 }, fill_bots: 2 });
    const joined = await a.waitAfter(rj, (e) => e.ev === 'room_joined', 5000, 'room_joined');
    b.send({ cmd: 'join_room', room: joined.room });
    await b.waitAfter(b.mark(), (e) => e.ev === 'room_joined' && e.seat >= 0, 5000, 'room_joined');
    a.send({ cmd: 'ready', ready: true });
    b.send({ cmd: 'ready', ready: true });
    await a.waitAfter(0, (e) => e.ev === 'game_start', 10000, 'game_start');
    await a.waitAfter(0, (e) => e.ev === 'round_start', 10000, 'round_start');

    const aSeat = a.seat;
    check(aSeat >= 0 && b.seat >= 0, `开局：甲在座位 ${aSeat}、乙在座位 ${b.seat}`);

    // 先让甲正常打一手（证明这条连接本来是在打的）
    const firstDiscard = await b.waitAfter(
        b.mark(), (e) => e.ev === 'discard' && e.seat === aSeat, 15000, '甲的第一张牌');
    check(firstDiscard.tile !== undefined, `甲掉线前正常出牌过：${firstDiscard.tile}`);

    // ---------- 掉线 ----------
    const dropMark = b.mark();
    a.close();
    // 服务端会在掉线时广播一条 room（这就是"谁被标成托管了"的公开信息）
    const roomEv = await b.waitAfter(
        dropMark, (e) => e.ev === 'room' && (e.seats || [])[aSeat]
                && e.seats[aSeat].away === true, 8000, 'room(away)');
    const row = roomEv.seats[aSeat];
    check(row.away === true, `掉线后那一格 away=true（实际 ${JSON.stringify(row)}）`);
    check(row.bot !== true, '掉线后那一格 **bot 不是 true** —— 绝不换成机器人代打');
    check(row.name === '掉线甲', `托管中仍然保留昵称（桌上还看得到他是谁）：${row.name}`);
    check(roomEv.playing === true, '掉线那一刻牌局仍在进行（playing=true）');

    // ---------- 托管者自动摸切、且不鸣牌 ----------
    // 等几张"甲掉线之后打出的牌"（他的回合一到，服务端就会替他摸切）。
    // 样本尽量多取几张：一张也可能是巧合（手里正好是刚摸到的那张），多张才有说服力；
    // 这一小局打完（round_end）或超时就收工。
    const deadline = Date.now() + 30000;
    const afterDrop = () => b.log.slice(b.log.indexOf(roomEv));
    const laterDiscards = () => afterDrop().filter((e) => e.ev === 'discard' && e.seat === aSeat);
    while (Date.now() < deadline
            && laterDiscards().length < 3
            && !afterDrop().some((e) => e.ev === 'round_end')) {
        await sleep(300);
    }
    const later = laterDiscards();
    check(later.length > 0,
          `确实观察到了托管者的自动摸切（掉线后打出 ${later.length} 张 / 整局共 `
          + `${b.all((e) => e.ev === 'discard' && e.seat === aSeat).length} 张）`);
    check(later.length > 0 && later.every((e) => e.tsumogiri === true),
          `托管者掉线后打出的每一张都是**摸切**（${later.length} 张：`
          + later.slice(0, 5).map((e) => `${e.tile}/${e.tsumogiri}`).join(' ') + '）');
    const melds = b.all((e) => e.ev === 'meld' && e.seat === aSeat);
    check(melds.length === 0,
          `托管者**从不吃碰杠**（整局 meld 数 ${melds.length}）`);

    // ---------- 牌局没有结束，房间还在 ----------
    const ended = await b.sawWithin(1500, (e) => e.ev === 'game_end');
    check(!ended, '掉线**没有**让牌局立刻结束（没有 game_end）');
    const alive = b.all((e) => ['discard', 'draw', 'meld', 'round_end'].includes(e.ev)).length;
    check(alive > 0, `牌局照常推进（乙这边收到 ${alive} 条对局事件）`);

    b.send({ cmd: 'list_rooms' });
    const roomsEv = await b.waitAfter(b.mark(), (e) => e.ev === 'rooms', 5000, 'rooms');
    const mine = (roomsEv.rooms || []).find((r) => r.id === joined.room);
    check(!!mine, `房间 ${joined.room} 仍在房间列表里（有托管者时不该被回收）`);
    check(mine && mine.playing === true, '房间状态仍是"对局中"');
    check(mine && mine.players === 4,
          `托管中的座位**仍被占着**（players=${mine ? mine.players : '?'}，应为 4）`);

    // ---------- 真人全掉线 → 依旧回收 ----------
    b.close();
    const obs = await connectClient(HOST, PORT, { name: '观察者' });
    clients.push(obs);
    let gone = false;
    for (let i = 0; i < 12 && !gone; i++) {
        obs.send({ cmd: 'list_rooms' });
        const ev = await obs.waitAfter(obs.mark(), (e) => e.ev === 'rooms', 3000, 'rooms');
        gone = !(ev.rooms || []).some((r) => r.id === joined.room);
        if (!gone) await sleep(400);
    }
    check(gone, `真人全部掉线后房间 ${joined.room} **依旧被回收**（房间列表里没有了）`);
}

try {
    await main();
} catch (e) {
    failed++;
    console.error(`[away-test] 异常：${e.message}`);
} finally {
    for (const c of clients) c.close();
}

console.log(failed === 0 ? '[away-test] AWAY PASS' : `[away-test] AWAY FAIL（${failed} 条）`);
process.exit(failed === 0 ? 0 : 1);
