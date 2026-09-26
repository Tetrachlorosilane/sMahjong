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
