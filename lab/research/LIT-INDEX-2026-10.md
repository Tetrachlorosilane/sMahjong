# LIT-INDEX-2026-10 — 立直麻将四人自对弈文献索引

- **检索日**：2026-10-20。**范围**：麻将 AI、不完全信息博弈、多人非零和自对弈、事件/序列建模、价值与校准、评估方法论、课程与数据配比。
- **验证口径**：arXiv 条目一律用 `export.arxiv.org/api/query?id_list=...` 回读标题（等价于打开 abs 页）；GitHub / JMLR / FDA 逐页实际抓取。**表中 62 条全部真实访问过、URL 可打开**。未找到的另列第 8 节，不编造编号。
- **主张来源**：摘要 + 页面简介，**不是全文精读**；一句话主张是我对本项目相关点的概括，不是作者原话。
- **可迁移性判据**：按本项目硬约束评——≤2M 参数、单决策 ≤1.70 ms、特权信息只进 critic、无人类对局数据、四人非零和。

## 0. 图例

| 记号 | 含义 |
| --- | --- |
| (a) | 让事件顺序/历史真正起作用的结构或目标 |
| (b) | 四人非零和不完全信息下可靠的价值/优势，且不泄漏特权进策略 |
| (c) | 自对弈样本效率 |
| (d) | 评估分辨率（方差缩减/配对/序贯/MDE/座位平衡） |
| (e) | 自对弈共适应/串通（喂牌式虚高顺位）的检测与预防 |
| 高 | 机制或判据可直接落到现有 Python/C++/Java 三端，且不改硬约束 |
| 中 | 思路可借，但需重写/改规则/加预算，收益不确定 |
| 低 | 只在特定设定成立，或工程代价超过收益 |
| 不可 | 与硬约束或已实测负结果直接冲突（本表不收；见第 12 节） |

---

## 1. 麻将 AI（12 条）

> 已含 Suphx / Mortal 作锚点；其余覆盖天凤牌谱、三麻、四川麻将、国际麻将、GPU 仿真与抽象。

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| Suphx | 论文 | https://arxiv.org/abs/2003.13590 | 全局奖励预测+oracle guiding，天凤超99.99%人类 | 高 — 训练期用特权引导、推理不用，正是 MV-2 的边界范式 | (b)(c) |
| 天凤CNN麻将玩家 | 论文 | https://arxiv.org/abs/1906.02146 | 天凤牌谱训CNN，切牌一致率70.4%，rating约1850 | 高 — 特征张量与BC基线可直接对照 | (c) |
| 麻将向听快速算法 | 论文 | https://arxiv.org/abs/2108.06832 | 用可见牌算向听，比基线快约100倍 | 高 — 可移植到C++候选生成与合法性剪枝 | (c) |
| 多人数麻将MDP抽象 | 论文 | https://arxiv.org/abs/1904.07491 | 多个MDP抽象建搜索树并推断状态价值 | 中 — 抽象可借，搜索受1.70ms硬约束 | (b) |
| CFR-p 二人麻将 | 论文 | https://arxiv.org/abs/2307.12087 | 按和牌策略分层抽象，把CFR用于二人麻将 | 低 — 二人零和全宽CFR，与四人非零和不匹配 | (b) |
| Meowjong 三麻 | 论文 | https://arxiv.org/abs/2202.12847 | 三麻五动作CNN，再用蒙特卡洛策略梯度自对弈 | 中 — 动作分解可借，三麻规则与四人不同 | (b)(c) |
| Mahjax | 论文 | https://arxiv.org/abs/2605.20577 | JAX全向量化立直麻将，8×A100达200万步/秒 | 高 — 采集吞吐直接决定(c)的样本效率上限 | (c) |
| 国际麻将规则适配 | 论文 | https://arxiv.org/abs/2601.08211 | 用AI自对弈量化先手优势，改补偿分替代轮座 | 高 — 座位/先手偏差的量化与平衡设计可直接借 | (d)(e) |
| Abstraction Agent | 论文 | https://arxiv.org/abs/2609.04303 | LLM零样本发现立直麻将抽象特征并聚类 | 中 — 抽象降方差，但依赖LLM且非在线 | (b) |
| RLCard | 工具 | https://arxiv.org/abs/1910.04376 | 含麻将等卡牌环境与基线算法的开源工具包 | 中 — 可作对照环境，规则非立直 | (c) |
| Mortal | 代码 | https://github.com/Equim-chan/Mortal | Rust+DRL的立直麻将AI，工程结构开源可读 | 高 — 推理实现与工程结构可逐层对照 | (b)(c) |
| 四川麻将RL实验 | 代码 | https://github.com/jaysonph/sichuan-mj-AI | 四川麻将RL智能体的实验仓库，仅3次提交 | 低 — 未成熟无评测，只能看状态编码 | (c) |

## 2. 不完全信息博弈（6 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| DeepStack | 论文 | https://arxiv.org/abs/1701.01724 | 连续再求解+自学直觉，二人无限注德州超人 | 中 — 价值网+再求解思想可借，二人零和不适用 | (b) |
| ReBeL | 论文 | https://arxiv.org/abs/2007.13544 | 公共信念状态+深度CFR，二人零和收敛到纳什 | 低 — 依赖二人零和与子博弈分解 | (b) |
| Deep CFR | 论文 | https://arxiv.org/abs/1811.00164 | 神经网络近似CFR，免手工抽象 | 低 — 全宽遍历在四人麻将不可行 | (b) |
| Student of Games | 论文 | https://arxiv.org/abs/2112.03178 | 搜索+自对弈+博弈论推理的统一框架 | 中 — 统一视角可读，搜索仍受时延限制 | (b) |
| PerfectDou | 论文 | https://arxiv.org/abs/2203.16406 | 完美信息蒸馏进策略，斗地主达SOTA | 中 — 机制违反"特权只进critic"，只作反例 | (b) |
| Informed Asymmetric Actor-Critic | 论文 | https://arxiv.org/abs/2509.26000 | 任意特权信号进critic均无偏，并给筛选检验 | 高 — 直接回答"哪种特权信号值得进critic" | (b) |

## 3. 多人非零和自对弈（9 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| PSRO | 论文 | https://arxiv.org/abs/1711.00832 | 对策略混合做元博弈，量化对对手过拟合 | 中 — joint-policy correlation 可作(e)指标；算法已排除 | (e)(b) |
| Emergent Complexity via Competition | 论文 | https://arxiv.org/abs/1710.03748 | 自对弈产生自然课程；对手过拟合见 PSRO 条 | 高 — co-adaptation 的早期红证 | (e) |
| LOLA | 论文 | https://arxiv.org/abs/1709.04326 | 把对手学习纳入梯度，重复囚徒困境出合作 | 中 — 可解释串通机制，但对手不可微 | (e) |
| Open-Ended Learning | 论文 | https://arxiv.org/abs/2107.12808 | 不断改任务分布，避免自对弈停滞 | 中 — 课程替代方案，工程量大 | (e)(c) |
| PBT | 论文 | https://arxiv.org/abs/1711.09846 | 种群并行+超参继承，自动发现训练日程 | 低 — 调参种群，不是对手种群 | (c) |
| 夺旗种群训练 | 论文 | https://arxiv.org/abs/1807.01281 | 两级种群自对弈，夺旗达人类水平 | 中 — 种群+联赛结构可借，成本高 | (c)(e) |
| 长程LLM串通 | 论文 | https://arxiv.org/abs/2609.24967 | 长程互动中94%轨迹出现串通，限历史可降 | 高 — 直接给串通检测与"限历史"干预 | (e) |
| Securing Equal Share | 论文 | https://arxiv.org/abs/2406.04201 | 四人对称常和博弈以均分为目标，元算法可失效 | 高 — 为非零和目标提供可证保证 | (b)(e) |
| DanZero | 论文 | https://arxiv.org/abs/2210.17087 | 四人掼蛋用分布式深度蒙特卡洛，训30天 | 中 — 四人长局样本效率的反面教材 | (c) |

## 4. 事件/序列建模（15 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| Attention Is All You Need | 论文 | https://arxiv.org/abs/1706.03762 | 纯注意力取代循环，长程依赖可并行 | 高 — 事件流编码基础，但二次复杂度 | (a) |
| Transformer-XL | 论文 | https://arxiv.org/abs/1901.02860 | 段级循环+相对位置，依赖长度超RNN 80% | 高 — 跨局/长事件流carry机制 | (a) |
| Longformer | 论文 | https://arxiv.org/abs/2004.05150 | 局部窗口+全局注意力，线性复杂度 | 中 — 60事件用不上，扩到整局才有意义 | (a) |
| S4 | 论文 | https://arxiv.org/abs/2111.00396 | 结构化状态空间，16k序列仍可建模 | 中 — 长序列替代，C++手写成本高 | (a) |
| Long Range Arena | 基准 | https://arxiv.org/abs/2011.04006 | 1K–16K长程基准，Transformer普遍吃力 | 中 — 选型参考，尺度与麻将事件不同 | (a) |
| Deep Sets | 论文 | https://arxiv.org/abs/1703.06114 | 置换不变网络，集合输入有理论刻画 | 高 — 34牌种/候选集编码的正当性 | (a) |
| Set Transformer | 论文 | https://arxiv.org/abs/1810.00825 | 注意力池化，置换不变且建模元素交互 | 高 — 候选打分与牌集合交互 | (a)(b) |
| RoPE | 论文 | https://arxiv.org/abs/2104.09864 | 旋转矩阵编码相对位置，长度可外推 | 中 — 事件位置编码可选 | (a) |
| ALiBi | 论文 | https://arxiv.org/abs/2108.12409 | 按距离线性惩罚注意力，短训长测可外推 | 中 — 简单，适合固定事件窗 | (a) |
| RWKV | 论文 | https://arxiv.org/abs/2305.13048 | 线性注意力可写成RNN，推理常数内存 | 中 — 推理友好，需另起训练栈 | (a) |
| Mamba | 论文 | https://arxiv.org/abs/2312.00752 | 输入相关选择的状态空间，线性时间百万长度 | 高 — 长事件流+低时延的候选骨干 | (a) |
| Decision Transformer | 论文 | https://arxiv.org/abs/2106.01345 | 把RL当条件序列建模，用回报+历史出动作 | 高 — 改目标让历史起作用，而非加宽 | (a) |
| Trajectory Transformer | 论文 | https://arxiv.org/abs/2106.02039 | 整条轨迹当token，beam search做规划 | 中 — 离线可用，在线毫秒级不现实 | (a) |
| LTSF-Linear | 论文 | https://arxiv.org/abs/2205.13504 | 线性模型全面胜Transformer，自注意力丢时序 | 高 — 反证：顺序敏感先排除位置编码假象 | (a) |
| FlashAttention-2 | 论文 | https://arxiv.org/abs/2307.08691 | IO感知精确注意力，A100达72% MFU | 中 — 降时延但需GPU，C++端不适用 | (a)(c) |

## 5. 价值与校准（7 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| C51 | 论文 | https://arxiv.org/abs/1707.06887 | 学回报分布而非均值，51原子稳定提升 | 高 — 与value81bin同构，可直接对照 | (b) |
| QR-DQN | 论文 | https://arxiv.org/abs/1710.10044 | 分位数回归学分布，优于C51 | 高 — 分位数头可替换81bin | (b) |
| IQN | 论文 | https://arxiv.org/abs/1806.06923 | 隐式分位数网络，可做风险敏感策略 | 中 — 风险敏感可与顺位目标结合 | (b) |
| HL-Gauss | 论文 | https://arxiv.org/abs/2403.03950 | 交叉熵分类训练价值头，可扩展更稳 | 高 — 正是81bin该用的损失 | (b) |
| 现代网络校准 | 论文 | https://arxiv.org/abs/1706.04599 | 深度网络普遍过自信，温度缩放最有效 | 中 — belief/危险头需校准，非策略头 | (b) |
| 校准回归 | 论文 | https://arxiv.org/abs/1807.00263 | 把回归输出变成校准区间，保证覆盖率 | 中 — value/placement区间可校准 | (b) |
| GAE | 论文 | https://arxiv.org/abs/1506.02438 | 指数加权优势，λ调偏差-方差 | 高 — PPO优势估计的标准口径 | (b) |

## 6. 评估方法论（7 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| rliable | 论文 | https://arxiv.org/abs/2108.13264 | 少种子下用IQM+分层bootstrap+性能剖面 | 高 — 直接替代均值±sd，提升分辨率 | (d) |
| Hitchhiker's Guide | 论文 | https://arxiv.org/abs/1904.06979 | 系统比较检验的假阳率与功效随种子数变化 | 高 — MDE/功效与检验选型的操作手册 | (d) |
| Always Valid Inference | 论文 | https://arxiv.org/abs/1512.04922 | 持续监控下的always-valid置信区间 | 高 — 允许序贯看结果而不膨胀α | (d) |
| 成组序贯检验 | 论文 | https://arxiv.org/abs/2506.15599 | 独立增量+α消耗，期中分析可提前停 | 高 — 序贯评估的边界计算 | (d) |
| Demšar 多数据集比较 | 论文 | https://www.jmlr.org/papers/v7/demsar06a.html | 多数据集比较用Wilcoxon/Friedman+CD图 | 高 — 配对/多族比较的非参框架 | (d) |
| CUPED | 论文 | https://arxiv.org/abs/2312.02935 | 用实验前协变量做方差缩减，可大幅降方差 | 高 — 配对种子/固定对手池即协变量 | (d) |
| FDA 自适应设计指南 | 指南 | https://www.fda.gov/regulatory-information/search-fda-guidance-documents/adaptive-design-clinical-trials-drugs-and-biologics-guidance-industry | 自适应/序贯试验的Ⅰ类错误与报告规范 | 中 — 提供监管级检查清单 | (d) |

## 7. 课程与数据配比（6 条）

| 名称 | 类型 | URL | 一句话主张（≤40字） | 可迁移性 | 对应问题 |
| --- | --- | --- | --- | --- | --- |
| Prioritized Experience Replay | 论文 | https://arxiv.org/abs/1511.05952 | 按TD误差优先采样，配重要性权重纠偏 | 高 — 稀有和牌/危险决策可优先 | (c) |
| 课程学习综述 | 综述 | https://arxiv.org/abs/2003.04960 | RL课程的任务排序框架与开放问题 | 中 — 给课程设计词汇，非现成配方 | (c) |
| DoReMi | 论文 | https://arxiv.org/abs/2305.10429 | 小代理模型+Group DRO学数据域配比 | 中 — 数据配比可借，域定义需自建 | (c) |
| Retrace(λ) | 论文 | https://arxiv.org/abs/1606.02647 | 安全利用任意行为策略的离策略回报估计 | 高 — 多代/陈旧数据的纠偏 | (c) |
| OpenAI Five | 论文 | https://arxiv.org/abs/1912.06680 | 10个月大规模自对弈，长时程持续训练 | 中 — 规模证据，成本不可复制 | (c)(e) |
| AlphaZero | 论文 | https://arxiv.org/abs/1712.01815 | 纯自对弈+MCTS，24小时超越人类 | 低 — 完美信息+搜索，与1.70ms冲突 | (c) |

---

## 8. 未验证 / 未找到（不计入 62 条，⛔ 不编造编号）

| 名称 | 我查了什么 | 结论 |
| --- | --- | --- |
| PTIE（博弈版） | arXiv `all:PTIE` 全文检索，仅命中 OCR 论文 2211.04963 | 未找到"特权信息进 critic"的 PTIE 博弈论文 ⇒ 未验证 |
| AlphaStar（Nature 2019） | arXiv 检索与记忆 ID 回读；1902.04742 实为"Uniform convergence..." | 无 arXiv 预印本可核 ⇒ 未验证 |
| Neural Fictitious Self-Play | 记忆 ID 1805.08522 回读，实为"parameter-function map"论文 | 正确 ID 未定位 ⇒ 未验证 |
| Algorithm Distillation | 记忆 ID 2110.15943 回读，实为 MetaICL | 正确 ID 未定位 ⇒ 未验证 |
| RetNet | 记忆 ID 2307.08691 回读，实为 FlashAttention-2 | 正确 ID 未定位 ⇒ 未验证 |
| Kanachan 论文 | arXiv `all:mahjong`（27 条）与 `all:riichi`（7 条）逐条看标题 | 未命中论文条目；仅有 GitHub 框架，故只作代码参考 |
| 四川麻将学术论文 | arXiv `all:"Sichuan mahjong"` 返回 0 条 | 仅有 GitHub 实验仓库，已按"低"收入第 1 节 |
| 成组序贯教科书（Jennison & Turnbull） | 无可公开打开的单页 URL | 未收录；用 2506.15599 替代 |

**被我的记忆骗过的 ID（全部作废，未写入表）**：1902.04742（≠AlphaStar）、1806.02315（≠IMPALA）、1506.03353（≠Retrace）、2307.08691（≠RetNet）、2110.15943（≠Algorithm Distillation）、1805.08522（≠NFSP）、2112.03235（≠Student of Games）。

---

## 9. 与本项目已知负结果的对照

- **(a) 顺序敏感性仅 1.4%**：Decision Transformer / Transformer-XL 是"改目标/改 carry"的路线；同时用 LTSF-Linear 的结论先排除"位置编码把顺序信息洗掉"的假象，再判断结构无效。
- **(b) 特权 critic 无增益**：不要再加宽/加深读出头；先按 2509.26000 的"依赖检验 / 价值预测增益"筛信号，并保持"基线尺度"口径。
- **(c) 样本效率**：Mahjax 的吞吐、PER 的优先采样、Retrace 的离策略纠偏，分别对应"产得快、采得准、用得旧"三件事。
- **(d) 评估分辨率是硬瓶颈**：CUPED（配对种子作协变量）+ rliable 的 IQM/分层 bootstrap + always-valid/成组序贯，三者可叠加；先算该 N 下的 MDE，再谈显著性。
- **(e) 喂牌式虚高**：2609.24967 的"限制历史"干预 + 2601.08211 的座位/先手平衡 + PSRO 的 joint-policy correlation，可组成"检测—归因—预防"三联。
- **零和口径的陷阱**：ρ(Δ, 基准) 恒为 −1 是设计恒等式，不是相关证据；先判可识别性，再从学生席口径读真 ρ——这与 rliable 批评"只看聚合点估计"是同一类错误。

---

## 10. 检索日志（可复核）

- 2026-10-20 用 arXiv API 批量回读标题：`id_list=2003.13590,...`（多批，含 62 条中全部 arXiv 条目）。
- 主题检索：`all:mahjong`（27 条总，逐条看标题，剔除麻将 Solitaire / 婚姻关系 / 触觉传感器等无关项）、`all:riichi`（7 条，多为作者名误命中）、`all:"Sichuan mahjong"`（0 条）、`all:PTIE`（1 条，OCR 论文）。
- 逐页打开核验：JMLR Demšar 页、FDA 自适应设计指南页、GitHub Mortal、GitHub sichuan-mj-AI、arXiv abs/2312.02935。
- 未做：全文精读、引文追溯、非英文数据库（万方/CNKI）检索；因此"四川麻将"只有代码级证据，没有学术论文级证据。

---

## 11. 最值得先读的 8 篇（按与 (a)–(e) 的相关性排序）

1. **Informed Asymmetric Actor-Critic**（2509.26000）— (b)。它不只说"特权可以进 critic"，还证明**任意**特权信号给无偏策略梯度，并给出**训练前/事后两种信息量检验**——正好接"特权 critic 实测无增益"之后该问的问题：不是再加宽，而是先筛信号。
2. **Deep RL at the Edge of the Statistical Precipice**（2108.13264）— (d)。少种子下的 IQM、分层 bootstrap 置信区间、性能剖面；把"sd≈55、判 2 点要 4.3k 场"从抱怨变成可报告的区间。
3. **CUPED**（2312.02935）— (d)。用实验前协变量做方差缩减；在本项目里"配对种子 / 固定对手池 / 同族牌山"就是现成协变量，且可与配对设计叠加。
4. **Decision Transformer**（2106.01345）— (a)。把策略改成"回报+历史"条件化的序列模型：**改目标**让历史必须被读，而不是把 GRU 换大。
5. **Securing Equal Share**（2406.04201）— (b)(e)。四人对称常和博弈里纳什/可利用度都不再是合适目标；它给"均分"的可证算法，并展示现有元算法在最坏情形失效。
6. **Emergent Collusion in Long-Horizon LLM Agent Interaction**（2609.24967）— (e)。量化串通出现率（94%），并用"限制互动历史"做干预；对"喂牌式虚高顺位"是可直接照搬的检测/缓解实验设计。
7. **A Hitchhiker's Guide to Statistical Comparisons of RL Algorithms**（1904.06979）— (d)。假阳率/功效随种子数与效应量怎么变、非正态下该用什么检验；是 MDE 与检验选型的操作手册。
8. **Mahjax**（2605.20577）— (c)。JAX 全向量化、8×A100 达 200 万步/秒；说明"样本效率"在工程上首先是吞吐与并行，而不是换损失函数。

---

## 12. 看着相关但不可迁移的 5 条

> 另：**LOLA（1709.04326）** 需要可微的对手更新；自对弈里对手是采样策略、不可微，且它会主动学出串通行为——只能当"串通机制"的解释，不能当训练配方（不计入 5 条）。

1. **AlphaZero（1712.01815）** — 完美信息 + MCTS。本项目四人非零和、单决策 1.70 ms，搜索预算不存在；项目也已排除搜索路线。可借的只有"自对弈+价值目标"的骨架，不是算法。
2. **DeepStack（1701.01724）/ ReBeL（2007.13544）** — 二人零和 + 子博弈再求解。四人非零和没有干净的"可再求解子博弈"边界，且每次决策都要在毫秒内完成；把 2p 的收敛保证搬到 4p 是范畴错误。
3. **PerfectDou（2203.16406）** — 把完美信息蒸馏进**策略**。这与"特权信息只进 critic、绝不进 policy/推理"的硬约束直接冲突；项目还已实测特权 critic 无可测增益，蒸馏路线更不该回头。
4. **PSRO（1711.00832）** — 四人非零和的元博弈规模、种群存储与评估成本都远超预算；且 4p 下均衡不唯一、不可利用度无保证。项目已明确排除 PSRO/QD，这里只留它的 **joint-policy correlation** 作诊断指标。
5. **CFR / Deep CFR（1811.00164）** — 全宽遍历在四人麻将状态空间不可行；抽象后仍难在毫秒级出招，且与现有在线 PPO 数据管线不兼容。

---

## 13. 独立复核记录（2026-10-20，S41，第三方复核）

> 口径：**只核"编号 ↔ 标题"与 URL 可达**，不重做主题检索、不重读全文。方法：arXiv
> `export.arxiv.org/api/query?id_list=...` 逐条回读 `<title>`；非 arXiv 用 `curl -sS --compressed -L`
> 取状态码与页面 `<title>`/`citation_title`。

**汇总**：总 **62** 条（**58** arXiv + **4** 网页：GitHub ×2 / JMLR ×1 / FDA ×1）· 通过 **62** ·
就地更正 **1**（表述越界，非编号错）· 降级为未验证 **0** · 死链 **0**。

**逐条失败清单**：**空**。62 条编号全部存在，且回读标题与表内名称一一对应（含 2601.08211 /
2605.20577 / 2609.04303 / 2609.24967 / 2509.26000 这 5 条新号，均能取到标题）。

**就地更正 1 条（非编号/标题错，是概括越界）**：
- `1710.03748` 原文"自对弈产生课程，但训久会过拟合对手"——该文摘要**只说自然课程**，
  "过拟合对手"是 PSRO（`1711.00832`）的 joint-policy correlation。已改为
  "自对弈产生自然课程；对手过拟合见 PSRO 条"。

**"工具假红"三处（已排除，非死链）**：`Invoke-WebRequest` 在这些站点不可信 ——
`wolframcloud.com` HEAD 返 **405**、`ojs.aaai.org` 直接连接错、`fda.gov` HEAD 返 **404**；
三者在 `curl --compressed` GET 下均 **200**，且页面标题可读（AAAI 8674 = "Automating Collusion
Detection in Sequential Games"，作者含 Mazrooei；FDA 页面被反爬页替换，标题经检索确认为
"Adaptive Design Clinical Trials for Drugs and Biologics Guidance for Industry"，2019-12）。

**第 8 节的"被记忆骗过的 ID"全部复核**（这些 ID 真的指向别的论文，原记录无误）：
`1902.04742` = Uniform convergence… · `1806.02315` = Randomized Value Functions via MNF ·
`1506.03353` = Power Allocation for Multicell Massive MIMO · `2307.08691` = FlashAttention-2 ·
`2110.15943` = MetaICL · `1805.08522` = parameter-function map · `2112.03235` = Simulation
Intelligence · `2211.04963` = Pure Transformer with Integrated Experts（OCR）。

**一句话主张抽验**（对摘要核，非全文）：Suphx 的 99.99%、1906.02146 的 70.44%/1850、
2108.06832 的 100×、Mahjax 的 2M steps/s on 8×A100、2609.24967 的 94%、2601.08211 的
补偿分、2509.26000 的"任意特权信号无偏"、2406.04201 的"元算法最坏情形失效" —— **均与摘要一致**。

