# HOUSEKEEPING — `w-ladder` 只读盘点（2026-10-13）
> **盘点当时只读；⚠ §5 的 6 项删除已于 2026-10-13 执行 —— 见 §6（删 9,050 件 / 实测回收 23.437 GB）。本行以下的数字都是"删前"状态。** 根 `S:\mahjong-training\w-ladder` = **26.847 GiB / 11,819 文件**（S: 余 128.1 GiB）；本目录本应只有文档与证据。
## 1 Top-15 顶层条目（大小含子树）· 性质：①该留 ②可删 ③不确定
| 条目 | 大小 | mtime | 是什么 / 谁产生 | 性质 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `_bl13\` | 23.319 GB | 10-13 15:55 | BL-13 第八十二轮 gate 的**逐场轨迹** 9,000×`g*.jsonl` + <1 MB 判决件 | ② | 删轨迹、留判决 |
| `traces\` | 2.679 GB | 10-10 19:40 | 风格阶梯各代轨迹；`v4-wl-fold-c1-g01` 2.646 GB 是 `w-ladder.py` measure 的输入 | ① | ⛔ 本轮禁碰 |
| `_overlap\` | 0.590 GB | 10-12 21:41 | B1/B2/B3b 重叠**红证**（含 2 个失败脚本）；`tool\` 被 `overlap-orchestrator.py` 引用 | ① | 不删 |
| `_meldtype-smoke\` | 0.123 GB | 10-10 19:46 | `meldtype` 烟雾：`traces\` 50 场轨迹 + 结论 `smoke-50.json` | ② | 删 `traces\` |
| `style-vector-per-gen\` | 0.107 GB | 10-11 19:38 | 逐代风格向量 / CEIL·EXPA 读数 | ① | 不删 |
| `traces-post\` | 0.010 GB | 10-10 19:50 | 末代补采轨迹（`post` 主读数输入，`TRACES_POST`） | ① | 不删 |
| `_rt-shadow\` · `_dama-shadow\` | 0.003 + 0.003 GB | 10-10 15:46 / 14:31 | 影子包逐字节对拍（`riichi_turn` / `dama`） | ① | 不删 |
| `logs\` | 0.002 GB | 10-11 19:57 | 各轴链 / 补采 / 自检日志 | ① | 不删 |
| `_meldtype-shadow\` · `_fold-shadow\` | 0.002 + 0.002 GB | 10-10 19:11 / 16:22 | 影子包对拍（`meldtype` / `fold`） | ① | 不删 |
| `_p301-evidence\` | 0.001 GB | 10-12 09:40 | P3-01 证据（自检 / 内存读数） | ① | 不删 |
| `_fold-verify\` | 0.001 GB | 10-10 18:52 | `fold` 冻结件 + 复核 | ① | 不删 |
| `_probe-contrast-work\` | 0.001 GB | 10-12 13:36 | PROBE-CONTRAST 中间工作件 | ③ | 待定 |
| `_bl14\` | 0.001 GB | 10-13 14:13 | BL-14 证据（`rounds.jsonl` / `spec-*.json` / `ab\**` / 日志） | ① | 不删 |
| `audit\` · `paired\` · `ceiling-gate\` | 各 <0.001 GB | 10-10 ~ 10-12 | 奖励审计账 / 逐场配对账 / 天窗闸门 verdict；被 `w-ladder.py` / `ceiling-gate.py` 读 | ① | 不删 |
| 其余 ~380 个顶层小件（`*.md`/`*.py`/`*.json`/影子目录） | 各 <0.001 GB | 10-09 ~ 10-13 | 预注册稿 / 报告 / 判据脚本 / 一次性分析件 | ①③ | 逐件再看 |
## 2 26.85 GB 实际在哪
- `_bl13\gate\bl13-main\s{52940001,2,3}` = **15.531 GB**（3 族 × 2,000 件）· `bl13-{A,B}-vs-base\s52941001` = **3.897 + 3.890 GB**（各 1,500 件）· `_bl13` 非轨迹件 **<1 MB**
- `traces\v4-wl-fold-c1-g01` = 2.646 GB（⛔禁碰）· `_overlap\b2` = 0.557 GB（最大单件 `full-663k-chunk100k.jsonl` 0.146 GB）· `_meldtype-smoke\traces` = 0.123 GB
## 3 误落核查（特别核）
- ✅ **是**：`_bl13\gate\**` 的 **9,000 个 `g*.jsonl` = 23.319 GB** 是逐场轨迹**误落在证据目录**；全根 `.jsonl` = **10,118 件 / 26.672 GB**（96% 在 `_bl13`）
- ⛔ **无** `*.feat.bin`（0 件）· **无** `ov-A-s00` · **无** 真 `raw\` / `compact\` 大件（`raw` 命中 18 件全是 `draw*` 误匹配 0.003 GB；`compact` 命中 47 件全是 `_overlap` 读数 0.089 GB）
- 四专项：`_bl13` **23.319 GB** · `_bl14` **0.001 GB** · `_mslt-tmp` **0.000 GB**（4 件，`mslt-compress-check.py` 被 NOTES §6.5 引用 ⇒ 留） · `_overlap` **0.590 GB**
## 4 建议回收量
- **本批可安全回收 = 23.44 GB**（`_bl13\gate\**\g*.jsonl` 23.319 + `_meldtype-smoke\traces` 0.123）
- 解禁 `traces\v4-wl-fold-c1-g01` 后上限 **26.09 GB**（+2.646；⚠ 该项被 `w-ladder.py` measure / quality 读 ⇒ 删前须确认不再重量）
- ③ 不确定、不计入：`_overlap\b2\full-663k-{chunk100k,default-chunk}.jsonl` **0.292 GB**（分块峰值判据的物化输入，删后不能重放对账）
## 5 删除清单（✅ **已于 2026-10-13 执行完毕**，逐条结果见 §6；每条注来源 / 是否被引用）
1. `_bl13\gate\bl13-main\s52940001\g*.jsonl` 2,000 件 5.164 GB — 来源：BL-13 gate 主读数（`bl13-gate-main.log`）；引用：**无**（判决已冻在同目录 `verdict.json`）
2. `_bl13\gate\bl13-main\s52940002\g*.jsonl` 2,000 件 5.203 GB — 同上
3. `_bl13\gate\bl13-main\s52940003\g*.jsonl` 2,000 件 5.165 GB — 同上
4. `_bl13\gate\bl13-A-vs-base\s52941001\g*.jsonl` 1,500 件 3.897 GB — 来源：`bl13_pair.py`（`bl13-gate-A-vs-base.log`）；引用：读数已冻在 `_bl13\verdict-AB.json`、`readout-AB.log`
5. `_bl13\gate\bl13-B-vs-base\s52941001\g*.jsonl` 1,500 件 3.890 GB — 同上（`bl13-gate-B-vs-base.log`）
6. `_meldtype-smoke\traces\**` 50 件 0.123 GB — 来源：`_meldtype-smoke.py` 烟雾；引用：**无**（结论件 `smoke-50.json` 保留）
> ⚠ **必须保留**：`_bl13\gate\**\verdict.json` · `s*\summary.json` · `_bl13\{verdict-AB,base-readout,kl-curve-B-frozen}.json` · `_bl13\*.log` · `_bl13\ab\**` · `_bl13\bl13_pair.py`
> ⚠ **冲突待裁**：NOTES §6.5 第八十二轮残留行字面写"保留 `_bl13\**`" —— 该表述指的是**证据束**；实测 `gate\**` 里是 23.3 GB 逐场轨迹（同轮把"用完即删"写给的是 `tmp\bl13\`）。**删前请对这条拍板。**

## 6 执行记录（2026-10-13 16:21–16:26 · 已执行 · 极轻负载：只读枚举 + 逐条删除 + 空间核对，⛔ 未跑训练/采集/闸门）
- **① 已删 6 项 / 9,050 件（`Remove-Item -LiteralPath` 逐个精确路径，⛔ 无通配、⛔ 无 `-Recurse`、⛔ 未用 `-Force`）**：① `_bl13\gate\bl13-main\s52940001\g*.jsonl` 2,000 件 5.163 GB · ② 同目录 `s52940002` 2,000 件 5.202 GB · ③ 同目录 `s52940003` 2,000 件 5.163 GB · ④ `_bl13\gate\bl13-A-vs-base\s52941001\g*.jsonl` 1,500 件 3.896 GB · ⑤ `_bl13\gate\bl13-B-vs-base\s52941001\g*.jsonl` 1,500 件 3.889 GB · ⑥ `_meldtype-smoke\traces\g*.jsonl` 50 件 0.123 GB（**实测非硬链接**：fileid/大小与 `traces\v4-wl-fold-c1-g01\g0.jsonl` 均不同 ⇒ 独立副本；目录保留为空）。删前先验明是逐场轨迹（首行 = `{"type":"decision","game":0,…,"legal":[…]}`）。
- **② 保留件核对（删后逐个"在"）**：5 × `s*\summary.json`（929 KB–1.2 MB）· `_bl13\gate\bl13-main\verdict.json` · `_bl13\{verdict-AB,base-readout,kl-curve-B-frozen}.json` · `_bl13\*.log` 6 件 · `_bl13\ab\**` 27 件 · `_bl13\bl13_pair.py` · `_meldtype-smoke\smoke-50.json`；`_bl13` 下 `.json/.py/.md/.log/.txt` 共 **37 件 / 0.006 GB 全在**。
- **③ 空间（实测差）**：`S:` 可用 **128.17 → 151.62 GB（+23.45 GB）**；`w-ladder` 总 **26.848 GB / 11,820 件 → 3.411 GB / 2,770 件** ⇒ **实测回收 23.437 GB**（预估 23.44，差 0.003 = 取整）；`_bl13` 23.319 → **0.006 GB**。
- **④ 件数核对**：5 个 `s*` 目录 `g*.jsonl` 删前 = 2,000×3 + 1,500×2 = **9,000**（守卫断言件数 == 预期，不等则拒跑），删后**全为 0**；`_meldtype-smoke\traces` 删后 **0**。**0 件失败、0 件被占用**（无失败明细）。
- **⑤ 引用核对**：在 `S:\mahjong-training` 内搜 6 个目录名与牌山名（`*.py/*.md/*.mjs/*.js/*.ps1/*.txt/*.log/*.json`）—— 命中仅：本文件、`_bl13\*.log`（**历史命令行记录**，非消费）、`verdict-AB.json`/`base-readout.json`（**判决件里的来源 provenance**）、`PROGRESS-SUMMARY-2026-10-10.md`/`STYLE-CONTROL-STATUS.md`（**旧盘点数字**）。**无真实消费方**：`_meldtype-smoke.py` 的 `prep()` 只把 `traces\<tag>` **硬链接/拷贝写出**到该 `traces\` 缓存（`WORK.mkdir` + 可重建），⛔ 从不读它当输入。§5 末"保留 `_bl13\**`"冲突**按"证据束"裁决**：判决已冻在 `verdict.json`/`summary.json`，轨迹无消费方。
- **⑥ 禁碰核对（删前删后件数+GB 逐一相同，未碰）**：`raw` 43,399/45.501 · `style-reward` 8,148/21.197 · `compact\v4-heads-g01` 47/5.822 · `traces\v4-wl-fold-c1-g01` 1,001/2.646 · 仓库 `tools\build` 899/1.586 · `E:\mahjong-training\compact\ov-A-s00` 49/14.784 · `_bl14` 26/0.001 · `_overlap` 313/0.590 · `_mslt-tmp` 4/0 · `traces-post` 17/0.010 · `style-vector-per-gen` 175/0.107。
- **⑦ 未删掉的 / 未做的**：无（6 项全数删净）。本文件本身在**数据根**（`NOTES.md` §6.5 即称其为"数据根 `w-ladder\HOUSEKEEPING-WLADDER.md`"）⇒ **不进仓库、本轮零提交**。
