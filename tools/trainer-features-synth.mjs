// 定向合成语料（**进仓库**；`trainer-features-parity.mjs` 的边界补充）：真实轨迹**打不到**的 perCandidate 分支
//
//   · `obs.called_tile` 缺失 → Java `ObsFeatures.guessedThird` 的兜底补张（吃/碰/大明杠三种）
//   · 非法 / 半合法动作键（`Action.parse` 返回 null → 8 维全 0）
//   · 多指示牌（杠后 2 张）与"手牌 + 副露"的宝牌计数
//   · 赤五取法（`pon:0p+5p` / `kan:daiminkan:5s+5s+0s` / `discard:0m` / `riichi:0m`）
//
// 用法：node tools\trainer-features-synth.mjs <源轨迹 g0.jsonl> <输出目录>\n//   → 生成 <输出目录>/g0.jsonl，再跑 `node tools\trainer-features-parity.mjs <输出目录>` 逐字节比。
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [srcJsonl, dstDir] = process.argv.slice(2);
const rows = readFileSync(srcJsonl, 'utf8').split('\n').filter((l) => l.trim())
    .map((l) => JSON.parse(l));
const decision = rows.find((r) => r.type === 'decision');
const claim = rows.find((r) => r.type === 'decision' && r.kind === 'claim') ?? decision;

/** 34 维手牌：3m 5m 4m 6m 1m 2m 8m 9m / 5p×3 / 5s×4 / 1z×4 —— 共 15 张，故意 >13 触发"再挑一张打" */
const hand = new Array(34).fill(0);
for (const k of [2, 4, 3, 5, 0, 1, 7, 8, 13, 13, 13, 22, 22, 22, 22]) hand[k]++;
hand[26] = 1;   // 1z ×1（配 kan:ankan:1z）
hand[31] = 1;   // 5z ×1（配 kan:kakan:5z）

const mk = (obs, legal) => ({
    ...decision,
    kind: legal.some((k) => k === 'pass' || k.startsWith('chi:') || k.startsWith('pon:')
        || k.startsWith('kan:') || k === 'ron') ? 'claim' : 'turn',
    legal,
    chosen: legal[0],
    chosen_index: 0,
    obs: { ...obs, hand, visible: hand.map((v, i) => (i === 4 ? 1 : v)), called_tile: null },
});

const out = [];
// ① 吃：兜底补张的四种窗口（坎张 / 两面优先补小 / 边张 / 8m9m）+ 不合法键
out.push(mk(decision.obs, ['chi:3m+5m', 'chi:4m+6m', 'chi:1m+2m', 'chi:2m+3m', 'chi:8m+9m',
    'chi:9m+1m', 'chi:5m']));
// ② 碰 + 大明杠：普通 / 赤五取法 / 非法
out.push(mk(decision.obs, ['pon:5p+5p', 'pon:0p+5p', 'pon:5p', 'pon:1z+1z',
    'kan:daiminkan:5s+5s+0s', 'kan:daiminkan:1m+1m+1m', 'kan:ankan:1z', 'kan:kakan:5z']));
// ③ 真实鸣牌局面（带副露 → doraCount 要数副露里的牌）+ called_tile 缺失
out.push(mk(claim.obs, ['chi:3m+5m', 'pon:5p+5p', 'kan:daiminkan:5s+5s+0s', 'ron', 'pass']));
// ④ 打牌 / 立直 / 终局动作 / 认不出的键
out.push(mk(decision.obs, ['discard:5m', 'discard:0m', 'riichi:5m', 'riichi:0m',
    'tsumo', 'ron', 'kyuushu', 'pass', 'bogus', 'discard:zz', 'kan:ankan', '']));
// ⑤ 多指示牌 + 赤五指示牌（宝牌 kind 推导：0m 指示牌 → 6m）
out.push(mk({ ...decision.obs, dora_indicators: ['0m', '9s', '1z'] },
    ['discard:5m', 'discard:6m', 'discard:1m', 'discard:1z', 'chi:4m+6m', 'pon:5p+5p']));

mkdirSync(dstDir, { recursive: true });
writeFileSync(join(dstDir, 'g0.jsonl'),
    out.map((r) => JSON.stringify(r)).join('\r\n') + '\r\n', 'utf8');
console.log(`合成 ${out.length} 条决策（每条 legal 数：${out.map((r) => r.legal.length).join('/')}）→ ${dstDir}\\g0.jsonl`);
