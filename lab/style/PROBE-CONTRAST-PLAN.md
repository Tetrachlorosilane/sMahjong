# 探针预注册 · 同状态对比式优势「可学天花板」（2026-10-12 冻结；⛔ 跑后不改）

**问题**：只看公开状态，能否**样本外**预测"同一状态下两个动作哪个更好"（配对结果差的符号），且显著优于安慰剂？能 ⇒ 方向族有可学对比信号；不能 ⇒ **方向族彻底封存**（一条干净负结果）。
## 1 两臂（同 seed ⇒ 同牌山；⛔ 不开 `--rotate*` ⇒ 学生席恒 = 0；对手两臂逐字相同）
命令（逐字，各跑一次）：`trainer.exe selfplay 600 --workers 8 --policy <学生>,net:<def7>,net:<win2>,net:<atk3> --seed 52940001 --out <DIR> --aux --sample 1`
A0 学生 = `tools/build/v4-expert-defrep-g10/net.bin`（**起点网/现任**，`PREREGISTRATION-P3-01.md §2`）· A1 = `tools/build/v4-wl-dec6170-4g-c2-g04/net.bin`（风格网 **wl-riichi**）· 对手 = def7/win2/atk3（argmax）· 各 **600 场** · 落点 `S:\mahjong-training\raw\probe-contrast\{a0,a1}`（**逐手轨迹读完即删**，只留 summary/拟合结果/台账）。
## 2 同状态判据（逐位，`domdiff` 类口径）
按 `(game,hand_no)` 对齐两臂，取**第一处** `chosen` 不同的决策行 `k`（step 最小者）。同状态成立 = `step<k` 的全部学生行 `chosen` **逐位相同** 且 `md5(obs)` **逐位相同**，且 `k` 行 `md5(obs)` 相同；任一不符 ⇒ 丢该局并计数。
## 3 目标量与特征
标签 `y = sign(Δ)`，`Δ = hand_delta[A1][席0] − hand_delta[A0][席0]`（**净得失点**，取 `hand` 行的 `delta[0]`）；`Δ=0` 丢弃（计数照报）。
特征（**只许公开**）：① 该席**合法观测**白名单 —— 自家 34 维手牌/赤、`drawn`、`player_draws`、`menzen`、`self_riichi`、`furiten`、`melds`、四家 `discards`、`dora_indicators`、`riichi`/`ippatsu`、`scores`、`round`、`tiles_left`/`dead_wall_left`/`total_discards`/`kan_count`/`any_call`/`visible`/`kind`；② **两个候选动作的动作键编码**（`chosen` 串 → `kind` one-hot + 34 维牌 one-hot + 赤 + `chosen_index`/`nlegal`）。⛔ 不许含 logits/value/门控、臂身份、`hand` 行结果字段。
## 4 学习器与切分
**小 MLP**（只用 torch：本环境 `sklearn`/`scipy`/`gbdt` **不存在**）：`in→64→32→1`、ReLU、dropout 0.1、AdamW lr 1e-3、batch 256、≤30 epoch、早停看 OOS 折。
切分 **按场**（`game` id 5 折 GroupKFold；同一场的配对行**只进一折**）⇒ 报 **OOS 折拼接**上的读数（⛔ 不按行随机切）。
安慰剂三对照（同一学习器、同一折）：① **打乱标签**（按场整场打乱，3 个随机种子）；② **非学生席对照**（用席 1 的 `delta` 差当标签、特征不变）；③ **只用动作键不含状态**。
## 5 判据与止损（主判据 = OOS AUC）
报 **AUC + 95%CI**（**按场 cluster bootstrap 1000 次**）+ 准确率 + **幻想增益上界** `E[max(0,Δ)]` 点/小局（把预测器当神谕选动作）+ **交叉拟合可实现增益** `E[(2ŷ−1)Δ]` + `ρ(Δ, A0 自身该局得分)`（均值回归检查）。
止损：AUC 的 CI **含 0.5** ⇒ 判「**方向族封存**」；CI 排除 0.5 但交叉拟合增益 ≤ 0 或 ≤ 0.2×幻想上界 ⇒ 同样封存；只有 CI 排除 0.5 **且**增益 > 0 才给「下一步：对比式目标最小实现 + 预算」。
⚠ 单族牌山（1 个 seed）⇒ 结论**条件于该 seed 族**；⚠ 分岔点之后两臂各走自己的网 ⇒ 对照含**后继策略**混淆（登记为残余风险）。
## 6 成本
采集 2×600 场（8 线程，估 2×5–7 min）· 抽取+拟合 = CPU 分钟级 · 逐行前向（若用）走 `tools/v4-forward-gpu.py` · ⛔ 零训练 / 零闸门 / 零 PPO / 零 compact。
