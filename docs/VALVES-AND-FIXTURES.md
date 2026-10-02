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
| **增量缓存检查** | `node tools/v4-cache-check.mjs`（+ `SelfTest.v4CacheTests`） | 同种子开/关缓存 ⇒ **轨迹逐字节相同** | 缓存算错；⚠ **只有 `h` 被消费之后这条才非平凡**（现在 h 未被消费 ⇒ 它是"假绿"，见 W1） |
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

### W1 —— GRU 长程记忆接进融合（`h_evt` 变显式输入）【能力，最高优先】

**为什么**：事件窗口 `K_EVT = 60` 覆盖不到半局（一手约 11~18 个事件），GRU 的 carry 是**唯一的
长程记忆**；而它现在**没接进任何头**（`wiring_audit`：`event.cell.*` 梯度恒 0），
既永不训练、又每次前向白跑 K 次 GRUCell。

**为什么不能"顺手接"**：`V4Policy.cachedHidden` 的注释就是为这一天留的绊线 ——
**Java 生产路径的 `h` 是整局 carry（缓存）、Python/夹具的 `h` 是窗口冷启动**，接进融合前两者
"不同也不影响输出"，接上就会**分叉**（三端不同值 + "增量 == 全量"红证变红）。

**步骤**（每步都可独立验证）：
1. **语义定稿**：`h_evt` 作为**显式输入向量**进入 `V4Model.forward(...)`（`None` ⇒ 用窗口冷启动）。
   生产路径传"缓存 carry"，夹具/离线传显式值。
2. **夹具格式 +1**（`GOLDEN_FORMAT 1 → 2`）：夹具里带**每用例的 `h_evt`**（由 Python 生成），
   于是"给定同一个 h，三端逐位相同"成为可判据。
3. **三端同步**：`model.py`（Fusion 收 `h_evt`）、`V4Policy.java`（用**缓存槽的 h**，不再用
   `lastFullHidden`；全量路径也传自己的 h）、`trainer/src/v4policy.cpp` 同样。
4. **绊线改写**：把"h 不同但七头逐位相同"改成两条 ——
   ① **同 h ⇒ 同输出**（三端）；② **生产路径的 h 必须来自缓存**（增量路径不得退化成全量）。
5. **验收判据**：`wiring_audit` 把 `event.cell.` **从白名单删掉**后仍 PASS（非零梯度）；
   `v4-cache-check` 开/关缓存逐字节 PASS 且**这次是非平凡检验**；三端 `--selftest` 全绿。
6. **训练验证**：独立一季（与 W3 分开），起点 = `g08`，看闸门是否终于采纳。

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
