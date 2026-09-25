#!/usr/bin/env node
/**
 * **天鳳牌譜 JSON 校验器**（tenhou.net/6 原生格式；`docs/input-json.md`）。
 *
 *   node tools/tenhou-log-check.mjs <牌谱.json> [...]
 *
 * 为什么要独立再写一遍：导出侧（`client/src/model/TenhouLog.cpp`）与参考实现
 * （`Equim-chan/mjai-reviewer` 的 `convlog/src/{tenhou/json_scheme.rs, tenhou/log.rs, conv.rs}`）
 * 是两份代码 —— 校验器**照抄参考实现的判据**，包括它最容易踩的那条：
 * 「取/出两张表怎么配对」（`finalize_discards` + 主循环的收工条件）。
 * 编解码两头都自己写 = 等于没验证；这里把"牌谱能不能被真的解析器吃下去"变成可跑的判据。
 *
 * 覆盖：
 *   ① 顶层：`log` 数组 / `name` 恰好 4 个 / `rule`（`disp` 不含「三」）
 *   ② 每局 17 元组：meta 3 项、四家点数、宝牌指示牌（首个必填）、里宝、四家配牌**恰好 13 张**、结果
 *   ③ 牌码集合：11..19 / 21..29 / 31..39 / 41..47 / 51/52/53；`60` 只能出现在「出」里；`0` 只能出现在「出」里
 *   ④ 鸣牌串：长度 + 关键字位置（吃 `c`[0] / 碰 `p`[0|2|4] / 大明杠 `m`[0|2|6] / 加杠 `k`[0|2|4] / 暗杠 `a`[6]）
 *      且**关键字后面紧挨着的那张就是被鸣的牌**
 *   ⑤ 走一遍事件流：摸切能回填、大明杠的 `0` 空位对得上、鸣牌的来源座位与被鸣牌 = 上一张打出、
 *      四家取牌用尽时收工（参考实现的两种收工条件之一必须成立）
 *   ⑥ 结果：`和了` → `["和了", 四家增减, [和了家, 放銃家, …], …]`（成对）；否则 `[状态字, 四家增减]`
 *
 * ⚠ 这是**格式**校验，不是规则校验（不判和牌是否合法、番符对不对）。
 */
import { readFileSync } from 'node:fs';

const VALID_TILES = new Set();
for (let n = 11; n <= 19; n++) VALID_TILES.add(n);
for (let n = 21; n <= 29; n++) VALID_TILES.add(n);
for (let n = 31; n <= 39; n++) VALID_TILES.add(n);
for (let n = 41; n <= 47; n++) VALID_TILES.add(n);
for (const n of [51, 52, 53]) VALID_TILES.add(n);

/** 牌码 → 参考实现里的 `Tile` 值（0..33 普通牌、34..36 赤五、37 未知）。 */
function tileOf(n) {
    if (n === 51) return 34;
    if (n === 52) return 35;
    if (n === 53) return 36;
    if (n === 0 || n === 60) return 37;
    if (n >= 11 && n <= 19) return n - 11;
    if (n >= 21 && n <= 29) return 9 + (n - 21);
    if (n >= 31 && n <= 39) return 18 + (n - 31);
    if (n >= 41 && n <= 47) return 27 + (n - 41);
    return -1;
}

const two = (s) => parseInt(s.slice(0, 2), 10);

/**
 * 从鸣牌串里取出**各家牌码**：关键字占 1 个字符，其余每 2 个字符一张牌。
 * 关键字**后面紧挨着**的那张就是被鸣的牌（`conv.rs` 就是按 `naki[k+1..k+3]` 读的）。
 * @returns {string[]|null} 依次排列的牌码字符串（2 位），非法返回 null
 */
function nakiTiles(s, keyPos) {
    const parts = [];
    for (let i = 0; i < s.length; ) {
        if (i === keyPos) {
            i += 1;
            continue;
        }
        const part = s.slice(i, i + 2);
        if (!/^\d\d$/.test(part)) return null;
        parts.push(part);
        i += 2;
    }
    return parts;
}

/**
 * 解析一条鸣牌串（判据照抄 `conv.rs` 的 `take_action_to_events` / `discard_action_to_events`）。
 * @returns {{kind:string,called:number,target?:number,keyPos:number,order:number}|{error:string}}
 */
function parseNaki(s, actor, where) {
    const len = s.length;
    const idxOf = (ch) => {
        const i = s.indexOf(ch);
        return i < 0 ? -1 : i;
    };
    // 关键字位置固定 → 牌码用 `nakiTiles` 取；被鸣那张 = 关键字后面那张 = parts[keyPos / 2]
    const build = (kind, keyChar, keyPos) => {
        const parts = nakiTiles(s, keyPos);
        if (parts === null) {
            return { error: `${where}: ${kind}串里有非法牌码` };
        }
        const nums = parts.map((p) => parseInt(p, 10));
        if (!nums.every((n) => VALID_TILES.has(n))) {
            return { error: `${where}: ${kind}串里有非法牌码` };
        }
        const called = nums[keyPos / 2];
        return { kind, called, keyPos, tiles: nums };
    };
    if (idxOf('c') >= 0) {
        if (len !== 7 || s[0] !== 'c') return { error: `${where}: 吃串必须是 c + 3×2 位（7 字符）` };
        const r = build('chi', 'c', 0);
        if (r.error) return r;
        r.target = (actor + 3) % 4;   // 吃只能来自上家
        r.order = 0;
        return r;
    }
    const p = idxOf('p');
    if (p >= 0) {
        if (len !== 7) return { error: `${where}: 碰串必须是 7 字符` };
        if (p !== 0 && p !== 2 && p !== 4) {
            return { error: `${where}: 碰的关键字只能在 [0]/[2]/[4]（实际 ${p}）` };
        }
        const r = build('pon', 'p', p);
        if (r.error) return r;
        r.target = (actor + (p === 0 ? 3 : (p === 2 ? 2 : 1))) % 4;
        r.order = 1;
        return r;
    }
    const m = idxOf('m');
    if (m >= 0) {
        if (len !== 9) return { error: `${where}: 大明杠串必须是 9 字符` };
        if (m !== 0 && m !== 2 && m !== 6) {
            return { error: `${where}: 大明杠的关键字只能在 [0]/[2]/[6]（实际 ${m}）` };
        }
        const r = build('daiminkan', 'm', m);
        if (r.error) return r;
        r.target = (actor + (m === 0 ? 3 : (m === 2 ? 2 : 1))) % 4;
        r.order = 2;
        return r;
    }
    const k = idxOf('k');
    if (k >= 0) {
        if (len !== 9) return { error: `${where}: 加杠串必须是 9 字符` };
        if (k !== 0 && k !== 2 && k !== 4) {
            return { error: `${where}: 加杠的关键字只能在 [0]/[2]/[4]（实际 ${k}）` };
        }
        const r = build('kakan', 'k', k);
        if (r.error) return r;
        r.order = 1;
        return r;
    }
    const a = idxOf('a');
    if (a >= 0) {
        if (len !== 9) return { error: `${where}: 暗杠串必须是 9 字符` };
        if (a !== 6) return { error: `${where}: 暗杠的关键字只能在 [6]（实际 ${a}）` };
        const r = build('ankan', 'a', 6);
        if (r.error) return r;
        r.order = 1;
        return r;
    }
    if (idxOf('r') === 0) {
        if (len !== 3) return { error: `${where}: 立直串必须是 r + 2 位（3 字符）` };
        if (s.slice(1) === '60') return { kind: 'reach', called: 60, keyPos: 0, order: 0 };
        const n = two(s.slice(1));
        if (!VALID_TILES.has(n)) return { error: `${where}: 立直串里的牌码非法` };
        return { kind: 'reach', called: n, keyPos: 0, order: 0 };
    }
    return { error: `${where}: 认不出的鸣牌串 ${JSON.stringify(s)}` };
}

/** 取 → 事件流（`Tsumo` / `Chi` / `Pon` / `Daiminkan`）。 */
function takesToEvents(actor, takes, errs, tag) {
    const out = [];
    takes.forEach((t, i) => {
        const where = `${tag} takes[${i}]`;
        if (typeof t === 'number') {
            if (t === 60) {
                errs.push(`${where}: 60 不能出现在「取」里（tsumogiri should not exist in discard table）`);
                return;
            }
            if (!VALID_TILES.has(t)) {
                errs.push(`${where}: 非法牌码 ${t}`);
                return;
            }
            out.push({ type: 'tsumo', tile: t });
            return;
        }
        if (typeof t !== 'string') {
            errs.push(`${where}: 元素只能是整数或字符串`);
            return;
        }
        const nk = parseNaki(t, actor, where);
        if (nk.error) {
            errs.push(nk.error);
            return;
        }
        if (nk.kind === 'reach' || nk.kind === 'ankan' || nk.kind === 'kakan') {
            errs.push(`${where}: ${nk.kind} 不能出现在「取」里`);
            return;
        }
        out.push({ type: nk.kind, tile: nk.called, target: nk.target });
    });
    return out;
}

/** 出 → 事件流（`Dahai` / `Reach`+`Dahai` / `Kakan` / `Ankan`）。 */
function discardsToEvents(actor, discards, errs, tag) {
    const out = [];
    discards.forEach((d, i) => {
        const where = `${tag} discards[${i}]`;
        if (typeof d === 'number') {
            if (d === 60) {
                out.push({ type: 'dahai', tile: 60, tsumogiri: true });
                return;
            }
            if (d === 0) {
                out.push({ type: 'dahai', tile: 0, tsumogiri: false });
                return;
            }
            if (!VALID_TILES.has(d)) {
                errs.push(`${where}: 非法牌码 ${d}`);
                return;
            }
            out.push({ type: 'dahai', tile: d, tsumogiri: false });
            return;
        }
        if (typeof d !== 'string') {
            errs.push(`${where}: 元素只能是整数或字符串`);
            return;
        }
        const nk = parseNaki(d, actor, where);
        if (nk.error) {
            errs.push(nk.error);
            return;
        }
        if (nk.kind === 'reach') {
            out.push({ type: 'reach' });
            out.push(nk.called === 60
                ? { type: 'dahai', tile: 60, tsumogiri: true }
                : { type: 'dahai', tile: nk.called, tsumogiri: false });
        } else if (nk.kind === 'ankan' || nk.kind === 'kakan') {
            out.push({ type: nk.kind });
        } else {
            errs.push(`${where}: ${nk.kind} 不能出现在「出」里`);
        }
    });
    return out;
}

/** 参考实现的 `finalize_discards`：回填摸切、删掉大明杠留下的 `0` 空位。 */
function finalizeDiscards(takes, discards) {
    let di = 0;
    for (const take of takes) {
        if (di >= discards.length) break;
        if (discards[di].type === 'reach') di += 1;
        const d = discards[di];
        if (d && d.type === 'dahai') {
            if (d.tsumogiri) {
                if (take.type === 'tsumo') d.tile = take.tile;
            } else if (tileOf(d.tile) === 37) {
                discards.splice(di, 1);   // 大明杠的空位：删掉且不推进下标
                continue;
            }
        }
        di += 1;
    }
}

function checkKyoku(kyoku, ki, errs) {
    const tag = `第 ${ki + 1} 局`;
    if (!Array.isArray(kyoku) || kyoku.length < 17) {
        errs.push(`${tag}: 必须是 17 元组（实际 ${Array.isArray(kyoku) ? kyoku.length : typeof kyoku}）`);
        return;
    }
    const [meta, scoreboard, dora, ura] = kyoku;
    if (!Array.isArray(meta) || meta.length !== 3 || !meta.every((n) => Number.isInteger(n))) {
        errs.push(`${tag}: meta 必须是 3 个整数 [场局次, 本场, 供託]`);
    }
    if (!Array.isArray(scoreboard) || scoreboard.length !== 4) {
        errs.push(`${tag}: scoreboard 必须是 4 家点数`);
    }
    if (!Array.isArray(dora) || dora.length < 1) {
        errs.push(`${tag}: dora_indicators 第一个必填`);
    }
    for (const [name, arr] of [['dora', dora], ['ura', ura]]) {
        for (const n of arr || []) {
            if (!VALID_TILES.has(n)) errs.push(`${tag}: ${name} 指示牌里有非法牌码 ${n}`);
        }
    }

    const tables = [];
    for (let s = 0; s < 4; s++) {
        const haipai = kyoku[4 + s * 3];
        const takes = kyoku[5 + s * 3];
        const discards = kyoku[6 + s * 3];
        if (!Array.isArray(haipai) || haipai.length !== 13) {
            errs.push(`${tag} 座位 ${s}: 配牌必须**恰好 13 张**（实际 ${haipai?.length}）`);
        }
        for (const n of haipai || []) {
            if (!VALID_TILES.has(n)) errs.push(`${tag} 座位 ${s}: 配牌里有非法牌码 ${n}`);
        }
        if (!Array.isArray(takes) || !Array.isArray(discards)) {
            errs.push(`${tag} 座位 ${s}: takes / discards 必须是数组`);
            tables.push(null);
            continue;
        }
        const ev1 = takesToEvents(s, takes, errs, `${tag} 座位 ${s}`);
        const ev2 = discardsToEvents(s, discards, errs, `${tag} 座位 ${s}`);
        finalizeDiscards(ev1, ev2);
        tables.push({ takes: ev1, discards: ev2, ti: 0, di: 0 });
    }
    // 每杠一张指示牌
    const kans = tables.reduce((acc, t) => acc + (t ? t.takes.filter((e) => e.type === 'daiminkan').length
        + t.discards.filter((e) => e.type === 'ankan' || e.type === 'kakan').length : 0), 0);
    if ((dora || []).length < 1 + kans) {
        errs.push(`${tag}: 宝牌指示牌不够（有 ${kans} 次杠，需要 ≥ ${1 + kans} 张，实际 ${dora.length}）`);
    }
    // 结果
    const results = kyoku[16];
    if (!Array.isArray(results) || results.length < 1 || typeof results[0] !== 'string') {
        errs.push(`${tag}: results 必须是数组且第 0 项是状态字符串`);
    } else if (results[0] === '和了') {
        const rest = results.slice(1);
        if (rest.length % 2 !== 0 || rest.length === 0) {
            errs.push(`${tag}: 和了后必须成对出现 [四家增减, [和了家, 放銃家, …]]`);
        }
        for (let i = 0; i + 1 < rest.length; i += 2) {
            if (!Array.isArray(rest[i]) || rest[i].length !== 4) {
                errs.push(`${tag}: 每组第 1 项必须是四家点数增减`);
            }
            if (!Array.isArray(rest[i + 1]) || rest[i + 1].length < 2
                || !Number.isInteger(rest[i + 1][0]) || !Number.isInteger(rest[i + 1][1])) {
                errs.push(`${tag}: 和了明细前两项必须是 [和了家, 放銃家] 两个整数`);
            }
        }
    } else if (results.length >= 2 && (!Array.isArray(results[1]) || results[1].length !== 4)) {
        errs.push(`${tag}: 流局的第 2 项必须是四家点数增减（可省略）`);
    }

    // ---- 走一遍事件流（参考实现的主循环）----
    if (tables.some((t) => t === null)) return;
    const TRACE = process.argv.includes('--trace');
    const oya = ((meta[0] % 4) + 4) % 4;
    let actor = oya;
    let lastDiscard = 37;      // 未知
    let lastActor = null;
    let guard = 0;
    for (;;) {
        if (++guard > 4000) {
            errs.push(`${tag}: 事件流走不完（疑似取/出配对不齐）`);
            return;
        }
        const t = tables[actor];
        const take = t.takes[t.ti];
        if (TRACE && guard < 80) {
            console.log(`    k${ki + 1} #${guard} actor=${actor} take=${JSON.stringify(take)}`
                + ` last=${lastDiscard}(by ${lastActor}) di=${t.di}/${t.discards.length}`);
        }
        if (take === undefined) {
            // 参考实现：任一家取牌用尽就报 insufficient take sequence size
            errs.push(`${tag}: 座位 ${actor} 该摸牌了但「取」已用尽（insufficient take sequence size）`);
            return;
        }
        t.ti += 1;
        if (take.type !== 'tsumo') {
            if (tileOf(take.tile) !== lastDiscard || lastActor !== take.target || take.target === actor) {
                errs.push(`${tag}: 座位 ${actor} 的鸣牌对不上上一张打出`
                    + `（被鸣 ${take.tile}，上一张 ${lastDiscard === 37 ? '?' : lastDiscard}，`
                    + `来源 ${take.target}，上一手 ${lastActor}）`);
                return;
            }
        }
        if (take.type === 'daiminkan') {
            continue;   // 立刻再吃一次自家的「取」（岭上）
        }
        if (t.di >= t.discards.length) return;   // 收工条件①：这家没有下一张出牌
        let d = t.discards[t.di];
        t.di += 1;
        // ⚠ 立直宣言**占两个事件**（Reach + Dahai），但只占「出」表里的**一个元素** ——
        //   参考实现就在同一巡里把这两个一起消费掉（`if let Event::Reach { .. } = discard { … }`）。
        if (d.type === 'reach') {
            const next = t.discards[t.di];
            if (!next || next.type !== 'dahai') {
                errs.push(`${tag}: 座位 ${actor} 的立直宣言后面没有宣言牌（insufficient discard sequence size）`);
                return;
            }
            t.di += 1;
            d = next;
        }
        if (d.type === 'dahai') {
            lastDiscard = tileOf(d.tile);
            lastActor = actor;
        } else if (d.type === 'ankan' || d.type === 'kakan') {
            // ⚠ 暗杠/加杠之后**还是这一家摸牌**（岭上）—— 参考实现在这两个分支里 `continue`，
            //   同一家立刻消费下一手「取」。漏了这一步，后面每家都会错位一格。
            continue;
        }
        if (tables.every((x) => x.ti >= x.takes.length)) return;   // 收工条件②：四家取牌都用尽
        // 下一家：谁正等着鸣这张牌（同级取"等级更高"的那个：碰/杠 > 吃，与参考实现的 naki_ord 一致），
        // 没有鸣牌就是下家。
        let next = (actor + 1) % 4;
        let bestOrder = -1;
        for (let a = 0; a < 4; a++) {
            if (a === actor) continue;
            const cand = tables[a].takes[tables[a].ti];
            if (cand && cand.type !== 'tsumo' && cand.target === actor
                && tileOf(cand.tile) === lastDiscard && (cand.order ?? 0) > bestOrder) {
                next = a;
                bestOrder = cand.order ?? 0;
            }
        }
        actor = next;
    }
}

function checkFile(path) {
    const errs = [];
    let root;
    try {
        root = JSON.parse(readFileSync(path, 'utf8'));
    } catch (e) {
        return [`${path}: JSON 解析失败 —— ${e.message}`];
    }
    if (!Array.isArray(root.log)) errs.push('顶层必须有 `log` 数组（裸数组不行）');
    if (!Array.isArray(root.name) || root.name.length !== 4) errs.push('顶层 `name` 必须恰好 4 个');
    if (root.rule === null || typeof root.rule !== 'object') {
        errs.push('顶层必须有 `rule` 对象');
    } else {
        const disp = root.rule.disp ?? '';
        if (disp.includes('三') || disp.includes('3-Player')) {
            errs.push(`rule.disp 含三麻标记（${disp}）→ not four-player game`);
        }
    }
    (root.log || []).forEach((k, i) => checkKyoku(k, i, errs));
    return errs.map((e) => `${path}: ${e}`);
}

const argv = process.argv.slice(2);
const files = argv.filter((a) => !a.startsWith('--'));
if (files.length === 0) {
    console.error('用法：node tools/tenhou-log-check.mjs [--trace] <牌谱.json> [...]');
    process.exit(2);
}
let bad = 0;
for (const f of files) {
    const errs = checkFile(f);
    if (errs.length === 0) {
        console.log(`  [ok]   ${f}：结构与取/出配对全部通过`);
    } else {
        bad += errs.length;
        for (const e of errs.slice(0, 20)) console.error(`  [FAIL] ${e}`);
    }
}
console.log(bad === 0 ? 'TENHOU-LOG PASS' : `TENHOU-LOG FAIL（${bad} 条）`);
process.exit(bad === 0 ? 0 : 1);
