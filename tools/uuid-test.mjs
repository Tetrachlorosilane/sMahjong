#!/usr/bin/env node
/**
 * 身份（uuid）协议回归 —— 真 socket，对着真服务端跑。
 *
 * 钉住 `docs/PROTOCOL.md` §2.0 的四条：
 *   ① 连接后服务端**立刻**发 `uuid_ask`；客户端不带 uuid → `uuid_ok{issued:true,
 *      new_player:true}`（服务端生成一个）；
 *   ② 拿同一个 uuid 再连一次 → `issued:false`、`new_player:false`（**同一个 uuid = 同一个玩家**，
 *      服务端已经有档案了，不会再"新建一个初始玩家"）；
 *   ③ 形状不对的 uuid → 当作"没有记录"处理（重新生成，**不报错**）；
 *   ④ **掉线接回座位**：牌局进行中掉线（托管）之后，用同一个 uuid 重连会被接回**原座位**
 *      并收到 `state` 快照（手牌还在）；而原来那条连接**还活着**时，第二条同 uuid 的连接
 *      **不能**把人顶掉。
 *
 * 用法：node tools/uuid-test.mjs <host> <port>
 *
 * ⚠ ③ 之后的接回座位需要"房间里还有别人"（否则最后一个真人一走，房间按规则立即回收）——
 *    所以本脚本用 **2 个真人 + 2 个机器人**。
 */
import { connectClient, sleep } from './test-client.mjs';

const HOST = process.argv[2] || '127.0.0.1';
const PORT = Number(process.argv[3] || 10086);

let failed = 0;
const check = (ok, what) => {
    if (ok) {
        console.log(`  [uuid-test] OK   ${what}`);
    } else {
        failed++;
        console.error(`  [uuid-test] FAIL ${what}`);
    }
};

const clients = [];

async function main() {
    // ---------- ① 第一次来：服务端生成一个 ----------
    const fresh = await connectClient(HOST, PORT, { name: '身份甲' });
    clients.push(fresh);
    check(fresh.uuidOk && fresh.uuidOk.issued === true,
          `第一次连接：issued=true（服务端生成），实际 ${JSON.stringify(fresh.uuidOk)}`);
    check(fresh.uuidOk && fresh.uuidOk.new_player === true,
          '第一次连接：new_player=true（为它建了一份初始档案）');
    check(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(fresh.uuid || ''),
          `uuid 形状合法（36 字符 8-4-4-4-12）：${fresh.uuid}`);

    // ---------- ② 同一个 uuid 再连一次 = 同一个人 ----------
    const again = await connectClient(HOST, PORT, { name: '身份甲', uuid: fresh.uuid });
    clients.push(again);
    check(again.uuid === fresh.uuid, `同一个 uuid 被原样采纳：${again.uuid}`);
    check(again.uuidOk && again.uuidOk.issued === false,
          '第二次连接：issued=false（客户端给的那个被采纳）');
    check(again.uuidOk && again.uuidOk.new_player === false,
          '第二次连接：new_player=false（服务端已有档案 → 不是"新的初始玩家"）');
    // 字母大小写都要收（输入宽进、输出严出）
    const upper = fresh.uuid.toUpperCase();
    const upperClient = await connectClient(HOST, PORT, { name: '身份甲', uuid: upper });
    clients.push(upperClient);
    check(upperClient.uuid === fresh.uuid,
          `大写 uuid 也被规范成同一个小写身份：${upperClient.uuid}`);
    check(upperClient.uuidOk && upperClient.uuidOk.new_player === false,
          '大写形式认出的是**同一份档案**（不是又新建一个）');

    // ---------- ③ 形状不对 → 当作"没有记录" ----------
    const bad = await connectClient(HOST, PORT, { name: '身份丙', uuid: 'not-a-uuid' });
    clients.push(bad);
    check(bad.uuidOk && bad.uuidOk.issued === true,
          '形状不对的 uuid：按"没有记录"处理（重新生成，不报错）');
    check(bad.uuid !== 'not-a-uuid' && /^[0-9a-f-]{36}$/.test(bad.uuid || ''),
          `形状不对的 uuid 没被采纳：${bad.uuid}`);

    // ---------- ④ 掉线接回座位 ----------
    // 2 真人 + 2 机器人：A 掉线后 B 还在，房间不会被回收，A 才有座位可回。
    const a = await connectClient(HOST, PORT, { name: '甲' });
    const b = await connectClient(HOST, PORT, { name: '乙' });
    clients.push(a, b);
    const roomMark = a.mark();
    a.send({ cmd: 'create_room', name: '身份房', rules: { length: 'tonpuu', thinking_ms: 1000 }, fill_bots: 2 });
    const rj = await a.waitAfter(roomMark, (e) => e.ev === 'room_joined', 5000, 'room_joined');
    b.send({ cmd: 'join_room', room: rj.room });
    await b.waitAfter(b.mark(), (e) => e.ev === 'room_joined' && e.seat >= 0, 5000, 'room_joined');
    a.send({ cmd: 'ready', ready: true });
    b.send({ cmd: 'ready', ready: true });
    await a.waitAfter(0, (e) => e.ev === 'game_start', 10000, 'game_start');
    const rs = await a.waitAfter(0, (e) => e.ev === 'round_start', 10000, 'round_start');
    const mySeat = a.seat;
    check(mySeat >= 0 && (rs.hand || []).length >= 13,
          `开局：我坐在 ${mySeat}，拿到 ${(rs.hand || []).length} 张手牌`);

    // 原连接**还活着**时，第二条同 uuid 的连接不能顶掉座位
    const twinMark = b.mark();
    const twin = await connectClient(HOST, PORT, { name: '甲（冒充）', uuid: a.uuid });
    clients.push(twin);
    const stole = await twin.sawWithin(1200, (e) => e.ev === 'room_joined' && e.seat >= 0);
    check(!stole, '原连接还在时，同 uuid 的第二条连接**不会**顶掉那个座位');
    check(twin.seat < 0, `冒充者没有拿到座位（seat=${twin.seat}）`);
    twin.close();

    // 掉线（托管）→ 用同一个 uuid 重连 → 接回原座位 + 收到 state 快照
    a.close();
    await sleep(1200);
    const back = await connectClient(HOST, PORT, { name: '甲', uuid: a.uuid });
    clients.push(back);
    const rj2 = await back.waitAfter(0, (e) => e.ev === 'room_joined', 6000, 'room_joined');
    check(rj2.seat === mySeat, `接回**原座位** ${mySeat}（实际 ${rj2.seat}）`);
    const st = await back.waitAfter(0, (e) => e.ev === 'state' && e.phase === 'playing', 6000, 'state');
    check(st.seat === mySeat, `快照里的座位号也是 ${mySeat}`);
    check((st.hand || []).length >= 13,
          `接回后拿到的还是**自己的手牌**（${(st.hand || []).length} 张）`);
    // ⚠ 手牌内容**不保证**与开局那一刻相同：托管期间牌局照常推进（他会自动摸切），
    //    所以这里只能断言"还是同一小局、没有被重开"，不能比手牌内容。
    const stRound = st.round || {};
    check(stRound.bakaze === (rs.round || {}).bakaze && stRound.kyoku === (rs.round || {}).kyoku,
          `接回时还是**同一小局**（${stRound.bakaze}${stRound.kyoku} 局，牌局没有重开）`);
    const restarted = await back.sawWithin(1200, (e) => e.ev === 'round_start');
    check(!restarted, '接回时**没有**收到新的 round_start（不是"重开一局"）');

    // 掉线的那个人**不该**被换成机器人（这条在 away-test 里更细，这里只确认座位还归他）
    const roomEv = back.all((e) => e.ev === 'room' && e.playing).pop();
    const myRow = roomEv ? (roomEv.seats || [])[mySeat] : null;
    check(myRow && myRow.away !== true && myRow.bot !== true,
          `接回后那一格不再托管、也不是机器人：${JSON.stringify(myRow)}`);
}

try {
    await main();
} catch (e) {
    failed++;
    console.error(`[uuid-test] 异常：${e.message}`);
} finally {
    for (const c of clients) c.close();
}

console.log(failed === 0 ? '[uuid-test] UUID PASS' : `[uuid-test] UUID FAIL（${failed} 条）`);
process.exit(failed === 0 ? 0 : 1);
