"""训练准备的两套硬闸门（规范 §1.4 / 设计 §7.4 §7.5 §8.5）：

1. **封闭红证**（`closure_proofs`）：标签不进输入、框外量不进奖励 —— 都做成**可执行断言**，
   而不是"读代码确认"。
2. **场外均衡审计**（`balance_report`）：座位 / 亲家 / 局 / 对手配额 / 结局类型逐因素计数，
   偏差超阈（离散因素 1%、对手配额 CV 5%）就判该轮**不得用于判据**。

⚠ 两个都**不训练**、不写 S 盘以外的东西；`balance_report` 只读轨迹目录。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .. import features as f3
from . import blocks, spec
from .spec import ContractError

#: 均衡阈值（设计 §7.5）
SEAT_TOL = 0.01
OPP_CV_TOL = 0.05
#: 奖励路径里**绝不许出现**的隐藏量标识（红证③的机器判据）。
#: ⚠ 只收**只可能指隐藏量**的标识符：裸 `wall` 会撞上 `wall = time.perf_counter()`（墙钟计时），
#: 那种误伤会让扫描器变成噪声源（第一次跑就被它咬过）。
FORBIDDEN_REWARD_PATTERNS = (
    r"\bura\b", r"uraIndicators", r"ura_indicators",
    r"wallOrder", r"wall_order", r"wallTiles", r"debugAllTiles",
    r"opp_hand", r"opp_tenpai", r"otherHand", r"other_hand",
    r"furitenTemp", r"furitenPerm", r"furiten_temp", r"furiten_perm",
)


def reward_purity_scan(*, text: str | None = None, name: str = "<inline>") -> tuple[bool, list[str]]:
    """红证③：奖励/优势的计算路径里不许读**过程隐藏量**（设计 §7.4）。

    `text` 给了就只扫这段（**负向对照**用：塞一个 `opp_tenpai` 进去必须被抓出来）。
    """
    import re
    targets: list[tuple[str, str]] = []
    if text is not None:
        targets.append((name, text))
    else:
        for p in _reward_sources():
            if p.is_file():
                targets.append((p.name, p.read_text(encoding="utf-8")))
    bad: list[str] = []
    for fname, body in targets:
        for pat in FORBIDDEN_REWARD_PATTERNS:
            for m in re.finditer(pat, body):
                line_no = body[:m.start()].count("\n") + 1
                bad.append(f"{fname}:{line_no} {pat}")
    return not bad, bad


def _reward_sources() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    return [root / "rewards.py", root / "ppo.py"]


def aux_separation_scan() -> tuple[bool, list[str]]:
    """判据⑥：推理路径（`v4/` 的前向链）不许碰 `aux` 标签文件。

    ⚠ 扫的是 `spec/blocks/cache/model/cli`——**不含 `harness.py`**（它自己是扫描器，
    代码里当然写着 `.npz` / `aux` 这些词）。
    """
    bad: list[str] = []
    for name in ("spec.py", "blocks.py", "cache.py", "model.py", "cli.py"):
        p = Path(__file__).resolve().parent / name
        if not p.is_file():
            continue
        for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            if "aux" not in low:
                continue
            if any(k in line for k in ("不许", "绝", "≠", "not", "never")):
                continue                      # 注释里在说"不许"
            if any(k in low for k in (".npz", "open(", "load(", "read_text")):
                bad.append(f"{name}:{line_no} {line.strip()[:80]}")
    return not bad, bad


def closure_proofs(obs: Mapping[str, Any], sidecar: Mapping[str, Any] | None = None, *,
                   seeds: int = 3) -> dict[str, Any]:
    """跑封闭红证（① 标签不进输入；③ 奖励不含隐藏量；⑥ 标签与推理物理分离）。

    红证①的判据是**逐位不变**：同一个 `obs`，带不带"隐藏真值标签"算出来的 logits 必须完全相同
    —— 因为拼装与模型**根本没有**读标签的入口。
    """
    import torch

    from . import model as M

    t = blocks.assemble(obs, sidecar, allow_degraded=True)
    m = M.build(seed=7)
    x = {k: torch.tensor(v, dtype=torch.float32).unsqueeze(0) for k, v in t.as_dict().items()}
    with torch.no_grad():
        a = m(**x)["policy"]
    # 让"标签"以最容易被误用的方式存在：直接挂在 obs / sidecar 上
    obs_with_labels = dict(obs)
    obs_with_labels["aux_labels"] = {"opp_hand": [[1] * 34] * 3, "opp_tenpai": [1, 0, 1]}
    sc_with_labels = dict(sidecar or {})
    sc_with_labels["aux_labels"] = obs_with_labels["aux_labels"]
    t2 = blocks.assemble(obs_with_labels, sc_with_labels, allow_degraded=True)
    x2 = {k: torch.tensor(v, dtype=torch.float32).unsqueeze(0) for k, v in t2.as_dict().items()}
    with torch.no_grad():
        b = m(**x2)["policy"]
    identical = bool(torch.equal(a, b))
    reward_ok, reward_bad = reward_purity_scan()
    sep_ok, sep_bad = aux_separation_scan()
    return {"aux_logits_identical": identical,
            "reward_purity": reward_ok, "reward_violations": reward_bad,
            "aux_separation": sep_ok, "aux_violations": sep_bad,
            "degraded": sorted(t.degraded), "seeds": seeds}


# ------------------------------------------------------------------ 增量 == 全量
def incremental_proof(obs: Mapping[str, Any], *, atol: float = 1e-5) -> dict[str, Any]:
    """判据④：`EventStream` 增量推进 == 从头重算（张量级 + 表示级）。"""
    import torch

    from . import cache, model as M

    events = obs.get("events") or []
    st_ok, st_d = cache.incremental_equals_full(obs, atol=atol)
    m = M.build(seed=11)
    # ⚠ **座位要显式传**（`EventStream` 的默认 0 只适合"自己视角"的玩具 obs）：
    #   事件 token 里的 actor/from 是相对座位的 one-hot，写死 0 会让这条自证
    #   "自洽但与真实窗口不同源"（假绿）。
    stream = cache.EventStream(lambda tok, h: m.step_event(torch.tensor(tok, dtype=torch.float32)
                                                           .unsqueeze(0), h),
                               seat=int(obs.get("seat", 0)))
    for e in events:
        stream.push(e)
    h_inc = stream.step()
    h_ref = stream.recompute()
    if h_inc is None or h_ref is None:
        return {"tensor_ok": st_ok, "tensor_delta": st_d, "repr_ok": True, "repr_delta": 0.0}
    d = float((h_inc - h_ref).abs().max().item())
    return {"tensor_ok": st_ok, "tensor_delta": st_d, "repr_ok": d <= atol, "repr_delta": d}


# ------------------------------------------------------------------ 场外均衡
@dataclass
class BalanceReport:
    """一轮采集的**场外因素**计数 + 判据结论（设计 §7.5）。"""

    games: int = 0
    #: 小局（`hand` 行）数。⚠ `games` 与它同值：轨迹里"一场 = 一个 `g<序号>.jsonl`"，
    #: 而 `hand` 行记的是**小局** —— 两个口径在历史文档里混用过，这里两个都给出（`files` 才是场数）。
    rounds: int = 0
    policy_seat: dict[str, dict[int, int]] = field(default_factory=dict)
    #: 每个策略**占了多少"座位场"**（= 每局座位数 × 场数）—— 配席/对手配额的**正确单位**。
    #: ⚠ 别拿"决策行数"当配席：一个策略打多少手取决于它的行为（pass 多、和了少都不一样），
    #: 两个座位不等于两倍决策（实测 teacher 148,688 / net 76,206 ≈ 1.95 而非 2.00）。
    policy_seat_games: dict[str, dict[int, int]] = field(default_factory=dict)
    dealer: dict[str, int] = field(default_factory=dict)
    kyoku: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)
    decisions: int = 0
    files: int = 0
    #: `--expect` 的配席清单（策略 → 该轮占的座位数）；空 = 不核这一条
    expected: dict[str, int] = field(default_factory=dict)
    #: 只有当四个座位**本就该等分**时才用 CV 判对手配额（`A,B,C,D` 或 2+2 那种）
    equal_shares: bool = False

    # -- 判据 ------------------------------------------------------------
    def seat_deviation(self) -> float:
        """每个策略在 4 个座位上的最大相对偏差（理想各 1/4）。

        用**座位场**口径（不是决策行）：决策数受策略行为影响，会把"分配不均"与"打得多少"混在一起。
        """
        src = self.policy_seat_games or self.policy_seat
        worst = 0.0
        for pol, seats in src.items():
            total = sum(seats.values())
            if total == 0:
                continue
            for seat in range(4):
                worst = max(worst, abs(seats.get(seat, 0) / total - 0.25))
        return worst

    def opponent_cv(self) -> float:
        """各策略（当作彼此的对手）总座位场的变异系数 —— 对手配额是否受控。"""
        if self.policy_seat_games:
            vals = np.array([sum(s.values()) for s in self.policy_seat_games.values()], dtype=float)
        else:
            vals = np.array([sum(s.values()) for s in self.policy_seat.values()], dtype=float)
        if vals.size < 2 or vals.mean() == 0:
            return 0.0
        return float(vals.std(ddof=1) / vals.mean())

    def seat_games(self) -> dict[str, int]:
        """策略 → 座位场数（`--expect` 的同一单位）。"""
        if self.policy_seat_games:
            return {p: sum(s.values()) for p, s in self.policy_seat_games.items()}
        return {p: sum(s.values()) for p, s in self.policy_seat.items()}

    def verdict(self) -> tuple[bool, list[str]]:
        """判据（设计 §7.5）。⚠ **空数据必须判不均衡** —— 2026-09-27 首次跑真轨迹时，
        决策行识别写的是"没有 `type` 字段"，而真轨迹写 `"type":"decision"` ⇒ 一条决策都没统计到，
        `seat_deviation`/`opponent_cv` 全是 0 ⇒ 报了个**假绿**。现在把"有没有读到东西"也做成闸门。"""
        reasons: list[str] = []
        if self.rounds == 0:
            reasons.append("没有小局记录（跑的是评测采样轨迹？）")
        if self.decisions == 0:
            reasons.append("一条决策行都没有（轨迹形状不对：决策行读不出来）")
        if not self.policy_seat:
            reasons.append("没有任何 (策略, 座位) 记录 —— 座位/对手配额无从审计")
        sd = self.seat_deviation()
        if sd > SEAT_TOL:
            reasons.append(f"座位偏差 {sd:.3%} > {SEAT_TOL:.0%}")
        if self.expected:
            got = self.seat_games()
            bad = [f"{p}: {got.get(p, 0)} != {n}" for p, n in self.expected.items()
                   if got.get(p, 0) != n]
            extra = [f"{p}: {got[p]}（清单里没写）" for p in got if p not in self.expected]
            if bad or extra:
                reasons.append("与 --expect 的配席不符（座位场）：" + "；".join(bad + extra))
        if self.equal_shares:
            cv = self.opponent_cv()
            if cv > OPP_CV_TOL:
                reasons.append(f"对手配额 CV {cv:.1%} > {OPP_CV_TOL:.0%}")
        return not reasons, reasons

    def to_json(self) -> dict[str, Any]:
        ok, why = self.verdict()
        return {"games": self.games, "rounds": self.rounds, "files": self.files,
                "decisions": self.decisions,
                "seat_deviation": self.seat_deviation(), "opponent_cv": self.opponent_cv(),
                "policy_seat": self.policy_seat, "policy_seat_games": self.policy_seat_games,
                "seat_games": self.seat_games(),
                "dealer": self.dealer, "kyoku": self.kyoku,
                "outcomes": self.outcomes, "balanced": ok, "reasons": why,
                "expect": self.expected, "equal_shares": self.equal_shares,
                "thresholds": {"seat": SEAT_TOL, "opponent_cv": OPP_CV_TOL}}


def balance_report(run_dir: str | Path, *, limit_files: int | None = None,
                   expect: dict[str, int] | None = None,
                   equal_shares: bool = False) -> BalanceReport:
    """扫一轮采集的轨迹，统计场外因素（设计 §7.5 的表）。

    ⚠ 决策行的判据是 `type == "decision"`（**或没有 `type` 的老形状**）—— 2026-09-27 只认了后者，
    于是一整轮真轨迹被统计成"0 决策"，座位/对手配额全是 0、结论假绿。`verdict()` 现在把
    "读到东西了没有"也当闸门。

    @param expect 本轮**声明的配席**（策略 → 座位数，例如 `{"net:..": 1, "teacher": 2, "random": 1}`）；
        给了就逐项核对实际场次，配错即判不均衡
    @param equal_shares 四个座位本就该等分时才用 `opponent_cv` 判对手配额
        （`A,B,C,D` 或 2+2 配对）；1+2+1 这种**刻意不对称**的阵容不该被 CV 判红
    """
    rep = BalanceReport(expected=dict(expect or {}), equal_shares=bool(equal_shares))
    files = sorted(Path(run_dir).glob("g*.jsonl"))
    if limit_files:
        files = files[:limit_files]
    for f in files:
        rep.files += 1
        seat_of: dict[int, str] = {}
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = row.get("type")
                if typ in (None, "decision"):                    # 决策行（两种形状都认）
                    rep.decisions += 1
                    pol = row.get("policy")
                    seat = row.get("seat")
                    if pol is None or seat is None:
                        continue
                    seat_of[int(seat)] = str(pol)
                    rep.policy_seat.setdefault(str(pol), {}).setdefault(int(seat), 0)
                    rep.policy_seat[str(pol)][int(seat)] += 1
                elif typ == "hand":
                    rep.games += 1
                    rep.rounds += 1
                    rnd = row.get("round") or {}
                    kyoku = int(rnd.get("kyoku", 1))
                    rep.kyoku[f"{rnd.get('bakaze', '?')}{kyoku}"] = \
                        rep.kyoku.get(f"{rnd.get('bakaze', '?')}{kyoku}", 0) + 1
                    dealer_seat = (kyoku - 1) % 4          # 东1 庄家 = 0 号，依次轮转
                    pol = seat_of.get(dealer_seat)
                    if pol:
                        rep.dealer[pol] = rep.dealer.get(pol, 0) + 1
                    key = ("agari_tsumo" if row.get("tsumo") else
                           "agari_ron" if row.get("agari") else
                           "abortive" if row.get("abortive") else "ryuukyoku")
                    rep.outcomes[key] = rep.outcomes.get(key, 0) + 1
        # 一个文件 = 一场：把这一场四个座位上的策略记成"座位场"（配席的正确单位）
        for _seat, _pol in seat_of.items():
            rep.policy_seat_games.setdefault(_pol, {}).setdefault(_seat, 0)
            rep.policy_seat_games[_pol][_seat] += 1
    return rep


def write_balance(run_dir: str | Path, out: str | Path | None = None, **kw) -> Path:
    rep = balance_report(run_dir, **kw)
    out = Path(out) if out else Path(run_dir) / "balance.json"
    out.write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
