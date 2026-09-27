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
    stream = cache.EventStream(lambda tok, h: m.step_event(torch.tensor(tok, dtype=torch.float32)
                                                           .unsqueeze(0), h))
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
    policy_seat: dict[str, dict[int, int]] = field(default_factory=dict)
    dealer: dict[str, int] = field(default_factory=dict)
    kyoku: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)
    decisions: int = 0
    files: int = 0

    # -- 判据 ------------------------------------------------------------
    def seat_deviation(self) -> float:
        """每个策略在 4 个座位上的最大相对偏差（理想各 1/4）。"""
        worst = 0.0
        for pol, seats in self.policy_seat.items():
            total = sum(seats.values())
            if total == 0:
                continue
            for seat in range(4):
                worst = max(worst, abs(seats.get(seat, 0) / total - 0.25))
        return worst

    def opponent_cv(self) -> float:
        """各策略（当作彼此的对手）总场次的变异系数 —— 对手配额是否受控。"""
        vals = np.array([sum(s.values()) for s in self.policy_seat.values()], dtype=float)
        if vals.size < 2 or vals.mean() == 0:
            return 0.0
        return float(vals.std(ddof=1) / vals.mean())

    def verdict(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        sd = self.seat_deviation()
        if sd > SEAT_TOL:
            reasons.append(f"座位偏差 {sd:.3%} > {SEAT_TOL:.0%}")
        cv = self.opponent_cv()
        if cv > OPP_CV_TOL:
            reasons.append(f"对手配额 CV {cv:.1%} > {OPP_CV_TOL:.0%}")
        if self.games == 0:
            reasons.append("没有赛事记录（跑的是评测采样轨迹？）")
        return not reasons, reasons

    def to_json(self) -> dict[str, Any]:
        ok, why = self.verdict()
        return {"games": self.games, "files": self.files, "decisions": self.decisions,
                "seat_deviation": self.seat_deviation(), "opponent_cv": self.opponent_cv(),
                "policy_seat": self.policy_seat, "dealer": self.dealer, "kyoku": self.kyoku,
                "outcomes": self.outcomes, "balanced": ok, "reasons": why,
                "thresholds": {"seat": SEAT_TOL, "opponent_cv": OPP_CV_TOL}}


def balance_report(run_dir: str | Path, *, limit_files: int | None = None) -> BalanceReport:
    """扫一轮采集的轨迹，统计场外因素（设计 §7.5 的表）。

    ⚠ 只认 `type == "hand"` 的账目行做赛事/结局统计；决策行的 `policy` + `seat` 用来建
    "这一场哪个座位是谁"的映射（采集轨迹里策略是固定的 4 个 spec）。
    """
    rep = BalanceReport()
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
                if typ is None:                                  # 决策行
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
    return rep


def write_balance(run_dir: str | Path, out: str | Path | None = None) -> Path:
    rep = balance_report(run_dir)
    out = Path(out) if out else Path(run_dir) / "balance.json"
    out.write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
