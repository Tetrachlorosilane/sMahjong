#!/usr/bin/env node
/**
 * 离线合成音效（吃 / 碰 / 杠 / 立直 / 自摸 / 荣和 / 提示 / 摸牌）→ `client/assets/sfx/*.wav`。
 *
 *   node tools/gen-sfx.mjs [输出目录]
 *
 * ## 为什么是「离线合成 WAV」而不是运行时 MIDI
 *
 * 1. **Qt 没有 MIDI 合成器**：QtMultimedia 在 Windows 上只有 WMF 后端，不能合成 MIDI；
 *    要么绑系统 GS 波表（换台机器就可能没声），要么引第三方合成器 —— 后者会破坏
 *    本项目「客户端只依赖 Qt」这条原则（见 AGENTS §6.1）。
 * 2. **WAV 可以直接喂系统音频 API**：Windows 用 `winmm` 的 `PlaySound(SND_MEMORY|SND_ASYNC)`，
 *    零第三方依赖、不挑后端（见 `client/src/model/Sound.cpp`）。
 * 3. **合成脚本进仓库 ⇒ 可复现**：音效不是二进制黑盒，改一个音符重跑本脚本即可，
 *    也方便材质包作者照着换（THEME.md 的 `sfx` 类别）。
 *
 * ## 声音设计（全部是"麻将桌上听得出来"的短音，不刺耳）
 *
 *   吃   单音上滑（短促"叮"）        碰   两连音（更硬）
 *   杠   三连上行 + 铃尾（更重）     立直 铃声（长衰减，公告感）
 *   自摸 明亮三音上行（喜悦）        荣和 大铃声 + 低音垫
 *   提示 极短轻声双击（轮到你）      摸牌 极轻短音（每巡都响，必须最轻）
 *
 * 采样率 22050、16 bit 单声道 —— 短音效足够，体积小（每个 8~30 KB）。
 */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const OUT_DIR = path.resolve(process.argv[2] || path.join(root, 'client', 'assets', 'sfx'));

const RATE = 22050;

// ---------------------------------------------------------------- 波形与合成

/** 等音高：MIDI 音号 → 频率（A4=69=440Hz）。 */
function midiFreq(note) {
  return 440 * Math.pow(2, (note - 69) / 12);
}

/**
 * 一个音：正弦基频 + 少量二次谐波（听起来像木琴/铃，不像纯电子音）。
 * `decay` 是指数衰减速度（越大越短）。
 */
function tone(buf, startSec, durSec, freq, gain, decay, harmonics = [[1, 1], [2, 0.28], [3, 0.12]]) {
  const s0 = Math.floor(startSec * RATE);
  const n = Math.floor(durSec * RATE);
  for (let i = 0; i < n; i++) {
    const idx = s0 + i;
    if (idx < 0 || idx >= buf.length) continue;
    const t = i / RATE;
    const env = Math.exp(-decay * t);
    // 起音 3ms 淡入：避免"啪"的爆音（方波般的瞬变会让短音效很刺耳）
    const atk = Math.min(1, t / 0.003);
    let v = 0;
    for (const [mult, amp] of harmonics) {
      v += amp * Math.sin(2 * Math.PI * freq * mult * t);
    }
    buf[idx] += v * gain * env * atk;
  }
}

/** 噪声（用于"碰"的木质敲击感）。 */
function hit(buf, startSec, durSec, gain, decay) {
  const s0 = Math.floor(startSec * RATE);
  const n = Math.floor(durSec * RATE);
  let seed = 12345;
  for (let i = 0; i < n; i++) {
    const idx = s0 + i;
    if (idx < 0 || idx >= buf.length) continue;
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const r = (seed / 0x3fffffff) - 1;      // [-1,1)
    const t = i / RATE;
    buf[idx] += r * gain * Math.exp(-decay * t) * Math.min(1, t / 0.002);
  }
}

/** 归一化到 target 峰值并按需淡出，再转 16bit PCM。 */
function finish(buf, peak = 0.85, fadeOutSec = 0.02) {
  let max = 0;
  for (const v of buf) max = Math.max(max, Math.abs(v));
  const k = max > 0 ? peak / max : 1;
  const fade = Math.max(1, Math.floor(fadeOutSec * RATE));
  const out = Buffer.alloc(buf.length * 2);
  for (let i = 0; i < buf.length; i++) {
    let v = buf[i] * k;
    const tail = buf.length - i;
    if (tail < fade) v *= tail / fade;      // 收尾淡出，避免截断爆音
    v = Math.max(-1, Math.min(1, v));
    out.writeInt16LE(Math.round(v * 32767), i * 2);
  }
  return out;
}

/** 44 字节标准 WAV 头 + PCM 数据。 */
function wav(pcm) {
  const h = Buffer.alloc(44);
  h.write('RIFF', 0);
  h.writeUInt32LE(36 + pcm.length, 4);
  h.write('WAVE', 8);
  h.write('fmt ', 12);
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20);                   // PCM
  h.writeUInt16LE(1, 22);                   // 单声道
  h.writeUInt32LE(RATE, 24);
  h.writeUInt32LE(RATE * 2, 28);            // 字节率
  h.writeUInt16LE(2, 32);                   // 块对齐
  h.writeUInt16LE(16, 34);                  // 位深
  h.write('data', 36);
  h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}

function make(durSec, build) {
  const buf = new Float64Array(Math.ceil(durSec * RATE));
  build(buf);
  return wav(finish(buf));
}

// ---------------------------------------------------------------- 每个音效

// 音名（C4 = 60）
const C4 = 60, D4 = 62, E4 = 64, F4 = 65, G4 = 67, A4 = 69, B4 = 71;
const C5 = 72, D5 = 74, E5 = 76, G5 = 79, A5 = 81, C6 = 84;

const SFX = {
  // 吃：单音上滑，短
  chi: () => make(0.30, (b) => {
    tone(b, 0.00, 0.16, midiFreq(E5), 0.55, 14);
    tone(b, 0.07, 0.20, midiFreq(G5), 0.42, 12);
  }),
  // 碰：两连音 + 木质敲击
  pon: () => make(0.42, (b) => {
    hit(b, 0.00, 0.06, 0.45, 60);
    tone(b, 0.00, 0.18, midiFreq(A4), 0.50, 12);
    tone(b, 0.11, 0.26, midiFreq(E5), 0.48, 10);
  }),
  // 杠：三连上行 + 铃尾，比碰更重
  kan: () => make(0.62, (b) => {
    hit(b, 0.00, 0.06, 0.40, 55);
    tone(b, 0.00, 0.20, midiFreq(A4), 0.46, 11);
    tone(b, 0.10, 0.22, midiFreq(C5), 0.46, 10);
    tone(b, 0.20, 0.38, midiFreq(E5), 0.50, 7);
    tone(b, 0.20, 0.38, midiFreq(A5), 0.22, 7);
  }),
  // 立直：铃声（长衰减）+ 上行两音，公告感
  riichi: () => make(0.95, (b) => {
    tone(b, 0.00, 0.70, midiFreq(D5), 0.48, 4.2);
    tone(b, 0.00, 0.70, midiFreq(A5), 0.20, 4.2);
    tone(b, 0.16, 0.75, midiFreq(E5), 0.42, 3.6);
    tone(b, 0.16, 0.75, midiFreq(B4), 0.14, 3.6);
  }),
  // 自摸：明亮三音上行
  tsumo: () => make(0.85, (b) => {
    tone(b, 0.00, 0.24, midiFreq(C5), 0.46, 9);
    tone(b, 0.11, 0.26, midiFreq(E5), 0.48, 8);
    tone(b, 0.22, 0.58, midiFreq(G5), 0.52, 4.5);
    tone(b, 0.22, 0.58, midiFreq(C6), 0.20, 4.5);
  }),
  // 荣和：大铃 + 低音垫，比自摸更"重"
  ron: () => make(1.05, (b) => {
    tone(b, 0.00, 0.85, midiFreq(C5), 0.44, 3.4);
    tone(b, 0.00, 0.85, midiFreq(G4), 0.24, 3.4);
    tone(b, 0.00, 0.85, midiFreq(C6), 0.16, 3.4);
    tone(b, 0.14, 0.80, midiFreq(E5), 0.34, 3.0);
    tone(b, 0.00, 0.30, midiFreq(C4) / 1, 0.20, 6);   // 低音垫
  }),
  // 提示（轮到你 / 需要操作）：极短轻声双击
  notify: () => make(0.26, (b) => {
    tone(b, 0.00, 0.09, midiFreq(A5), 0.30, 24);
    tone(b, 0.10, 0.12, midiFreq(A5), 0.24, 22);
  }),
  // 摸牌：最轻最短（每巡都响，不能烦人）
  draw: () => make(0.14, (b) => {
    tone(b, 0.00, 0.09, midiFreq(D5), 0.18, 30);
  }),
};

// ---------------------------------------------------------------- 写盘

fs.mkdirSync(OUT_DIR, { recursive: true });
const written = [];
for (const [name, build] of Object.entries(SFX)) {
  const data = build();
  const file = path.join(OUT_DIR, `${name}.wav`);
  fs.writeFileSync(file, data);
  written.push({ name, bytes: data.length });
}
const total = written.reduce((a, w) => a + w.bytes, 0);
console.log(`[gen-sfx] 写出 ${written.length} 个音效到 ${OUT_DIR}（合计 ${(total / 1024).toFixed(1)} KB）`);
for (const w of written) console.log(`   ${w.name}.wav  ${(w.bytes / 1024).toFixed(1)} KB`);
