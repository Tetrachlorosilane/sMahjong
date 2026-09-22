/**
 * 联调脚本共用的小客户端（NDJSON / TCP）。
 *
 * 为什么单独抽一份：`uuid-test` / `vote-test` / `away-test` 三个脚本都要
 * 「连接 → hello/uuid 握手 → 收事件 → 等某一个事件」这套动作，各写一份的话
 * 超时口径、事件序号、握手顺序会各漂各的 —— 而这三个脚本恰恰是用来钉住
 * **协议时序**（PROTOCOL §2.0 / §2.5 / §3.13）的。
 *
 * 刻意保持很小：没有依赖，不懂麻将规则，只负责收发与等待。
 */
import net from 'node:net';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class TestClient {
    constructor(name, host, port) {
        this.name = name;
        this.host = host;
        this.port = port;
        this.log = [];            // 收到的全部事件（按到达顺序）
        this.waiters = new Set();
        this.buf = '';
        this.closed = false;
        this.socket = null;
        this.uuid = null;         // uuid_ok 给的身份
        this.pid = 0;
        this.seat = -1;
        this.roomId = '';
        this.handlers = new Map();
    }

    connect() {
        return new Promise((resolve, reject) => {
            const sock = net.createConnection({ host: this.host, port: this.port });
            this.socket = sock;
            sock.setEncoding('utf8');
            sock.on('connect', () => resolve(this));
            sock.on('error', (e) => { this.error = e; reject(e); });
            sock.on('close', () => { this.closed = true; });
            sock.on('data', (chunk) => {
                this.buf += chunk;
                let i;
                while ((i = this.buf.indexOf('\n')) >= 0) {
                    const line = this.buf.slice(0, i).trim();
                    this.buf = this.buf.slice(i + 1);
                    if (!line) continue;
                    let ev;
                    try { ev = JSON.parse(line); } catch { continue; }
                    this.dispatch(ev);
                }
            });
        });
    }

    dispatch(ev) {
        this.log.push(ev);
        if (ev.ev === 'uuid_ok') this.uuid = ev.uuid;
        if (ev.ev === 'hello_ok') this.pid = ev.pid;
        if (ev.ev === 'room_joined') { this.seat = ev.seat; this.roomId = ev.room; }
        const hs = this.handlers.get('*');
        if (hs) for (const h of hs) h(ev);
        const list = this.handlers.get(ev.ev);
        if (list) for (const h of list) h(ev);
        for (const w of [...this.waiters]) {
            if (w.pred(ev)) {
                this.waiters.delete(w);
                clearTimeout(w.timer);
                w.resolve(ev);
            }
        }
    }

    /** 注册事件回调（`'*'` = 全部事件）。 */
    on(name, fn) {
        if (!this.handlers.has(name)) this.handlers.set(name, []);
        this.handlers.get(name).push(fn);
    }

    send(obj) {
        if (this.closed || !this.socket) return;
        this.socket.write(JSON.stringify(obj) + '\n');
    }

    /** 当前事件序号：之后只关心"这个序号之后"的事件，避免匹配到历史事件。 */
    mark() {
        return this.log.length;
    }

    /** 等一个满足 `pred` 的事件（只看 `mark` 之后的；还没有就等它来）。 */
    waitAfter(mark, pred, timeoutMs = 15000, what = 'event') {
        for (let i = mark; i < this.log.length; i++) {
            if (pred(this.log[i])) return Promise.resolve(this.log[i]);
        }
        return new Promise((resolve, reject) => {
            const w = {
                pred,
                resolve,
                timer: setTimeout(() => {
                    this.waiters.delete(w);
                    reject(new Error(`[${this.name}] 等待「${what}」超时（${timeoutMs}ms）`));
                }, timeoutMs),
            };
            this.waiters.add(w);
        });
    }

    /** 等一个特定事件名（可再加额外判据）。 */
    waitEv(name, timeoutMs = 15000, extra = null) {
        const mark = this.mark();
        return this.waitAfter(mark, (e) => e.ev === name && (!extra || extra(e)), timeoutMs, name);
    }

    /** 某个窗口内有没有出现满足条件的事件（用于"不该发生"的断言）。 */
    async sawWithin(ms, pred) {
        const mark = this.mark();
        try {
            await this.waitAfter(mark, pred, ms, '不应出现的事件');
            return true;
        } catch {
            return false;
        }
    }

    /** 全部事件里满足条件的那些（从头扫，用于统计）。 */
    all(pred) {
        return this.log.filter(pred);
    }

    close() {
        this.closed = true;
        if (this.socket) {
            try { this.socket.destroy(); } catch { /* 忽略 */ }
        }
    }
}

/**
 * 连上并完成握手：`hello` →（收到 `uuid_ask`）→ `uuid` → 等 `hello_ok` / `uuid_ok`。
 *
 * @param opts.name 昵称
 * @param opts.uuid 本地保存的 uuid（不给 = 第一次玩，服务端会生成一个）
 */
export async function connectClient(host, port, opts = {}) {
    const c = new TestClient(opts.name || 'T', host, port);
    await c.connect();
    c.send({ cmd: 'hello', name: opts.name || 'T', ver: 1 });
    // 服务端一 accept 就发 uuid_ask（PROTOCOL §2.0）；先等它再回 uuid，顺序与真客户端一致
    await c.waitAfter(0, (e) => e.ev === 'uuid_ask', 5000, 'uuid_ask');
    c.send(opts.uuid ? { cmd: 'uuid', uuid: opts.uuid } : { cmd: 'uuid' });
    const hok = await c.waitAfter(0, (e) => e.ev === 'hello_ok', 5000, 'hello_ok');
    const uok = await c.waitAfter(0, (e) => e.ev === 'uuid_ok', 5000, 'uuid_ok');
    c.pid = hok.pid;
    c.uuid = uok.uuid;
    c.uuidOk = uok;
    return c;
}

/**
 * 建房间 + 入座若干真人 + 补机器人 + 全员准备，直到 **开局**（收到 `game_start`）。
 *
 * @param creator 房主连接（已经在握手后的状态）
 * @param others  其余真人连接
 * @param bots    补几个机器人
 * @param rules   规则（缺省东风战 + 短思考时间，脚本跑得快）
 */
export async function startGame(creator, others, bots, rules = null) {
    const mark = creator.mark();
    creator.send({
        cmd: 'create_room',
        name: '脚本房',
        rules: rules || { length: 'tonpuu', thinking_ms: 1000 },
        fill_bots: bots,
    });
    const joined = await creator.waitAfter(mark, (e) => e.ev === 'room_joined', 5000, 'room_joined');
    for (const o of others) {
        o.send({ cmd: 'join_room', room: joined.room });
        await o.waitAfter(o.mark(), (e) => e.ev === 'room_joined', 5000, 'room_joined');
    }
    // 全员准备 → 牌桌线程会自动开局（见 Table.run/readyToStart）
    creator.send({ cmd: 'ready', ready: true });
    for (const o of others) o.send({ cmd: 'ready', ready: true });
    await creator.waitAfter(0, (e) => e.ev === 'game_start', 10000, 'game_start');
    return joined.room;
}
