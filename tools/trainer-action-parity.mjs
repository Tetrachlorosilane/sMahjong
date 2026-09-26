#!/usr/bin/env node
/**
 * **训练端 C++ 引擎 ↔ Java 引擎：动作空间（key / 下标 / 回包 / 落位）差分对拍**。
 *
 *   node tools/trainer-action-parity.mjs
 *
 * 为什么这一层要单独对拍：轨迹里的 `legal` / `chosen` 写的就是 `Action.key()`，
 * 而键的构造规则里有几条**踩过坑**的细节：
 *   · 碰/大明杠的键必须带"从手里取哪几张"（赤五与普通五是两个不同的合法动作）——
 *     折成裸 `pon` 会让 `legal` 出现重复键、`chosen_index` 无从分辨；
 *   · 牌码槽位是 37 个（34 种牌 + 赤 5m/5p/5s 三个槽），"0p" 与 "5p" 的 kind 相同但槽位不同；
 *   · 固定头下标里 76 是**历史槽位**（老数据的裸 pon），现在只有裸 pon 用它；
 *   · `resolve` 只在碰/杠上允许"同类型第一条"的退让，其余动作必须精确匹配。
 *
 * 顶点覆盖：全部 37 个打牌槽 × 立直、吃/碰（含赤五两种取法）/三种杠（含大明杠的取法）、
 * 自摸/荣和/过/九种九牌、以及一批**非法键**（必须两边都判 null，而不是各自猜一个）。
 *
 * 退出码：0 = 全部一致；1 = 有差异；2 = 环境问题。
 */
import { execFileSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const JAR = join(ROOT, 'server', 'build', 'mahjong-server.jar');
const PROBE = join(ROOT, 'tools', 'ActionProbe.java');
const BUILD = join(ROOT, 'trainer', 'build');
const EXE = existsSync(join(BUILD, 'trainer.exe')) ? join(BUILD, 'trainer.exe')
    : existsSync(join(BUILD, 'trainer')) ? join(BUILD, 'trainer') : null;

if (!existsSync(JAR)) {
    console.error(`[action-parity] 找不到 ${JAR} —— 先 pwsh -File server\\build.ps1`);
    process.exit(2);
}
if (!EXE) {
    console.error('[action-parity] 找不到 trainer 可执行 —— 先 pwsh -File trainer\\build.ps1');
    process.exit(2);
}
if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

const SUITS = ['m', 'p', 's'];
const TILE_CODES = [];
for (const s of SUITS) for (let n = 1; n <= 9; n++) TILE_CODES.push(`${n}${s}`);
for (let n = 1; n <= 7; n++) TILE_CODES.push(`${n}z`);
const RED_CODES = ['0m', '0p', '0s'];

function buildCorpus() {
    const rows = [];
    // ---- 牌码 ↔ 槽位
    for (const c of [...TILE_CODES, ...RED_CODES, '10m', '0z', '5', '', '0x']) {
        rows.push(`tile ${c}`);
    }
    // ---- 打牌 / 立直：全部 37 槽 × {不声明摸切, 声明摸切}
    for (const c of [...TILE_CODES, ...RED_CODES]) {
        rows.push(`key type=discard;tile=${c}`);
        rows.push(`key type=discard;tile=${c};tsumogiri=1`);
        rows.push(`key type=riichi;tile=${c}`);
        rows.push(`parse discard:${c}`);
        rows.push(`parse discard:${c}/tsumogiri`);
        rows.push(`parse riichi:${c}`);
    }
    // ---- 单类型动作
    for (const t of ['tsumo', 'ron', 'pass', 'kyuushu', 'pon']) {
        rows.push(`key type=${t}`);
        rows.push(`parse ${t}`);
    }
    // ---- 吃 / 碰（含赤五）/ 三种杠（含大明杠取法）
    const pairs = [
        ['1m', '2m'], ['2m', '3m'], ['8m', '9m'], ['5p', '0p'], ['0p', '5p'], ['5s', '0s'],
        ['1z', '1z'], ['7z', '7z'], ['0m', '0m'],
    ];
    for (const [a, b] of pairs) {
        rows.push(`key type=chi;tiles=${a}+${b}`);
        rows.push(`key type=pon;tiles=${a}+${b}`);
        rows.push(`parse chi:${a}+${b}`);
        rows.push(`parse pon:${a}+${b}`);
    }
    for (const c of ['5m', '0m', '1z', '9s']) {
        for (const kind of ['ankan', 'kakan', 'daiminkan']) {
            rows.push(`key type=kan;kind=${kind};tile=${c}`);
            rows.push(`parse kan:${kind}:${c}`);
        }
    }
    rows.push('key type=kan;kind=daiminkan;tile=5s;tiles=5s+5s+0s');
    rows.push('key type=kan;kind=daiminkan;tile=5s;tiles=0s+5s+5s');   // 顺序会被规范化
    rows.push('parse kan:daiminkan:5s+5s+0s');
    rows.push('parse kan:daiminkan:0s+5s+5s');
    // ---- 非法键（两边都必须判 null）
    for (const bad of [
        'discard:', 'discard:5', 'discard:0z', 'discard:10m', 'riichi:', 'riichi:0z',
        'pon:5p', 'pon:5p+5p+5p', 'pon:0z+0z', 'chi:1m', 'chi:1m+2m+3m', 'chi:0z+1z',
        'kan:ankan', 'kan:ankan:5m:extra', 'kan:ankan:0z', 'kan:ankan:5p+5p+5p',
        'nosuch', '', 'discard:5m/tsumogiri/extra',
    ]) {
        rows.push(`parse ${bad}`);
    }
    // ---- resolve：精确匹配 / 碰与杠的退让 / 其余必须精确
    const legalPon = ['pon:5p+5p', 'pon:5p+0p'];
    rows.push(`resolve type=pon;tiles=5p+5p ${legalPon.join(',')}`);
    rows.push(`resolve type=pon;tiles=5p+0p ${legalPon.join(',')}`);
    rows.push(`resolve type=pon ${legalPon.join(',')}`);                     // 裸 pon → 第一条
    rows.push(`resolve type=pon;tiles=0p+0p ${legalPon.join(',')}`);         // 取法不在 legal → 第一条
    const legalKan = ['kan:ankan:5m', 'kan:kakan:5m'];
    rows.push(`resolve type=kan;kind=ankan;tile=5m ${legalKan.join(',')}`);
    rows.push(`resolve type=kan;kind=kakan;tile=5m ${legalKan.join(',')}`);
    rows.push(`resolve type=kan;kind=ankan;tile=5m kan:ankan:5m`);           // 单条 legal
    rows.push(`resolve type=kan;kind=daiminkan;tile=5m kan:ankan:5m`);       // 杠种对不上 → null
    const legalTurn = ['discard:1m', 'discard:2m', 'riichi:1m'];
    rows.push(`resolve type=discard;tile=1m ${legalTurn.join(',')}`);
    rows.push(`resolve type=discard;tile=9m ${legalTurn.join(',')}`);        // 打牌必须精确 → null
    rows.push(`resolve type=tsumo ${legalTurn.join(',')}`);                  // 不在 legal → null
    rows.push('resolve type=pass -');
    return rows;
}

const corpus = join(BUILD, 'action-corpus.txt');
const outJava = join(BUILD, 'action-java.txt');
const outCpp = join(BUILD, 'action-cpp.txt');
const rows = buildCorpus();
writeFileSync(corpus, rows.join('\n') + '\n', 'utf8');
console.log(`[action-parity] 语料 ${rows.length} 行 → ${corpus}`);

function runToFile(cmd, cmdArgs, outFile) {
    const fd = openSync(outFile, 'w');
    try {
        execFileSync(cmd, cmdArgs, { stdio: ['ignore', fd, 'inherit'] });
    } finally {
        closeSync(fd);
    }
}

runToFile('java', ['-cp', JAR, PROBE, corpus, outJava], join(BUILD, 'action-java.log'));
runToFile(EXE, ['action', corpus, outCpp], join(BUILD, 'action-cpp.log'));

const j = readFileSync(outJava, 'utf8').split('\n');
const c = readFileSync(outCpp, 'utf8').split('\n');
if (j.length && j[j.length - 1] === '') j.pop();
if (c.length && c[c.length - 1] === '') c.pop();

let fails = 0;
let shown = 0;
if (j.length !== c.length) {
    fails++;
    console.error(`  [FAIL] 行数不同：java=${j.length} cpp=${c.length}`);
}
const n = Math.min(j.length, c.length);
for (let i = 0; i < n; i++) {
    if (j[i] !== c[i]) {
        fails++;
        if (shown < 8) {
            shown++;
            console.error(`  [FAIL] 第 ${i + 1} 行\n         java = ${j[i]}\n         cpp  = ${c[i]}\n         语料: ${rows[i]}`);
        }
    }
}
if (fails) {
    console.error(`[action-parity] FAIL：${fails} 行不一致（共 ${n} 行）`);
    process.exit(1);
}
console.log(`  [ok]   ${n}/${n} 行一致（键 / 下标 / 回包 / 落位 / 非法键）`);
console.log('[action-parity] PASS：动作空间与 Java 逐字符一致');
