# 训练侧（Python）—— 环境与纪律

这里是 `docs/TRAINING.md` 的**可执行部分**：只放源码与环境说明，**不放数据、不放环境**。

- **训练数据一律在 S 盘**：`S:\mahjong-training\`（卷标 `Silicon_files`）——见 TRAINING §0.1.1。
  ⚠ 2026-09 从 `T:\mahjong-training\`（TrainingData）迁来：T 的**每文件开销**顶不住自对弈吞吐
  （实测把每场轨迹压到 1/50 大小，吞吐一点没变）。默认值在 `mahjong_ml/paths.py`，
  可用环境变量 `MAHJONG_DATA_ROOT` 覆盖。
- **`.venv` / uv 缓存 / uv 的 Python 安装目录都在本目录**（都已 gitignore）——
  受限沙箱下 uv 的默认目录（`%LOCALAPPDATA%`）**会被拒**，所以必须显式指到仓库内。
- 服务端仍然是**零第三方依赖**的 Java：Python 只负责训练，权重导出后由**纯 Java 手写前向**加载
  （TRAINING §2、`AGENTS.md` §6.5）。**改产出格式要三处一起改**（`TraceRecorder` / `PROTOCOL §8.4` /
  `tools/selfplay-check.mjs`）。

---

## 一、装环境（一次性，约 3 分钟 + 约 6 GB 磁盘）

```powershell
cd C:\Users\HP\source\games\mahjong

# uv 的两个默认目录在工作区外，受限沙箱下会被拒 → 指到仓库内（.gitignore 已收录）
$env:UV_CACHE_DIR          = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"

uv venv --python 3.12 python\.venv
uv pip install --python python\.venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python python\.venv\Scripts\python.exe numpy
```

实测装出来的是 **CPython 3.12.13 + torch 2.11.0+cu128 + numpy 2.5.3**。
为什么要 cu128：本机是 **RTX 5060 Laptop（Blackwell，sm_120）**，CUDA 12.8 之前的轮子里没有它的内核。

> 系统 Python 是 3.14，**不要**直接用它装 torch（没有官方 wheel）；一律用 `python\.venv\Scripts\python.exe`。

## 二、自检（每次换机器 / 升级驱动后跑一次）

```powershell
python\.venv\Scripts\python.exe python\verify_env.py
```

它逐项验证**"装上了"之外的东西**，并打印可抄进文档的基线：

| 组 | 验什么 |
| --- | --- |
| Python / torch / GPU | 版本、`+cu128`、`cuda.is_available()`、**capability sm_120 在内核列表里**（否则会 `no kernel image`）、GPU 上真的做一次 matmul |
| 显存预算 | `mem_get_info()`：总/别家占用/留给训练的量（目标自己 ≤4.5 GB） |
| 训练吞吐 | 0.57M 参数 / batch 4096 的 fwd+bwd 步频与峰值显存 |
| **GPU ≤80%** | 未节流的均值与瞬时峰值；再跑一段**带占空比节流**的，判据"最近 3 秒均值 ≤80%" |
| **CPU ≤75%** | `torch.set_num_threads(4)` 生效；提醒 `--workers` 默认 32 会越界（本机上限 24） |
| 采集器 | `server/build/mahjong-server.jar` 在不在（轨迹靠它产） |
| **数据盘** | 数据根存在、六个子目录、**可写探针**（写→删）、剩余 ≥10 GB |

判据：每项 `PASS/WARN/FAIL` + 末尾 `VERIFY PASS/FAIL`（退出码）。**只有真问题才 FAIL**：
- 受限沙箱里"写 S 盘被拒"是 **WARN** —— 但它正是 `SelfPlay` **静默不落盘**（只打一条 WARN 就继续跑）
  的那条闸门，所以采集前必须看到这一项是 PASS。

本机当前结果（迁 S 盘后）：**20 通过 / 0 警告 / 0 失败**（此前那条警告是"受限沙箱写数据盘被拒"；
数据根换到 S 盘、且会话放开写权限后不再出现）。

Python 侧另有一份**单元自检**（统计口径 / 配对评测 / 两条纪律 / 特征 / 数据集 / 网络 / BC / DAgger / 混合 / P3 的转移与校准，**157 项**）：

```powershell
python\.venv\Scripts\python.exe python\selfcheck.py     # 期望 SELFCHECK PASS
```

> ⚠ 两条本机环境事实（都实测踩过，别重踩）：
> ① **`tempfile` 不可用**：系统临时目录不可写，且 `tempfile.mkdtemp` / `TemporaryDirectory`
>    建出来的目录**自己也写不进去**（在里面再 `mkdir` → `WinError 5`）→ scratch 一律用工作区内的
>    `python\.tmp\`（已 gitignore），手写 `Path.mkdir(parents=True)`；
> ② **numpy 要单独装**（`torch` 不会带它，而特征/评测都要用）。

## 二·五、评测（P0：顺位点 + 配对显著性）

```powershell
# 单次 run：各策略指标 + 95% 自助法置信区间 + **组内配对检验**（同一副牌山比不同策略）
python\.venv\Scripts\python.exe -m mahjong_ml.eval S:\mahjong-training\raw\run-001

# 两次 run：**按 (seed, 座位) 配对**比较两个不同策略（比较 网络 vs teacher 的正解）
python\.venv\Scripts\python.exe -m mahjong_ml.eval <dirA> <dirB> --labels net,teacher

# 合并**任意多次**独立 run（同一对策略、不同牌山）—— 把小效应钉到显著 / 看它能不能复现
python\.venv\Scripts\python.exe -m mahjong_ml.eval --pool <dir1> <dir2> <dir3> `
    --labels awr-002,bc-003 --json T:\…\pooled.json
```

- 指标默认**顺位点**（`rank_points`），也可 `--metric place`（取负，正=更好）。
- 统计口径：自助法 10000 次（**固定种子 → 同输入同结果**）+ **符号检验**精确 p 值，
  并按实测 `sd(Δ)` 反推"还要打多少场才能检出 Δ"（TRAINING §7 的样本量表由此落地）。
  ⚠ 这个反推默认按 **α=0.05 + 80% 功效**（第一版只用了 `z_{α/2}`，那是 50% 功效、低估 2.04 倍）。
- `--pool` 的纪律：**同一副牌山不能数两遍** —— 两次 run 只要有一场 seed 相同就直接报错；
  策略标签用**子串**（`awr-002`）会解析成完整标签，命中不唯一时报错而不是猜。
- **读法**：CI 跨 0 或 p ≥ 0.05 → **证不出差别**。实测过一次反面教材：`first vs random` 24 场
  Δ=+3.45 看着"更强"，配对后 95%CI=[−5.60,+12.55]、p=0.471 —— 纯噪声。

## 三、三条硬约束（细节见 TRAINING §0.1）

| # | 约束 | 怎么守 |
| --- | --- | --- |
| ① | 数据只落 S 盘 | 唯一数据根 `S:\mahjong-training\`；`raw ≤30 GB`、`compact ≤10 GB`、`ckpt+league ≤3 GB`、`logs ≤1 GB`，任何时刻留 ≥10 GB |
| ② | GPU ≤80% | **时间平均**口径；`GpuMonitor` + `DutyCycle`（占空比节流，实测把稳态均值压到 46%，吞吐代价约 21%）。⛔ 不用 `nvidia-smi -lgc` 锁频（全局设置） |
| ③ | CPU ≤75% 的核 | 采集 `--workers 24`（**必须显式**）、训练 `torch.set_num_threads(4)`、**生成与训练不并行**（24+4 > 24） |

## 四、采集一条命令（示例）

```powershell
# ⚠ 在受限沙箱里，子进程写数据盘（S:）会被拒 → 这一步请在**普通 shell**里跑（或放宽权限的会话）
java -Dstdout.encoding=UTF-8 -jar server\build\mahjong-server.jar `
     --selfplay 2000 --workers 24 --rotate --sample 4 `
     --seed 20260101 --out S:\mahjong-training\raw\run-001

node tools\selfplay-check.mjs S:\mahjong-training\raw\run-001   # 必须 DATASET PASS 才拿去训练
```

实测吞吐：`--workers 24` → **1.95 场/秒**（≈7.0k 场/小时 ≈ 7.8 GB/小时，`--sample 1`）。

## 四·五、训练流水线（P1 行为克隆）

```powershell
cd C:\Users\HP\source\games\mahjong

# ① 派生特征富化：给每个 g*.jsonl 生成 g*.feat.bin（**轨迹格式不变**）
#    向听/进张/听牌形/逐张危险度由 Java 的权威实现算 —— 不在 Python 里再写一份（必然漂移）
java -Dstdout.encoding=UTF-8 -jar server\build\mahjong-server.jar `
     --features S:\mahjong-training\raw\bc-001 --workers 24

# ② 轨迹 → 紧凑数组（按整场切训练/验证；**缺 sidecar 会报错**而不是悄悄填 0）
python\.venv\Scripts\python.exe -m mahjong_ml.dataset build `
    S:\mahjong-training\raw\bc-001 S:\mahjong-training\compact\bc-001

# ③ 行为克隆训练（checkpoint 落 S 盘 ckpt/，配额闸门在 paths.allocate 里）
python\.venv\Scripts\python.exe -m mahjong_ml.bc `
    --data S:\mahjong-training\compact\bc-001 --label bc-002 --epochs 60
```

特征规格（**唯一来源** = `mahjong_ml/features.py`，`python -m mahjong_ml.features` 打印分段偏移）：

| 段 | 维度 | 由谁算 |
| --- | --- | --- |
| 状态（obs 原始字段） | 539 | Python `features.py` |
| 状态（派生危险度 `danger_worst` + `danger_riichi`） | **68** | **Java** `ObsFeatures.perDecision`（sidecar） |
| 候选（类型 / 牌码 / 取法 / 摸切 / 杠种…） | 88 | Python |
| 候选（向听 / 进张 / 听牌形 / 宝牌） | **8** | **Java** `ObsFeatures.perCandidate`（sidecar） |
| **合计** | **607 / 96** | 特征版本 **v2** |

# ③ 看指标 / 复现：metrics.json 里带 args、特征版本、数据 meta、每 epoch 的 train/val
```

**评一个已有 checkpoint**（DAgger 对比、换数据集复评都靠它 —— 不用重训即可同尺子比较）：

```powershell
python\.venv\Scripts\python.exe -m mahjong_ml.bc eval `
    --data S:\mahjong-training\compact\bc-001 --ckpt S:\mahjong-training\ckpt\bc-002\model.pt
# → top-1 / 类型 / nll + **同粒度基线**（首合法、多数类型），带 n 与 split
```

## 四·六、DAgger 一轮（P2：让学生暴露自己的状态分布）

```powershell
# 一条命令跑完：采集（学生坐 2 席、逐场轮转，学生座位额外记老师动作）
#   → --features 富化 → selfplay-check 校验 → 建混合集/评测集 → 重训 → 无泄漏对比
python\.venv\Scripts\python.exe -m mahjong_ml.dagger `
    --student S:\mahjong-training\ckpt\bc-002 `
    --bc-src S:\mahjong-training\raw\bc-001 --bc-compact S:\mahjong-training\compact\bc-001 `
    --label bc-003 --round 1 --games 300 --workers 24 --epochs 60
# 断点续跑：--skip-collect / --skip-features / --skip-validate（数据已采好时）
# 报告：S:\mahjong-training\ckpt\bc-003\dagger-r1.json（含 leak_check 与按场聚类 CI）
```

三条**必须守住**的口径（理由都写进了脚本注释与 `TRAINING.md` §4 P2 / §7）：

- **对比只在"两个模型都没训过"的场次上做**：先建混合集，再从它的 **val 切分**里挑评测集
  （`val_frac=1.0` 建成全 val 的紧凑集）。直接在 `compact/dagger-rN` 的 val 上比会大面积撞上
  混合集的**训练**切分 —— 那是泄漏，不是进步。跑的时候有硬闸门：评测集混进训练场直接退出。
- **显著性按场聚类**（配对 bootstrap 的 cluster = 场号）：一局里的决策高度相关，逐行 bootstrap
  会把 CI 缩到假显著（`selfcheck.py` 里有这条红证）。
- **离线一致率 ≠ 变强**：它只说"更像老师"。强度只能由 `eval.py` 的实战顺位检验给出。


三件事值得留意：

- **特征规格只有一份**：`mahjong_ml/features.py`（v2：`state_dim()=607`、`cand_dim()=96`）。
  打印分段偏移用 `python -m mahjong_ml.features`。**派生量由 Java 算**（`ObsFeatures`），
  两侧由 `SelfTest.obsFeaturesTests`（obs 通路 == Round 通路，带红证）与 `python/selfcheck.py`
  （sidecar 格式契约、缺 sidecar 必须报错）钉住。
- **验证集是整场切出来的**（`--val-frac` / `--split-seed` 决定，与数据量无关）：
  按决策随机切会把同一场的后续信息漏进验证集，指标虚高。
- **指标一定同时看"同粒度基线"**：模型报的 `val top1`（精确动作）要和"总是选第一个合法动作"比，
  类型准确率要和"多数动作类型"比 —— 拿精确动作去比类型基线是自欺欺人。

纪律（§0.1）：训练脚本自己封线程（`torch.set_num_threads(4)`）与压 GPU（`DutyCycle`），
**生成与训练不并行**（24 + 4 > 24 会越界）。

## 四·七、P5b 混合（teacher 先验）的 α 选择

服务端策略串 `net:<net.bin>@<α>` = `argmax(student + α·1[该候选 == 老师动作])`（`PROTOCOL.md` §8.4）。
**α 不要照抄**——它绑定网络的 logit 尺度（未训练的网 α=0.25 就 100% 让位）。先量"让位曲线"：

```powershell
python\.venv\Scripts\python.exe -m mahjong_ml.hybrid `
    --ckpt S:\mahjong-training\ckpt\bc-003\model.pt `
    --data S:\mahjong-training\compact\dagger-r1 --target 0.95
# 输出：margin 中位/p90、各 α 的让位比例、推荐 α（= 让位首次达标的最小值）
# 例（bc-003）：中位 −4.08 / p90 0.63；α=0.25→87.8% α=1→91.7% α=2→94.8% α=4→97.7% → 推荐 α=4
```

⚠ 它只回答「让位多少」（数据结构上唯一的真值就是老师，让位即"对"），**不回答「是不是更强」**——
强弱只能用同牌山配对实战：`java -jar … --selfplay N --rotate --policy "net:…@1,net:…,…"` 再
`python -m mahjong_ml.eval <run dir>`。**评测跑记得 `--sample 64`**（只写 ~11 行/场，
summary 与配对检验不受影响；实测吞吐瓶颈是**每文件开销**，不是字节数）。

**已测（round 1）**：`hybrid@1` − 纯网（2400 场）= **+1.95 顺位点**（95% CI [−0.21, +4.10]，p=0.045，
胜 1248/负 1149）；`hybrid@1` − `teacher`（1200 场）= **+1.20**（CI [−1.76, +4.12]，**p=0.977，胜 600 负 598**）
→ 三臂点估计都在 ±2 顺位点内、**CI 全跨 0**：**机制正确且可证安全，但强度效应未确立**
（`eval.py` 的尺子：检出 Δ=2 约需 2700 场）。⚠ α=1 时 91.7% 的决策本来就听老师，
所以天花板就是老师 —— 想更强得靠 P3 的价值信号，混合则留作在线 RL 的**保底**。

## 四·八、P3 离线 RL（IQL → AWR）

```powershell
# ① 数据集必须带 P3 的列（file/hand_no/seat/placement/is_student）——旧数据集读回来是 None，会明确报错
python\.venv\Scripts\python.exe -m mahjong_ml.dataset build S:\mahjong-training\raw\bc-001 `
    S:\mahjong-training\compact\rl-001 --src S:\mahjong-training\raw\dagger-01 `
    --src S:\mahjong-training\raw\control-r1 --val-frac 0.1 --split-seed 20260401

# ② 学价值（IQL）：expectile V + 单动作 Bellman Q + Polyak；**按验证 MAE 选最优 epoch 落盘**
python\.venv\Scripts\python.exe -m mahjong_ml.offline_rl `
    --data S:\mahjong-training\compact\rl-001 --label iql-002 --epochs 40 --lr 5e-4 --eval-every 2

# ③ 判据①：校准报告（含**恒 0 / 恒均值**同粒度基线；恒均值的常数取训练集均值，不吃验证集信息）
python\.venv\Scripts\python.exe -m mahjong_ml.offline_rl --data S:\mahjong-training\compact\rl-001 `
    --eval-only --ckpt S:\mahjong-training\ckpt\iql-002\model.pt

# ④ 判据②：优势加权 BC（AWR）+ 行为锚定；β 越大越激进，**看 ESS**（塌了就不可信）
python\.venv\Scripts\python.exe -m mahjong_ml.awr --data S:\mahjong-training\compact\rl-001 `
    --critic S:\mahjong-training\ckpt\iql-002\model.pt `
    --init S:\mahjong-training\ckpt\bc-003\model.pt --label awr-001 --beta 3 --bc-anchor 0.2

# ⑤ 判据②的实战检验（离线 top-1 **不是**判据）：导出 net.bin → 同牌山配对 → eval
python\.venv\Scripts\python.exe -m mahjong_ml.export weights `
    --ckpt S:\mahjong-training\ckpt\awr-001\model.pt --out S:\mahjong-training\ckpt\awr-001\net.bin
java -jar server\build\mahjong-server.jar --selfplay 2400 --workers 24 --rotate --sample 64 `
     --seed 888001 --out S:\mahjong-training\raw\awr-vs-bc `
     --policy "net:…awr-001\net.bin,net:…bc-003\net.bin,net:…awr-001\net.bin,net:…bc-003\net.bin"
python\.venv\Scripts\python.exe -m mahjong_ml.eval S:\mahjong-training\raw\awr-vs-bc
```

**MDP 口径**（权威在 `rewards.py` 的 docstring）：一条 episode = 某家在一小局里的全部决策；
只有该家末决策吃 `hand_delta[seat]`；γ=1 ⇒ `V(s)` = "本小局还能再赚多少点"，与 teacher 那三个
粗模型同量纲。**终局顺位这一轮不进奖励**（另一套量纲，且精算在 Java 里，不在 Python 重写）。
⚠ P3 的列是 2026-09 才加的：旧紧凑集（`bc-001`/`mix-r1`/`dagger-r1`…）**没有**，`rewards.py`
会明确要求重建 —— 不会拿 `game` 冒充 `file`（那个跨来源会撞车）。

**一轮实测（2026-09，`rl-001` = 622,472 条 / 57,355 条 episode）**：

| 判据 | 结果 |
| --- | --- |
| ① 价值校准（`iql-002`，λ=0，按 MAE 回滚 ep6） | 行级 MAE **3.0266** < 恒0 3.0634；**小局级 Pearson +0.396** → 通过但很弱 |
| ① 价值校准（`iql-004`，λ=1 加顺位点，按小局级 ρ 回滚 ep40） | 小局级 ρ **+0.554**（有收支的小局 +0.577）但行级 MAE 6.399 **打不过**恒 0 的 6.241 → **排序口径通过、逐点口径不通过**；分箱可靠性两端尚可（−6.9→−11.1、+13.9→+13.2）、**中间 8 箱压平**（预测 −0.4…+2.6 对实际 −1.2…+0.0，偏乐观） |
| ⚠ 两条训练陷阱 | ①验证指标在中段后持续恶化（λ=0：ep6 的 3.0266 → ep40 的 3.4709）→ 必须**按指标回滚最优 epoch**；②**MAE 与排序能力会选出不同的模型**（MAE 选中的 ep4 ρ=0.20，ρ 选中的 ep40 ρ=0.55）→ `--select-metric` 要跟用途走 |
| ② 策略改进（`awr-001` λ=0 critic / `awr-002` λ=1 critic，ESS≈14%） | 同牌山配对：**AWR−BC = +0.92（p=0.391）/ +1.27（p=0.094）**；**AWR−teacher = −1.16（p=0.977）/ −1.66（p=0.885）** |
| ② **定论（n=12,000：三批独立 run 合并，`eval --pool`）** | **AWR−BC = +1.89 顺位点，95% CI [+0.93, +2.82]，符号检验 p<0.001 ⇒ 显著**；run 级随机效应 CI [+0.35, +3.33] 仍排除 0；次要指标「平均顺位」+0.043 名，CI [+0.02, +0.07]。逐批 +1.27 / +0.93 / +3.16。⚠ 效应小（≈一场 ±52 散度的 3.6%），且 AWR 仍不如 teacher |



## 四·九、P4 在线自对弈（PPO + 联赛）

**三个模块**：`ppo.py`（PPO 训练）/ `online.py`（世代循环 + 联赛阶梯）/ `league.py`（Plackett-Luce Elo + 对手池加权采样）。

```powershell
# 0) P4 的前置是 Java 侧的**探索口**：`net:<权重>[@<α>][#<T>]` = 从 softmax(logits/T) 采样。
#    T 省略/0 = argmax（与加这个语法之前逐决策相同）；随机源按 (seat, gameSeed) 派生 ⇒ 同种子可复现。
#    PPO 是同策略算法，没有它数据就全落在"贪心那一条"上（重要性权重没有支撑集）。

# 1) 世代循环：每代 [采集(2 席自己 + 2 席联赛对手) → --features → 紧凑集 → PPO → 导出 net.bin]
python\.venv\Scripts\python.exe -m mahjong_ml.online run `
    --init S:\mahjong-training\ckpt\awr-002\model.pt `
    --init-value S:\mahjong-training\ckpt\iql-004\model.pt `
    --label ppo --generations 4 --gen-games 1500 --temp 1.0 --epochs 4 --workers 20

# 2) 阶梯（判据在这里，不在训练日志里）：每代一跑 net:gNN,teacher,first,random 出 Elo + 脚本基线，
#    另跑一批 **2+2**（net×2 vs teacher×2）给顺位点的逐场配对 CI
python\.venv\Scripts\python.exe -m mahjong_ml.online ladder --label ppo --generations 4 `
    --games 1200 --pair-games 800

# 3) 也可以只跑一代 PPO（数据必须是**当前网络**采的；--temp 必须等于采集时用的 #<T>）
python\.venv\Scripts\python.exe -m mahjong_ml.ppo --data S:\mahjong-training\compact\ppo-g01 `
    --init S:\mahjong-training\ckpt\awr-002\model.pt --temp 1.0 --label ppo-g01 --epochs 4

# 4) **步长体检**（调 lr/epoch 之前先跑它）：各代网络在固定数据集上的贪心一致率
python\.venv\Scripts\python.exe -m mahjong_ml.online displace `
    --data S:\mahjong-training\compact\ppo-g02 --label ppo --generations 4
# 输出示例（第一轮）：逐代位移 2.68% / 2.56% / 2.66% / 2.42%；首→末累计 **4.85%**
#   ⇒ 累计位移 <5% 时别指望 Elo 能分辨（1200 场/代 ≈ ±4 顺位点）——先调步长，不是加代数。
```

口径与坑（细节见 `NOTES.md` §6.5、判据见 `TRAINING.md` §4 P4）：
**λ=1**（奖励只在末决策记一次，λ<1 是系统性偏差）· 优势**只在学生行**归一化 ·
价值头冷启动自 P3 的 IQL critic（只取 `trunk.* + v_head.*`）· 策略损失只算学生行、价值损失用全部行 ·
**价值头不导出到 Java**（线上仍只认 `CandidateScorer`，`NeuralPolicy.java` 一行没动）·
阶梯 run 是一席对一席（实测 `sd(Δ)≈80`），**配对判据只在 2+2 那批读**（`sd(Δ)≈53`）。

**一轮实测（2026-09，4 代 × 1500 场采集，每代 ~26 分钟）**：

| 判据 | 结果 |
| --- | --- |
| ① Elo（每代 1200 场，四代结构相同的阶梯 run） | **四代彼此不可区分、无单调趋势**：`g04−g01 = +0.058`（p=0.51）、`g02−g01 = +0.065`（p=0.44）、`g03−g01 = +0.016`（p=0.85）⇒ **"Elo 单调上升"不成立** |
| ② 2+2 头对头（每代 800 场，顺位点配对） | `g01/g02/g03/g04 − teacher` = **+2.33 / −0.02 / +2.47 / +0.83**，95%CI **全部跨 0**（p 0.157~0.750）⇒ 与 `teacher` 仍**分不出胜负** |
| ③ 对脚本基线不掉 | `first` θ=−2.516 / `random` θ=−2.566（vs 网络 ≥ +1.02、teacher +0.840）⇒ **没有过拟合到自己策略的迹象** ✓ |
| 为什么没涨（可测） | 每代只改 **~2.5%** 决策（相邻代贪心一致率 0.973~0.975），四代累计 4.9%；训练日志 **KL≈0.006/代**（早停阈值 0.03 未触发）⇒ 步长太小，Elo 在 1200 场/代下分辨不出 |
| 副产品：**判据① 价值校准** | 在线价值头**四代单调变好**：MAE 5.750→5.403（全部 < 常数基线）、逐点 ρ 0.599→**0.723**、小局级 ρ 0.663→**0.765** ⇒ **首次三项全过**（P3 的 `iql-004` 逐点 MAE 6.399 > 6.241 不过） |
| ⚠ 配额副作用 | 四代紧凑集把 `compact` 顶过 10 GB ⇒ **`compact/rl-001` 被滚动淘汰**（`raw` 三个来源还在）。已把 `compact` 配额提到 **50 GB** 并重建成功（重建结果逐项一致） |

**第二轮（`ppo2`：把步长提上去 —— `lr` ×3、`epoch` ×2，其余全同）**：

| 项 | 结果 |
| --- | --- |
| **每代位移**（`online displace`） | 4.83% / 5.59% / 5.00% / 5.19%（第一轮 2.68/2.56/2.66/2.42%）⇒ **约 2 倍**；首→末累计 **9.49%**（从 `awr-002` 算 13.9%） |
| KL 早停 | **每代都触发**（5/3/4/3 epoch）⇒ 步长上去后**信任域成了限制**（第一轮四代都没触发、KL≈0.006） |
| 价值头 | MAE **5.17~5.44**（全部 < 常数基线）、逐点 ρ 0.682→0.748、小局级 ρ 0.711→**0.779** |
| **判据① Elo**（两轮 9200 场联合 PL 拟合 + 配对自助法） | ✅ **第二轮内部单调上升成立**：四代都显著高于 `teacher`（+0.150 / +0.334 / +0.333 / +0.371），**`g04−g01 = +0.221`（CI [+0.075,+0.358]，p=0.010）**；第一轮同口径只有 +0.058（p=0.51）⇒ **步长是第一轮的限制因素**，已修 |
| **第二轮 vs 第一轮** | ⚠ **打平（且 CI 把"强过 +1.6"排除）**：2+2 合并 n=5100 得 Δ=−0.193（CI [−1.62,+1.30]，p=0.183）、θ 差 +0.139（p=0.110）⇒ **位移翻倍 ≠ 强度提升** |
| **判据③ 对脚本基线** | ✅ 不掉：`first` −3.416 / `random` −3.470（相对 `teacher`，CI 全排除 0） |
| **判据② 2+2 头对头** | 🏁 **成立**：`ppo2-g04 − teacher` 合两批独立 run（1500+3600=5100 场）得 **Δ=+2.290，95%CI [+0.85,+3.71]，符号检验 p=0.006**（run 级随机效应 τ=0.00，CI [+0.85,+3.73]）⇒ **本项目第一次有策略在头对头上显著超过老师**（此前最好一次 `hybrid@1 − teacher` 是 +1.20 / p=0.977 打平） |

⛔ **存储事故（2026-09-25 05:05）→ ✅ 当日恢复**：第二轮的判据评测在写轨迹时报
`java.nio.file.AccessDeniedException`、随后读报 `ERROR_IO_DEVICE`，再往后 **`S:` 与 `E:` 两个卷一起
从系统里消失**（只剩 C:/D:）——**同一个物理设备掉总线**，不是沙箱权限。重新插拔后四卷齐备：
**全树 55,894 个文件逐个读头、读失败 0**，原先报错的文件解析结果与控制台日志逐项一致 ⇒ **数据没坏**；
恢复次序 = 写探针 → 删掉中断的半截 run → 60 场烟雾 + `selfplay-check` DATASET PASS → 再开评测。
配额同时按指示再调到 **`compact ≤ 100 GB`**（`paths.QUOTA_GB`，自检钉着这个数）。

- ✅ **P0 评测口径**：服务端 `SelfPlay` 的顺位点指标（`avg_rank_points` / `per_game[].rank_points`）

  + `mahjong_ml/eval.py`（配对显著性）+ `paths.py` / `guard.py` 两条纪律。
- ✅ **P1 行为克隆冒烟**：`features.py`（唯一规格）/ `dataset.py`（按整场切分，内存映射）/
  `nets.py`（候选打分头）/ `bc.py`（封线程 + GPU 节流 + checkpoint 落 S 盘）。
  - **v1（只吃 obs 原始字段，539+88）**：val top-1 **0.616**（峰值 @ep40；随机 0.116 / 首合法基线 0.156），
    类型准确率 0.977（同粒度基线 0.755）；同 seed 两次训练 `model.pt` **sha256 相同**。
  - **v2（接上 Java 算的派生特征，607+96）**：val top-1 **0.795**（@ep60，nll 1.24 → 0.63）
    —— **同数据、同超参，唯一变量是特征：+0.176 绝对（+28%）**。
- ✅ **派生特征**：`server/.../ai/ObsFeatures.java`（权威实现）+ `--features` 富化 CLI（写 sidecar，
  轨迹契约不变）。两侧由 `SelfTest.obsFeaturesTests`（obs 通路 == Round 通路 + 红证，连抓 3 个真 bug）
  与 `python/selfcheck.py`（sidecar 格式契约、缺 sidecar 必须报错）钉住。
- ✅ **P2 DAgger 一轮（结论：无可测增益，已裁定）**：`--teacher-label`（学生座位额外记老师动作）+
  `dagger.py`（采集→富化→校验→受控建集→重训→**多臂对比**）。一轮实测（学生状态 / 老师状态的无泄漏评测集）：

  | 臂 | 训练行数 | 学生状态 top-1 | 老师状态 top-1 |
  | --- | --- | --- | --- |
  | 基线 `bc-002` | 250,000（BC，建集时截断） | 0.7893 | 0.7924 |
  | DAgger `bc-003` | 441,981（BC + 学生轨迹） | 0.8602 | 0.8564 |
  | **配量对照 `bc-005`** | **441,981**（BC + 纯 teacher，`--control-max-decisions` 裁到同量） | **0.8653** | **0.8607** |
  | 未配量对照 `bc-004` | 478,041（多 8.2%，仅参考） | 0.8676 | 0.8691 |

  **Δ dagger − control（严格同量）= −0.0051，95% CI −0.0127..+0.0019（跨 0）→ 没有可测增益。**
  ⚠ 口径教训：未配量那版的 −0.0074（CI 不含 0）**不能**读成"DAgger 有害" —— 那对照臂多训了 8.2% 行。
  主因是**数据量**（BC-only 250k → 0.789、442k → 0.865、478k → 0.868）；与学生分布本来就贴近老师
  （基线在学生状态上只掉 0.003）一致 —— **不再迭代 DAgger**，优先加数据（纯 teacher 轨迹最便宜）、
  加容量/正则，或 P5b 混合（有保底）。
- ✅ **P5b 混合（teacher 先验）已落地**：策略串 `net:<net.bin>@<α>`（`PROTOCOL.md` §8.4），
  α 用 `python -m mahjong_ml.hybrid` 的"让位曲线"来选；`SelfTest.hybridPolicyTests` 钉住
  **上位集合性质**（α 极大 ⇒ 整局决策序列与 teacher 完全一致）。配对实战一轮：
  `hybrid@1` − 纯网 = +1.95 顺位点（CI [−0.21,+4.10]，p=0.045）、`hybrid@1` − teacher = +1.20
  （CI [−1.76,+4.12]，p=0.977 打平）→ **机制可证安全，强度效应未确立**（天花板 = teacher）。
  → 混合留作在线 RL 的**保底**；继续提强度要走 **P3（价值/胜负信号）**。
- ⏳ **下一步**：**P4 在线自对弈（PPO）第一轮已落地并给出结论**（见 §四·九）——
  **Elo 没有单调上升**（四代不可区分），**与 `teacher` 的 2+2 头对头仍分不出胜负**，
  但**在线价值头四代单调变好且首次过判据①**。下一轮的顺序：**先量策略位移**（一致率矩阵，30 秒）
  → 再定 `lr`/epoch/代数（现在每代只改 2.5% 决策，加代数只是给噪声加样本）。
  **P5 联赛**仍只是最小可用子集（对手池 + 加权采样 + Plackett-Luce Elo），
  PSRO-lite 的"最佳响应 + 多样性下限"还没做。
- ✅ **B 形态（进程内推理）**：`export.py` 导出纯 Java 可读的二进制权重，
  服务端 `--policy net:<net.bin>` 直接让网络打（零 socket、零第三方依赖）：

  ```powershell
  python\.venv\Scripts\python.exe -m mahjong_ml.export weights `
      --ckpt S:\mahjong-training\ckpt\bc-002\model.pt --out S:\mahjong-training\ckpt\bc-002\net.bin
  java -Dstdout.encoding=UTF-8 -jar server\build\mahjong-server.jar --selfplay 24 --workers 24 --rotate `
       --policy "net:S:\mahjong-training\ckpt\bc-002\net.bin,teacher,teacher,teacher" `
       --seed 4242 --out S:\mahjong-training\raw\net-smoke
  ```

  一致性由 **golden 夹具**钉住：`python -m mahjong_ml.export golden --trace <dir> --out python\tests\golden\forward.bin`
  生成（小网络 + 真实 obs + Python 侧 state/cand/logits），`SelfTest.neuralForwardTests` 读它逐元素对拍
  （<1e-4）+ 红证。⚠ 实测：网络打一场约比 teacher 慢 **3 倍**（推进它是纯 Java 手写 + 每候选都要算派生量），
  所以大规模自对弈要按 `~0.7 场/秒`（24 workers）规划。
