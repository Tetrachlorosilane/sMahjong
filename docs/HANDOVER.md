# 会话交接（HANDOVER）—— 2026-10-07

> **这份文件是"会话状态转移"**：新会话/新同事从它接上，不必重读整段对话。
> 它**只记状态与下一步**（判据与规范在 `AGENTS.md` / `docs/TRAINING-V4.md` / `docs/FEATURES-V4.md`），
> 每次交接**整段重写**（不要追加成历史堆）。索引：`docs/INDEX.md`。
> ⚠ **§1 的仓库事实不要手抄**：跑 `node tools/handover-facts.mjs` 取那两行，
> 并用 `node tools/handover-facts.mjs --check` 验它还成立（手抄的 sha 抄完就烂 —— 2026-10 实测过一次，见 §1）。

---

## 1. 十秒速览

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `Training` · 基线 **`2e524c9`**（`亲自核查文档口径：补两处遗漏 —— §11 适用范围段引用 +358 未带跨批次`）；其父 `7daa979`。**当前 HEAD = `7cbdc30`**（tree `57aa30d5…`，把整个工作区一次性提交，见下"远端"行），工作区**已清空**（0 未提交）。⚠ 本行第一句记的是**基线**（§1 改动建立在它之上）：判据是"它仍是 HEAD 的祖先"，不是"它 == HEAD" |
| 远端 | **本地 ref** `origin/Training` 头 = **`c026e89`**（2026-10-02）⇒ 按本地 ref 算落后 47 个提交。⚠ **远端真实头已前进到 `ea59e353`**（2026-10-08，REST 推送，API 回读；本地 ref 无法更新是因为 **`git fetch` 也走不通**）。⚠⚠ **但远端 tree ≠ 本地 HEAD tree**：本轮**只推成功 27/66 个文件**（`client/**` 13 + `docs/**` 12 + `AGENTS.md`/`README.md`），其余**未推**（清单以 §5 第 1 条为准；⚠ 本文件自身的记实修正也会计入未推数 ⇒ 别在此追一个精确计数）—— 写通道对 github.com 返回 **400 malformed**（手工 `POST /git/blobs` 同样 400）与 **ECONNRESET**。⛔ **别把这里读成"已同步"**；补推清单、配方与判据见 §5 第 1 条 |
| 发布 | **v1.13.0 已上线**（release **#397762262** → <https://github.com/Tetrachlorosilane/sMahjong/releases/tag/v1.13.0>，tag `v1.13.0` → 远端 `4d7cb9ec`，6 个资产）：**第一次随包发布 v4 的 3 代网络**（`v4-bc-004` / `v4-p3-001` / `v4-ppo-001`，格式 2 权重）+ 逐决策 reward-to-go 契约。上一版 v1.12.0（#397670321）只带 client + server（那时 v4 还不能导出）。逐资产 sha256 / asset id 见 `NOTES.md` §9.5 |
| 数据盘 | `S:\mahjong-training` ≈ **112 GB**（实测：gate 63.9 / compact 21 / raw 13.7 / arena 12.9 / ckpt 1.1，2026-10-07 读数）；S 盘可用 ≈ **117 GB**。⚠ 旧稿写的"17 GB / 212 GB 可用"是 2026-09 的读数，别再引 |
| 训练进度 | **v3 谱系已到平台**；v4：teacher 预训练 0.608 → 0.619 → **0.903**（§2.12）；P3 开局 RWR（§2.14，−1.96 / −3.08 证不出）；**PPO 一轮**（§2.15：2,000 场 2+2 **−3.54 [−5.84,−1.24]** ⇒ 略低于 teacher）；**第六轮**（§2.16）修掉两处 NaN + 降到 `#0.5` + 逐决策 `rtg` 优势 ⇒ **−1.06 [−3.34,+1.29] / vs 起点 +0.39 [−1.93,+2.72]** ⇒ **退步消掉、但只是打平**。⚠ 全部为**当时口径**的读数，见 §1.1 的标注纪律 |
| v4 状态 | **P0 收口** + **P5 前向三端落地** + **§7.5 审计已做** + **PPO 回路已落地且数值上稳了**（§2.15/§2.16，四道闸门）+ **增量事件缓存已上（L2，§2.17）** + **价值头审计已做（§2.18）** + **critic 已有结论（§2.19）** + **训练端脱离 Java（§2.20：C++ 补 `--aux`，`v4 loop` 编排）**。细节见 §2 对应小节 |
| **下一步（唯一清单）** | **训练侧**：① 判据登记表投入使用 + 两件最便宜的算力效率（`EXPERT-PLAN` §12）；② 唯一有机理证据的方向 = **优势源**（§1.1 末条）；③ **产品侧**：把"可选对手的风格多样性"当产品任务 + §15.8 盲测流程（`EXPERT-PLAN` §15–§16）。**工程/文档侧**：A/B/C 三组**已全部完成**（§1.2，以及 2026-10-07/08 这轮的三端头表统一、NOTES 轮次索引、HANDOVER 日志搬迁、训练文档口径按代码校正）。⚠ **"下一步"在本文件里只此一处** —— 其它节若再出现"先做哪件 / 优先级改口"，一律改成指向本行的指针 |
| 并行工作 | ⚠ **有另一个会话在同一仓库工作**（见 §5 第 1 条：推送要串行 + 比 tree sha）；本次推送前其改动已在基线里（§2.11） |

### 1.1 训练路线现状（2026-10-06）

**只记状态，不搬细节** —— 完整结论与规划见 `docs/EXPERT-PLAN.md` §11–§13，方法/口径见 `docs/TRAINING-V4.md` §14–§16。

> ⚠ **本节所有强弱类结论都是「筛查级、未定论」**（2026-10 标注）：它们的场次池 ≤24,000，而 §1.1 自己那条
> **判据升级**（本页下方 ★ 条）已经把门槛改成"**≥24,000 场池化 + 超出噪声底 + 事先登记**" ——
> 也就是说**下面这些数字没有一条满足现行门槛**，只能当"当时的筛查读数"，⛔ 不许拿它们当"已验证的强弱"，
> 也不许当"这些模型没价值/可删"的证据（见本页末条与 `EXPERT-PLAN` §11/§13–§16 的适用范围段）。

- **现任 = `v4-league-g08`（九季未变）**：15 个代表候选在同一批 8 套牌山上**全部 ≤ 它**；三条风格线跑完也没有后继被证明更强。
- **三条线终点（一律 vs `g08`，多套新牌山逐场配对）**：
  - 打点线（种子 `14-g05`）：**+0.00 [−0.96,+1.00] n=12,000** —— 打点确实 +358（⚠ **跨批次拼合 —— 不可与修正后数字并排**，见 `docs/EXPERT-PLAN.md` §15.0 的口径账），顺位点不动。
  - 防守线：**曾**合并出 `+0.867 [+0.188,+1.560] n=24,000`（唯一一次 CI 排除 0），**同配方独立复现 −3.54 [−4.50,−2.58] n=12,000（8/8 为负）⇒ 撤回**。
  - 和率线（便宜手对手池）：**−1.12 [−2.09,−0.17] n=12,000**；方向测试 +0.30 [−0.67,+1.27] n=12,000。
  - ⇒ 结论：**"单线单独练 10 代就稳定变强"不成立**（⚠ 不是"多专家路线不成立"）。
- **阶段 4（风格抉择）已判定：门控不值得训**。四方同桌 4,000 场；oracle 抉择器**诚实上界 +0.327 顺位点/场**（留一牌山），
  相对**桶内标签置换零分布 z=+0.65**（零分布 sd **1.008 顺位点/场**）；**门控本身增量 −0.166**（只挑全局最强 `atk2` 反而 +0.493）；
  22 桶仅 1 桶通过而**纯噪声平均 0.6 桶**。⇒ 三条线 + 阶段 4 都已收口，议题转**架构与算力效率**（`EXPERT-PLAN` §12）。
- **★ 一条判据升级（强制，往后所有候选都适用）**：门槛从"**CI 排除 0**"改成"**闸门 + ≥24,000 场池化 + 超出噪声底 + 事先登记**"。
  理由：12,000 场 CI 半宽 ±0.96 与**链间散布 ≥4 点**同量级；而阶段 4 的估计量噪声底本身就有 **1.008 顺位点/场** ⇒
  "CI 排除 0"**不等于**"不是噪声"（`EXPERT-PLAN` §12.5；含"两条噪声源不通用"的警告）。
- **下一步**：**只看 §1 的「下一步（唯一清单）」**（本节原先那两条"先做哪件 / 优先级改口"已合并过去）。
- ⛔ **不许重做**：`NOTES.md §6.5 第六十六轮` 的 12 条否证；门控小模型（阶段 4 已判）；缩 `dModel`。
- **★ 评价体系补记（2026-10-06，`docs/EXPERT-PLAN.md` §15–§16）**：**强度结论已收口**（`g08` 未被超越；三条线终点与阶段 4 的门控判定**只走顺位点族口径** —— 闸门 + 多套新牌山配对 + 池化 + 置换零分布）；
  ⚠ 但**玩家体验维度尚未评价**：§15 的八轴里 **真人主观评分（§15.8）与难度梯度（§15.6，人类胜率/放铳率）是两块空白**，拟人度（§15.3）缺**真人牌谱对照语料**，风格可分性（§15.2）缺工具。
  ⇒ **"闸门没测出差异" ≠ "没有价值"**：`g08` 之外的网络（`atk2-g09` / `def3-g08` / `win-g10` / `10-g01`）是**风格候选**，出货与否由 §15 的判据与 §16 的候选清单（+ `docs/BOT-AI.md` 的挂包机制）决定，**不许**拿 §11/§14 当"这些模型没价值/可以删"的证据。
  **下一步**：**只看 §1 的「下一步（唯一清单）」**（"把风格多样性当产品任务"这一条已并进去）。
  **★ 画像补实（2026-10-06，同日）**：`def3-g08` / `win-g10` 的"画像空白"已用**新采的同牌山同桌数据**填实（A 桌 8×500 = 4,000 场、B 桌 8×400 = 3,200 场，五个网络分两张桌，修正后口径，报表在 `S:\mahjong-training\reports\stage5{a,b}-profile.{txt,json}`，复算命令与两桌纪律见 §16.1）。
  两条由此而来的**标签修正**：① `atk2-g09` 的"高打点"**第一次被配对证实**（平均和点 **+262.475 [+118.088,+407.368]**，p=0.002，该表唯一显著轴）；② **`def3-g08` 的"防守型"标签被画像否定**（铳率 16.57% vs 现任 16.43%，不低）⇒ 改称"**稳健·自摸连庄型**"（自摸率/一位率/连庄率三项该桌最高）。⚠ **同一代换一张桌强弱读数会翻**（`atk2` 在 B 桌 `rank_points +6.594`）⇒ 画像表的数字**只能当风格描述，不是强弱判据**。
- ⚠ **归并后的读法**：`EXPERT-PLAN` §0–§10 是 2026-10-04 的**计划原文**（数字一个没改，只在口径上标了"已跑完/已判定"），
  要看**现在的结论**请直接读 §11–§14；**要看"这些模型给了玩家什么"读 §15–§16**。

### 1.2 本会话的非训练工程项（2026-10-07）

- **A 组 8/8 完成**（规则/协议侧，全部有判据）：`aka` 归一化（0/3 + WARN）· `round_end.next` ·
  **延长战实装**（《天凤》《雀魂》开、东→南入、西场到点即止）· **吃也能选赤五** · `state.turn` 与
  `drawn_seat` 分离 · **抢杠见逃**（询问 + 同巡振听 + 杠照常成立 + 多家走 `head_bump`）·
  **`pid`/`token` 重连**（凭据保留到 TTL，光有 uuid 接不回座位）· **客户端收包超时**（60 s 静默 ⇒ 明确提示 + 重连）。
- **B 组 7/7 完成**（文档/口径纠错）：档位单调性、跨批次拼合禁令、`aka` 0/3、`tiles_left` 69/68、
  `room.host` = pid、删除幽灵字段 `ura_revealed`、`docs/INDEX.md` 去重与补登。
- **C 组**：`C7`（本文件 §1 重写 + `tools/handover-facts.mjs`）本轮完成；`C8` / `C10` 见"下一步（唯一清单）"。
- **验证状态**：L1 **1507/0** · L2 **899/0** · `e2e`/`claim-priority`/`spectate`/`uuid`/`away` 全 PASS ·
  三套 `trainer-*` 对拍全 PASS（含 3 场 × 8 局逐字节一致）。⚠ 具体数字以 `AGENTS.md` §4 为准。

---

## 2. 本会话已完成（可判据的）

> 本节（§2.1–§2.40）**已整体搬进 `NOTES.md` §13**（原文一字未改）：`NOTES.md` §13.1 = 原 §2.1 … `NOTES.md` §13.40 = 原 §2.40（⚠ 原 §2.13 在 HANDOVER 里本就缺号，且原 §2.14 起接着 §3 快照）；逐节映射表见 `NOTES.md` §13 开头。这里只留指针，避免两份"已完成"清单互相矛盾。

---

## 3. 下一步：**性能（增量缓存）→ 再 P1 收尾 → 放量**

> ⚠ **本节是 2026-10-04 的历史规划快照，不是当前下一步**；当前**唯一**下一步见 §1 的「下一步（唯一清单）」。
> 保留原文是为了让"当时为什么这么排"可查；⚠ 其中关于 P0/能力缺口的现状描述可能已被后续会话推翻
> （例如 `aux.npz` 一行 —— 以夹具与代码为准，见 §3 末的复核注）。

> 优先级变了：P5 之前"checkpoint 只能离线评估"，现在**能上线了但太慢** —— 40 ms/决策的网
> 当 bot 可以、当自对弈采集主力不行。所以第一件事是把前向压快（**没有时间指标，见 §2.17 与
> `FEATURES-V4.md` §8**）。

**清单在 `docs/TRAINING-V4.md` §「P0 剩余工作」**（六步**全部 ✅**）+ **§9 P1 设计**。摘要：

| # | 端 | 文件 | 状态 |
| --- | --- | --- | --- |
| 1~3 | Java + 契约 | `Round` / `Observation` / `PROTOCOL` §8.2 · `selfplay-check` | ✅ |
| 4 | Java | `ai/ObsFeatures.java`（sidecar v3 四段） | ✅ |
| 5 | C++ | `event.hpp` / `round.*` / `observation.hpp` / `danger.hpp` / `obffeatures.*` / `features.cpp` | ✅ |
| 6 | Python | `v4/traces.py`（数据集层硬拒绝） | ✅ |
| 7 | Java+Python | 标签侧 `aux.npz`（§2.8）+ `v4 plan` 里原有的那一条 ⚠️ | ✅ |

**开工前要定的两条**（都在 §6 待决问题里）：
① `events` 的**累积 vs 增量**口径 —— 现在两侧都按"累积"实现了，改就要两侧一起改；
② 一轮的算力/磁盘预算 —— sidecar 实测 **518.5 B/决策**（6M 决策 ≈ 3.1 GB）+ 轨迹 ×2.4~2.8。

**P0 没有剩下的了**（`v4 plan` 12/13：唯一没绿的 ⛔ 是 P1/P2/P3 三个阶段本身）。
唯一的**已知能力缺口**：~~C++ 生产者不产 `aux.npz`（训练端 `--aux` 显式报错）~~
**⚠ 这句已被复核推翻（2026-10-07）**：C++ 侧**已产** `aux.npz`。判据是**夹具**、不是文档投票 ——
`node tools/trainer-aux-parity.mjs 5 4 teacher 20260101 --rotate --selfcheck` **PASS**：
5 场 × 4 小局逐字节一致（28218 / 37033 / 24737 / 22070 / 31350 B），且 `--selfcheck` 的负向对照也过
（同一对报"一致"、翻一个字节即报"不同"并定位到成员 `g0.aux.npz`）。
（另注：本节上方"唯一没绿的 ⛔ 是 P1/P2/P3 三个阶段本身"与 `aux` **无关**，两处原本就不矛盾。）

**这一轮要做的事**（**顺序已按 `docs/TRAINING-V4.md` §14 的重新规划改过**）：

⓪ **先量**（P0，Python-only）：跑**消融矩阵**（逐头/逐块关掉 + 2+2 配对 CI，回答"哪个头值钱"）
   + 值头**温度缩放**（一个标量，把覆盖率拉回 ≤3pp）。规划见 §14，代码事实见 `NOTES.md` §6.5 第十六轮。
   ✅ **已落地并跑过一轮**（§2.22 / `TRAINING-V4.md` §14.6）：消融表 + 校准都出来了；
   ⏳ 还差的：`--repeats ≥ 3` 才能给辅助头下结论；温度要升级成 loc-scale 两参数。
① **换回报与信用分配**（P1，Python-only，证据最强）：奖励 = **小局收支 + 顺位点**（对齐评测口径），
   **手级 GAE**（`(game,hand_no,seat)` 构造 `next_index`，λ<1 + bootstrap），`V = 已滚入 + 残差头`。
   判据：`value-audit --strict`（⚠ 原文写 "EV ≥ 0.4 + 覆盖率 ≤3pp" —— 第十九轮证明**合法天花板只有
   0.069**、谁都过不去 0.4；已改成 `EV ≥ 0.7 × 合法天花板` + `std(A_raw)/std(目标) < 1` + 覆盖率 ≤3pp）。
   ✅ **机制已全部落地**（`--advantage gae-hand` + 冻结优势、`rank_points` 列 + `--backfill-rank`）；
   ⚠ **实测未过门槛**（EV 0.094；`std(A)` 还涨了 24%）⇒ **下一步是"把 critic 的时间尺度压到一小局"**，
   而不是继续调 λ。
   ✅ **P1b 也做了**（§2.23）：小局口径 `EV(delta) = 0.055`、`std(A)/std(delta) = 2.671×`（量纲事故，
   第十九轮修成 0.998×）、冻主干只训值头更是**过拟合**（val CE 高于边缘）。
   ❌ **第十九轮的结论把这一条整段推翻**（§2.24）：信息**不在**（合法天花板 0.0693，网络已拿 80%），
   **读出头不是瓶颈 ⇒ P4-lite 取消**。`delta`/`rtg` 这类实现值目标上 critic 最多买 ~5% 方差削减。
② **补 PPO 口径**（P2，Python-only）：优势**冻结**（现在每步重算 = 移动基线）、`grad-clip`、KL 早停、
   全局优势标准化、`ppo` 时禁 RWR、双口径 KL。
③ **多头分层与危险头稠密化**（P3）→ **三端架构**（P4：接 `h_evt`、专用 state token、候选 Q 头）→
   **小步多轮在线**（P5，用 `v4 loop --no-java`）。
④ **放量 + 算法升级**（`#0.5` 与 `rtg` 已在 §2.16 落地并判过：两处修**消掉了退步但只到打平**）——
   按现有 `sd(Δ)≈53`，要证明"比 teacher 强 2 顺位点"需要 **~5,400 场 2+2**
   （一次评测 ~2 小时；v4 前向 22.5 ms/决策是瓶颈 ⇒ 与 ⑤ 绑在一起）。可选方向：
   （a）**放量到 5,000+ 场**（只买精度，不买强度）；
   （b）**在线多轮**（每轮采→训→换行为策略）——v3 上唯一打出过 CI 排除 0 的就是在线那一套
   （`ppo-v3-g01-g05` +2.66 [+0.14,+5.18]），v4 缺的是"多轮"；粒度按 ① 的结论走小局；
   （c）**P4 的对手池/联赛**（v4 目前只有 teacher 对练；v3 的自对抗续训虽无增益，但对手多样性没试过）；
⑤ **性能：把 v4 前向压快**（**不是验收项**，耗时指标已删）——**增量事件缓存（L2）已上**（§2.17：
   38.12 → 22.50 ms/决策 = 1.70×），**剩下 ~18.9 ms 全是不可复用的稠密计算**，按性价比：
   ① **逐候选/逐头并行**（同一决策里各头/各候选彼此独立，是一眼可见的并行度）；
   ② **L1 张量级缓存**（`tileMatrix` 的派生量，特征 3.55 ms 的大头）；
   ③ **C++ 侧镜像同一套缓存**（自对弈采集在 C++，训练/评测的墙钟收益比服务端更大）；
   ④ int8 量化；⑤ 必要时缩 `dModel`（要重训）。
   ⚠ **"去分配"不是大头**（实测 GC 只占墙钟 **0.03%**，`NOTES.md` §6.5 第十一轮）——
   扁平 `float[]` + 行偏移 + scratch 复用只算 ①④ 的地基，别把收益挂在这上面；
   **谁都不承诺到达某个数字**（耗时指标已从文档删除）。
⑥ **更难的自监督**（掩码事件类型一致率 0.968 ≈ 白给，**已按结论关掉**）：
   若要再用，得换"连 `tile_kind` 一起重建"或"掩码整段窗口"，否则把权重让给 policy。
⑦ **放量**：P3 的 6M 决策规模下，数据集构建（现 8 进程 ≈3,500 决策/s）与磁盘（v4 数据集 ≈22 KB/决策）
   都要再核一遍预算；

**三条纪律（不许省）**：
① **两侧的 obs/sidecar 都到 v3 了**，切 `MAHJONG_PRODUCER=cpp` 只影响速度 ——
但切之前先把 §6 的第 1 条（`events` 累积 vs 增量）定了；
② **老轨迹/老 sidecar 一律硬拒绝**（obs v2 在 `v4/traces.iter_decisions` 报错、`derived_version != 3`
在 `dataset.load_sidecar` 报错）——重采 / 重跑 `--features` 即可；
③ 每步都跑对应层自检（L1 / `selfplay-check` / `trainer-*-parity` / `v4 check` / `selfcheck`），**不许只看编译过**。

**执行记录（每轮都收在"全绿"上）**：
- **第 1 轮 ✅**：Java 权威实现 + 契约（`SelfTest.obsEventStreamTests` + `selfplay-check` 独立重建牌河）。
- **第 2 轮 ✅**：C++ obs v3 镜像 + 三套 parity（200 场完整半庄逐字节 200/200）。
- **第 3 轮 ✅**：sidecar v3（Java+C++）+ Python 数据集层硬闸门。
- **第 4 轮 ✅**：标签侧 `aux.npz`（§2.8，P0 收口）。
- **第 5 轮 ✅**：第一轮 teacher 预训练（§2.9，`v4/dataset.py` + `v4/pretrain.py`）。
- **第 6 轮 ✅**：分阶段训练（辅助头收敛）+ 掩码事件重建 + 数据集并行化（§2.10）。
- **第 7 轮 ✅**：提交 + 发布 **v1.12.0**（§2.11：远端 tree == 本地 tree 为判据，2 个资产 digest 逐一核对）。
- **第 8 轮 ✅**：**P5 三端 v4 前向**（§2.12）+ 修掉两个静默 bug（`cand` 派生段全 0 / 阶段 c 塌）+ 教师一致率 0.903。
- **第 9 轮 ✅**（收在"**结论明确**"上，不是全绿）：§7.5 均衡审计首次执行（§2.14）+ P3 开局一轮 RWR ——
  两条 2+2 配对**都证不出差别**（这本身是结论：上一代已在 teacher 水平，200 场 RWR 推不动）。
- **第 10 轮 ✅**：P3 加强版 —— 1,000 场自对弈（689,693 决策）+ **PPO 落地**（§2.15）；
  学生行 CE 0.861 → 0.601、KL 0.069 → 0.021（全程在信任域内）；判决 +0.66 / +1.47（**方向翻正、仍证不出**）。
- **第 11 轮 ✅**：发布 **v1.13.0**（v4 的 3 代网络随包 + 逐决策 reward-to-go 三端落地，§2.16）；
  PPO 两处 NaN 修掉 + 四道闸门 + 重训 **`v4-ppo-002`** ⇒ **2,000 场 2+2：vs teacher −1.06 [−3.34,+1.29]
  （第五轮是 −3.54 [−5.84,−1.24]）** ⇒ **退步消掉、但仍只是打平**。
- **第 12 轮 ✅**：**v4 增量事件缓存落地（L2）**（§2.17）：38.12 → **22.50 ms/决策（1.70×）**，
  三档深度七头逐位相同、命中 677/721、陈旧 0；判据 = `v4CacheTests`（1443/0）+ 
  `tools/v4-cache-check.mjs`（同种子开/关缓存 ⇒ 轨迹逐字节相同，红证已做）。
- **第 13 轮 ✅**：**价值头审计**（§2.18）—— §8.2 的分布判据落成工具 `v4 value-audit`：
  整场口径的值头**弱但真**（EV 0.44–0.57、ρ 0.66–0.76）但**覆盖率不达标**（95% 只覆盖 82–87%）；
  ⛔ **`rtg` 口径（`v4-ppo-002`）的值头是塌的**（EV 0.006、CE 只剩 0.019 nats 高于边缘基线）
  ⇒ PPO 的 critic 没在工作。自检 +16 项（含两条红证，抓出了 `explained_variance` 的一个实现 bug）。
- **第 14 轮 ✅**：**修 critic**（§2.19）—— `--freeze-trunk` / `--only-heads` 落地（自检成对判据）后实测：
  **整场口径 EV 0.4115 ✅ 达标；`rtg` 口径 0.002 ⛔ 判死**（放开主干也只到 0.010、还打坏策略头）；
  参照测量（`--ceiling`）说**可学的粒度是"这一小局"（0.632）而不是"剩下的整场"（0.090）**
  ⇒ **PPO 路线改为小局级（RWR/AWR）**，critic 要做就先过闸门（⚠ 当时的 "EV ≥ 0.4" 已废，见 §2.24）。
  ⚠ 第十九轮补注：0.632 与 0.090 **两栏都含结局列/隐藏真值**，合法列是 0.069 vs 0.014 ——
  **"小局 ≫ 整场"这个对比结论不变**，但绝对数不要再引用。

---

## 4. 环境与命令备忘（本机实测）

```powershell
# Python 侧（⚠ 系统 `python` 没有 torch，必须用 venv）
cd C:\Users\HP\source\games\mahjong\python
.venv\Scripts\python.exe selfcheck.py              # ← 必须在 python\ 下跑（子进程按 cwd 找模块）
.venv\Scripts\python.exe -m mahjong_ml.v4 check    # v4 训练准备体检
.venv\Scripts\python.exe -m mahjong_ml.v4 plan     # P0 状态表（当前 12/13 绿）
.venv\Scripts\python.exe -m mahjong_ml.features    # 权威特征规格（v3: state 615 / cand 96）

# 服务端 / 客户端
pwsh -File server\build.ps1 ; java -jar server\build\mahjong-server.jar --selftest   # L1（当前 1429 项）
pwsh -File client\build.ps1 -Deploy                                                  # 发布前必做
node tools\doc-refs-check.mjs                                                        # 文档自检（含索引完整性）

# 轨迹（自对弈 → 校验；obs v3 起自检里会核事件流；--aux 另落标签侧 g*.aux.npz）
java -jar server\build\mahjong-server.jar --selfplay 2 --workers 1 --policy teacher,teacher,teacher,teacher --seed 7 --aux --out .tmp-trace
node tools\selfplay-check.mjs .tmp-trace                                             # DATASET PASS（v2/v3 都收，按 obs.v 分档）
cd python
.venv\Scripts\python.exe -m mahjong_ml.auxlabels check ..\.tmp-trace                       # 标签侧：版本/行数/obs 版本对齐
.venv\Scripts\python.exe -m mahjong_ml.dataset build ..\.tmp-trace ..\.tmp-compact --aux   # 6 个 aux_* 标签列

# v4 训练回路（P1/P2：轨迹 → 四张量 → teacher 预训练；§2.9）
.venv\Scripts\python.exe -m mahjong_ml.v4.dataset S:\mahjong-training\raw\v4-bc-001 S:\mahjong-training\compact\v4-bc-001 --aux
.venv\Scripts\python.exe -m mahjong_ml.v4.pretrain --data S:\mahjong-training\compact\v4-bc-001 --label v4-bc-001 --epochs 6

# P5：v4 权重导出（net.bin **格式 2**）+ 三端对拍 + 性能（§2.12 / §2.17）
.venv\Scripts\python.exe -m mahjong_ml.v4.export weights --ckpt S:\mahjong-training\ckpt\v4-bc-004\model.pt --out ..\tools\build\v4-bc-004\net.bin
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --bench tools\build\v4-p3-001\net.bin S:\mahjong-training\raw\v4-sp-004\g0.jsonl 400   # 特征 / 前向分开计时
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --cache tools\build\v4-p3-001\net.bin S:\mahjong-training\raw\v4-sp-004\g0.jsonl 733   # 三档缓存 + 逐位对拍
node tools\v4-cache-check.mjs tools\build\v4-p3-001\net.bin 2 4 424242               # 同种子开/关缓存 ⇒ 轨迹逐字节（需先 build 服务端）
.venv\Scripts\python.exe -m mahjong_ml.v4.export golden --trace S:\mahjong-training\raw\v4-bc-002 --out tests\golden\forward-v4.bin --cases 10   # 覆盖闸门要求 ippatsu 等标签
node tools\trainer-v4-parity.mjs --golden                              # Java 与 C++ **各自**与 Python 夹具比（特征+前向+红证）
node tools\trainer-v4-parity.mjs --selfcheck                           # 比较器负向对照（扰动必须 FAIL）
node tools\trainer-v4-parity.mjs tools\build\v4-bc-004\net.bin .tmp-v4fix   # Java↔C++ 逐行（实测 1,885 条 maxΔ=0）
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --bench tools\build\v4-bc-004\net.bin .tmp-v4fix\g0.jsonl 200

# 第六轮：逐决策 reward-to-go（λ=1 的 GAE 目标；§2.16）—— 一轮 PPO 的完整回路
java -jar server\build\mahjong-server.jar --selfplay 1000 --workers 24 --aux --seed 20260928 --out S:\mahjong-training\raw\v4-sp-004 --policy "teacher,net:tools\build\v4-p3-001\net.bin@0#0.5,teacher,net:tools\build\v4-p3-001\net.bin@0#0.5"
node tools\selfplay-check.mjs S:\mahjong-training\raw\v4-sp-004       # 含 reward_to_go 的独立重算
.venv\Scripts\python.exe -m mahjong_ml.v4 balance --dir S:\mahjong-training\raw\v4-sp-004 --expect "teacher=2,net:...=2" --equal-shares
java -jar server\build\mahjong-server.jar --features S:\mahjong-training\raw\v4-sp-004 --workers 24.venv\Scripts\python.exe -m mahjong_ml.v4.dataset S:\mahjong-training\raw\v4-sp-004 S:\mahjong-training\compact\v4-sp-004 --aux --student "net:tools\build\v4-p3-001\net.bin@0#0.5"
.venv\Scripts\python.exe -m mahjong_ml.v4.pretrain --data S:\mahjong-training\compact\v4-sp-004 --label v4-ppo-002 --objective ppo --value-target rtg --behaviour ..\tools\build\v4-p3-001\net.bin --behaviour-temp 0.5 --init S:\mahjong-training\ckpt\v4-p3-001\model.pt --epochs 4

# 训练端 C++（改了 trainer/ 之后必跑；判据见 §2.6/§2.7 与 docs\TRAINER-CPP.md §6.20/§6.21）
pwsh -File trainer\build.ps1                                                          # 增量编译 + 编后自检
node tools\trainer-selfplay-parity.mjs 1 1 pass 20260101                               # 轨迹逐字节（秒级）
node tools\trainer-opts-parity.mjs 4 first,pass,random 2                               # 询问内容逐字符
node tools\trainer-features-parity.mjs <轨迹目录> 4                                    # sidecar 逐字节（sidecar v3 七段）
node tools\selfplay-check.mjs trainer\build\sp-<tag>-cpp                               # C++ 产出过独立校验器（语义腿）
# 完整半庄验收（≥200 场；约 10 分钟）：$env:SP_TAG='big' 可固定产物目录名
node tools\trainer-selfplay-parity.mjs 200 0 teacher,first,random,pass 20260101 --rotate --cpp-workers 8

# 训练（v4 采集在 C++ 到位前必须用 java 生产者）
$env:MAHJONG_PRODUCER='java'                       # 本会话实测：cpp 用于 v3 采集（35 场/s）
.venv\Scripts\python.exe -m mahjong_ml.online run --init <ckpt> --label <label> --generations N  # 缺省 20 分钟/轮、缓存档硬闸门
```

- **数据根**：`S:\mahjong-training`（配额 200 GB，回收机制在 `paths.py`，`MIN_FREE_GB=10`）。
- `probe/` 有 **3 个 ACL 残留目录**（`cmp-par4` / `cmp-par5` / `mj-diag-pru96cdj`，0 B）——**删不掉，别再当"清理没做完"**。
- `client/dist` 只在 `-Deploy` 时更新；**发布前先看 `client/` 有没有改过**。

---

## 5. 操作纪律（本会话踩过/确认过的）

1. **推送（与并行会话共存）**：原生 `git fetch/push` 在本机不通（直连 `github.com:443` 超时；
   broker 模式下 `github_git_push` 走 `<broker>/git/…`，**对 github.com 返回 502**）。
   ✅ **2026-10-08 实测可行的那条路 = `github_commit_files`（REST / Git Data API）**，配方（照抄即可）：
   ① `github_permissions` 确认有 `write:content`；② **`dryRun:true` 先演练**（它会回读基线 + 按 `.gitattributes` 做写入保真预检）；
   ③ 本仓 `.gitattributes` 是 `* text=auto eol=lf` ⇒ **必须传 `normalizeEol:true`**（缺省 false 会直接拒绝）；
   ④ **内容来源必须是"干净的 HEAD 检出"**，不能直接用工作区（工作区含未提交改动，混进去就等于把别人/自己的半成品一起发布）：
   `git worktree add --detach ../mahjong.head HEAD` 然后用那棵树里的绝对路径喂 `file:`；
   ⑤ 真实调用（一个提交 + 移动 ref，**非 force**）；⑥ **判据 = 远端提交的 tree == 本地 HEAD 的 tree**
   （REST 建的 sha 与本地**天然不同**，永远别指望 sha 相等）。
   ⚠ **实测（第 1 段）**：远端头 `c026e89` → `d549b248`，其 tree `5531c22e…` 与本地 HEAD tree **逐字符相同** ⇒ 46 个提交的内容已上去。
   ⚠⚠ **实测（第 2 段，一次推 66 个文件）：只成功 27 个，39 个被写通道挡下** ——
   远端头 `d549b248` → **`ea59e353`**（3 个分批提交：`bb0a62d0` 13 个 client/** · `034be92d` 12 个 docs/** · `ea59e353` `AGENTS.md`+`README.md`）。
   **未推的 39 个**：`NOTES.md` · `python/**` 9（`README.md`、`producer.py`、`v4/{cli,export,model,pretrain,value_audit}.py`、`selfcheck.py`）·
   `server/**` 10 · `tools/**` 7 · `trainer/**` 12（清单可直接 `git diff --name-only <远端 tree 对应提交> HEAD` 复算）。
   **失败签名（都不是配置/权限问题）**：`github_commit_files` 报 **`建 tree → HTTP 400 malformed`** 或 **`ECONNRESET`**；
   连**手工** `github_request POST /git/blobs`（`bodyFile` 送 base64）也 **400 malformed** ⇒ **是代理→github.com 的写路径在返回畸形请求**。
   同一时刻**读**路径完全正常（`GET /git/ref/...` 200、限速计数器正常递减）。
   ⛔ **失败会消耗限速**（每次尝试几十个请求）⇒ **别连续硬重试**；判据是**重试能不能过**（第 1 段里 13 文件那批第一次 400、重试即成功）。
   **补推配方**：① 重建干净检出（`git worktree add --detach ../mahjong.head 7cbdc30`）；② **按目录分批，每批 10–15 个文件**（66 个一次必失败）；
   ③ 每批失败就**重试 1–2 次**（间歇性）；④ **NOTES.md（604 KB）单独一批**；⑤ 全部推完后按下面判据复核。
   **算法（判断"远端内容等于本地哪一代"）**：
   ① `GET /git/ref/heads/Training` 拿真实远端头；② `GET /git/commits/<头>` 读它的 **tree sha**；
   ③ 与本地 `git log --format='%h %T %s'` 逐行比 —— 命中哪个本地提交，就知道远端内容等于哪一代；
   ④ `git diff --name-status <那个提交> HEAD` 得出**要推的文件**（含删除项）—— 第 1 段 = **35 个文件**（30 改 + 5 新增、无删除），第 2 段 = **66 个**（63 改 + 3 新增）。
   ⛔ 别用 `GET /git/trees/<sha>?recursive=1` 列全仓：本仓 352 个文件，**响应会被工具截断**。
   ⚠ 推送前先想清"要不要把并行会话的半成品一起发"（本次用户裁决 = 一起发，故 §1 写明）。
   ⚠ **本地 `refs/remotes/origin/Training` 不会自动更新**（fetch 不通）⇒ §1 的"远端"行**两个值都写**：本地 ref（哪一个工具能校验）+ 远端真实头（API 回读）。
2. **多行提交信息不要塞进 PowerShell 引号**（`"` 会提前结束字符串）⇒ 用 `write` 落一个文件 + `git commit -F`。
3. **改文件一律用 `edit`/`write` 工具**，别用 PowerShell 做多行替换（CRLF 与 `` `n `` 不匹配会静默不生效）。
4. **`AGENTS.md` 预算**：**65,005 B**（提醒线 61,440 / **实测截断点 65,244** —— 只剩 **239 B**）——
   新内容先想能不能进 NOTES，改完必跑 `doc-refs-check`。⚠ **几乎没有余量了**：下次要往 AGENTS 加判据，
   先在同一节里删掉等量文字（本会话就是这么加进"头表判据"那句的：同节替换掉一条已被 §6.24 覆盖的旧句）。
5. **v4 契约以代码为准**：`python -m mahjong_ml.v4 spec` 现场打印块清单；文档与代码冲突时改文档。

---

## 6. 待决问题（留给下一轮定）

1. ~~要不要推那几个本地提交~~ ✅ **已推 + 已发布 v1.12.0**（§2.11）—— 下一轮的推送照 §5 第 1 条走。
   ⚠ **但本会话（2026-10-08）的第 2 段推送只成功 27/66**：**还剩 39 个文件没推**（清单与配方见 §5 第 1 条、
   现状见 §1"远端"行）。**这是下一轮第一件该做的事**：等写通道恢复 → 按目录分批（10–15 个/批、NOTES 单独一批）
   → 推到 `git diff --name-status <远端 tree 对应提交> HEAD` 为空 ⇒ 才算真同步。
2. **v3 谱系是否彻底封存**：数据集已删，若想再要"更强的 v3 基准对手"，只能重采重训（同种子可复现）。
3. **步长/epoch 实验**（`--epochs 4→2` 把 PPO 读盘 5 遍压到 3 遍）—— 本会话实测"KL 撞墙但无增益"，
   留作 v4 落地后的对照项，不是当务之急。
4. ~~**`AGENTS.md` 是否再瘦身**（把 §2.3-15 / §6.7 的流程细节搬进 NOTES）—— 目前余量仅 **95 B**，
   下一轮只要往 §2.3/§6 加一段就会越过提醒线。~~ ✅ **已处理（2026-10-07）**：
   ① 预算口径改按**实测截断点 65,244 B**（`tools/doc-refs-check.mjs` 的 `BUDGET`，并会打印"距实测截断点还剩 N B"）——
   名义值 65,536 已被两次真实截断证伪；② 那一轮用"同节净减 + 细节搬 NOTES"把 AGENTS 压回安全区。
   ⚠ **项数与余量只在 `AGENTS.md` §4 / 检查器输出里记一处**，本节不再复述数字（防漂）。
5. **`events[]` 的累积口径要不要改成"只发增量"**（obs v3 第 1 轮新出）：累积让轨迹 ×2.4~2.8
   （1,608 → 3,798 B/决策），增量能把体积压回 ~1.1×，但 `blocks.tile_matrix` /
   `RiverState.full_recompute` 都得改成"消费侧按小局攒"。**现在 Java 与 C++ 两侧都已按累积实现**
   （C++ 已过 200 场逐字节），所以改的代价是**两侧一起改 + 重跑 parity**；
   ⚠ 前提变了：v4 数据集**已经不是零**（`v4-bc-001/002` 已建）⇒ 改口径还要**重采 + 重建数据集**。
6. ~~`aux.npz`（标签侧）何时落盘~~ ✅ 已落地（§2.8，`v4 plan` 12/13）。
7. **v4 的 `belief` 头是否进推理**（设计里标 ✅：与危险头一起驱动押し引き）—— P1 校准达标后再定权重。

---

## 7. 2026-10-02 会话（v1.14.1 资产 + 阀门/夹具体系 + W1 接线）

> 本节（§7.1–§7.4）**已整体搬进 `NOTES.md` §13**（原文一字未改）：`NOTES.md` §13.41 = 原 §7.1 … `NOTES.md` §13.44 = 原 §7.4；本节开头那段"本节写在最后（编号独立）…"也随正文一并搬进 `NOTES.md` §13。逐节映射表见 `NOTES.md` §13 开头。这里只留指针，避免两份会话日志互相矛盾。
