# 阀门与夹具的工作体系（v4 训练）

> 这份文档回答三件事：**哪些东西是"阀门"、哪些是"夹具"、它们各自在什么时机跑**。
> 起因（2026-10-02）：连续四季 16 个候选全部没能显著超过现任 `g08`，而我们在这个过程中
> 抓到**三类不同性质的缺陷**（假接入 / 判据反向 / 夹具没覆盖新路径），它们都属于"体系"问题：
> **判据跑错时机、或根本没跑到那条路径**。所以先把体系钉死，再动模型。

## 0. 一句话规则（用户指定，必须守）

| 时机 | 跑什么 | 不跑什么 |
| --- | --- | --- |
| **开训前**（人工触发、独立完成） | 全部**夹具类**检查：golden 夹具重生（模型布局变了才需要）、`wiring_audit`、`python/selfcheck.py`、Java/C++ `--selftest`、`tools/v4-cache-check.mjs`、`gate --self-check`、`doc-refs-check` | — |
| **训练中**（回路自动） | 只有**阀门**：接受闸门（每代 1 次竞技场）、值头审计（`value-audit --strict`）、磁盘/轮换闸门 | ⛔ **不额外加自检**（不把 selfcheck / fixture / parity 塞进 `v4 loop`） |

理由：训练一轮是**小时级**的；把自检塞进回路会让每轮多花分钟级开销、并让"训练日志"里混进
与训练无关的判据噪声。**判据放前面，训练只做被阀门约束的事。**

## 1. 三类阀门（valve）

| 阀门 | 实现 | 时机 | 判据 | 不过怎么办 |
| --- | --- | --- | --- | --- |
| **方向自证** | `python -m mahjong_ml.v4.gate --self-check` | **开训前**（`tools/run-league.ps1` 已内置，不过就不开跑） | 弱现任（`first`）vs 强候选（`teacher`）⇒ **必须采纳**（实测 Δ≈+91.7）；反向 ⇒ 必须拒绝；并把"顺序写反"跑一遍确认会被抓 | ⛔ 停止，先修闸门 |
| **接受闸门** | `python -m mahjong_ml.v4.gate --incumbent … --candidate …`（多套牌山集合的**合并**配对检验） | **每代训练后 1 次** | 候选 − 现任的 **CI 排除 0 且为正** ⇒ 采纳；否则回滚（现在的门槛：2 套牌山 × 1000 场 = n=2000，可测 Δ≈2.2） | 回滚到现任；候选副本进 `tools/build/_rejected/`（⚠ 用 **Copy**，池里还指着原位） |
| **值头审计** | `v4 loop` 的 `value-audit --strict` 相位 | **每代 1 次**（回路内置） | `EV ≥ 0.7 × 同轮合法天花板` + 覆盖率达 标称±3pp + CE < 边缘 | **只报不拦**（诊断项）。⚠ 步数 <3000 时回路会打印"结论不可读"——那是门槛提示，不是失败 |

补充（不是阀门，但决定训练形状）：

- **对手池** = **最近 2 个候选**（无论是否被采纳）；**现任** = 阀门采纳过的那一个。
  两者**必须分开**（第五/六季踩过：把"进池"绑在"采纳"上 ⇒ 池恒空 ⇒ 采集桌只剩 student×2+teacher×2，
  把第一季赖以起效的多样性掐掉）。
- **磁盘/轮换**：每代成功后删 `raw/<tag>`、`compact/<tag>`、`raw/eval-<tag>`。

## 2. 夹具体系（fixture）—— 全部在**开训前**跑

| 夹具 | 文件 | 它保证什么 | 失败意味着 |
| --- | --- | --- | --- |
| **golden 前向夹具** | `python/tests/golden/forward-v4.bin`（生成器 `v4/export.py:build_golden`） | Python ↔ **Java** ↔ **C++** 三端前向**逐位相同**（唯一输入格式） | 三端不同源 ⇒ 训练端看到的网与上线的不一样 |
| **接线审计** | `python/wiring_audit.py` | ① **梯度可达性**（只用 policy 损失反传，列出梯度恒 0 的参数；白名单外 = **假接入**）② 零初始化张量清单 ③ **门控非零**时的导出往返 | 有"接上了但收不到梯度"的路径（第五十轮拼接、第五十七轮 GRU 都是这里抓出来的） |
| **Python 自检** | `python/selfcheck.py` | 导出契约、跨代权重归一、闸门纯判据、arena 计划、缓存/标签不变式……（当前 ~651 项） | 纯 Python 侧的契约漂移 |
| **服务端自检** | `java -jar server/build/mahjong-server.jar --selftest` | 规则引擎 + **v4 前向对拍夹具** + 增量缓存判据（当前 1443 项，必须全绿） | Java 前向/加载与夹具不符 |
| **训练端自检** | `trainer/build/trainer.exe --selftest` | C++ 引擎 + v4 前向对拍 | 同上（C++ 侧） |
| **增量缓存检查** | `node tools/v4-cache-check.mjs`（+ `SelfTest.v4CacheTests`） | 同种子开/关缓存 ⇒ **轨迹逐字节相同** | 缓存算错；⚠ W1 之后这条**第一次非平凡**（`h` 被融合消费了 —— 在此之前它是"假绿"） |
| **文档自检** | `node tools/doc-refs-check.mjs` | AGENTS 预算、章节号、全仓 `§引用`、`docs/INDEX.md` 完整性 | 文档漂移 |
| **打包自检** | `tools/bot-ai-test.mjs`（需服务端） | 机器人包能被服务端挂载/建房/换包 | 发布包坏了 |

### 2.1 两条"夹具必须显式扰动"的铁律

1. **训练起点为 0（或常量）的权重，夹具里必须显式随机化**，否则那条路径**三端都没被测到**
   （第五十七轮实测：`build_golden` 沿用初始化权重 ⇒ 门控全 0 ⇒ `g ≡ 1` ⇒ Java/C++ 里
   tanh/索引/偏置写错都会**照样全绿**）。当前手动覆盖：`heads.policy_gate.*`。
   ⇒ **待办**：做成通用规则（遍历 state_dict，凡 `max|·| == 0` 的**权重**都扰动）。
2. **"可缺张量"必须在所有加载点统一归一**（`export.normalize_state`）：`state_from_net`（net.bin）、
   `value_audit.load_model`（ckpt）、`pretrain._load_model`（ckpt）、`packbot.export_net`（ckpt）。
   ⚠ 抄漏一处就是"某些旧权重能载、某些不能"的鬼故事（实测踩过两次）。

## 3. 工作包（代码）

### W1 —— GRU 长程记忆接进融合（`h_evt` 变显式输入）【能力，最高优先】—— ✅ 代码已落地（2026-10-02）

> **落地状态**：①语义定稿 ✅（`h0` = 窗口之前、`h_evt` = 窗口之后，只喂真实事件行）
> ②夹具格式 2 ✅（每用例带 `h0` + `h_evt`）③三端同步 ✅（Python/Java/C++）
> ④绊线改写 ✅ ⑤验收判据 ✅（`wiring_audit` 去掉 `event.cell.` 白名单仍 PASS；
> Java/C++ 两条**逐位**判据；`selfcheck` +8 项）⑥训练验证季 ⏳**未跑**。
> 细节与三个"假绿"的抓取过程见 `NOTES.md` §6.5 第六十一轮。

**成本核算（决定实现路线的那一步）**：无缓存前向实测 **34.69 ms/决策**，事件塔 GRU 是 60 步 × ~111k MAC。
若融合消费"**窗口冷启动**的 `h`"，**缓存路径每决策必须重跑 60 步 GRU** ⇒ 22.5 ms → ~35 ms（**缓存白做**）；
消费"**整手 carry**"则**零额外成本**（缓存路径本来就在增量推进 `s.h`，今天只是把它扔掉）。
⇒ 口径必须是"整手 carry"，且**无缓存路径也要重放整手**（否则两条路径分叉）。

**为什么**：事件窗口 `K_EVT = 60` 覆盖不到整手（一手 ~40~100 条事件），GRU 的 carry 是**唯一的
长程记忆**；而它此前**没接进任何头**（`wiring_audit`：`event.cell.*` 梯度恒 0），
既永不训练、又每次前向白跑 K 次 GRUCell。

**为什么不能"顺手接"**：`V4Policy.cachedHidden` 的注释就是为这一天留的绊线 ——
**Java 生产路径的 `h` 是整手 carry（缓存）、Python/夹具的 `h` 是窗口冷启动**，接进融合前两者
"不同也不影响输出"，接上就会**分叉**（三端不同值 + "增量 == 全量"红证变红）。

**步骤与实际落地结果**：
1. **语义定稿** ✅：`h0`（进来）= **窗口之前**的 carry；`h_evt`（出去）= 窗口之后的 carry；
   融合消费 `h_evt`，方式是**逐维门控** `g = 1 + tanh(W·h_evt + b)`（乘在**逐候选**的 `u_i` 上）——
   拼接/加常数对 policy 是**数学空操作**（第五十轮的教训）。门控零初始化 ⇒ 旧网**逐位不变**。
   两条纪律：**只喂真实事件行**（padding 不喂）、**只作用在所有头之前**（policy 之外的头也看长程）。
2. **夹具格式 +1**（`GOLDEN_FORMAT 1 → 2`）✅：每用例带 `h0`（输入）与 `h_evt`（期望输出），
   于是"给定同一个 `h0`，三端逐位相同"成为可判据。另外三条**空转闸门**：归零 `fusion.mem.weight`
   必须改变策略头 / 长事件流用例上扰动 `h0` 必须改变策略头 / 策略头**组内极差 > 1e-4**
   （对应三个"假绿"，见 `NOTES.md` §6.5 第六十一轮）。
3. **三端同步** ✅：`model.py`（Fusion 收 `h_evt` + `EventTower` padding 屏蔽）、
   `V4Policy.java`（全量路径 = **整手重放**、缓存路径 = `s.h`、夹具路径 = `h0` 推进窗口）、
   `v4policy.cpp` 同样。
4. **绊线改写** ✅：老断言"h 不同但七头逐位相同"改成两条**逐位**硬判据 ——
   ① **缓存 carry == 全量整手重放**；② **前缀 carry + 窗口 == 整手重放**（这条同时钉住夹具/离线路线）。
5. **验收判据** ✅：`wiring_audit` 把 `event.cell.` **从白名单删掉**后仍 PASS
   （`weight_ih` 梯度 6.6e-5）；`v4-cache-check` 开/关缓存逐字节 PASS 且**这次非平凡**；三端 `--selftest` 全绿；
   `selfcheck` 新增 8 项（含两条 `maxΔ=0.000e+00` 的逐位判据）。
   ⚠ 接线审计自身也要先按铁律①扰动零初始化参数再量梯度，否则 `mem ≡ 0` ⇒ `h_evt` 无梯度 ⇒ **假红**。
6. **训练侧 `h0`（W1b）** ✅/⏳：紧凑集加 `h0` 列（用**学生网**重放 `events[0, n-K)`；**不按行换网**，
   否则 `h0` 是否为 0 会泄漏 `is_student`）；没有这一列时 `pretrain` **硬拒**（除非显式 `--no-carry`）。
7. **训练验证** ⏳**未跑**：独立一季（与 W3 分开），起点 = `g08`，看闸门是否终于采纳。

### W2 —— 删 `belief_hand`（无价值）【提速 + 去掉无用梯度】

**依据**：实测 BCE **0.6506 比"每格频率常数"0.6403 还差**、`inference=False`（不上线）
⇒ 零价值，且往躯干灌一条无用辅助梯度。
**步骤**：`model.py` 删头 → `export.py` 契约与 `HEAD_WIDTHS` → 三端（Java/C++ 读**可缺**张量、
把旧网的 `heads.belief_hand.*` 记入 `used` 以免"有张量没人读"报错）→ 夹具重生 → 三端自检。
**验收**：三端 selftest 全绿 + `wiring_audit` 白名单同步（去掉 `heads.belief_hand.`）。

### W3 —— "单调精修"目标：对现任的 KL 锚【训练目标】

**依据**：16 个候选里 13 负、无显著正 ⇒ 现目标在把网**推离**好点。
**步骤**：`pretrain` 加一项 `β · KL(π_θ ‖ π_ref)`（`ref` = `--init` 那份网，冻结），
β 走 CLI；默认 0（= 现状，向后兼容）。**验收**：纯函数单测（β=0 时与现状逐位相同；
β>0 时同一批数据的梯度方向把 logits 拉向 ref）。
**训练验证**：独立一季（与 W1 分开）。

**已落地（2026-10-04）**：`pretrain --ref <net.bin|model.pt|ckpt 目录>`（缺省 = `--init` 那一份
**现任**）+ `--ref-beta <β>`（**缺省 0**）。损失项 `β·KL(π_θ(·|s) ‖ π_ref(·|s))` **只在学生行上**
（与策略损失同一个 `row_keep`）、用**同一个** `softmax(logits/T)`，π_ref 冻结
（`requires_grad_(False)` + `eval()` + `no_grad` 预算 logits）；每个 epoch 行打
`ref_kl=<值>（β=…，占总损失 …%）`，`train()` 的返回 dict 带 `ref_beta`/`ref_kl`/`ref_kl_mean`。
**为什么是 π_θ‖π_ref（反向 KL）**：它是 **mode-seeking**（π_ref 低概率处只要 π_θ 也低就不受罚）
⇒ "别自作聪明地偏离现任"，设计文档写的也是这个方向；正向 `KL(π_ref‖π_θ)` 是 mass-covering，
会逼 π_θ 覆盖 π_ref 的整个支撑集（把尾巴摊平），在"单调精修"这个目标下不是我们想要的。
细节、判据与 CLI 表见 `docs/TRAINING-V4.md` §14.11。

### W4 —— 稠密逐候选危险标签（杠杆①）【监督信号】

**依据**：`danger` 头 AUC 0.638~0.650，而输入里**最好的单列只有 0.5582**
⇒ 头已超过便宜特征，天花板在**标签**（`aux_opp_dealin` 只落在被选中的候选上、无反事实）。
**步骤**：引擎侧给"每个候选 × 每家"的威胁标签（`danger_per_seat[3][34]` 已有稠密来源；
或从真手牌算威胁）→ sidecar `DERIVED_VERSION` +1 → 三端 → 重训 danger 头 → 重量 AUC。
**验收**：新标签下 danger 头 AUC 显著高于 0.65，且 `heads_audit` 能读出。

## 4. 回归清单（改动后必跑，开训前一次性过）

```powershell
python wiring_audit.py                                  # 接线/假接入
python selfcheck.py                                     # 纯 Python 判据
java -jar server\build\mahjong-server.jar --selftest     # 规则引擎 + v4 夹具对拍
trainer\build\trainer.exe --selftest                    # C++ 引擎 + v4 夹具对拍
node tools\v4-cache-check.mjs                           # 增量 == 全量（逐字节）
python -m mahjong_ml.v4.gate --self-check               # 闸门方向
node tools\doc-refs-check.mjs                           # 文档
```

**布局变了才需要**：`python -m mahjong_ml.v4.export golden --trace <轨迹> --out python\tests\golden\forward-v4.bin`。

## 5. 不可破坏的既有判据

- 观测只许含**合法信息**（`Observation` 白名单 + `selfplay-check` 的 `OBS_KEYS`）；
- **可复现**：`debugDeterministicSeed` + 每局一份策略实例；
- **增量 == 全量**（缓存开关逐字节）——W1 之后这条才有牙齿；
- **训练侧只实现 `ActionPolicy`**（拿不到 `Round`）；
- **三端同源**：任何前向改动都要 golden 夹具 + 三端自检；
- **接受闸门的方向**：`gate --self-check` 必须 PASS（弱现任 vs 强候选 ⇒ 采纳）。
