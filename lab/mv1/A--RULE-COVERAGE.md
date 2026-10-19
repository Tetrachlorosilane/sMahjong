# A− 新网络：立直麻将规则/情景覆盖审计（第一百一十二轮，2026-10-19）

> 问题：a−（T1 图，`--arch a-`）**是否覆盖全部会影响决策的规则与情景**。判据 = 每条规则必须能指名「观测由哪个字段承载」+「a− 图哪个部件读它（`文件:行`）」。
> 口径：观测 = `docs/PROTOCOL.md` §8.2（obs v3）；张量 = `v4/spec.py` / `v4/blocks.py`；图 = `v4/model.py`（`ATileTower` 522 / `AEventTower` 569 / `AFusion` 635 / `AHeads` 702）。⛔ 未采集、未跑闸门。
## 1 规则清单（**穷举**，共 37 条；分组 = 下表 ID 前缀）
- **A1–A8 自家回合**：摸打 · 手切/摸切 · 立直宣言（含横置）· 暗杠（立直后形状）· 加杠 · 自摸 · 九种九牌 · 一发/里宝。
- **B1–B8 鸣牌段**：吃（含取法/赤）· 碰 · 大明杠 · 抢杠 · 荣和 · 过 · 食い替え · 鸣牌优先级。
- **C1–C8 特殊和了/流局**：海底/河底 · 岭上 · 人和 · 流局满贯 · 中途流局（四种）· 形式听牌罚符 · 连庄/轮庄/本场 · 终局条件（トビ/agari-yame/All-Last/延长战）。
- **D1–D9 信息状态**：三种振听 · 宝牌指示/杠宝 · 里宝 · 赤五 · 可见/剩余/可摸 · 对手立直/一发/巡目 · 点数与顺位 · 场风/自风/庄家 · 副露身份。
- **E1–E4 值域与不可见**：动作空间（`cand` 的 `type/tile/tiles/tsumogiri/kan/noarg`）· 规则预设差异 · 每候选打点 · 对手手牌/牌山/里宝（合法不可见）。
## 2 覆盖矩阵（"消费"列的证据全部是 `model.py` 的行号；写入侧给 `blocks.py` 行号）
| ID | 规则/情景 | 决策是否需要 | 观测承载（字段 → 写入） | a− 是否消费（证据） | 结论 | 缺口影响 |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | 摸打 | 是 | `legal`→`cand` type/tile 槽（`blocks.py:443-476`，键 `discard:<码>`） | cand→cross-attn q（686-687）→out（692） | 覆盖 | — |
| A2 | 手切/摸切 | 是 | `cand` base `tsumogiri` 槽 83（`spec.py:133`；`blocks.py:466`） | 同上（686） | 覆盖 | 与 D9 的牌河口径分离（evt 另有 92 槽） |
| A3 | 立直宣言（含横置） | 是 | `cand` riichi+牌码；横置 = evt `sideways`（`blocks.py:350-351`）+ `river_after_riichi`（212） | cand（686）+evt（583） | 覆盖 | — |
| A4 | 暗杠（立直后形状） | 是 | `cand` `kan:ankan` + `kan_kind` 84..86；形状约束在引擎侧（`Rules.java:105`） | cand（686） | 覆盖 | 策略只见合法集 ⇒ 无需观测 |
| A5 | 加杠（小明杠） | 是 | `cand` `kan:kakan`（同 A4）；被抢另发询问 | cand（686） | 覆盖 | — |
| A6 | 自摸和 | 是 | `cand` type=`tsumo`（noarg 87） | cand（686） | 覆盖 | 番/符不在（见 E3） |
| A7 | 九种九牌 | 是 | `cand` type=`kyuushu`（`Round.java:1275`）+`ctx.ask`（433-438） | cand（686） | 覆盖 | — |
| A8 | 一发 / 里宝 | 一发是 / 里宝否 | 一发=`ctx.seats`（427）；里宝**绝不下发**（PROTOCOL §3.10） | ctx→`AMlp`（959） | 覆盖 / 合法不可见 | 里宝只在结算，决策时点不存在 |
| B1 | 吃（取法/赤） | 是 | `cand` `chi:<a>+<b>`（`tiles` 46..82） | cand（686） | 覆盖（自家） | 对手的吃见 D9 |
| B2 | 碰 | 是 | `cand` `pon:<a>+<b>` | cand（686） | 覆盖 | — |
| B3 | 大明杠 | 是 | `cand` `kan:daiminkan`+`tiles` | cand（686） | 覆盖 | — |
| B4 | 抢杠 | 是 | `kind` 归一到 `claim`（`Round.java:1796`）；`from`（431）+ 候选集只剩 ron/pass | ctx（959）+cand（686） | 覆盖（可由候选集反推） | 报文有 `chankan`、漏斗没有（刻意） |
| B5 | 荣和 | 是 | `cand` type=`ron`+`ctx.ask.win_note`（432-438） | ctx+cand | 覆盖 | 打点不在（E3） |
| B6 | 过 | 是 | `cand` type=`pass` | cand（686） | 覆盖 | — |
| B7 | 食い替え | 是 | 引擎把禁打的牌**从 legal 删掉**（`Round.java:500/2500-2509`）⇒ 只在"候选缺失"里体现 | cand 掩码（733） | 部分 | 分不清"食替禁"与"手里没这张" |
| B8 | 鸣牌优先级 | 否（只决定谁被问） | — | — | 覆盖（引擎侧） | 无需观测 |
| C1 | 海底/河底 | 是 | `ctx.wall` haitei/houtei（400-401） | ctx（959） | 覆盖 | — |
| C2 | 岭上 | 是 | `ctx.wall` rinshan + `dead_wall_left`（396/402） | ctx（959） | 覆盖 | — |
| C3 | 人和 | 否（`Rules.java:21` `renhou="off"`） | — | — | 覆盖（不存在） | — |
| C4 | 流局满贯 | 是（罕见） | **无字段**（`Rules.java:28` 有开关、obs 没有） | — | 缺口 | 极端手牌下的弃牌选择只能瞎猜 |
| C5 | 中途流局（四种） | 否（流局本身无决策） | 开关 `Rules.java:23-27`；obs 无 | — | 部分 | "四家立直前该不该宣言"缺依据；`round.honba` 只给事后 |
| C6 | 形式听牌罚符 | 是（终局 EV） | **无 `notenPenalty`**（`Rules.java:35`）；自己听牌可由 `cand[88..95]` 向听反推 | cand（686） | 部分 | 罚符额（1000/1500/3000）缺失 ⇒ 终局弃和取舍按默认预设 |
| C7 | 连庄/轮庄/本场 | 是 | `ctx.round` honba/riichi_sticks/dealer（372-378） | ctx（959） | 覆盖 | — |
| C8 | 终局条件（トビ/agari-yame/All-Last/延长战） | 是 | **全不在 obs**（`Rules.java:12/29/30/31/77`） | — | 缺口 | "这局能不能结束、必须直击谁"不可判；`bakaze/kyoku` 只给第几局 |
| D1 | 振听（三种） | 是 | 只有**单个** `furiten` 布尔（415）；三种的区别与期限不在 | ctx（959） | 部分 | 同巡振听"下次摸牌解除" vs 立直振听"到本局结束"不可分 |
| D2 | 宝牌指示/杠宝 | 是 | `tile.global` dora_indicator/is_dora（268-269）+evt `dora_flip`（323-328） | tile（548）+evt（583） | 覆盖 | 杠宝即时更新（`dora_indicators` 含全部已翻） |
| D3 | 里宝/杠里宝 | 否（不可见） | 绝不下发（PROTOCOL §3.10） | — | 覆盖（合法不可见） | — |
| D4 | 赤五 | 是 | `tile.own` own_aka（172）+evt tile_aka/called_aka（343-346）+cand `tiles` 槽 | tile+evt+cand | 覆盖 | — |
| D5 | 可见/剩余/可摸 | 是 | `tile.global` visible/unseen/drawable（260-267） | tile（548） | 覆盖 | 后两者是恒等式派生（§3④） |
| D6 | 对手立直/一发/巡目 | 是 | `ctx.seats` riichi+riichi_turn+ippatsu（423-427）+evt `turn`/`rip_phase` | ctx+evt | 覆盖 | — |
| D7 | 点数与顺位 | 是 | `ctx.points` 12（386-393） | ctx（959） | 覆盖 | 量纲见 §3③ |
| D8 | 场风/自风/庄家 | 是 | `ctx.round` bakaze one-hot+dealer 偏移（372-378） | ctx（959） | 覆盖 | 自风隐含在 dealer 偏移里 |
| D9 | 副露身份（种类/被鸣牌/组合） | 是 | evt `meld_kind`(5)+`called_kind`+`actor`+`from`（338-342）；`tile` 只有 `meld_count`（142/219） | evt（583） | **部分** | 吃的组合不可重建（§3①） |
| E1 | 动作空间（`cand` 全槽） | 是 | 88 基段（`spec.py:97/133`；`blocks.py:466`）+`legal` 顺序 | cand（686）+掩码（733） | 覆盖 | 定长头 79 只用于输出，参数化动作用键 |
| E2 | 规则预设差异 | 是 | 只有 `kuitan` 进 obs（`Observation.java:311`），**v4 四张量一个都不读**（`python/mahjong_ml/**` 命中 0） | — | 缺口 | トビ/食替/流局开关/赤五数/立直条件全不可见 ⇒ 跨预设按 mleague 直觉打 |
| E3 | 每候选打点（番/符/点） | 是 | `cand.derived` ⑨–⑪ **三端接线**（Python `blocks.py:_cand_v4_dims` · Java `ObsFeatures.perCandidateV4` · C++ `obffeatures.cpp:perCandidateV4`）：⑨ 打点期望（`value_points/(1+打后向听)` `/32000`）+ ⑩⑪ 打后危险度两口径（A 段 `danger_worst[k]`/`danger_riichi[k]` `/100`） | cand（686）+ 首层已解除掩码（`model.py:161` 现为 `99..127`） | **覆盖（已修，第一百一十三轮）** | 番/符仍不在（只有粗估点）；旧紧凑集这 3 列仍 0（`load_split` 告警） |
| E4 | 对手手牌/听牌、牌山顺序、里宝 | 否 | 绝不下发（PROTOCOL §8.2 末段） | — | 覆盖（合法不可见） | — |
## 3 ⭐ 五条已知缺口的再确认
**① 副露身份 —— 部分可重建。** `evt` 每条带 `meld_kind`（**5 种**：`chi/pon/ankan/kakan/daiminkan`，`spec.py:58`）+`called_kind`（43..77）+`actor`（78）+`from`（82）；因此**碰/大明杠/暗杠/加杠的身份完全可定**（同一牌种 3~4 张，被鸣牌即身份）。**但 `chi` 的具体组合不可重建**：整副牌码数组 `tiles` 在 obs 里有（PROTOCOL §8.2），`blocks.py:322-354` 的 `event_matrix` **只写 `tile`/`called_tile` 两个单张槽、从不写 `tiles`** ⇒ 被吃牌是"顺子里的哪一头"（3 选 1）丢失；`tile` 塔每对手只有 `meld_count` 1 通道（`blocks.py:142/219`），补不回来。判据：**能从 evt 重建"副露的种类+被鸣牌+来源座位"，不能重建"吃的三张是哪三张"**。
**② 牌河顺序 —— 窗口内够、整手不够。** `tile` 塔只有聚合计数（river_all/before/after/tedashi/tsumogiri，`blocks.py:195-219`）；**顺序**只在 `evt`：每条 `discard` 带 `tile`+`turn`+`tsumogiri`+`sideways` 且事件有序（`blocks.py:322-354`）。所以"哪一巡打了哪张"在 K=60 窗口里完整、窗口外只剩 `h_evt` 的压缩记忆 + `ctx` 的永久摘要（`riichi_turn`、`riichi`）。⇒ 结论：**顺序信息在窗口内充足，跨整手依赖 carry**。
**③ `ctx.points` 量纲 —— 首层 LN 真的做了。** a− 的 ctx 编码器是 `AMlp`：`self.norm = nn.LayerNorm(cin)`（`model.py:473`）+ `self.norm(x*mask)*mask`（`model.py:485`），实例化在 `model.py:959`（`AMlp(C_CTX, d_model, CTX_DEAD_DIMS)`）。写入侧是 `(x−25000)/1000`（`blocks.py:386-393`）⇒ ±75 量级（std ~11.8）在**进 `fc1` 之前**被逐样本 LN 消掉；`ctx` 出现在 `AFusion` 的 `out` 拼接里（688/692）。⚠ 判据 9 量到的 `ctx 列 std 比 180.9×` 是 **LN 之前**的输入列，两件事不矛盾。**第一百一十六轮（P4）对账**：`ctx` / `cand` 的首层 LN **已存在**（`AMlp.norm`）⇒ **不重复加**，如实标"已存在"；**`evt` 原本没有** ⇒ 本批新增 `event.norm0`（`LayerNorm(96)`，+192 参数，**可缺** ⇒ 旧包逐位不变）。⚠ 判据 9 的读数**不会**因"加 LN"而变（它量 LN **之前**的输入列，实测前后逐位相同）⇒ 该判据要么改口径、要么等 T2。
**④ 死维/恒等维 —— 第一百一十六轮（P4）后：已掩 42 维；`tile` 的 13 维**待 T2**。**
`CTX_DEAD_DIMS = (17,35,37,58..63)` 9 维 + `CAND_DEAD_DIMS = 99..127` 29 维（E3 摘掉 96..98 之后）
+ **`EVT_DEAD_DIMS = (0,6,7,95)` 4 维（本批新增）** ⇒ 掩码在 `AMlp.__init__/forward`（471-479/485）
与 `AEventTower._encode`（**与 evt 首层 LN 同一个构造期开关** `evt_ln`）。
- **已修（零重建）**：`evt` 的 3 个"服务端从不进窗口"的 type 槽（`draw`/`agari`/`ryuukyoku`；
  全量 33,615 决策 × 60 行实测**最大绝对值恒 0**）+ `seq_delta`（**恒 0.125**、方差 0 ⇒ 零信息维，
  与 `ctx[17]≡ctx[8]` 同一类）。⛔ 另外 11 个"小样本看着是 0"的列（`called_kind` 9 个 + `called_aka`
  等）在全量上最大绝对值是 **1.0** ⇒ **不掩**（掩了就是真丢信息）。
- **待 T2（`FEATURE_VERSION 5` + 重建/重采）**：`tile` 的 13 个冗余维（river_all 3 / before_riichi 3 /
  unseen / drawable / is_yakuhai / suit_s / reserved 3）—— 它们**冗余但有信息**，真删要换特征版本。
- 判据 2 的分母随之从 evt 96 → **92**（前后对比表见 `ARCH-REDESIGN` §14.4）。
**⑤ 事件窗口 K=60 —— carry 确实进模型（但只经一个 token）。** 一手约 70–125 条公开事件 ⇒ 60 条窗口装不下整手。补偿链：`h`（**窗口之前**的 GRU carry，数据侧 `h0[128]`，S30 非零率 5.19%）→ `AEventTower.forward` 从 `h` 起逐事件推进（`model.py:600-606`，真实行掩码 `real`）→ `h_evt` = **窗口之后**的 carry → `AFusion` 把它当**第 95 个 token**（`model.py:678-681`）进 joint attention（683-685）。⇒ **是，carry 真的进前向**（不是死参数：0 参数但参与注意力）；⚠ 局限在 §14.3 已实测：顺序敏感性只有 1.37%/0.0084 ⇒ 这条通道的**梯度太弱**，不是"没接"。**第一百一十六轮（S1）**：已加 `h_evt` 的**逐候选读出**（`fusion.evt_kv`+`evt_gamma`，+32,896 参数，三端跑在 3,083/3,083 上）；**但** 1,200 步 BC 短训后事件打乱仍只有 **1.66% / KL 0.0037**（<5% / <0.02）⇒ 判据 3 **仍未达标**，归因见 `ARCH-REDESIGN` §14.5。
## 4 结论与缺口排序
**一句话**：a− 覆盖了**回合与鸣牌段的全部动作/役种判定入口**（动作空间、宝牌、振听、点数顺位、巡目、副露"够用"的那部分），但**不覆盖"规则预设/终局条件/每候选打点"三类语义**，且副露组合、振听种类、牌河跨手顺序是**部分**。
**缺口按"是否影响正确性"排序**（①最重）：
1. **E2 规则预设不可见**（トビ/食替/流局开关/赤五数/立直条件）—— 影响**全部决策的"规则直觉"**；补救：加 `ctx` 预设位（输入侧 = **T2**，要 `FEATURE_VERSION 5` + 全量重建）。
2. **C8 终局条件缺失**（All-Last/トビ/agari-yame）—— 影响**终局押し引き**（最大 EV 项）；补救：`ctx` 加 4 位（T2）。
3. **E3 每候选打点 —— 已修（第一百一十三轮）**：⑨–⑪ 已三端接线并解除首层掩码（`96–98` 出死维，32→29 列），**零重建**（张量宽度、sidecar 段长、`FEATURE_VERSION` 都没变；旧轨迹不用重新 featurize，但**旧紧凑集这 3 列仍是 0** ⇒ `dataset.load_split` 告警）。三端逐位对拍见 NOTES §6.5 第一百一十三轮。
4. **D1 振听只 1 位** —— 影响**立直见逃后的荣和预期**；补救：`ctx` 加 2 位（T2）或由 evt 反推（T1 可先做注意力读出）。
5. **D9 吃的组合不可重建** —— 影响**对手危险度的手工估计**（但 `tile.danger/safety` 已由引擎算好，影响被大幅抵消）；补救：`evt` 加 `tiles` 三槽（T2）或把组合做进 sidecar（T1/T2 皆可，但都要重建）。
6. **B7 食替不可辨识 / C4 流局满贯 / C5 中途流局 / C6 罚符额** —— 影响**边角取舍**；前三者可由"补规则位"一并解决（T2），C6 可先按预设常量（不改图）。
