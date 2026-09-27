"""P4 · **按时间划分"一轮"**：把"每轮采多少场"从常数改成**时间预算**（`docs/TRAINING.md` §4 P4）。

用户口径（2026-09-27）："每轮训练按时间划分，记 30 分钟左右的训练为一轮（上下浮动是因为数据生成
与训练学习的循环的对齐需求）"。`--gen-games 2000` 是**拍出来的常数** —— 换了机器、改了 `--workers`
或 `--epochs`，一轮就可能是 3 分钟也可能是 15 分钟，"一轮"作为**迭代单位**不再可比。
改成 `--target-minutes 30` 之后：

    这一轮的场次 = 时间预算反推的场次 ∩ 资源闸门允许的场次

资源闸门（见 `gates_now`）：`raw` 配额余量 · `compact` 配额余量 · **总配额余量**（200 GB）· 整盘余量
（留 `paths.MIN_FREE_GB`）· `--max-games`（RAM/安全兜底）。取最紧的那个，并**在报告里写清是谁限制的**
——"跑了 12 分钟就收工"必须是可读的结论，不能是"配额悄悄满了"。

## 成本模型（两个参数，且**必须实测回填**）

    t(一轮) ≈ fixed + per_game × 场次
    fixed     每轮与场次无关的开销（进程启动/torch import/配额目录遍历）—— 缺省取实测常数
    per_game  秒/场 —— **从台账实测回填**（`fit_cost`：≥2 个不同场次就两参数最小二乘）

⚠ **别把 `per_game` 写成"离线标定的常数"** —— 那正是"每代固定场次"的翻版，换机器就错。
⚠ **纯比例模型（`t = per_game × g`）在放大轮次时会系统性高估**：2026-09-27 实测第一轮
（2000 场 / 288 s）里分相之和只有 236 s，**52 s 是与场次无关的开销**；把它摊进 `per_game`
后拿去做 6 倍大的轮次，会高估 ≈20%（30 分钟被算成 36 分钟）。所以固定项要**单独列出来**。
`DEFAULT_PER_GAME` / `DEFAULT_FIXED_SECONDS` / `DEFAULT_*_BYTES_PER_GAME` **只用于还没有任何实测的第一次**
（2026-09-27 v3 谱系实测：177.6 s / 2000 场、分相之和 ~176 s ⇒ fixed ≈50 s；raw 1.30 MB/场、
compact 2.79 MB/场）。

同理，磁盘那两个 `bytes_per_game` 也从台账回填（每轮记 `raw_bytes` / `compact_bytes`）——
它们随"每场决策数""是否截断 `--max-decisions`"而变，写死同样会漂。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from . import paths

#: 一轮的分相（与 `online.cmd_run` 的落账字段一一对应）
PHASES = ("collect", "features", "compact", "ppo", "export")

#: 默认秒/场 —— **只用于还没有任何实测的第一次**。
#: 2026-09-27 冒烟实测：一轮 2000 场共 **288 s**，其中 52 s 是与场次无关的开销 ⇒
#: `(288 − 50) / 2000 = 0.1184 s/场`（分相：采集 34 s / 特征 8 s / 紧凑 44.6 s / **PPO 142.6 s** / 导出 6.7 s）。
#: ⚠ 它**明显大于**文档里那批老台账反推的 0.0638 —— 原因是分相口径：老记录里的 PPO 是"策略+价值"的
#: 净时长（69–79 s），而现在一轮的 PPO 含 `冻结一遍`（32 s）与 val 价值报告（13.9 万条）共 142.6 s。
#: 这条差异本身就是要"实测回填"的证据（见 NOTES §6.5）。
DEFAULT_PER_GAME = 0.1184

#: 默认**每轮固定开销**（秒）：进程启动 + torch import + 配额目录遍历 + 台账 I/O 等与场次无关的部分。
#: 两处独立实测都落在这个量级（P5 那代：235.9 s 总时长 − 分相 176 s ≈ 50 s；2026-09-27 冒烟：
#: 288 s − 236 s = 52 s）⇒ 取 50 s。⚠ 别把它并进 `per_game`（见模块 docstring 的高估警告）。
DEFAULT_FIXED_SECONDS = 50.0

#: 默认分相占比（2026-09-27 冒烟第 2 轮实测：18.3 / 4.4 / 26.3 / 83.7 / 3.1 s）——
#: **只用于把预测总时长拆开显示**，不参与选场次。⚠ 它与老文档里那批"每代 3.6 分钟"的占比不同：
#: 那时 PPO 只算"策略+价值"的净时长，现在一轮的 PPO 含 `冻结一遍` 与 val 价值报告 ⇒ **PPO 占六成**。
DEFAULT_PHASE_SHARE = {
    "collect": 0.1348,     # 自对弈采集（含 JVM/C++ 进程）
    "features": 0.0323,    # 派生特征
    "compact": 0.1938,     # 建紧凑集（并行，24 worker）
    "ppo": 0.6161,         # PPO 4 epoch（含冻结一遍 + 价值头报告）← **现在的大头**
    "export": 0.0230,      # 导权重（采集前一次 + 收尾一次）
}

#: 默认每场落盘字节 —— **2026-09-27 实测（v3 谱系 g05–g08 四代均值，`fit_disk` 回填口径）**：
#: raw **1.52 MB/场**、compact **3.06 MB/场**（旧值 1.30/2.79 是更早那批 688 决策/场的账；
#: 现在 750+ 决策/场 ⇒ 每场更大）。⚠ 这两个数直接决定**缓存档上限**能排多少场：
#: 取小了会把轮次排进磁盘档（实测差 10% 就够越档），所以要用最近的实测。
DEFAULT_RAW_BYTES_PER_GAME = 1.52e6
DEFAULT_COMPACT_BYTES_PER_GAME = 3.06e6

#: **整盘余量**闸门的安全系数：物理上限只按余量的 90% 排计划（写满盘会让采集**静默失败**）。
#: ⚠ 分项配额 / 总配额**不打折**：它们是**策略上限**，超出的部分下一次 `paths.allocate` 会按"最旧的先删"
#: 淘汰掉（那正是配额的本意），而给策略上限再打九折等于把配额悄悄改小。
FREE_SPACE_SAFETY = 0.9

#: **缓存档**：一轮的紧凑集要装得进内存才便宜（PPO 要把数据读 5 遍：冻结一遍 + 4 epoch）。
#: 实测（2026-09-27，32 GB 机器）：紧凑集 **27.2 GB → 一轮 12.1 分钟**（缓存档，PPO 4.0 分钟）；
#: **41.78 GB → 一轮 56.8 分钟**（磁盘档，PPO 46.4 分钟，S 盘 ~120 MB/s）。拐点夹在两者之间，
#: 所以取 **0.8 × 物理内存** 作上限 —— 刻意压在"已验证走通"的那一档上（0.8×32 GB ≈ 27.5 GB）。
CACHE_FRACTION = 0.8

#: 探测不到物理内存时的兜底（本机实测 32 GB）。
DEFAULT_RAM_GB = 32.0


def total_ram_gb() -> float | None:
    """物理内存总量（GB）。取不到返回 `None`。

    ⚠ 为什么是 `ctypes`：本机沙箱**拒 `Get-CimInstance`**（"拒绝访问"），而 `psutil` 不是这个仓库的依赖。
    `GlobalMemoryStatusEx` 是纯查询、不需要管理员权限，Linux 侧走 `sysconf`。
    """
    try:
        if os.name == "nt":
            import ctypes

            class _MemStatusEx(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MemStatusEx()
            st.dwLength = ctypes.sizeof(_MemStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys / 1024**3
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
    except Exception:                                     # noqa: BLE001
        return None


def cache_budget_gb(cache_gb: float = 0.0) -> float:
    """这一轮**允许占用的缓存**（GB）：显式值优先，否则 `CACHE_FRACTION × 物理内存`。

    `cache_gb < 0` = 关掉这道闸门（故意跑磁盘档时用）。
    """
    if cache_gb < 0:
        return -1.0
    if cache_gb > 0:
        return cache_gb
    return (total_ram_gb() or DEFAULT_RAM_GB) * CACHE_FRACTION

_GB = 1024.0 ** 3


class ResourceError(RuntimeError):
    """资源闸门连 `--min-games` 都供不起（配额满/盘满）—— 要人先清理或抬配额，不许静默缩小轮次。"""


@dataclass(frozen=True)
class Cost:
    """时间成本模型。

    两档：
      · **有 ≥1 个实测点**：`predicted(g)` = 实测点 `(场次, 总时长)` 上的**分段线性（弦）**，
        点外用端点斜率外推 —— 见 `predict`/`games_for`。
      · **没有实测点**：`fixed + rate × 场次`（默认值）。
    `rate` 一直是"最近一段的边际秒/场"（报告里显示用）。
    """

    fixed_seconds: float
    rate: float
    share: dict[str, float]
    rounds: int
    fit: str = "默认值"
    points: tuple[tuple[int, float], ...] = ()      # ((场次, 总时长秒), …) 按场次升序

    # ---- 分段线性：实测点之间插值，点外用该端斜率外推 ----------------------------------
    def _segments(self) -> list[tuple[float, float, float]]:
        """`[(g0, t0, 斜率), …]`；没有实测点就退化成"单点 + 默认比例"。"""
        if not self.points:
            return []
        segs: list[tuple[float, float, float]] = []
        for (g0, t0), (g1, t1) in zip(self.points, self.points[1:]):
            segs.append((float(g0), float(t0), (t1 - t0) / (g1 - g0)))
        return segs

    def predict(self, games: int) -> float:
        pts = self.points
        if not pts:
            return self.fixed_seconds + self.rate * games
        if games <= pts[0][0]:
            # 比最小实测点还小：**按比例缩**（`fixed + (t_min−fixed)·g/g_min`）。
            # ⚠ 别用第一段斜率往下推：数据超过内存那一侧的第一段斜率很陡（0.39 s/场），
            # 往下推会在 g≈4.6k 处算出**负时长** ⇒ 小目标会被排成一个大轮（危险方向）。
            return max(0.0, self.fixed_seconds
                       + (pts[0][1] - self.fixed_seconds) * games / pts[0][0])
        for (g0, t0), (g1, t1) in zip(pts, pts[1:]):  # 落在两个实测点之间
            if games <= g1:
                return t0 + (t1 - t0) * (games - g0) / (g1 - g0)
        g1, t1, k = (pts[-1][0], pts[-1][1],
                     self._segments()[-1][2] if len(pts) > 1 else self.rate)
        return t1 + k * (games - g1)                  # 比最大实测点还大：用最后一段斜率外推（偏保守）

    def games_for(self, target_seconds: float) -> int:
        """反解 `predict(g) = target`（返回**未取整**的实数场次）。"""
        pts = self.points
        if not pts:
            return max(0.0, (target_seconds - self.fixed_seconds) / self.rate)
        if target_seconds <= pts[0][1]:
            denom = max(1e-9, pts[0][1] - self.fixed_seconds)
            return max(0.0, (target_seconds - self.fixed_seconds) * pts[0][0] / denom)
        for (g0, t0), (g1, t1) in zip(pts, pts[1:]):
            if target_seconds <= t1:
                return g0 + (target_seconds - t0) * (g1 - g0) / (t1 - t0)
        g1, t1, k = (pts[-1][0], pts[-1][1],
                     self._segments()[-1][2] if len(pts) > 1 else self.rate)
        return g1 + (target_seconds - t1) / k

    def source(self) -> str:
        if self.rounds <= 0:
            return "**默认值**（台账里还没有带 total_seconds 的轮次）"
        pts = " · ".join(f"{g}→{t/60:.1f}min" for g, t in self.points)
        if len(self.points) >= 2:
            return (f"实测点**分段线性**（{self.rounds} 轮：{pts}）——"
                    f"点外按端点斜率外推，边际 {self.rate:.4f}s/场")
        return (f"{self.fit}（{self.rounds} 轮：{pts}）：固定 {self.fixed_seconds:.0f}s"
                f" + {self.rate:.4f}s/场")


@dataclass(frozen=True)
class Disk:
    """落盘成本模型（字节/场；`rounds` = 标定用了几个历史轮）。"""

    raw_bytes_per_game: float
    compact_bytes_per_game: float
    rounds: int

    def source(self) -> str:
        return (f"实测回填（{self.rounds} 轮）" if self.rounds else "**默认值**")


@dataclass(frozen=True)
class Gate:
    """一条资源闸门：最多还能采多少场。"""

    name: str
    games: int


@dataclass(frozen=True)
class Plan:
    """一轮的场次计划。"""

    games: int
    seconds: float
    by_time: int
    gates: tuple[Gate, ...]
    clamped_by: str | None          # 实际最紧的闸门名（None = 时间预算自己最紧）


# ------------------------------------------------------------------ 标定（从台账回填）

def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else float(v)


def fit_cost(log: list[dict]) -> Cost:
    """从台账标定 `(fixed, per_game)` + 分相占比。没有可用轮次 → 默认值（`rounds=0`）。

    - **≥2 个不同场次**：两参数普通最小二乘（`t = a + b·g`）；`b ≤ 0`（数据太少/太噪）退回下面那条。
    - **只有一个场次**：固定项取实测常数 `DEFAULT_FIXED_SECONDS`，`per_game = (Σt − fixed·轮数) / Σ场次`
      —— 这样模型能**逐字复现**那一轮的实测总时长（自检钉着这条）。

    只用**带 `total_seconds` 且 `games > 0`** 的行：老台账只有 `collect_seconds`（单相），
    拿它当整轮时长会**低估**到 1/4 —— 那比用默认值更糟（`rounds` 会诚实地报 0）。
    """
    pts: list[tuple[int, float]] = []
    acc = dict.fromkeys(PHASES, 0.0)
    for r in log:
        g = int(_num(r.get("games")) or 0)
        t = _num(r.get("total_seconds"))
        if g <= 0 or t is None or t <= 0:
            continue
        pts.append((g, t))
        ph = r.get("phases") or {}
        for k in PHASES:
            acc[k] += _num(ph.get(k)) or 0.0
    if not pts:
        return Cost(DEFAULT_FIXED_SECONDS, DEFAULT_PER_GAME, dict(DEFAULT_PHASE_SHARE), 0)
    share_total = sum(acc.values())
    share = {k: (acc[k] / share_total if share_total > 0 else DEFAULT_PHASE_SHARE[k])
             for k in PHASES}
    pts.sort()
    #: 实测点**原样留着**（分段线性用它们）；`rate` 取最近一段的边际秒/场（显示用）
    margin = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0]) if len(pts) >= 2 else \
        max(0.0, (pts[0][1] - DEFAULT_FIXED_SECONDS) / pts[0][0])
    fit = ("实测点分段线性" if len(pts) >= 2 else "单场次实测（固定项取实测常数）")
    # `fixed` 一律保留实测常数：多点时它**不参与**弦（弦在实测点上逐点成立），只用于
    # "比最小实测点还小"那条比例外推（那里必须有固定开销，否则 t(0) 会是负数）。
    return Cost(DEFAULT_FIXED_SECONDS, margin, share, len(pts), fit, tuple(pts))


def fit_disk(log: list[dict]) -> Disk:
    """从台账标定字节/场（raw / compact 各一个）。没有可用轮次 → 默认值（`rounds=0`）。"""
    g = 0
    rb = cb = 0.0
    used = 0
    for r in log:
        n = int(_num(r.get("games")) or 0)
        a = _num(r.get("raw_bytes"))
        b = _num(r.get("compact_bytes"))
        if n <= 0 or (a is None and b is None):
            continue
        g += n
        rb += a or 0.0
        cb += b or 0.0
        used += 1
    if g <= 0:
        return Disk(DEFAULT_RAW_BYTES_PER_GAME, DEFAULT_COMPACT_BYTES_PER_GAME, 0)
    return Disk((rb / g) or DEFAULT_RAW_BYTES_PER_GAME,
                (cb / g) or DEFAULT_COMPACT_BYTES_PER_GAME, used)


# ------------------------------------------------------------------ 资源闸门

def _floor100(x: float) -> int:
    """向下取到百场（台账里是可读的整数）。⚠ 不足 100 场不取整 —— 冒烟与自检会用到小数字。"""
    n = int(x)
    return n if n < 100 else (n // 100) * 100


def gates_now(disk: Disk, *, safety: float = FREE_SPACE_SAFETY,
              cache_gb: float = 0.0) -> tuple[Gate, ...]:
    """**当前**资源还能供多少场（每条闸门一个数）。单位：字节 → 场。

    五道闸门，判据取**最紧**的那条：
      · **缓存档上限**（`cache_gb`，缺省 `CACHE_FRACTION × 物理内存`）——**用户口径：一轮只跑缓存档**；
      · `raw` / `compact` 分项配额余量；· **总配额**（200 GB）余量；· 整盘余量（留 `MIN_FREE_GB`）。
    ⚠ 配额类闸门按**全额**算、整盘余量按 `safety`（默认 90%）算 —— 理由见 `FREE_SPACE_SAFETY`。
    配额类闸门是"这一轮能把 `compact/` 写到多满"：写到配额上限不是错误，下一次 `allocate` 会淘汰最旧的。
    ⚠ **可回收的旧代也算可用空间**（`paths.reclaimable_bytes`）：`allocate` 现在会**预扣**回收，
    所以"余量 + 可回收"才是这一轮真能用的量。不算它的话，规划会永远卡在"余量只剩 8 GB"，
    而前面明明躺着十几 GB 的旧代等着被回收。
    ⚠ **缓存档上限为什么是硬闸门**：越过它就从"缓存档（≈12 分钟/轮）"掉进"磁盘档（≈45+ 分钟/轮）"，
    中间没有过渡 —— 那是**读盘遍数**决定的（PPO 把数据读 5 遍），不是场次能微调的。
    """
    out: list[Gate] = []
    cap = cache_budget_gb(cache_gb)
    bpg_c = disk.compact_bytes_per_game
    if cap > 0 and bpg_c > 0:
        out.append(Gate("缓存档上限", _floor100(cap * _GB / bpg_c)))
    for kind, bpg in (("raw", disk.raw_bytes_per_game), ("compact", bpg_c)):
        quota = paths.QUOTA_GB.get(kind)
        if quota is None or bpg <= 0:
            continue
        room = (quota * _GB - paths.dir_size_bytes(paths.DATA_ROOT / kind)
                + paths.reclaimable_bytes(kind))
        out.append(Gate(f"{kind} 配额余量", _floor100(max(0.0, room) / bpg)))
    # 总配额 / 整盘余量：这一轮的 raw+compact 会**同时**存在 ⇒ 按两者之和算
    both = disk.raw_bytes_per_game + disk.compact_bytes_per_game
    if both > 0:
        # 可回收（两类都能预扣回收）也算可用 —— 与分项闸门同一口径
        rec = (paths.reclaimable_bytes("raw") + paths.reclaimable_bytes("compact")) / _GB
        room = (paths.TOTAL_QUOTA_GB - paths.total_gb() + rec) * _GB
        out.append(Gate("总配额余量", _floor100(max(0.0, room) / both)))
        room = (paths.free_gb() - paths.MIN_FREE_GB) * _GB
        out.append(Gate("盘余量", _floor100(max(0.0, room) * safety / both)))
    return tuple(out)


# ------------------------------------------------------------------ 计划

def plan_round(target_seconds: float, cost: Cost, gates: tuple[Gate, ...], *,
               min_games: int = 400, max_games: int = 24000) -> Plan:
    """时间预算反推场次 → 被资源闸门夹逼 → 得这一轮的场次。

    @param target_seconds 目标时长（秒）
    @param min_games 小于它就不值得跑一轮（资源供不起 → `ResourceError`，**不许静默缩小**）
    @param max_games RAM/安全兜底（它也是一条闸门，会出现在报告里）
    @raise ResourceError: 最紧的闸门 < `min_games`
    """
    if cost.rate <= 0 and not cost.points:
        raise ValueError("成本模型非法：rate <= 0 且没有实测点")
    # 反解 `t(g) = target`：有实测点走分段线性，否则 fixed + rate·g
    by_time = _floor100(cost.games_for(target_seconds))
    allg = (Gate("时间预算", by_time), Gate("--max-games", max_games)) + tuple(gates)
    tight = min(allg, key=lambda gt: gt.games)
    games = max(0, min(gt.games for gt in allg))
    if games < min_games:
        why = "；".join(f"{gt.name} → {gt.games} 场" for gt in allg)
        raise ResourceError(
            f"时间预算 {target_seconds/60:.1f} 分钟最紧只允许 {games} 场（< --min-games {min_games}）：{why}")
    return Plan(games=games, seconds=cost.predict(games), by_time=by_time,
                gates=tuple(g for g in allg if g.name != "时间预算"),
                clamped_by=None if tight.name == "时间预算" else tight.name)


def format_plan(plan: Plan, cost: Cost, target_seconds: float, *, generation: int | None = None) -> str:
    """把计划写成**可读的结论**（谁在限制、预计多久、分相怎么分）。"""
    head = f"⏱ 第 {generation} 轮" if generation else "⏱ 本轮"
    diff = plan.seconds - target_seconds
    lines = [f"{head}按时间预算（目标 {target_seconds/60:.1f} 分钟）：",
             f"   成本模型 {cost.source()}",
             f"   时间预算 → {plan.by_time} 场",
             "   资源闸门 → " + " · ".join(f"{g.name} {g.games} 场" for g in plan.gates)]
    if plan.clamped_by:
        lines.append(f"   ⇒ 取 {plan.games} 场（**受 {plan.clamped_by} 限制**），"
                     f"预计 {plan.seconds/60:.1f} 分钟（比目标{'少' if diff < 0 else '多'} "
                     f"{abs(diff)/60:.1f} 分钟）")
    else:
        lines.append(f"   ⇒ 取 {plan.games} 场（时间预算内），预计 {plan.seconds/60:.1f} 分钟")
    lines.append("   预计分相：" + " · ".join(
        f"{k} {plan.seconds*cost.share[k]/60:.1f}" for k in PHASES) + " 分钟")
    return "\n".join(lines)
