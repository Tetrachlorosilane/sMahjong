# v4 架构修改前后对比清单（决策用 · 只读产出）

> 2026-10-18 · `Develop`@`245cc15` · **未改任何代码/未提交/未训练/未采集**；本文件保持 untracked。依据：`lab/mv1/ARCH-AUDIT.md`（下称"审计"，数字均出自其 §1–§7）· `v4/model.py` · `v4/pretrain.py` · `TRAINER-CPP.md §6.25` · `lab/housekeeping/JAVA-MODEL-INTERFACE-AUDIT.md` · `docs/FEATURES-V4.md` · `tools/{policy-gate-check,trainer-v4-parity,v4-cache-check}.mjs`。每条理由只落到"当前结构的可测缺陷"，⛔ 不用"更现代/更主流"。

## 1 总览

| # | 名称 | 级别 | 优先级 | 参数增量 | 重建数据 | 重训基线 |
| --- | --- | --- | --- | ---: | --- | --- |
| C1 | 去输出端 ReLU（只 `fusion.out`）（✅ **已实现（C1-a，仅训练侧）**：第一百零一轮，`v4/model.py: Mlp(final_relu)` + `Fusion.out` + `pretrain --legacy-out-relu`；⛔ Java/C++ 未同步、未跑闸门） | T1 | 建议 | 0 | 否 | 是 |
| C2 | state 级头改 mask-aware 池化（✅ **已实现**：第一百轮，`v4/model.py: state_pool` + `pretrain --legacy-pool`） | **T0** | **必做** | 0 | 否 | 否（要变强才重训） |
| C3 | `mem`/`policy_gate` 非零小初始化 | T0 | 可后做 | 0 | 否 | 是（否则验不到） |
| C4 | value ±30→±60、51→81 bin | T1 | 建议 | +5,790（+0.41%） | 否 | 是 |
| C5 | `ctx.points` ÷1000→÷5000 | T2 | 可后做 | 0 | **是** | 是 |
| C6 | 接线 `cand[96..98]` / 删死维 | T2 | 可后做（接线：建议；删维：不建议） | +576 / −8,448 | 删维要 | 是 |
| C7 | 容量从 evt 挪给 tile | T1 | **不建议先做** | −69,760（−4.99%） | 否 | 是 |

## 2 逐条（before → after → 理由 → 半径/文件/判据 → 风险/成本）
路径前缀：`python/mahjong_ml/v4/`（`model.py`/`blocks.py`/`spec.py`/`pretrain.py`/`export.py`/`value_audit.py`）· `server/src/main/java/mahjong/ai/`（`V4Policy.java`/`V4Features.java`）· `trainer/src/`（`v4policy.cpp`/`v4features.cpp`/`net.cpp`）· `tools/`（`policy-gate-check.mjs`/`trainer-v4-parity.mjs`/`v4-derived-check.mjs`/`V4Probe.java`）· `docs/`（`TRAINING-V4.md`/`FEATURES-V4.md`）· `python/tests/golden/forward-v4.bin`。
### C1 去输出端 ReLU ｜T1｜建议｜Δ参数 0 · ΔFLOPs −3.5k（−0.003%）
- **before** `model.py:141` `nn.Sequential(..., nn.Linear(hidden, cout), nn.ReLU())`（`Mlp` 末端）⇒ `:182` 的 `u` 恒 ≥0；实测 `u` 维 **78.5%** 恰为 0、有效宽度 **41/192**（审计 §4）；`pretrain.py:3138` 记过一次真塌方："融合输出 ReLU 全死、logits 变成常数"（top1 0.165）。三端同款：`V4Policy.java:1340`、`v4policy.cpp:872`。
- **after** `Mlp` 加 `final_relu: bool = True`，只在 `Fusion.out`（`model.py:168`）传 `False`；`ctx`/`cand`（`:257-258`）保持（无缺陷证据）。三端只删 `fusion.out` 那一处 `relu`。
- **理由** 死的不是"接近零"而是**恒零单元**：78.5% 的 `u` 维精确为 0，policy 只在 41 个有效维上打分；且已有一次真事故（ReLU 全死 ⇒ logits 常数 ⇒ top1 掉回合法基线 0.165）⇒ 属可测的容量/稳定性缺陷。
- **半径/文件/判据** T1（改算子；同一份旧权重在新代码上 logits 会变且**形状相同** ⇒ 不用 `format` 区分就会静默换行为）· 改 `model.py`⚠ `V4Policy.java`⚠ `v4policy.cpp`⚠ `export.py`+`python/tests/golden/forward-v4.bin`⚠（夹具重生成）`python/selfcheck.py` `docs/TRAINING-V4.md` · 判据：`frac(u==0)` 78.5%→**<10%**、`u` 非零维 41→**>120**、`trainer-v4-parity --golden` PASS 且真权重逐行 **argmax 全同 N/N（≥3,083）**、`max|Δ|≤tol`；红证 = 旧 format-2 网在新代码上 logits **必须变**（变才证明 bump 必要）。
- **风险·成本** 新网 logits 可负 ⇒ PPO 的 lr/clip 需重标；9 个 v4 包必须走 bump 分支或重训；golden/`refcache` 指纹重算。**4–8 人时**；不重建；**要重训基线**。
### C2 state 级头读未掩码 `u.mean(1)` ｜**T0**｜必做｜Δ参数 0 · ΔFLOPs ≈0（同阶 7k，<0.01%）｜✅ **已实现 2026-10-18（第一百轮）**：`python/mahjong_ml/v4/model.py: state_pool`（缺省 mask-aware）+ `pretrain --legacy-pool`（旧口径逐位复现）+ fail-closed；自证与 KL 读数见 `NOTES.md` §6.5 第一百轮
- **before** `model.py:281` `out = self.heads(u, u.mean(dim=1), mask)` ⇒ 训练批 padding 到 Lmax 时分母 = Lmax；实测候选槽 **48.9% 是 padding**（nlegal 9.2 / Lmax 18），换成 mask-aware 后 value logits `rel=1.118`、`max|Δ|=149.7`（审计 §4）。而 `V4Policy.java:963` / `v4policy.cpp:1837-1840` 除以**真实 n**（推理无 padding）。
- **after** `m = mask.unsqueeze(-1); s = (u*m).sum(1) / m.sum(1).clamp_min(1)`（现成实现 `value_audit.py:538-541`），`Heads.forward` 只收 `s`；`mean_all` 仅留作探针对照。
- **理由** 现状是**训练/上线两套统计量**（Python 除以 Lmax，Java/C++ 除以 n），value/belief 主要由"合法集大小+padding"决定（`rel=1.118`，自身 logit std 5.74）⇒ 正确性缺陷，不是风格。
- **半径/文件/判据** T0（Java/C++ 本来就是 mask-aware ⇒ 旧网在两端**逐位不变**、仍可加载；只改 Python 训练批口径）· 改 `model.py`⚠ `python/selfcheck.py`（加断言）`value_audit.py`（保留 `mean_all` 对照）｜**Java/C++ 不改** —— 这恰是 T0 的证据 · 判据：同一批状态把候选从 n 补 7 个零槽 ⇒ 四个 state 级头（value/placement/belief_hand/belief_tenpai）逐位不变（改前 value `rel=1.118`）；B=1 与 B=32 的 `meanU` 逐位相同；旧网在 Java/C++ 上 logits 逐位不变。
- **风险·成本** 历史"padded 批"上算的读数（value EV 0.0513 等）要重算；head 输入尺度变、训练动态变。**2–4 人时**；不重建；不必重训（要变强才重训）。
### C3 `mem`/`policy_gate` 零初始化 ｜T0｜可后做｜Δ参数 0 · ΔFLOPs 0
- **before** `model.py:166-167` `nn.init.zeros_(self.mem.weight); nn.init.zeros_(self.mem.bias)`；`:204-205` 同 `policy_gate` ⇒ 出生 `gate ≡ 1`、`h_evt` 被乘 0；实测 `policy-gate-check.mjs`：9 个 v4 包里 **4 个 `policy_gate.weight` 全 0 且 `fusion.mem.*` 整组缺失**；训练后打乱 60 行事件窗口 policy 只变 **0.78%**（初始化时 0.00000）。
- **after** 两处改 `nn.init.normal_(w, std=1e-3)`、bias 0；`normalize_state`（`export.py:341-344`）对旧网补 0 不变 ⇒ 旧包逐位不变。
- **理由** 零初始化让"事件顺序/听牌信念"在出生时**零影响**；4/9 包至今门控全 0 ⇒ 这些包的 `belief_tenpai` 退化为 `{policy}`。⚠ 这 4 包是否**由**零初始化造成 **无法判定**（缺：它们的训练配置与 ckpt 原始张量表）；可判定的只是"出生即零影响"。
- **半径/文件/判据** T0（只改初始化，张量名/形状/前向不变；旧版权重从文件读 ⇒ 逐位不变）· 改 `model.py` `python/selfcheck.py` `docs/TRAINING-V4.md §5`；`tools/policy-gate-check.mjs` 不改（用它判）· 判据：新 ckpt step0 `max|w|>0`；`policy-gate-check.mjs --strict` 退出 0；打乱窗口 policy 变化 **>1%**；旧 9 包加载后 logits 逐位不变。
- **风险·成本** 训练早期方差变大；从旧网 warm-start 不再恒等（实验记录须写明）；可能白花钱（门控仍学不活）。**1–3 人时**；不重建；需重训才能验。
### C4 value range ±30 / 51 bin ｜T1｜建议｜Δ参数 +5,790（+0.41%） · ΔFLOPs +11.6k（+0.011%）
- **before** `model.py:27-29` `VALUE_BINS=51`、`VALUE_RANGE=30.0`；`:365` `values.clamp(-30, 30)`；readout `linspace(-30,30,51)`（`pretrain.py:204,538`）⇒ bin 宽 **1.176 千点**。实测夹逼 **12.13%**（`|v|>30`）、>40 **3.85%**、真值 std **21.14**、min/max −55.5/+106.1（审计 §3）。
- **after** `VALUE_RANGE=60.0`、`VALUE_BINS=81` ⇒ bin 宽 1.481 千点；`heads.value` 192×81+81=15,633（原 9,843）。
- **理由** 12% 的标签被夹到两端（两端 bin 同时吃 12.13% 的质量），缺的是**量程**不是分辨率（1.176 千点已够）；值头是 critic，夹逼直接污染优势的基线项。
- **半径/文件/判据** T1（头输出宽度/形状变：`heads.value.weight` ⇒ 新网必须重导；`value_bins` 已在头部 ⇒ **不强制** format bump，但 golden 与宽度断言必须同步）· 改 `model.py`⚠ `export.py:82`（`GOLDEN_DIMS.value_bins=5`）⚠ `python/tests/golden/forward-v4.bin`⚠ `SelfTest.java:4037`⚠ `tools/V4Probe.java` `v4policy.cpp`（golden 宽度）`docs/TRAINING-V4.md:270,817`⚠ · 判据：val `|v|>60` ≤**1%**（改前 12.13%）；首/末 2 格质量 ≤**2%**；参数 +5,790；旧 51-bin 网 ⇒ Python value 读出头必须**报错**（形状不符）而不是错读。
- **风险·成本** golden/宽度断言先红；value 尺度变 ⇒ 优势尺度变，必须重训值头/基线；`_advantages`/`_behaviour_values` 用模块常量 ⇒ 与旧网混用会形状错（fail-loud）。**6–12 人时**；不重建；重训基线。
### C5 量纲 1725×（`ctx.points`） ｜T2｜可后做｜Δ参数 0 · ΔFLOPs 0
- **before** `blocks.py:386-393` `*[(x - 25000.0) / 1000.0 for x in rel]`；三端同值（`V4Features.java:534-544`、`v4features.cpp:905-918`）。实测 `ctx.points` std **11.82**（最大列 **18.8**）vs ctx 其余段 **0.21–0.40**（审计 §2）；points 前 4 列占该层输入方差 **59.4%**。
- **after** 三端同一常数改 `/5000.0` ⇒ std **2.36**（典型段比 **56×→11×**）。若还要压到 20× 以下（按 max/min 口径）则需 `LayerNorm`（+128 参数）或 ÷10⁵。
- **理由** 审计写 **1725×**，但它同时写"其余段 0.21–0.40"——按后者算只有 **56×**；1725× 只在"最大列 18.8 / 最稀疏列 0.0109"口径成立，且 ÷5000 后同口径仍 **345×**。⇒ **判据必须先写清按哪一列比**，否则改完无法判红/绿。
- **半径/文件/判据** T2（改输入张量定义 ⇒ `FEATURE_VERSION` 4→5 + 块指纹变 ⇒ `dataset.py:1522-1526` 构造期拒旧 compact；旧网 featVer=4 被 `V4Policy.java:160` / `v4policy.cpp:634` 拒绝）· 改 `blocks.py`⚠ `V4Features.java`⚠ `v4features.cpp`⚠ `spec.py`⚠ `golden`⚠ `docs/FEATURES-V4.md:225`⚠ + 数据集重建 · 判据：列 std 11.82→**2.36**、典型段比 56×→**11×**；三端常数机械比对（照 `v4-derived-check.mjs`）；`meta.feature_version` 不匹配 ⇒ 拒跑。⚠ v3 的 `net.cpp:240-268` 是**另一版特征，别一起改**。
- **风险·成本** 全量重建 + 9 包全部失效（featVer 硬拒）；重建成本 **无法判定**（缺：当前 compact 行数/构建墙钟）。**8–16 人时 + 重建**；重训基线。
### C6 死维/重复编码（接线 96–98 / 删死维） ｜T2｜可后做（接线建议、删维不建议）｜Δ参数 +576 / −8,448 · ΔFLOPs +1.2k / −16.9k（<0.02%）
- **before** `blocks.py:473-475` `# v4 新增 3 维…这里先留 0`（cand 96–98 三端恒 0，而 `spec.py:44-46` 留了 `CAND_DERIVED=11`、`CAND_RESERVED=29`）；`spec.py:75,80` tile reserved 3 + ctx reserved 6（`blocks.py:419,421` 硬编码 0.0）。实测有效秩 tile **1052/1632**、ctx **46/64**、evt **65/96**、cand **85/128**（审计 §2）。
- **after** ①接线 96–98（sidecar `derived_version` 4 + 三端各算 3 维）；②若删死维：`C_TILE 45 / C_CTX 56 / C_EVT 93 / C_CAND 96`；恒等式重复编码（`unseen≡1−visible`、`drawable≡unseen−own_count` 等）**不删**（首层可线性消去，删了要动三端+重建，收益未证）。
- **理由** 96–98 是"布局承诺 11 维、三端只填 8 维"的死维，且这 3 列**确实被前向读取**（`JAVA-MODEL-INTERFACE-AUDIT §G⑤`：Java 置 1 后 4 条 golden 判据一起红）⇒ 一端填一端不填立即不一致。死维规模审计写 **41/336（12.2%）**，但它列出的槽位是 **46**（tile 3 + ctx 8 + cand 32 + evt 3）⇒ 清单自身不自洽，删维前必须出逐槽清单。
- **半径/文件/判据** T2（改张量内容/布局 ⇒ `FEATURE_VERSION` 4→5 + 块指纹 ⇒ 旧 compact 与旧网一起失效）· 改 `blocks.py`⚠ `features.py`⚠ `spec.py`⚠ `V4Features.java`⚠ `v4features.cpp`⚠ `tools/v4-derived-check.mjs`⚠ `docs/FEATURES-V4.md:195-210`⚠ · 判据：逐列非零率 **>0**（改前 0）；删维只删"构造期恒 0"的列 ⇒ 前向**逐位不变**；参数接线 **+576**（3×192）、删维按实际首层宽 **−8,448**（审计 −5,248 = 41×128 是估算，不可复现）。
- **风险·成本** sidecar 版本升 ⇒ 旧 sidecar 重算；三端漏一端 ⇒ golden 4 条逐位判据一起红；删维最贵（布局+三端+数据+9 包）。**接线 12–24 人时 / 删维 6–10 人时**；删维要重建；都要重训。
### C7 容量从 evt 挪给 tile ｜T1｜**不建议先做**｜Δ参数 −69,760（−4.99%） · ΔFLOPs −0.14M（−0.135%）
- **before** `model.py:104-108` evt = `Linear(96→192)+ReLU+Linear(192→192)` + `GRUCell(192,192)` + `TransformerEncoderLayer(dim_feedforward=384)`；tile `:86-90` 只有 2×Linear(48→64)。实测 evt **575,040（41.13%）**、fusion **641,856（45.91%）**、tile **36,544（2.61%）**、**96.1% FLOPs** 在 evt+fusion（审计 §1）。
- **after** 可选：`dim_feedforward 2d→d`（−73,920）、tile 加 1 层 64→64（+4,160）⇒ 净 −69,760。
- **理由** 无机制证据：打乱 60 行事件窗口 policy 只变 **0.78%** ⇒ 事件塔的问题是**没被用到**（C3 的门控），不是"不够大"；`TRAINING-V4.md:1166` 实测加宽/加深读出头没用。容量不是已测到的瓶颈，收益未证。
- **半径/文件/判据** T1（层数/宽度变 ⇒ 张量表变 ⇒ format bump；旧网形状不符不可加载）· 改 `model.py`⚠ `export.py`（`expected_shapes`）⚠ `V4Policy.java`⚠ `v4policy.cpp`⚠ `golden`⚠ · 判据：同预算消融 + 闸门配对增益 CI 排除 0（**现在没有这个读数 ⇒ 收益无法判定**）。
- **风险·成本** 形状全变、9 包全废、必须重训+闸门（≥数百场）。**16–40 人时**；不重建；重训。

## 3 为什么是这个顺序
- **T0 先做**：C2（train/serve skew，`rel=1.118`）必做；C3（门控出生零影响）同为 T0，但成因未证，可与任一次重训搭车、不必单独立项。两者都**不动 net.bin、不动数据、旧包逐位不变**，失败可回滚。
- **再 T1 里证据最直接的 C1**：78.5% 死单元 + 一次真塌方，0 参数、判据一眼可机检；它和 C2 都作用在 `u` 上，**一次重训验两条**。
- **C4 排其后**：value **不进 argmax**（`JAVA-MODEL-INTERFACE-AUDIT` 表：计算但不进决策），改它不会改打牌；等 C1/C2 稳定后再动值头，否则"值头变了/策略变了"分不开。
- **C6/C7 最后**：删维只有"更干净"，挪容量收益未证；0.78% 的顺序不敏感还没修好就投容量，等于替未证假设付重训费。

## 4 ⚠ 格式 bump 方案（两个选项，⛔ 不替你决定）
头部已有三个版本轴（`export.py:21-28`）：`format`（图/加载器语义）· `feature_version`（输入布局）· `dims`（宽度）。T1 动 `format` 2→3；T2 动 `feature_version` 4→5 **且**块指纹变；C4 只动 `dims.value_bins`（头部已带 ⇒ 严格说不用 bump）。
- **A 双格式并存**：bump 后三端 reader **同时接受 2/3**，前向按 `format` 分支（2 = 旧 ReLU 语义 / 3 = 新语义）。**代价**：三端各一条**永久**兼容分支（Java `forwardAll`、C++ `v4policy.cpp`、Python `model.forward`），每加一个图改动都要维护两套语义；golden 要出两套（否则旧格式没有逐位证据）；硬编码 `NET_FORMAT_V4=2` 的工具（`policy-gate-check.mjs:32`）要改；测试矩阵 ×2。**好处**：9 包继续可加载、可金丝雀回滚。
- **B 硬切只支持新格式**：旧包在加载期报 `format != 3`。**代价**：9 个 v4 包必须处置——4 个退化包本就该重训，**5 个 `wl-*` 是当前唯一非退化基线**（`max|w|` 0.0509–0.0716），图语义变了**不能靠 re-export 保住**（ReLU 语义与训练绑定）⇒ 要么重训、要么永久留在旧服务器；回滚要同时保留旧 jar 与旧 net。**好处**：单一路径、无静默混用、测试面小。
- T2 天然是 B（旧网 featVer 被硬拒，`V4Policy.java:160` / `v4policy.cpp:634`）⇒ **只有 T1 需要你选 A/B**。C4 不 bump 的代价：51-bin 旧网与 81-bin 新网并存，只能靠 `dims` 区分（读得出、意义不同）。

## 5 ⭐ 最小可行集 + 做完先量什么
**选 C2 + C1**（一次重训同时验两条；两条都作用在 `u` 上）。只允许一条 ⇒ **只做 C2**（T0、2–4 人时、旧包逐位不变）。不选 C4 的理由：value 不进 argmax。
**第一件事按序量**：① `frac(u==0)`（78.5%→<10%）+ `u` 非零维（41→>120）；② **padding 不变性**：同一批状态补 7 个零候选槽 ⇒ 四个 state 级头逐位不变（改前 value `rel=1.118 / max|Δ|=149.7`），且 B=1 与 B=32 的 `meanU` 逐位相同；③ 重跑 **`KL(step)` 曲线**（同数据同配方，写清**跑满的步数**与 `stop_reason`）+ value 稳定性（首/末 bin 质量、`std(V)`）——C1 改的是所有头的输入尺度，只有这条曲线能同时看见"学到了"和"炸了"；④ `trainer-v4-parity --golden` + 真权重逐行 **argmax 全同 N/N（≥3,083）**（历史实测 `maxΔ=0`）；⑤ 若同批做 C3：`policy-gate-check.mjs --strict` 退出 0。
