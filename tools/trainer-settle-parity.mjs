#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎：顺位点 / 终局余棒 / 连庄判据 / 种子链差分对拍**。
 *
 *   node tools/trainer-settle-parity.mjs [每类用例数]
 *
 * 为什么这一层要单独对拍：`rank_points`（顺位点）是**奖励函数的直接输入**
 * （`python/mahjong_ml/rewards.py` 读它、从不自己重算 uma），而它由一串"看起来都很简单"的
 * 判据算出来 —— 连庄/本场数/和了止/延长战/同点拆分/终局余棒。这些判据在 Java 侧原来是散在
 * 五个地方各写一行（AUDIT S-46 / S-54 / S-55 / S-64），每一处写错都只表现为"分数算错但不报错"。
 *
 * 比对面（每行一次结果）：
 *   settle   顺位 order[4] / rank[4] / 顺位点 point[4] / 马点 uma[4] / 头名赏 oka[4]
 *   sticks   终局余棒分配 add[4]（并列第一时按"100 点为单位、尾数归更接近起家者"）
 *   honba    下一局本场数（连庄 / 闲家和了 / 流局满贯 / 荒牌流局轮庄 四种口径的真值表）
 *   alllast  和了止 / 听牌止（三条件）
 *   west     延长战门槛（⚠ 是 requiredPoints，不是 returnScore；场风上限 lastWind+1）
 *   nagashi  流局满贯支付 / ngelig 流局满资格
 *   split    通用拆分（负数下取整 —— 末位是负顺位点）
 *   seeds/seedfor/place  种子链与顺位（"同种子 → 同轨迹"的地基）
 *
 * ⚠ 浮点比的是**原始位模式**（两边都打印 `%016x`）：值相同但十进制格式化不同也会被判为不同，
 *   这是刻意的 —— 顺位点会写进数据集，位模式一致才算同源。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'SettleProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

const argv = process.argv.slice(2);
const N = Number(argv.find((a) => /^\d+$/.test(a)) ?? 20000);

if (!existsSync(JAR)) {
    console.error(`[settle-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[settle-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

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

const PRESETS = ['mleague', 'tenhou', 'majsoul', 'mleague+dbl', 'tenhou+kazoe', 'majsoul'];
const YAOCHU_KINDS = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33];

function buildCorpus(want) {
    const rows = [];
    const r = rng(0x5E77 ^ 0x9e37);
    const pick = (arr) => arr[Math.floor(r() * arr.length)];
    const score = () => {
        // 刻意高频制造**同点**（同点拆分/并列顺位是这一层最容易写错的地方）
        if (r() < 0.25) {
            const base = [25000, 30000, 0, -2000, 53600, 28600, 20000, -2200][Math.floor(r() * 8)];
            return base;
        }
        return Math.round((r() * 120000 - 20000) / 100) * 100;
    };

    // ① 精算（含同点、并列、极端分）
    rows.push('settle mleague 53600 28600 20000 -2200');       // 文档里的例子：+73.6/+8.6/−20/−62.2
    rows.push('settle majsoul 53600 28600 20000 -2200');       // 《雀魂》：+43.6/+8.6/−10/−42.2
    rows.push('settle mleague 25000 25000 25000 25000');        // 四家同点
    rows.push('settle mleague 30000 30000 30000 10000');        // 三家同点（尾数归更接近起家者）
    rows.push('settle tenhou 30000 30000 10000 10000');         // 两家同点（不开 tieSplitPoint）
    rows.push('settle mleague 0 0 0 0');
    rows.push('settle tenhou 99999 -99999 1 -1');
    for (let i = 0; i < want; i++) {
        const preset = pick(PRESETS);
        const nTie = r() < 0.3 ? 2 + Math.floor(r() * 3) : 0;
        const scores = [score(), score(), score(), score()];
        for (let k = 0; k < nTie; k++) {
            scores[(k + 1) % 4] = scores[0];
        }
        rows.push(`settle ${preset} ${scores.join(' ')}`);
    }
    // ② 终局余棒（1..5 根；含并列第一）
    for (let sticks = 1; sticks <= 5; sticks++) {
        rows.push(`sticks 30000 30000 20000 20000 ${sticks}`);
        rows.push(`sticks 30000 29000 20000 1000 ${sticks}`);
        rows.push(`sticks 25000 25000 25000 25000 ${sticks}`);
        rows.push(`sticks -5000 25000 30000 30000 ${sticks}`);
    }
    for (let i = 0; i < want; i++) {
        const s = [score(), score(), score(), score()];
        if (r() < 0.4) {
            s[1] = s[0];
            if (r() < 0.5) s[2] = s[0];
        }
        rows.push(`sticks ${s.join(' ')} ${1 + Math.floor(r() * 6)}`);
    }
    // ③ 本场数真值表（honba × 连庄 × 和了 × 流局满贯）
    for (let honba = 0; honba <= 3; honba++) {
        for (let renchan = 0; renchan <= 1; renchan++) {
            for (let agari = 0; agari <= 1; agari++) {
                for (let nagashi = 0; nagashi <= 1; nagashi++) {
                    rows.push(`honba ${honba} ${renchan} ${agari} ${nagashi}`);
                }
            }
        }
    }
    // ④ 和了止 / 听牌止
    for (const preset of ['mleague', 'tenhou', 'majsoul']) {
        for (let dealer = 0; dealer < 4; dealer++) {
            for (const agari of [0, 1]) {
                for (const nagashi of [0, 1]) {
                    for (let bits = 0; bits < 16; bits++) {
                        const scores = preset === 'mleague'
                            ? [30000, 25000, 25000, 20000]
                            : [32000, 25000, 25000, 18000];
                        if (dealer !== 0) scores[dealer] = preset === 'mleague' ? 29000 : 28000;
                        rows.push(`alllast ${preset} ${dealer} ${agari} ${nagashi} ${bits} ${scores.join(' ')}`);
                    }
                }
            }
        }
    }
    for (let i = 0; i < want; i++) {
        rows.push(`alllast ${pick(PRESETS)} ${Math.floor(r() * 4)} ${r() < 0.5 ? 1 : 0} `
            + `${r() < 0.3 ? 1 : 0} ${Math.floor(r() * 16)} ${score()} ${score()} ${score()} ${score()}`);
    }
    // ⑤ 延长战门槛（⚠ 必须开 westExtension，否则那条分支永远比不到 —— 之前这里全是 0 值）
    for (const preset of ['mleague', 'mleague+west', 'tenhou+west', 'majsoul+west']) {
        for (const top of [0, 29000, 30000, 31000, 40000]) {
            for (let nw = 0; nw <= 3; nw++) {
                for (let lw = 0; lw <= 2; lw++) {
                    rows.push(`west ${preset} ${top} ${nw} ${lw}`);
                }
            }
        }
    }
    // ⑥ 流局满贯支付 / 资格
    for (let w = 0; w < 4; w++) {
        for (let d = 0; d < 4; d++) {
            rows.push(`nagashi ${w} ${d}`);
        }
    }
    rows.push('ngelig 0 -');
    rows.push('ngelig 1 0,8,9,17');
    rows.push('ngelig 0 0,4,8');
    rows.push(`ngelig 0 ${YAOCHU_KINDS.map((k) => k * 4).join(',')}`);
    rows.push(`ngelig 1 ${YAOCHU_KINDS.map((k) => k * 4).join(',')}`);
    for (let i = 0; i < want; i++) {
        const n = Math.floor(r() * 14);
        const ids = [];
        for (let k = 0; k < n; k++) {
            ids.push(r() < 0.5 ? YAOCHU_KINDS[Math.floor(r() * YAOCHU_KINDS.length)] * 4
                : Math.floor(r() * 34) * 4);
        }
        rows.push(`ngelig ${r() < 0.3 ? 1 : 0} ${ids.length ? ids.join(',') : '-'}`);
    }
    // ⑦ 通用拆分（含负数的 total：末位顺位点是负的）
    for (const total of [1000, 2000, 3000, 300, 500, -1, -300, -1000, -73, -2200, 0, 7]) {
        for (let n = 1; n <= 4; n++) {
            for (const unit of [1, 100]) {
                rows.push(`split ${total} ${n} ${unit}`);
            }
        }
    }
    // ⑧ 种子链与顺位
    for (const base of [20260101, 0, 1, -1, 9223372036854775807n, -9223372036854775808n]) {
        rows.push(`seeds ${base} 8`);
        rows.push(`seedfor ${base} 8`);
    }
    rows.push('place 30000 25000 25000 20000');
    rows.push('place 25000 25000 25000 25000');
    rows.push('place 10000 20000 30000 40000');
    rows.push('place 1 1 2 2');
    for (let i = 0; i < Math.floor(want / 4); i++) {
        const s = [score(), score(), score(), score()];
        if (r() < 0.4) s[1] = s[0];
        rows.push(`place ${s.join(' ')}`);
    }
    // ⑨ 鸣牌仲裁判据（`RoundClaims`）：等级 / 能否压过 / 荣和收齐 / 收工 / 回包认领
    //    —— 这一层最容易写错的是"提前收工改变赢家"（AGENTS §2.3-10）
    for (const t of ['ron', 'kan', 'pon', 'chi', 'pass', 'tsumo', 'nosuch', '']) {
        rows.push(`rank ${t}`);
    }
    for (const bestRank of [-1, 0, 1, 2, 3]) {
        for (const bestDist of [0, 1, 2, 3]) {
            for (const rank of [-1, 0, 1, 2, 3]) {
                for (const dist of [0, 1, 2, 3]) {
                    rows.push(`beat ${bestRank}:${bestDist} ${rank} ${dist}`);
                    rows.push(`beat - ${rank} ${dist}`);          // 还没有任何人鸣牌
                }
            }
        }
    }
    for (const ron of ['-', '0', '1', '2', '3', '0,2', '1,2,3']) {
        for (const answered of ['-', '0', '0,1', '0,1,2,3', '1,3']) {
            rows.push(`allron ${ron} ${answered}`);
        }
    }
    const SEATS = ['-', '0', '1', '0,1', '1,2', '0,1,2,3', '2,3'];
    const RANKS = [-1, 0, 1, 2, 3];
    for (let i = 0; i < want; i++) {
        const asked = pick(SEATS);
        const ron = pick(SEATS);
        const answered = pick(SEATS);
        const remain = pick([-1, 0, 1, 500, 5000]);
        const best = r() < 0.25 ? '-' : `${pick(RANKS)}:${1 + Math.floor(r() * 3)}`;
        const askedBestRank = r() < 0.2 ? '-' : asked === '-' ? '-'
            : asked.split(',').filter((s) => r() < 0.8)
                .map((s) => `${s}:${pick(RANKS)}`).join(';') || '-';
        const seatDist = r() < 0.2 ? '-' : '0:1;1:2;2:3;3:1';
        rows.push(`stop ${asked} ${ron} ${answered} ${remain} ${best} ${askedBestRank} ${seatDist}`);
    }
    for (const seatIsAsked of [0, 1]) {
        for (const pending of ['-', '7']) {
            for (const hasReplyId of [0, 1]) {
                for (const replyId of [0, 7, 8]) {
                    rows.push(`accept ${seatIsAsked} ${pending} ${hasReplyId} ${replyId}`);
                }
            }
        }
    }
    // ⑩ 振听记账（用**真实的 Round** 驱动：公开字段 + `debugPushDiscard` 这唯一记账点）
    const winHands = [
        { mc: 0, kinds: [1, 2, 3, 4, 5, 15, 16, 17, 18, 18, 18, 28, 28] },   // 听 3m/6m
        { mc: 0, kinds: [0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 8] },          // 九莲听牌
        { mc: 1, kinds: [1, 2, 3, 4, 5, 15, 16, 17, 18, 18] },
        { mc: 0, kinds: [0, 2, 4, 6, 8, 9, 11, 13, 15, 18, 20, 22, 24] },   // 七对子听牌
    ];
    for (const h of winHands) {
        for (const discards of ['-', '1', '3', '1,6', '0,1,2,3,4,5,6,7,8', '27,33']) {
            for (const temp of [0, 1]) {
                for (const perm of [0, 1]) {
                    rows.push(`furiten ${h.mc} ${h.kinds.join(',')} ${discards} ${temp} ${perm} 0`);
                }
            }
        }
        rows.push(`furiten ${h.mc} ${h.kinds.join(',')} 3 0 0 1`);
        rows.push(`furiten ${h.mc} ${h.kinds.join(',')} 3 0 0 2`);
    }
    for (let i = 0; i < Math.floor(want / 8); i++) {
        const mc = Math.floor(r() * 3);
        const size = 13 - 3 * mc;
        const kinds = [];
        for (let k = 0; k < size; k++) kinds.push(Math.floor(r() * 34));
        const size2 = 13 - 3 * mc;
        const c = new Array(34).fill(0);
        for (const k of kinds) c[k]++;
        const disc = [];
        const nd = Math.floor(r() * 5);
        for (let k = 0; k < nd; k++) disc.push(Math.floor(r() * 34));
        rows.push(`furiten ${mc} ${kinds.join(',')} ${disc.length ? disc.join(',') : '-'} `
            + `${r() < 0.3 ? 1 : 0} ${r() < 0.2 ? 1 : 0} ${Math.floor(r() * 4)}`);
    }
    // ⑪ 可见牌统计（`Visible`）+ 和了形纯判断（`WinCheck`）—— 观测特征的底座
    const rivers = ['-', '0,4,8', '16,17,18,52,53', '0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18'];
    const meldSpecs = ['-', '0:ro:0,1,2', '1:to:52,53,54;2:qo:88,89,90,91',
        '3:qc:16,17,18,19;3:ko:20,21,22,23;0:ro:24,25,26'];
    for (const r0 of rivers) {
        for (const r1 of ['-', '52,53']) {
            for (const ms of meldSpecs) {
                for (const dora of ['-', '0', '8,9,27', '4,13,22']) {
                    rows.push(`visible ${r0} ${r1} - - ${ms} ${dora}`);
                }
            }
        }
    }
    for (let i = 0; i < Math.floor(want / 6); i++) {
        const mkRiver = () => {
            const n = Math.floor(r() * 6);
            const ids = [];
            for (let k = 0; k < n; k++) ids.push(Math.floor(r() * 136));
            return ids.length ? ids.join(',') : '-';
        };
        const vis = new Array(34).fill(0);
        for (let k = 0; k < 34; k++) vis[k] = r() < 0.3 ? Math.floor(r() * 5) : 0;
        const own = new Array(34).fill(0);
        for (let k = 0; k < 34; k++) own[k] = r() < 0.2 ? Math.floor(r() * 4) : 0;
        rows.push(`visible ${mkRiver()} ${mkRiver()} ${mkRiver()} ${mkRiver()} - `
            + `${r() < 0.5 ? '-' : String(Math.floor(r() * 34))}`);
        rows.push(`vis ${vis.join(',')} ${own.join(',')}`);
    }
    for (const tsumo of [0, 1]) {
        for (const furiten of [0, 1]) {
            rows.push(`block ${tsumo} ${furiten}`);
        }
    }
    for (const mc of [0, 1, 2, 3, 4]) {
        for (const tsumo of [0, 1]) {
            const size = (tsumo ? 14 : 13) - 3 * mc;
            const c = new Array(34).fill(0);
            let placed = 0;
            while (placed < size) {
                const k = Math.floor(r() * 34);
                if (c[k] >= 4) continue;
                c[k]++;
                placed++;
            }
            rows.push(`wcounts ${mc} ${c.join(',')} ${Math.floor(r() * 34)} ${tsumo}`);
            // 故意张数错一位 → 两边都必须判 "-"
            c[Math.floor(r() * 34)]++;
            rows.push(`wcounts ${mc} ${c.join(',')} ${Math.floor(r() * 34)} ${tsumo}`);
        }
    }
    // ⑫ `Round` 构造 + 配牌（真实 Round 驱动）：menzen/temp/perm/doubleRiichi/ippatsu 初值、
    //    牌山账（构造后 122 / 配牌后 69 / 岭上 4）、庄家第 14 张、手牌、指示牌、排序
    for (const preset of ['mleague', 'tenhou', 'majsoul']) {
        for (const dealer of [0, 1, 2, 3]) {
            for (const seed of ['20260101', '0', '1', '-1', '9223372036854775807']) {
                rows.push(`rinit ${preset} ${seed} ${dealer} ${dealer} 25000 25000 25000 25000`);
            }
        }
    }
    for (let i = 0; i < Math.floor(want / 10); i++) {
        rows.push(`rinit mleague ${BigInt(Math.floor(r() * 1e15))} ${Math.floor(r() * 4)} `
            + `${Math.floor(r() * 4)} 25000 25000 25000 25000`);
    }
    // ⑬ `RoundOptions` 的四个纯函数（选项生成的第一层：可打牌 / 食替 / 立直后杠 / 吃搭子）
    //    为什么单独钉：它们的输出**同时**是下发选项、服务端校验和训练端动作空间 —— 三处必须同源
    const yao = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33];
    if (yao.length !== 13) throw new Error('幺九牌表出错');
    // 可打牌：已立直（只剩摸切一张）/ 未立直（去重 + 振听禁打）/ 带赤五 / 振听禁打整色
    rows.push('ropts discard 0 -1 0,1,2,4,8,9,13,36,37 -');
    rows.push('ropts discard 1 5 0,1,2,4,8,9,13,36,37 -');
    rows.push('ropts discard 0 5 0,1,2,4,8,9,13,36,37 0,9,13');
    rows.push('ropts discard 1 -1 0,1,2,4,8,9,13,36,37 0');
    rows.push('ropts discard 0 -1 4,5,6,7,52,53,54 -');       // 赤五与普通五各自成串（0m vs 5m）
    for (let i = 0; i < 40; i++) {
        const n = 1 + Math.floor(r() * 14);
        const hand = [];
        for (let j = 0; j < n; j++) hand.push(Math.floor(r() * 136));
        const forb = [];
        if (r() < 0.5) {
            for (let j = 0; j < 1 + Math.floor(r() * 3); j++) forb.push(Math.floor(r() * 34));
        }
        rows.push(`ropts discard ${r() < 0.3 ? 1 : 0} ${r() < 0.5 ? -1 : Math.floor(r() * 136)} `
            + `${hand.join(',')} ${forb.length ? forb.join(',') : '-'}`);
    }
    // 食替：三种组合 × 两侧（吃的牌在顺子下边 / 上边 / 居中）× 花色边界（1m/9m/1s）
    for (const [ck, k0, k1] of [[2, 3, 4], [3, 4, 5], [4, 3, 5], [2, 0, 1], [0, 1, 2], [8, 6, 7],
                                [9, 10, 11], [11, 9, 10], [17, 15, 16], [18, 19, 20], [26, 24, 25],
                                [27, 27, 27], [31, 31, 31], [4, 0, 2], [5, 3, 4], [3, 0, 6]]) {
        rows.push(`ropts kuikae ${ck} ${k0} ${k1}`);
    }
    for (let i = 0; i < 60; i++) {
        const a = Math.floor(r() * 34);
        const b = Math.floor(r() * 34);
        rows.push(`ropts kuikae ${Math.floor(r() * 34)} ${a} ${b}`);
    }
    // 立直后杠：听牌形不变才允许（含听牌集合变大/变小/不听牌/字牌）
    rows.push('ropts kanriichi 0,4,8 2,0,0,0,4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0 0 4');
    rows.push('ropts kanriichi 3,6 0,0,0,1,4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0 0 4');
    rows.push('ropts kanriichi - 0,0,0,4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0 0 3');
    for (let i = 0; i < 60; i++) {
        const mc = Math.floor(r() * 4);
        const size = 14 - 3 * mc;                    // ⚠ 自己回合的暗牌 = 14 − 3×副露（含刚摸到那张）
        const c = new Array(34).fill(0);
        const kind = Math.floor(r() * 34);
        c[kind] = 4;                                 // 要杠的那张：手里真有 4 张
        let placed = 4;
        while (placed < size) {
            const k = Math.floor(r() * 34);
            if (c[k] >= 4) continue;
            c[k]++;
            placed++;
        }
        const drawnKind = c.findIndex((v) => v > 0);
        const wa = [];
        const nw = Math.floor(r() * 4);
        for (let j = 0; j < nw; j++) wa.push(Math.floor(r() * 34));
        rows.push(`ropts kanriichi ${wa.length ? [...new Set(wa)].join(',') : '-'} `
            + `${c.join(',')} ${mc} ${kind}`);
        // 同一条手牌再走一遍**真实调用点**口径（`Round.kanAllowedByRiichi`）：
        // 杠前听牌要先把刚摸到的那张减掉再算，而传给判据的计数仍是 14 张的
        rows.push(`ropts kanauto ${c.join(',')} ${mc} ${kind} ${drawnKind}`);
    }
    // 文档里点名的形状（`docs/日本麻将.md` §立直）：1m1m1m2m2m3m3m3m8p8p8p6z6z 家族，摸 9m 后问杠
    //   B：手里 8p×4 → 杠 8p 是文档里"可以"的那一手
    //   ⚠ 别放"被杠的牌不足 4 张"的行：Java 侧 `after[kind] -= 4` 会得到**负数**，
    //     `Shanten.dfs` 直接 StackOverflow（本轮就是这么炸的）—— C++ 侧已在门口挡住这种输入
    rows.push('ropts kanauto 3,2,3,0,0,0,0,0,1,0,0,0,0,0,0,0,4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0 0 16 8');
    rows.push('ropts kanauto 4,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,0 0 0 4');
    // 吃搭子：三种组合 × 边界 × 字牌（含手里有两张同种这种"正常吃不会出现"的形状）
    for (let k = 0; k < 34; k++) {
        const c = new Array(34).fill(0);
        c[k] = 1;
        for (const d of [-2, -1, 1, 2]) {
            const t = k + d;
            if (t >= 0 && t < 34) c[t] = Math.min(4, c[t] + 1);
        }
        rows.push(`ropts chi ${c.join(',')} ${k}`);
    }
    for (let i = 0; i < 40; i++) {
        const c = new Array(34).fill(0);
        for (let j = 0; j < 6; j++) c[Math.floor(r() * 34)] = Math.min(4, c[Math.floor(r() * 34)] + 1);
        rows.push(`ropts chi ${c.join(',')} ${Math.floor(r() * 34)}`);
    }
    return rows;
}

const corpus = join(BUILD, 'settle-corpus.txt');
const outJava = join(BUILD, 'settle-java.txt');
const outCpp = join(BUILD, 'settle-cpp.txt');
const rows = buildCorpus(N);
writeFileSync(corpus, rows.join('\n') + '\n', 'utf8');
console.log(`[settle-parity] 语料 ${rows.length} 行 → ${corpus}`);

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

runToFile('java', ['-cp', JAR, PROBE, corpus, outJava], join(BUILD, 'settle-java.log'));
runToFile(EXE, ['settle', corpus, outCpp], join(BUILD, 'settle-cpp.log'));

const j = readFileSync(outJava, 'utf8').split('\n');
const c = readFileSync(outCpp, 'utf8').split('\n');
if (j.length && j[j.length - 1] === '') j.pop();
if (c.length && c[c.length - 1] === '') c.pop();

let fails = 0;
let firstBad = 0;
const kinds = new Map();
if (j.length !== c.length) {
    fails++;
    console.error(`  [FAIL] 行数不同：java=${j.length} cpp=${c.length}`);
}
const n = Math.min(j.length, c.length);
for (let i = 0; i < n; i++) {
    if (j[i] !== c[i]) {
        fails++;
        const kind = rows[i].split(' ')[0];
        kinds.set(kind, (kinds.get(kind) || 0) + 1);
        if (firstBad < 5) {
            firstBad++;
            console.error(`  [FAIL] 第 ${i + 1} 行（${kind}）\n         java = ${j[i]}\n         cpp  = ${c[i]}\n         语料: ${rows[i]}`);
        }
    }
}
if (fails) {
    console.error('  按类型的差异计数：' + [...kinds].map(([k, v]) => `${k}=${v}`).join(' '));
    console.error(`[settle-parity] FAIL：${fails} 行不一致（共 ${n} 行）`);
    process.exit(1);
}
console.log(`  [ok]   ${n}/${n} 行一致（顺位点/余棒/连庄判据/种子链）`);
console.log('[settle-parity] PASS：精算与连庄判据与 Java 逐位一致');
