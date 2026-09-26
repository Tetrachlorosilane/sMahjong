#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎：役种 / 符数 / 打点 / 授受差分对拍**（M2 的判据之一）。
 *
 *   node tools/trainer-score-parity.mjs [行数]
 *
 * 做什么：按同一份**确定性语料**（每行 15 个字段，见 `trainer/src/main.cpp` 的 `score` 注释）
 * 分别跑
 *   ① Java 侧只读探针 `tools/ScoreProbe.java`（`Rules.applyPreset` + `Evaluator.evaluate` +
 *      `Payments.compute`）
 *   ② C++ 侧 `trainer/build/trainer.exe score <corpus> <out>`
 * 然后**逐行逐字段**比对（含和了形签名、役种列表的码/番/役满倍数、四家收支）。
 *
 * 语料覆盖（都是"手牌 + 上下文"的组合，不靠运气）：
 *   · 和了手：随机构造 need 个面子 + 雀头（可带 0~4 副露，含吃/碰/大明杠/暗杠/加杠），
 *     和了牌随机取手里一张 → 保证 `decompose` 有解，覆盖所有役种分派；
 *   · 破形手：把和了手里的一张换成别的牌 → 覆盖"不是和了形"；
 *   · 特型：七对子 / 国士（含十三面）/ 九莲 / 字一色 / 绿一色 / 清老头 / 大三元 / 四暗刻 …；
 *   · 上下文：自摸/荣和、立直/两立直/一发、海底/河底、抢杠/岭上、天和/地和/人和、古役、流局满贯；
 *   · 规则：mleague / tenhou / majsoul × {+koyaku, +kazoe, +dbl}；
 *   · 包牌责任、本场棒、立直棒、赤宝牌（赤五按 copy 0 分配）也各覆盖一批。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题（jar / 可执行缺失）。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'ScoreProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const ROWS = Number(argv.find((a) => /^\d+$/.test(a)) ?? 200000);

if (!existsSync(JAR)) {
    console.error(`[score-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[score-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

// ------------------------------------------------------------------ 语料
const KIND = 34;
const YAOCHU = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33];

function rng(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/** 手牌构造器：按 kind 分配牌 id（赤五优先用 copy 0，让"数赤宝牌"那条路真的被走到）。 */
function handBuilder() {
    const nextCopy = new Array(KIND).fill(0);
    return {
        /** 取一张该 kind 的牌 id（同一手牌内不会重复）。 */
        take(kind) {
            if (nextCopy[kind] > 3) return -1;
            const id = (kind << 2) | nextCopy[kind];
            nextCopy[kind]++;
            return id;
        },
        count(kind) { return nextCopy[kind]; },
    };
}

const MELD_TYPES = ['ro', 'to', 'qo', 'qc', 'ko'];   // 吃 / 碰 / 大明杠 / 暗杠 / 加杠

/** 造一个"和了牌型"的暗手 + 副露；返回 { handIds, melds, winKind } 或 null。 */
function buildWinHand(r, opts = {}) {
    const b = handBuilder();
    const melds = [];
    const meldCount = opts.meldCount ?? Math.floor(r() * 5);
    for (let i = 0; i < meldCount; i++) {
        for (let attempt = 0; attempt < 20; attempt++) {
            const ty = opts.meldTypes ? opts.meldTypes[Math.floor(r() * opts.meldTypes.length)]
                : MELD_TYPES[Math.floor(r() * MELD_TYPES.length)];
            const isRun = ty[0] === 'r';
            const quad = ty[0] === 'q' || ty[0] === 'k';
            const k = isRun ? Math.floor(r() * 27 / 9) * 9 + Math.floor(r() * 7) : Math.floor(r() * KIND);
            const kinds = isRun ? [k, k + 1, k + 2] : [k];
            const need = isRun ? 1 : (quad ? 4 : 3);
            let ok = true;
            for (const kk of kinds) {
                if (b.count(kk) + need > 4) ok = false;
            }
            if (!ok) continue;
            if (ty === 'ko' && b.count(k) < 1) continue;      // 加杠：先有碰
            const ids = [];
            for (const kk of kinds) {
                for (let n = 0; n < need; n++) ids.push(b.take(kk));
            }
            melds.push(`${ty}:${ids.join(',')}`);
            break;
        }
    }
    const need = 4 - melds.length;
    // 雀头
    let pair = -1;
    for (let attempt = 0; attempt < 40; attempt++) {
        const k = Math.floor(r() * KIND);
        if (b.count(k) + 2 <= 4) { pair = k; break; }
    }
    if (pair < 0) return null;
    const pairIds = [b.take(pair), b.take(pair)];
    // need 个面子
    const setIds = [];
    for (let s = 0; s < need; s++) {
        for (let attempt = 0; attempt < 40; attempt++) {
            if (r() < 0.5) {
                const k = Math.floor(r() * KIND);
                if (b.count(k) + 3 > 4) continue;
                setIds.push([b.take(k), b.take(k), b.take(k)]);
                break;
            }
            const suit = Math.floor(r() * 3);
            const start = suit * 9 + Math.floor(r() * 7);
            if (b.count(start) >= 4 || b.count(start + 1) >= 4 || b.count(start + 2) >= 4) continue;
            setIds.push([b.take(start), b.take(start + 1), b.take(start + 2)]);
            break;
        }
    }
    const concealed = [...pairIds, ...setIds.flat()];
    if (concealed.length !== need * 3 + 2) return null;
    if (concealed.includes(-1)) return null;
    const winKind = concealed[Math.floor(r() * concealed.length)] >> 2;
    return { handIds: concealed, melds, winKind };
}

/** 特型手（手写）：七对子 / 国士 / 九莲 / 字一色 / 绿一色 / 清老头 / 大三元 / 四暗刻 / 各种同顺同刻。 */
function specialHands() {
    const out = [];
    /** `winKind` 可以与 `extra` 不同：国士十三面 vs 国士、纯正九莲 vs 九莲，差别就在这一处。 */
    const mk = (spec) => {
        const b = handBuilder();
        const ids = [];
        for (const kind of spec.kinds) {
            const id = b.take(kind);
            if (id < 0) return null;
            ids.push(id);
        }
        let winKind = spec.winKind;
        if (winKind === undefined) {
            winKind = spec.kinds[Math.floor(spec.kinds.length / 2)];
        }
        return { handIds: ids, melds: spec.melds ?? [], winKind };
    };
    const pairs = (list) => list.flatMap((k) => [k, k]);
    const trips = (list) => list.flatMap((k) => [k, k, k]);
    // 七对子
    for (let t = 0; t < 6; t++) {
        const kinds = [0, 2, 4, 6, 8, 9, 11, 13, 15, 18, 20, 22, 24].slice(t, t + 7);
        const h = mk({ kinds: pairs(kinds), winKind: kinds[0] });
        if (h) out.push(h);
    }
    // 国士：十三面（和牌前已 13 种）与普通（和牌前缺一种）
    for (let t = 0; t < 3; t++) {
        const extra = YAOCHU[t];
        const h = mk({ kinds: [...YAOCHU, extra], winKind: extra });
        if (h) out.push(h);
        const other = YAOCHU[(t + 5) % YAOCHU.length];
        const h2 = mk({ kinds: [...YAOCHU, extra], winKind: other });
        if (h2) out.push(h2);
    }
    // 九莲宝灯 / 纯正
    {
        const base = [0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 8];
        for (const extra of [0, 1, 4, 8]) {
            for (const w of [extra, base[Math.floor(base.length / 2)]]) {
                const h = mk({ kinds: [...base, extra], winKind: w });
                if (h) out.push(h);
            }
        }
        // 清一色 + 平和 + 一气 + 一杯口
        const h = mk({ kinds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12], winKind: 12 });
        if (h) out.push(h);
    }
    // 字一色 / 大三元 / 小三元 / 大四喜 / 小四喜
    {
        const add = (kinds, winKind) => {
            const h = mk({ kinds, winKind });
            if (h) out.push(h);
        };
        add([...trips([27, 28, 29, 31]), 32, 32], 27);                     // 字一色 + 三暗刻
        add([...trips([31, 32, 33]), ...trips([27]), 0, 0].slice(0, 14), 31); // 大三元（+ 1z 刻 + 1m 对）
        add([...trips([31, 32]), 33, 33, ...trips([0]), 9, 10].slice(0, 14), 31); // 小三元
        add([...trips([27, 28, 29, 30]), 31, 31], 27);                     // 大四喜
        add([...trips([27, 28, 29]), 30, 30, ...trips([0])], 30);           // 小四喜（+ 1m 刻）
    }
    // 绿一色 / 清老头 / 混老头 / 混一色 / 混全 / 纯全 / 同顺同刻 / 二杯口 / 一气
    {
        const add = (kinds, winKind) => {
            const h = mk({ kinds, winKind });
            if (h) out.push(h);
        };
        add([...trips([19, 20, 21, 23]), 32, 32], 32);                      // 绿一色
        add([...trips([0, 8, 9, 17]), 18, 18], 0);                          // 清老头（四刻 + 索对）
        add([...trips([0, 8, 27, 28]), 31, 31], 0);                         // 混老头（刻子含字牌）
        add([...trips([27, 28]), 0, 1, 2, 6, 7, 8, 31, 31].slice(0, 14), 27); // 混一色 + 混全带
        add([0, 1, 2, 6, 7, 8, 9, 10, 11, 15, 16, 17, 18, 18], 0);          // 纯全带幺九（无字）
        add([0, 1, 2, 9, 10, 11, 18, 19, 20, 3, 4, 5, 6, 6], 0);            // 三色同顺
        add([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 31, 31], 0);             // 一气通贯
        add([0, 0, 1, 1, 2, 2, 12, 12, 13, 13, 14, 14, 26, 26], 0);         // 二杯口
        add([...trips([0, 9, 18, 8]), 27, 27], 0);                          // 三色同刻
        add([...trips([0, 1, 2]), 12, 13, 14, 26, 26], 0);                  // 三连刻（需 koyaku）
        add([0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 4, 5, 7, 7], 0);                 // 一色三顺（需 koyaku）
        add([...trips([0, 1, 3, 4]), 6, 6], 0);                             // 对对和 + 三暗刻
    }
    // 大数邻 / 大车轮 / 大竹林 / 大七星（都要 koyaku）
    {
        for (const base of [0, 9, 18]) {
            const kinds = pairs([base + 1, base + 2, base + 3, base + 4, base + 5, base + 6, base + 7]);
            const h2 = mk({ kinds, winKind: base + 1 });
            if (h2) out.push(h2);
        }
        const h3 = mk({ kinds: pairs([27, 28, 29, 30, 31, 32, 33]), winKind: 33 });
        if (h3) out.push(h3);
    }
    // 一筒摸月 / 九筒捞鱼（古役 + 海底/河底 + 特定和了牌）
    {
        const h = mk({ kinds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 13], winKind: 9 });
        if (h) out.push(h);
        const h2 = mk({ kinds: [0, 1, 2, 3, 4, 5, 15, 16, 17, 18, 19, 20, 8, 8], winKind: 17 });
        if (h2) out.push(h2);
    }
    // 带副露的：碰 + 顺子 + 暗杠 + 加杠 + 大明杠
    {
        const b = handBuilder();
        const meldIds = {
            to: [b.take(31), b.take(31), b.take(31)],
            ro: [b.take(9), b.take(10), b.take(11)],
            qc: [b.take(20), b.take(20), b.take(20), b.take(20)],
        };
        const ids = [];
        for (const k of [0, 1, 2, 4, 4]) {
            const id = b.take(k);
            if (id < 0) return out;
            ids.push(id);
        }
        out.push({
            handIds: ids,
            melds: [`to:${meldIds.to.join(',')}`, `ro:${meldIds.ro.join(',')}`, `qc:${meldIds.qc.join(',')}`],
            winKind: 4,
        });
    }
    {
        const b = handBuilder();
        const qo = [b.take(0), b.take(0), b.take(0), b.take(0)];
        const ko = [b.take(2), b.take(2), b.take(2), b.take(2)];
        const ids = [];
        for (const k of [9, 10, 11, 13, 13]) {
            const id = b.take(k);
            if (id < 0) return out;
            ids.push(id);
        }
        out.push({
            handIds: ids,
            melds: [`qo:${qo.join(',')}`, `ko:${ko.join(',')}`],
            winKind: 13,
        });
    }
    return out;
}

const PRESETS = [
    'mleague', 'mleague+koyaku', 'mleague+dbl', 'tenhou', 'tenhou+kazoe', 'tenhou+koyaku',
    'majsoul', 'majsoul+koyaku', 'majsoul+kazoe', 'mleague+renhou', 'mleague+renhouy',
];

function pickFlags(r, opts) {
    let flags = 0;
    const menzen = opts.menzen;
    const tsumo = r() < 0.5;
    if (tsumo) flags |= 1 << 0;
    const dblRiichi = r() < 0.05;
    const riichi = !dblRiichi && r() < 0.25;
    if (riichi) flags |= 1 << 1;
    if (dblRiichi) flags |= 1 << 2;
    if ((riichi || dblRiichi) && r() < 0.2) flags |= 1 << 3;         // 一发
    if (!tsumo && r() < 0.05) flags |= 1 << 4;                        // 抢杠
    if (tsumo && r() < 0.05) flags |= 1 << 5;                         // 岭上
    if (tsumo && r() < 0.05) flags |= 1 << 6;                         // 海底
    if (!tsumo && r() < 0.05) flags |= 1 << 7;                        // 河底
    if (tsumo && menzen && r() < 0.03) flags |= 1 << 8;               // 天和（调用方把座位设成庄家）
    if (tsumo && menzen && r() < 0.03) flags |= 1 << 9;               // 地和（调用方把座位设成闲家）
    if (!tsumo && menzen && r() < 0.05) flags |= 1 << 10;             // 人和
    if (!tsumo && r() < 0.02) flags |= 1 << 11;                       // 燕返
    if (!tsumo && r() < 0.02) flags |= 1 << 12;                       // 杠振
    if (r() < 0.01) flags |= 1 << 13;                                 // 流局满贯
    return { flags, tsumo };
}

function buildCorpus(want) {
    const rows = [];
    const r = rng(0x5C0FEED ^ 0x9e37);
    const push = (o) => rows.push(o);

    // ① 特型
    for (const h of specialHands()) {
        for (const preset of ['mleague', 'mleague+koyaku', 'majsoul+dbl', 'tenhou']) {
            const { flags, tsumo } = pickFlags(r, { menzen: h.melds.length === 0, menzenOnly: false });
            const menzen = h.melds.every((m) => m.startsWith('qc'));
            push(mkRow({ r, preset, h, flags, tsumo, menzen }));
        }
    }

    // ② 随机构造
    let guard = 0;
    while (rows.length < want && guard++ < want * 30) {
        const meldCount = Math.floor(r() * (r() < 0.6 ? 1 : 5));
        const h = buildWinHand(r, { meldCount });
        if (!h) continue;
        // 破形：20% 的情况把一张换成别的牌（多数会变成"不是和了形"）
        if (r() < 0.2) {
            const idx = Math.floor(r() * h.handIds.length);
            const used = new Set(h.handIds);
            for (let a = 0; a < 30; a++) {
                const nk = Math.floor(r() * KIND);
                const cand = (nk << 2) | 0;
                if (h.handIds.some((x) => (x >> 2) === nk) && h.handIds.filter((x) => (x >> 2) === nk).length >= 4) continue;
                if (used.has(cand)) continue;
                h.handIds[idx] = cand;
                break;
            }
            h.winKind = h.handIds[Math.floor(r() * h.handIds.length)] >> 2;
        }
        const openMeld = h.melds.some((m) => !m.startsWith('qc'));
        const menzen = !openMeld;
        const preset = PRESETS[Math.floor(r() * PRESETS.length)];
        const { flags, tsumo } = pickFlags(r, { menzen, menzenOnly: false });
        push(mkRow({ r, preset, h, flags, tsumo, menzen }));
    }
    return rows;
}

function mkRow({ r, preset, h, flags, tsumo, menzen }) {
    let seat = Math.floor(r() * 4);
    const dealerSeat = Math.floor(r() * 4);
    const roundWind = 27 + Math.floor(r() * 4);
    let f = flags;
    if ((f & (1 << 8)) !== 0) {
        seat = dealerSeat;                                              // 天和只可能庄家
    } else if ((f & (1 << 9)) !== 0) {
        seat = (dealerSeat + 1) % 4;                                    // 地和只可能闲家
    }
    const loser = (f & 1) !== 0 ? -1 : ((seat + 1 + Math.floor(r() * 3)) % 4);
    const nDora = Math.floor(r() * 6);
    const dora = Array.from({ length: nDora }, () => Math.floor(r() * KIND));
    const ura = (f & (1 << 1)) !== 0 || (f & (1 << 2)) !== 0
        ? Array.from({ length: Math.floor(r() * 6) }, () => Math.floor(r() * KIND)) : [];
    let pao = '-';
    if (r() < 0.05) {
        const pseat = (seat + 1 + Math.floor(r() * 3)) % 4;
        const parts = [`${pseat}:${[8000, 16000, 6000][Math.floor(r() * 3)]}`];
        if (r() < 0.2) {
            const p2 = (pseat + 1 + Math.floor(r() * 2)) % 4;
            if (p2 !== seat) parts.push(`${p2}:${8000}`);
        }
        pao = parts.join(';');
    }
    return [
        preset, h.winKind, f, roundWind, seat, dealerSeat, menzen ? 1 : 0,
        Math.floor(r() * 4), Math.floor(r() * 4), loser,
        dora.length ? dora.join(',') : '-',
        ura.length ? ura.join(',') : '-',
        pao,
        h.handIds.join(','),
        h.melds.length ? h.melds.join(';') : '-',
    ].join(' ');
}

const corpus = join(BUILD, 'score-corpus.txt');
const outJava = join(BUILD, 'score-java.txt');
const outCpp = join(BUILD, 'score-cpp.txt');
const rows = buildCorpus(ROWS);
writeFileSync(corpus, rows.join('\n') + '\n', 'utf8');
console.log(`[score-parity] 语料 ${rows.length} 行 → ${corpus}`);

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

const t0 = Date.now();
runToFile('java', ['-cp', JAR, PROBE, corpus, outJava], join(BUILD, 'score-java.log'));
const tJava = Date.now() - t0;
const t1 = Date.now();
runToFile(EXE, ['score', corpus, outCpp], join(BUILD, 'score-cpp.log'));
const tCpp = Date.now() - t1;

const j = readFileSync(outJava, 'utf8').split('\n');
const c = readFileSync(outCpp, 'utf8').split('\n');
if (j.length && j[j.length - 1] === '') j.pop();
if (c.length && c[c.length - 1] === '') c.pop();

let fails = 0;
const firstBad = [];
if (j.length !== c.length) {
    fails++;
    console.error(`  [FAIL] 行数不同：java=${j.length} cpp=${c.length}`);
}
const n = Math.min(j.length, c.length);
const FIELDS = ['valid', 'hanYaku', 'han', 'fu', 'yakuman', 'dora', 'ura', 'aka', 'base', 'limit',
    'reason', 'form', 'yaku', 'delta', 'winnerGain', 'winnerPoints', 'riichiTaken'];
const fieldFails = new Array(FIELDS.length).fill(0);
for (let i = 0; i < n; i++) {
    if (j[i] !== c[i]) {
        fails++;
        const a = j[i].split(' ');
        const b = c[i].split(' ');
        for (let k = 0; k < FIELDS.length; k++) {
            if (a[k] !== b[k]) fieldFails[k]++;
        }
        if (firstBad.length < 6) firstBad.push(i);
    }
}
for (const i of firstBad) {
    const a = j[i].split(' ');
    const b = c[i].split(' ');
    const diff = FIELDS.map((fname, k) => (a[k] === b[k] ? null : `${fname}: java=${a[k]} cpp=${b[k]}`))
        .filter(Boolean);
    console.error(`  [FAIL] 第 ${i + 1} 行 —— ${diff.join(' | ')}`);
    console.error(`         语料: ${rows[i]}`);
}
if (fails) {
    console.error('  各字段差异计数：'
        + FIELDS.map((fname, k) => `${fname}=${fieldFails[k]}`).filter((s) => !s.endsWith('=0')).join(' '));
}
const speed = tCpp > 0 ? (tJava / tCpp).toFixed(2) : '—';
if (fails === 0) {
    console.log(`  [ok]   ${n}/${n} 行逐字段一致（java ${tJava} ms / cpp ${tCpp} ms → ${speed}×）`);
    console.log('[score-parity] PASS：役种 / 符数 / 打点 / 授受与 Java 逐字段一致');
    process.exit(0);
}
console.error(`[score-parity] FAIL：${fails} 行不一致（共 ${n} 行）`);
process.exit(1);
