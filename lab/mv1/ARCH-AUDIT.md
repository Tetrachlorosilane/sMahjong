# v4 架构只读审计（权重形状 / 连接结构 / 性价比排序）

> 2026-10-18 · `Develop` · **零训练 / 零采集 / 未改代码 / 未提交**。证据：`v4/{model,spec,blocks}.py` 静态读 + **纯 CPU 前向**：val **33,615 行**（`tmp/mahjong-mslt/compact/mslt-s00`，只读 mmap，feature_version=4）+ 已发布 ckpt `bot-ai/wl-dama/net.bin`（256 行）。⚠ 同期另一会话改了 `pretrain.py`（MV-1 第九十九轮 lr 调度），**不是本审计**。⛔ 本审计不判"改完更强"。
## 1 逐子模块参数（总 **1,397,992** = 1.40 M ✓，fp32 5.592 MB；**103.3 MFLOP/决策** @L=18,K=60）

| 子模块 | 参数 | 占比 | 输入→输出 | 层 / 激活 / 归一化 | FLOPs |
| --- | ---: | ---: | --- | --- | ---: |
| tile 塔 `model.py:80-96` | 36,544 | 2.61% | `[34,48]`→`[34,64]`+池化`[192]` | 2×Linear(48→64→64)+ReLU、1×MHA(64,4头,head_dim=16)、LayerNorm(残差)、Linear 64→192、mean-pool | 1.6% |
| evt(GRU) 塔 `:99-134` | **575,040** | **41.13%** | `[60,96]`→`[60,192]`+`h_evt[192]` | 2×Linear(96→192→192)+ReLU、GRUCell(192,192)、**1×TransformerEncoderLayer**(d=192,ffn=384,4头,pre-norm,dropout=0) | **66.4%** |
| ctx 塔 `:257` Mlp | 49,536 | 3.54% | `[64]`→`[192]` | Linear 64→192→192，**末尾也有 ReLU**（`Mlp:141`） | 0.1% |
| cand 塔 `:258` Mlp | 61,824 | 4.42% | `[n,128]`→`[n,192]` | 同上（128→192→192） | 2.1% |
| fusion `:147-185` | **641,856** | **45.91%** | 见 §4 | tile_proj 12,480 / q_tile 148,224 / q_evt 148,224 / gate 73,920 / write 37,056 / film 37,056 / mem 37,056 / out-Mlp(576→192) 147,840 | 29.7% |
| heads（7 个） | 33,192 | 2.37% | 全部单层 Linear | 无隐层、无归一化 | 0.1% |

- 7 头逐项：policy 193（0.014%）· policy_gate 768 · value 9,843（0.70%）· placement 772 · belief_hand **19,686（1.41%，最大头）** · belief_tenpai 579 · danger 772 · effect 579。**87.0% 参数 / 96.1% FLOPs 在 evt+fusion**；tile（自家手牌+四家牌河）只占 2.61% 参数。
- 文档 `TRAINING-V4.md:151-159` 分项与代码不符：Tile「2 层/·120k」实为 1 层/36.5k；Event「嵌入+正弦位置/2 层/·520k」实为无嵌入无位置/1 层/575k；Heads「·400k」实为 33.2k（12×）；合计「≈1.5M」只是碰巧接近。
## 2 输入维（`state=615` 是 v3 口径；v4 实读四张量 336 维）

- **615 = 544 + 71**（`features.py:131-154`）：544 = 34 暗牌 + 34 赤五 + 4×39 副露(156) + 4×34 牌河(136) + 34 宝牌指示 + 4 立直 + 4 一发 + 4 点数 + 5 位置点数 + 5 场次 + 5 牌山 + 4 自家 + 4 询问 + 34 visible + 37 摸牌槽 + 37 被鸣槽 + 4 from + 3 win_note；71 = 34 `danger_worst` + 34 `danger_riichi` + 3（after_shanten/han/points），int16，分母 100/100/8/13/32000（`features.py:31,44-45`）。
- **sidecar 七段**（`FEATURES-V4.md:259-263`，518.5 B/决策）：A 71·**int16**/决策 · B nLegal·int16 · C 8·**int16**/候选 · D/E 102·**int8**/决策 ×2 · F/G 34 位图 5 B/决策 ×2。
- **evt 96**（`spec.py:48-64`）= 8 type + 34 tile + 1 aka + 34 called + 1 + 4 actor + 4 from + 5 meld + turn/tsumogiri/sideways/rip/seq 各 1；**cand 128** = 88 基础 + 11 派生 + 29 预留；**`cand[96..98]` 恒 0** = 派生⑨–⑪（打点期望/两种打后危险度）**三端都没接线**，`blocks.py:473-475` 只写 0（`FEATURES-V4.md:209-210`、`JAVA-MODEL-INTERFACE-AUDIT.md:41`）。
- **重复编码（恒等式，max|diff|=0）**：`unseen ≡ 1−visible`、`drawable ≡ unseen−own_count`、`is_yakuhai ≡ is_honor`、`suit_m+p+s ≡ 1−is_honor`；`ctx[17] ≡ ctx[8]`（自家点数 r=1.000000）；`river_all = before+after = tedashi+tsumogiri`（丢包时差 0.25，r=0.993）。
- **有效秩（中心化 SVD，6000 样本）**：tile 1052/1632（64%）· ctx **46/64（72%）** · evt 65/96（68%）· cand 85/128（66%）⇒ 每张量约 1/3 维线性冗余或死维。**稀疏/稠密**：evt/cand 的 one-hot 段用 dense Linear 吃满，cand 基础段实测仅 **2.29%** 非零——但首层只 24.8k，不是主要矛盾。
- **量纲（val 实测 std）**：ctx.points **11.82**（最大列 18.8）vs ctx 其余段 0.21–0.40 ⇒ **同层列 std 比 1725×**，points 前 4 列占该层输入方差 **59.4%**，第一层权重病态；tile 0.019–0.441（23×）· evt 0.0087–0.334（38×）· cand 0.0052–0.235（46×）尚可。
- **稀疏/稠密**：evt/cand 的 one-hot 段用 dense Linear 吃满，cand 基础段实测仅 **2.29%** 非零——但首层只 24.8k，不是主要矛盾。
## 3 输出维

| 头 | 维度 | 语义 | 容量判 |
| --- | --- | --- | --- |
| policy | `[L]` | 每个**合法候选**一个 logit，softmax 只在 `legal` 上（`:220-221`） | 193 = 纯线性打分；**可变动作空间 + 共享 `u` 内积**方向正确（Suphx 是固定 34/2 输出），容量足够（表示在 `u`） |
| value | `[51]` | `final_scores−起点`（千点）HL-Gauss | 9,843；**range ±30 太窄**：真值 std **21.14**、`|v|>30` 占 **12.13%**（>40 3.85%）、min/max −55.5/+106.1 ⇒ 12% 标签夹到两端；bin 宽 **1.176 千点（1176 点）**，绝对分辨率够，缺的是量程 |
| placement | `[4]` | 整场顺位 | 772；靠 state，容量偏少（可接受） |
| belief_hand | `[3,34]` | 对手手牌真值 | **19,686（1.41%，最大头）**，但输入是候选池化向量（§4），语义错配 |
| belief_tenpai | `[3]` | 三家听牌 | 579+门控 768；**经 `policy_gate` 真影响 argmax**（实测 2/256 行翻转） |
| danger | `[L,4]` | 候选 × 目标家放铳概率 | 772，逐候选共享权重，合适 |
| effect | `[L,3]` | 打后牌效 | 579，免费标签，合适 |
## 4 连接结构

- **汇合 = 拼接 + 两次乘法门控**（不是加）：`u_tile=MHA(cand→tile)`、`u_evt=MHA(cand→evt)`（两个独立 MHA）→ 拼 `[u_tile, evt_mod, ctx]` 576 维 → MLP → `u`；`g=sigmoid(Linear([u_evt,u_tile]))`、`state=tile_pool+g·write(u_evt)`、`evt_mod=u_evt·(1+tanh(film(state)))`、`u←u·(1+tanh(mem(h_evt)))`。
- ⚠ **`state` 是逐候选 `[B,L,192]` 而非全局状态**（`tile_pool[B,1,192]` 广播 + 逐候选 delta）；它**只进 FiLM**，`forward` 不返回（`:185`）。
- ⚠ **state 级头读 `u.mean(dim=1)`（`:281`）且未掩码**：候选槽 **48.9% 是 padding**（mean nlegal 9.2/Lmax 18）。实测换 mask-aware 均值：value logits **rel=1.118、max|Δ|=149.7**（自身 logit std 5.74）⇒ **value/belief 主要由"合法集大小+padding"决定，不是状态**。
- **candidate 塔与主干不共享权重**：cand 只当 query，tile/evt 只当 KV，无共享 trunk；**跨塔**：cand↔tile、cand↔evt 有注意力，**tile↔evt 没有**（只经 g/FiLM 乘法调制）。
- **层宽比 192:64=3:1**：tile MHA head_dim 仅 16；tile 2.6% 参数 vs evt 41%，与"手牌/牌河是主信息"的直觉相反。
- **残差/归一化**：只有 tile（post-LN 残差）与 evt Transformer（pre-norm）；ctx/cand/fusion.out **无归一化、输出端 ReLU**（`Mlp:141`）⇒ `u≥0` 恒成立，实测 **78.5% 的 u 维恰为 0**（有效宽度 ~41/192）。
- **梯度短路**：`fusion.mem`/`policy_gate` 零初始化（`:166-167,204-205`）⇒ 出生时 `h_evt`/`belief_tenpai` 对 argmax 零影响；4/9 已发布 v4 包至今全 0/缺张量（§6）。
## 5 与 Suphx / Mortal 的结构差异（只比结构）

- **Suphx**（[arXiv:2003.13590](https://arxiv.org/abs/2003.13590) §2.2）：五个**分开的** CNN 模型（打牌 34 类；立直/吃/碰/杠 2 类）+ 规则和牌模型；输入 `34×838/34×958` 通道、**无 pooling**、100+ 组 look-ahead 特征；奖励预测器是 2 层 GRU（回报归因，不是输入编码器）。**它有我们没有**：34 轴卷积的局部性/平移共享、手工 look-ahead、按动作类型分模型。**我们有它没有**：单一候选打分器（可变动作空间，一个 softmax 覆盖吃碰杠）、事件流+GRU carry、逐候选 danger/effect。
- **Mortal**（[model.py](https://github.com/Equim-chan/Mortal/blob/main/mortal/model.py)、[文档](https://mortal.ekyu.moe/)）：`Conv1d(k=3)` ResNet + BatchNorm + Mish + SE 通道注意力 + `32×34→1024`；dueling Q `v+a−mean(a)` 在**固定 ACTION_SPACE** 上掩码；另有 GRU 顺位预测器与 AuxNet。**它有我们没有**：局部卷积、BatchNorm、通道注意力、dueling、真正的全局标量 `v`。**我们有它没有**：候选特征塔+双向门控、逐候选 danger/effect、增量事件缓存。⇒ **路线不落后**（candidate scoring 比两家固定动作空间更表达得动"吃哪两张"），落后的是**读出头接线**与**事件顺序建模**。
## 6 死维度 / 未被前向读取的张量

- **结构性死维（代码永不填）**：tile 45–47（预留 3）· ctx 35、37（硬编码 0.0）+ 58–63（预留 6）· cand 96–98（承诺未接线）+ 99–127（预留 29）· evt type 的 draw/agari/ryuukyoku（3 槽，服务端从不发）。合计 **41/336 输入维（12.2%）**。
- **数据级死列（val）**：cand 共 **37 列恒 0**（含 col 8 `kyuushu`、col 80–82 赤五槽位）；ctx 2、3（场风 W/N，语料只有 E/S）；`seq_delta` 恒 0.125（= 真实行标记，可省）。`e_tokens`/`h_evt`/`state` 三个返回键不进头（`h_evt` 仅经 mem 门控）；`Fusion` 内部 `state` 除 FiLM 外无人消费。
- **未被读的张量**：`fusion.mem.*`（37,056）+ `heads.policy_gate.*`（768）= **37,824 参数（2.71%）**，在 `v4-bc-004 / v4-league-g08 / v4-p3-001 / v4-ppo-001` **整组缺失或全 0**（`policy-gate-check.mjs` 实测：在线 5 · 退化 4）；这些包里 `belief_tenpai`（579）也不在推理路径。`v4-bc-004/net.bin` 5,443,270 B ≈(1,397,992−37,824)×4+头/名表 ⇒ 与"缺张量"一致。
## 7 判定 + 结构性改进排序

**判定：基本适合四人立直麻将，但有几处明显结构问题**——① 候选打分+可变动作空间是对的（§3/§5）；② **state 级头读未掩码候选均值**（value 对合法集 rel=1.118）是接线缺陷；③ **事件窗口对顺序不敏感**（打乱 60 行：初始化 rel=0.00000，训练后仅 0.78%）；④ 量纲/容量分配次优（ctx 1725×、tile 2.6% vs evt 41%）。

| # | 改什么 | 预期收益（代理指标） | 成本 | 风险 | 静态判据 |
| --- | --- | --- | --- | --- | --- |
| R1 | state 级头改读真状态（tile_pool⊕h_evt⊕ctx），至少 mask-aware 均值 | **能提升**：value 输入不再随合法集/padding 变（现状 1.118）；value EV 0.0513→合法特征天花板 0.0693 有 0.018 空间 | +74,112（+5.3%）或 0 | 中（三端+语义） | 加/删一个候选后 value logits 逐位不变 |
| R2 | `VALUE_RANGE 30→60`、bins 51→81 | **能提升**：夹逼 12.13%→~1%（>40 占 3.85%） | +5,790（+0.41%） | 低（标签+三端 readout） | val `|v|>range` ≤1%、边 bin 质量 ≤2% |
| R3 | 事件加相对位置编码（0 参数）+ GRU 输出进 Transformer；或 mem 非零小初始化 | **能提升**：顺序敏感度 0→>0；现在 25.7% FLOPs 的 GRU 在退化 ckpt 里被乘 0 | 0 参数 | 中（缓存口径不许变） | 打乱窗口后 policy 变化 >1%，且"增量==全量"仍绿 |
| R4 | 去掉 `Mlp` 输出端 ReLU | **可能提升**：u 零单元 78.5%→目标 <10%；有效宽度 41→~150 | 0 | 低 | `frac(u==0)` 与 policy 头有效秩 |
| R5 | ctx.points ÷5000（或 LayerNorm） | **能提升（条件数）**：列 std 比 1725×→<20× | 0（+384） | 很低 | 首层输入列 std 比、权重梯度范数占比 |
| R6 | 容量从 evt 挪给 tile（ffn 2d→d 或 tile +1 层/34 轴 conv） | **更现代/更合理，收益未证**（`TRAINING-V4` §14.8 读出探针显示加深无效） | −74,112~+12,288 | 中（需重训） | 参数/FLOPs 占比 + 消融 |
| R7 | 接线 cand 96–98；删 41 个死维 | 接线：**能提升（小）**；删预留：**只是更干净** | +576 / −5,248 | 低（三端同改） | 逐列非零率 >0；删除前后前向逐位不变 |
## 8 局限

只能回答"结构是否合理、哪里有浪费/缺陷"，**不能**回答"改完是否更强"（那只能由闸门判决）。证据面：1 份 ckpt（`wl-dama`）+ 1 份 val（33,615 行）纯前向；未训练、未采集、未改代码、未提交；`raw/**`、`style-reward/**`、`tools/build/**` 权重全程只读。
