# A− 完全重构落地：逐函数映射 + `net.bin` format 2→3（段 3/段 2 只读盘查）

> 2026-10-19 · `Develop` @ `87cd2c8`（= `3056f51` + #0 Python 侧，父会话 03:58 提交；本文按该提交读；工作区只有本文件未跟踪）· **零训练/零采集/未改代码/未提交**。
> 行号 = 2026-10-19 04:0x 版本：Java `V4Policy.java` **1,634 行**、C++ `v4policy.cpp` **3,172 行**（⚠ 简报的 1,473/2,980 是旧读数，**文件行长 ≠ 改动量**）。基线只读实测：`node tools/trainer-v4-parity.mjs --golden` ⇒ **PASS 11/11**，Java/C++ 特征 `5.96e-8`、前向 `2.26e-6`、`dValue=0.00 dBelief=0.0248 dGate=0.359`。
## 1 ⭐ C++ 前向逐函数映射（`trainer/src/v4policy.cpp`，3,172 行）

| # | 现函数/类（`文件:行`） | 现在做什么 | A− 后必须变成什么 | 改动量 | 复用/重写 |
| --- | --- | --- | --- | ---: | --- |
| 1 | `V4Policy::bindAll` `:436-618` | 逐张量取一遍 + 钉 `[rows,cols]` + 记 `used`（81 张量） | 新张量表（conv/axial/joint/cross/seg/heads 81）+ `tile.tile_id` + arch↔张量一致性 | +70/−20 | 改（骨架留） |
| 2 | `finishPolicy` `:164-183` | `used==allNames`（"有张量没人读"）`:`171-179` | 保留；+ flags(`hasB3/hasTileId/hasFiLM`) 与实际张量**双向**校验 | +30 | 复用+扩 |
| 3 | `v4LoadPolicyBytes` `:622-795` | 64 B 头 → 块表 → 张量表；尾字节报错 | 头后先读 **arch 段**（`archLen`），再块表；`format`=3 | +75/−6 | 改 |
| 4 | `v4WeightFormatOf` `:805-832` | 返回 1(v3)/2(v4) | 返回 1/**3**；读到 2 ⇒ 专门错误（指向冻结快照） | +8 | 改 |
| 5 | **算子层**：`linearFast`/`linearBatchFast` `:203-300` · `reluBuf`:301/`layerNormBuf`:323/`softmaxBuf`:340 · `mha` `:902-1028` | matvec（输出行 SIMD、**逐位同序**、禁 FMA）；LN 有偏方差 eps=1e-5；`mha` 支持任意 `lq/lk/d` | 全部**原样复用**：`mha` 直接当 axial(34,160)/joint(95,128)/cross(cand→95) 三处；只新增 `geluBuf`（erf 双精度，⛔ 非 tanh 近似） | +14 | 复用+1 个 |
| 6 | **conv1d（不存在）** | — | `Conv1d(k)` = **k 次 `linearBatchFast` + 移位指针 + 两端零 pad**（不新增 SIMD kernel）；tile 另建 `[48][34]` 转置缓冲（现布局 `TileMat[34][48]`，`v4features.cpp:399`） | +46 | **新（拼现有算子）** |
| 7 | `mlp` `:874-901` | `Lin-ReLU-Lin`（**末尾 ReLU**） | 参数化 act/hidden/末激活（joint FFN 用 GELU、**无**末 ReLU） | +25/−8 | 改 |
| 8 | tile 塔（`forwardAll` 内联）`:1612-1671` | `Lin→ReLU→Lin→ReLU`＋34-token MHA＋post-LN＋mean | stem `conv1d(48→160,k=5)+LN+GELU` ＋ `3×ResConv(k=5,3,3)+LN` ＋ **pre-LN** axial MHA ＋ `proj 160→128` | +95/−25 | 重写 |
| 9 | `EventEnc` `:1050-1145` · `EventGru` `:1076-1127` · `eventTowerWindow` `:1277-1295` · `roundAdvance` `:1252` | `Lin-ReLU-Lin`（**无末 ReLU**）+ GRU；窗口级无卷积 | GRU/编码器**原样**；后接 `2×conv1d(k=3)+LN+GELU`（60 轴），窗口输出并入 `e_tokens` | +68 | 复用+增 |
| 10 | Fusion `:1691-1782` | `q_tile`+`q_evt` 两条独立 MHA → `gate/write/film` → `out Mlp(3d→d)` | 段嵌入(3)＋**joint MHA(95 token)**＋FFN＋`cross(cand,95)`＋`out Mlp(3Df→Df)`；**删 gate/write/film** | +150/−110 | 重写 |
| 11 | `mem` 门 `:1784-1815` | `h_evt` 逐维门乘在 `u` 上 | **删**：`h_evt` 改作第 **95** 个 token（0 参数，§2.3） | −32 | 删 |
| 12 | B3 `:1817-1859` | `u += tanh(u_res)⊙LN(u)`（`(w=1,b=0)` 可缺） | **原样保留**（第一百零四轮已实现，本轮核实 `bindAll :545-561` + 前向 `:1817-1859` 都在） | 0 | 复用 |
| 13 | heads `:1861-1980` | 7 头（policy/value/placement/belief_hand/belief_tenpai/danger/effect）+ `policy_gate` | 保留；`value` 宽**已从 header `valueBins` 建** ⇒ 81 bin **0 行改动** | +15 | 复用 |
| 14 | tie-break（**不存在**；`net.cpp:610-617` 是最小下标） | 并列取最小 legal 下标 | `gap<1e-3` 且同动作族 ⇒ `effect` 进张数 → **牌码序**；**归属 = caller**（`policies.hpp:269`），因为 `netArgmax` 只看 logits、拿不到候选语义 | +18 | 新 |
| 15 | CLI `v4net/v4bench/v4golden/v4cache` `:2105-3172` | 只认格式 2 夹具 | 格式 3 + arch 行 + 缓存不变量（风险 5） | +80 | 改 |
| 16 | `v4policy.hpp` `:71-142` | 4 个宽度 | +arch 字段（`dTile/dEvt/dFuse/dHead/flags/topoId`） | +30 | 改 |

**C++ 合计：新增 ≈724 行 / 删 ≈201 行 ⇒ 被改 ≈925 行（29%）**；其余 2,247 行（加载尾/CLI/cache/golden/自检）**一行不动**。
## 2 Java 前向同类映射（`server/src/main/java/mahjong/ai/V4Policy.java`，1,634 行）

> ⚠ **Java 是服务端推理唯一路径**（`docs/TRAINER-CPP.md` §6.25 的"头是否上线 = 前向是否读取"只在 Java/`V4Probe` 上实测）⇒ **任何"先 Python 后 Java"的过渡网都不许上线/上闸门**；段 3b 未过之前 `bot-ai/` 一律不许换包。

| # | 现函数/类（`文件:行`） | 现在做什么 | A− 后 | 改动量 | 复用/重写 |
| --- | --- | --- | --- | ---: | --- |
| 1 | `loadBytes` `:139-256` + 常量 `:50-54` | 64 B 头 + 块表 + 张量表 + 指纹/顺序/重复/尾部 | +arch 段；`FORMAT_VERSION 2→3`；`NetWeights.java:25-27` 分派改；`describe:1321` 打印 arch | +78/−8 | 改 |
| 2 | `bindAll` `:426-516` + `bindPolicyGate:296`/`bindMemGate:339`/`bindUNorm:380` | 81 张量形状 + `used` | 新表 + `tile.tile_id` + arch 交叉校验（构造器尾 `used` 校验） | +75/−15 | 改 |
| 3 | `forwardWith` `:859-1069` | tile MLP+MHA / `q_tile`+`q_evt` / gate/write/film / mem / B3 | tile conv+axial(pre-LN) / joint 95+cross / 删 gate/write/film/mem / **B3 保留** | +260/−140 | 重写 |
| 4 | `mha` `:1404-1450` | 任意 `lq/lk/d`、无 mask | **复用**（三处注意力）；段嵌入加在送入前 | 0 | 复用 |
| 5 | `mlp` `:1393-1401` | `Lin-ReLU-Lin-ReLU` | 参数化 act/hidden/末激活 | +25/−5 | 改 |
| 6 | `layerNorm` `:1375-1391` · `relu` `:1354` | 有偏方差 eps=1e-5 | +`gelu`（Java **无 `Math.erf`** ⇒ 自带 erf，见风险 8） | +18 | 复用+1 |
| 7 | `eventTowerCached/Window/Round` `:608-857` | enc+GRU+1 层 Transformer（`norm_first`） | 保留 GRU/行级缓存；Transformer 层改 GELU + FFN→Df | +45/−20 | 复用+改 |
| 8 | `transformerFromQkv` `:1545-1574` · `attentionQkv` `:1522` | pre-LN 残差 + ReLU FFN(2d) | 参数化 act/hidden ⇒ **直接当 joint encoder 用** | +20/−6 | 复用 |
| 9 | `head` `:1071-1076` | 按 width 建 Linear | **复用**；value 宽 = header `valueBins`（81） | 0 | 复用 |
| 10 | tie-break（`chooseIndex` `:1194-1205` → `Logits.argmaxOf` `:24-34`） | 并列取最小下标（`:21-23` 明写别改随机破平） | 同 C++ #14：`effect`→牌码序，新 helper，`Logits` 加带 keys 的重载 | +30 | 新 |
| 11 | `V4Cache` `:1-146`（槽键 `:29`） | 行级 `tok/enc/qkv`+`h`；**"行是事件+座位的纯函数"**（`:10-17`） | 行级仍可缓存，但**相对巡目位置编码必须放在 `in_proj` 之后**（否则 qkv 变绝对位置依赖、窗口左移即陈旧） | +35 | 改 |

**Java 合计：新增 ≈588 行 / 删 ≈194 行 ⇒ 被改 ≈782 行（48%）**；两端口径合计 **≈1,707 行**——这才是"完全重构"的真实改动量。
## 3 `net.bin` format 2→3

**现有格式**（`export.py:237-249` 写、`:283-320` 读；`docs/FEATURES-V4.md` §6.1 同）：`magic u32 "MJNN" · format=2 · featureVersion=4 · obsVersion=3 · derivedVersion=3 · dModel · tileD · nHeads · valueBins · nBlocks · nTensors · fingerprint 16 B · params u32`（=64 B）→ 块表（`idLen u16·id·width u32`×nBlocks）→ 张量表（`nameLen u16·name·ndim u8·dims u32×n·f32 数据`×nTensors）。⚠ `params` 三端"读了不用"（C++ `:675`、Java `:178`）⇒ format 3 把它变成**校验位**。

**format 3 = 原 64 B 头（`format` 改 3，其余偏移不变）+ arch 段 + 块表 + 张量表**（`archLen` 是 arch 段首 4 B，块表偏移 = `64+archLen`）。

| 偏移(相对 arch) | 字段 | 类型 | 值/含义 | 可选 |
| ---: | --- | --- | --- | --- |
| 0 | `archLen` | u32 | **96**（硬上限 2048；越界/非 4 倍 ⇒ 报错） | 否 |
| 4 | `archVersion` | u16 | 1 | 否 |
| 6 | `topoId` | u16 | 3 = A−（T1）；未知 ⇒ 报错 | 否 |
| 8/10/12/14/16/18 | `dTile/dEvt/dFuse/dHead/dCand/headDim` | u16×6 | T1 = **160/128/128/128/128/40** | 否 |
| 20/21/22 | `nTileConv/nTileAttn/nJointLayer` | u8×3 | 4/1/1 | 否 |
| 23/24 | `actId/normId` | u8×2 | 1=GELU、1=pre-LN（0=ReLU/post-LN 留作消融） | 否 |
| 25/26 | `segEmbMode/memTokenMode` | u8×2 | 1=3 段相加 / 1=`h_evt`=第 95 token | 否 |
| 27 | `flags` | u8 | bit0 `hasB3` · bit1 `hasTileId` · bit2 `hasFiLM`(0) · bit3 `hasMemGate`(0) · bit4–7 保留=0 | 否 |
| 28/30 | `valueBins/nHeads` | u16×2 | 81 / 4 | 否 |
| 32/36 | `valueRange/lnEps` | f32×2 | 60.0 / 1e-5（诊断与 eval 用） | 是 |
| 40-43 | `tileKernel[4]` | u8×4 | 5,5,3,3 | 是 |
| 44/45 | `nSeg/pad` | u8×2 | 3 / 0 | 否 |
| 48/52/56 | `featVersion/obsVersion/derivedVersion` | u32×3 | 4/3/3（**与头部冗余，必须相等**） | 否 |
| 60-69 | `cTile/cEvt/kEvt/cCtx/cCand` | u16×5 | 48/96/60/64/128（**不重建数据**的硬证据） | 否 |
| 70-95 | reserved | 26 B | **必须全 0**（archVersion=1 时非 0 ⇒ 报错） | 否 |

**预算**：arch = `96 B` 核心 + `1,952 B` 保留尾 = **2,048 B**；头部 2,712 B + 2,048 = **4,760 B**：2,000,000 参数 ⇒ `8,004,760 B` < 文档上限 `8,010,904 B`（余 **6,144 B**）✓；现网 1,400,200 参数（1,397,992+576 B3+1,632 #0）⇒ `5,605,560 B`。arch 段是**必需**而非可选：`topoId/actId/normId/flags` 决定前向分支，`dHead` 让"`dHead≠dFuse`"可表达（§2.2 的 `384→192` 与 §0.1 的 `d_head=Df=128` **互相矛盾**；本设计按 §0.1 取 128，字段留出）。

**硬切**：`format==2` ⇒ `权重格式版本 2 已退役：format 3 起是 v4 新拓扑；旧网请用冻结快照（lab/mv1/frozen-format2，jar sha 3E7D0ADC…）`；`format==1` ⇒ 仍走 v3 `NeuralPolicy`；其它 ⇒ 报版本号。三处常量同步：`export.py:74`、`v4policy.hpp:38`、`V4Policy.java:52`，外加 `NetWeights.java:25-27`、`policy-gate-check.mjs:32`（现在 `format!==2` 就判"非 v4"，须改成认 3、把 2 标成冻结快照）。

**冻结快照**（⛔ 不重建数据、不做永久双格式分支）：tag = 最后一个 `--golden` 11/11 干净的提交（建议锚 `3056f51`，在 clean checkout 上**重新构建**；⚠ 现有 `build/` 产物 mtime 19:49–19:50 早于其源码提交时刻 20:55，⛔ 别把 mtime 当版本证据，以 sha256 为准）→ `tools/build/frozen-format2/`（gitignored）存 **4 件**并记 sha256：`mahjong-server.jar` `3E7D0ADC…AB1F`（422,530 B）· `trainer.exe` `F6AAA307…A83C`（1,541,632 B）· `V4Probe.class` `F80EEBFA…73F8` · `forward-v4.bin` `407BBE21…089A`（631,551 B，C2 语义）。**谁用**：5 个 `wl-*`（5,594,680 B、`vb=51`、format 2）的基线对照**只能**用这套跑（硬切后新 Java/C++ 再也载不动它们）；`bot-ai/` 一字节不动。

**golden 三档并存**（按 magic/format 选，⛔ 不按文件名）：**G0** = 冻结 C2 夹具 `407BBE21…`（只被冻结 jar/exe 读，`wl-*` 回归）；**G1** = 新 format-3 夹具（小网 `32/16/2/81` + arch，只被新三端读，`export golden --semantics current --format 3`）；**G2** = 真实权重 + 3,083 决策语料（R-4，仓库外，只归档 verdict/sha）。CI 负向：G0 喂新 Java **必须报错**、G1 喂冻结 jar **必须报错**。

**fail-loud 清单**（⛔ 一律报错、不许补 0）：`format`/魔数/版本三件套（feat/obs/derived，头与 arch 两处必须相等）；`archLen ∉[96,2048]` 或 `%4`；`archVersion>1`；reserved 非 0；`topoId/actId/normId/segEmbMode/memTokenMode` 未注册；`valueBins` 三处（头/arch/`heads.value.weight` 行数）不等；`hasB3=1` 但 `fusion.u_norm.*`/`u_res` 缺（或反之存在但 flag=0）；`hasTileId` 与 `tile.tile_id` 同理；`segEmbMode=1` 时 `nSeg≠3`；`kEvt/cTile/...` 与代码常量不等；`params` ≠ 实算（新校验）；张量名重复/尾字节/形状不符/`used≠allNames`（沿用）。
## 4 段序、回滚点、最小验收

```
段0 冻结快照 ──┐                       （可与段1并行；必须先于段3）
段1 Python 新图 ─┴─> 段2 format3+export+G1 ─┬─> 段3a C++ ─┐
                                            └─> 段3b Java ┴─> 段4 基线+验收
```
- **可并行**：段0∥段1；段3a∥段3b（同一份 G1、arch 冻结后互不阻塞）。**必须等**：段2 等段1 的 arch 定稿；段3 两端口都等段2 的 G1；段4 等 **3a∧3b**（Java 是唯一推理路径，缺一不许上闸门）。
- **回滚点**：R0 = tag `v4-format2-freeze` + 上述 4 件 sha（唯一能跑 `wl-*` 的状态）；段1 = 分支 `a-refactor/net`（`model.SEMANTICS` `:206-237` 仍能构造旧图）；段2 = 一个常量 3 处 + `net_blob` 的 arch 写入（revert 单提交即回 format 2）；段3a/3b = 逐端提交 + 冻结二进制（`PATH` 指回 frozen 即回滚）；段4 = 只读产物，不改代码。
- **最小验收命令**：段0 `node tools/trainer-v4-parity.mjs --golden`（期望 PASS 11/11）；段1 `python -m mahjong_ml.v4 check` + `python selfcheck.py`（⚠ 系统 `python` **无 numpy**，必须用项目 torch 环境）；段2 `python -m mahjong_ml.v4.export weights --ckpt <pt> --out <net>` → `python -m mahjong_ml.v4.export golden --trace <dir> --out <g1> --semantics current` → `node tools/policy-gate-check.mjs <net> --strict`（先支持 3）；段3 `pwsh -File server\build.ps1` → `java -jar server\build\mahjong-server.jar --selftest`（`SelfTest.java:110-111`）→ `node tools/trainer-v4-parity.mjs --golden --fixture-semantics current` → `node tools/trainer-v4-parity.mjs <net> <corpus> --tol 1e-6`；段4 `node tools/v4-cache-check.mjs <net> 2 4 <seed>` + `node tools/policy-gate-check.mjs --strict` + 3 族闸门（口径见 `MV1-AMENDMENT §4`）。
- **若段 3 改动量"远超 2,980 行"——先改度量**：真实改动 ≈1,707 行。仍超时的降级阶梯：**D1** conv 用 k 次 `linearBatchFast`（已含在本计划，+0 kernel）；**D2** 轴向注意力先用**现成 34-token MHA**（只加宽+pre-LN，省 +60 行/124K 参数，判据 3 由 joint attention 单独给）；**D3** joint 先 1 层 4 头 `head_dim=32`（省 FLOPs、不省行数）；**D4** 砍 `cross`（cand 直接与 joint memory 点积）——⚠ D2/D4 会改判据 3/10，须**预注册**并如实报。
## 5 风险（触发 / 症状 / 止损）

1. **三端数值分歧**：长 carry 行 Python↔Java/C++ `max|Δ|` 实测 **base 0.2152 / aux 1.2890**（短行 4.9e-6，`NOTES.md:6554-6555`）⇒ 触发：`len(events)>60` 且训练后 `mem/policy_gate` 放大；症状：`TIE=max(1e-5,2max|Δ|)` 失去分辨率（0.430/2.578 > 中位 gap 0.61）；止损：**长/短 carry 分行**报 `TIE`；argmax 必须 3,083/3,083 且真分歧 0；长行 `max|Δ|>0.05` ⇒ 判"不可比"、不上闸门。
2. **tie-break 三端不一致**：现状三端都是"最小 legal 下标"（`Logits.java:24-34`、`net.cpp:610-617`、`pretrain.py:1507` 的 torch argmax）；A− 要 `effect`→牌码序 ⇒ 触发：只在 Java 实现或 `effect` 口径不同；症状：自对弈轨迹与训练标签分叉、golden 全绿但闸门是噪声；止损：**归属写在 caller**（`chooseIndex:1194` / `policies.hpp:269` / Python 采样器），一份规格 + 强制并列（复制 logit）≥1,000 行的三端 argmax 用例。
3. **format 解析偏移错位**：触发：arch 段插在头与块表之间而某读者跳过 `archLen`；症状：张量名乱码/尺寸爆炸或"恰好读到尾"；止损：三端已有 `off==len(raw)` 尾检查（`export.py:314`、`:786`、`:251`）+ 新增 `archLen`/`nTensors≤10,000`/`params` 三处夹逼；`policy-gate-check.mjs` 同步认 3。
4. **旧包误加载**：触发：硬切后 `bot-ai/` 9 个 format-2 包（`vb=51`）被新 jar 读；症状：启动即错 **或更糟**——`chooseIndex` 的 `catch(RuntimeException)` 返回 -1 让上层**静默退回内置 Bot**（`V4Policy.java:1201-1204`）；止损：在 `NetWeights` 分派处就报错 + **逐包负向测试**（9 包逐一期望非 0 退出）+ 启动日志必须打印 `format`。
5. **缓存键未含新语义**：`refcache.code_sha256` 只覆盖 5 个文件（`pretrain.py:3094-3098`：pretrain/model/dataset/spec/export），**不含 `blocks.py`/`cache.py`**；`V4Cache` 槽键只有 `handKey`（`V4Cache.java:29`），C++ `CarrySlot.key` 同（`:1339`）⇒ 触发：A− 在 `blocks.py` 改 tile 布局/转置或同进程换网；症状：π_ref/V_old 用旧 logits、carry 用旧行；止损：`refcache.CACHE_VERSION 1→2`（`refcache.py:33`）+ 键加 `arch/topoId/format/model_sha`；`V4Cache` 槽键加 `fingerprint`，`load` 后 `clearAll()`。
6. **训练/推理池化口径再分叉**：`state_pool` 已 mask-aware（`model.py:507-555`，fail-closed），Java/C++ 一直是 `/max(1,n)`（`:1022`/`:1899`）⇒ 触发：A− 把 state 改成"joint memory 池化 token"而只改 Python；症状：value/placement/belief `max|Δ|` 暴涨（改前实测 rel=1.118、`max|Δ|=149.7`）；止损：无 padding 时四头 `torch.equal` + 2,000 行三端 `value/placement/belief max|Δ|≤1e-5`。
7. **#0 `tile.tile_id` 已在 Python、三端未同步（今天就会炸）**：当前缺省导出 = **82 张量**，Java/C++ 只认到 81 ⇒ 新包载入报 `权重里有 1 个张量没有任何层读它：[tile.tile_id]`（与 `NOTES.md:6615` 的 B3 旧引擎事故同一形态）；止损：段3 先加 bind（Java `:431/871/129`、C++ `:453/1618/171`，各 20-30 行）再导出；CI 加"张量集 == expected"检查。
8. **GELU 的 erf 三端不同**：Java 无 `Math.erf`、C++ `std::erf` 与 torch 末位不同 ⇒ 触发：用 tanh 近似或 A&S 近似；症状：逐层放大、长 carry 行更糟；止损：golden `maxΔ≤1e-4` + 真实权重 `≤1e-6`；不过就**显式**改 Python 为 `approximate='tanh'` 并在 arch `actId=2` 记录。
