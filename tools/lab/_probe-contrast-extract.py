# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""探针「同状态对比式优势 · 可学天花板」—— 抽取（零训练、只解析；口径 = PROBE-CONTRAST-PLAN.md §2/§3，跑前冻结）。

输入：两臂 raw 目录（同 seed、同对手、**只有席 0 的学生网不同**）。
输出（落 --out）：
  pairs.npz        S[N,Ds] / A0[N,Da] / A1[N,Da] / dy / y0 / y1 / sign / game / hand / step
  pairs.jsonl      逐配对行的非特征字段（game/hand_no/step/kind/nlegal/a0/a1/a0i/a1i/y0/y1/dy/sign/obs_hash）
  extract-meta.json 计数自证（逐场 seed 相同、同状态命中/丢局原因）+ 特征维度 + 臂策略串
⛔ 特征只来自**公开观测**白名单 + 两个候选动作的动作键；⛔ 不含 logits/value/门控、臂身份、hand 结果字段。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

TILE_RE = re.compile(r"[0-9][mpsz]")
PREFIXES = ["discard", "riichi", "pon", "chi", "kan", "pass", "ron", "tsumo"]
KAN_ALIAS = {"kan", "ankan", "kakan", "daiminkan", "shouminkan"}
MELD_KINDS = ["chi", "pon", "ankan", "kakan", "daiminkan"]


def tile_index(code: str):
    """牌码 → (0..33 下标, 是否赤五)。`0m/0p/0s` = 赤五（映射到 5）。"""
    n = int(code[0]); s = code[1]
    red = n == 0
    if red:
        n = 5
    return {"m": 0, "p": 9, "s": 18, "z": 27}[s] + n - 1, red


def obs_hash(obs) -> str:
    return hashlib.md5(json.dumps(obs, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def vec34(tiles) -> list:
    v = [0.0] * 34
    for t in tiles or []:
        v[tile_index(t)[0]] += 1.0
    return v


def state_feats(obs: dict) -> list:
    """该席的**合法公开观测** → 定长向量（字段序即口径，见 plan §3）。"""
    f: list = []
    f += [x / 4.0 for x in (obs.get("hand") or [0] * 34)[:34]]                    # 自家手牌
    d = [0.0] * 34
    if obs.get("drawn"):
        d[tile_index(obs["drawn"])[0]] = 1.0
    f += d                                                                        # 刚摸到的牌
    f += [x / 4.0 for x in (obs.get("visible") or [0] * 34)[:34]]                 # 未见面子（公开）
    disc = obs.get("discards") or [[], [], [], []]
    allv = [0.0] * 34
    for seat_disc in disc:
        for t in seat_disc or []:
            allv[tile_index(t)[0]] += 1.0
    f += [x / 4.0 for x in allv]                                                  # 四家牌河合计
    f += [1.0 if x > 0 else 0.0 for x in vec34(disc[0] if disc else [])]          # 自家牌河
    f += [x / 5.0 for x in vec34(obs.get("dora_indicators") or [])]               # 宝牌指示牌
    hr = obs.get("hand_red") or [False] * 34
    f += [1.0 if (len(hr) > k and hr[k]) else 0.0 for k in (4, 13, 22)]           # 自家赤五
    melds = obs.get("melds") or [[], [], [], []]
    f += [min(len(m or []), 4) / 4.0 for m in melds]                              # 四家副露数
    kc = Counter()
    for m in (melds[0] if melds else []) or []:
        if isinstance(m, dict):
            kc[str(m.get("kind", ""))] += 1
    f += [min(kc.get(k, 0), 4) / 4.0 for k in MELD_KINDS]                         # 自家副露种类
    f += [1.0 if obs.get(k) else 0.0 for k in
          ("menzen", "self_riichi", "furiten", "any_call", "haitei", "houtei", "rinshan")]
    sc = obs.get("scores") or [0, 0, 0, 0]
    s = int(obs.get("seat", 0))
    f += [(float(sc[k]) - float(sc[s])) / 25000.0 for k in range(4)]              # 点棒（相对自家）
    rnd = obs.get("round") or {}
    f += [1.0 if rnd.get("bakaze") == b else 0.0 for b in ("E", "S", "W", "N")]
    f += [float(rnd.get("kyoku", 0)) / 4.0, float(rnd.get("honba", 0)) / 8.0,
          float(rnd.get("riichi_sticks", 0)) / 4.0,
          1.0 if int(rnd.get("dealer", -1)) == s else 0.0]
    rt = obs.get("riichi_turn") or []
    own_rt = float(rt[s]) if (isinstance(rt, list) and len(rt) == 4 and rt[s] is not None) else -1.0
    f += [float(obs.get("tiles_left", 0)) / 70.0, float(obs.get("dead_wall_left", 0)) / 4.0,
          float(obs.get("total_discards", 0)) / 70.0, float(obs.get("kan_count", 0)) / 4.0,
          float(obs.get("player_draws", 0)) / 18.0,
          min(len(obs.get("events") or []), 40) / 40.0,
          (own_rt / 18.0) if own_rt >= 0 else -1.0]
    f += [1.0 if str(obs.get("kind", "")) == x else 0.0 for x in ("turn", "claim", "chankan")]
    return [float(x) for x in f]


def action_feats(key: str, nlegal: int, idx: int) -> list:
    """动作键 → 定长向量：34 维牌 multi-hot + 赤 + 前缀 one-hot + 候选位次/条数。"""
    key = str(key or "")
    pre = key.split(":")[0] if ":" in key else key
    if pre in KAN_ALIAS:
        pre = "kan"
    v = [0.0] * 34
    red = 0.0
    for tok in TILE_RE.findall(key):
        i, isred = tile_index(tok)
        v[i] = 1.0
        if isred:
            red = 1.0
    f = v + [red]
    f += [1.0 if pre == p else 0.0 for p in PREFIXES] + [1.0 if pre not in PREFIXES else 0.0]
    f += [float(idx) / max(1, nlegal - 1) if nlegal > 1 else 0.0, min(nlegal, 20) / 20.0]
    return [float(x) for x in f]


def gnum(p: Path) -> int:
    s = p.stem[1:]
    return int(s) if s.isdigit() else 0


def read_file(f: Path, stu: str):
    """单个 `gNNN.jsonl` → (game 记录, {hand_no: {"rows": [学生席决策行], "delta": [...]}})"""
    game = None
    hands: dict = {}
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            t = r.get("type")
            if t == "game":
                game = r
            elif t == "hand":
                e = hands.setdefault(int(r["hand_no"]), {"rows": [], "delta": None, "rkey": None})
                e["delta"] = [int(x) for x in r["delta"]]
                e["rkey"] = str(r.get("hand"))
            elif t == "decision" and int(r.get("seat", -1)) == 0 and r.get("policy") == stu:
                hands.setdefault(int(r["hand_no"]), {"rows": [], "delta": None, "rkey": None})["rows"].append(r)
    return game, hands


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_probe-contrast-extract.py")
    ap.add_argument("--a0", required=True)
    ap.add_argument("--a1", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pairs", type=int, default=0)
    a = ap.parse_args(argv)

    d0, d1, out = Path(a.a0), Path(a.a1), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    s0 = json.loads((d0 / "summary.json").read_text(encoding="utf-8"))
    s1 = json.loads((d1 / "summary.json").read_text(encoding="utf-8"))
    p0 = list(s0["per_game"][0]["policies"]); p1 = list(s1["per_game"][0]["policies"])
    stu0, stu1 = p0[0], p1[0]
    if p0[1:] != p1[1:]:
        raise SystemExit(f"⛔ 两臂对手不一致：\n  A0={p0}\n  A1={p1}")
    if s0.get("seed_base") != s1.get("seed_base"):
        raise SystemExit(f"⛔ 两臂 seed_base 不一致：{s0.get('seed_base')} vs {s1.get('seed_base')}")
    if s0["games"] != s1["games"]:
        raise SystemExit(f"⛔ 两臂场数不一致：{s0['games']} vs {s1['games']}")

    reason = Counter()
    S_all, A0_all, A1_all = [], [], []
    y0v, y1v, dyv, sig, games, hands, steps = [], [], [], [], [], [], []
    d0v, d1v = [], []
    n_seed_mismatch = 0
    step_drift = 0
    n_hands = 0
    round_key_mismatch = 0
    kind_hist = Counter()
    files0 = sorted((p for p in d0.iterdir() if p.suffix == ".jsonl"), key=gnum)
    with (out / "pairs.jsonl").open("w", encoding="utf-8") as jf:
        for f in files0:
            g = gnum(f)
            f1 = d1 / f.name
            if not f1.is_file():
                reason["missing_peer_file"] += 1
                continue
            gm0, h0 = read_file(f, stu0)
            gm1, h1 = read_file(f1, stu1)
            if gm0 is None or gm1 is None or int(gm0.get("seed", 0)) != int(gm1.get("seed", -1)):
                n_seed_mismatch += 1
                continue
            for hn in sorted(set(h0) & set(h1)):
                r0, r1 = h0[hn], h1[hn]
                if r0["delta"] is None or r1["delta"] is None:
                    reason["no_hand_record"] += 1
                    continue
                if r0["rkey"] != r1["rkey"]:
                    round_key_mismatch += 1
                    continue
                n_hands += 1
                A, B = r0["rows"], r1["rows"]
                div, why = None, None
                for i in range(min(len(A), len(B))):
                    ra, rb = A[i], B[i]
                    # ⚠ `step` 是**每场累计**的决策计数（跨小局不清零，实测 g0 席 0 = 0,4,10,… / 次局 39,44,…）
                    #   ⇒ 它**不是**同状态判据；同状态只看 obs 逐位 + legal 逐位（`step` 差异另计数）。
                    if obs_hash(ra["obs"]) != obs_hash(rb["obs"]):
                        why = "state_mismatch"; break
                    if list(ra.get("legal") or []) != list(rb.get("legal") or []):
                        why = "legal_mismatch"; break
                    if ra.get("chosen") != rb.get("chosen"):
                        div = i; break
                if div is None:
                    if why:
                        reason[why] += 1
                    elif len(A) != len(B):
                        reason["len_mismatch"] += 1
                    else:
                        reason["no_divergence"] += 1
                    continue
                if int(A[div]["step"]) != int(B[div]["step"]):
                    step_drift += 1
                    obs_ok = all(obs_hash(A[i]["obs"]) == obs_hash(B[i]["obs"]) for i in range(div))
                    if not obs_ok:
                        reason["prefix_state_mismatch"] += 1
                        continue
                ra, rb = A[div], B[div]
                legal = list(ra.get("legal") or [])
                try:
                    ia, ib = legal.index(ra["chosen"]), legal.index(rb["chosen"])
                except ValueError:
                    reason["chosen_not_in_legal"] += 1
                    continue
                if ia != int(ra["chosen_index"]) or ib != int(rb["chosen_index"]):
                    reason["chosen_index_mismatch"] += 1
                    continue
                if obs_hash(ra["obs"]) != obs_hash(rb["obs"]):
                    reason["state_mismatch_after"] += 1
                    continue
                y0, y1 = float(r0["delta"][0]), float(r1["delta"][0])
                dy = y1 - y0
                if dy == 0.0:
                    reason["dy_zero"] += 1
                    continue
                nlegal = len(legal)
                S_all.append(state_feats(ra["obs"]))
                A0_all.append(action_feats(ra["chosen"], nlegal, ia))
                A1_all.append(action_feats(rb["chosen"], nlegal, ib))
                y0v.append(y0); y1v.append(y1); dyv.append(dy)
                d0v.append([float(x) for x in r0["delta"]]); d1v.append([float(x) for x in r1["delta"]])
                sig.append(1 if dy > 0 else 0)
                games.append(g); hands.append(hn); steps.append(int(ra["step"]))
                kind_hist[str(ra.get("kind"))] += 1
                jf.write(json.dumps({
                    "game": g, "hand_no": hn, "step": int(ra["step"]), "i": int(div), "kind": ra.get("kind"),
                    "nlegal": nlegal, "a0": ra["chosen"], "a1": rb["chosen"], "a0i": ia, "a1i": ib,
                    "y0": y0, "y1": y1, "dy": dy, "sign": 1 if dy > 0 else 0,
                    "obs_hash": obs_hash(ra["obs"]), "seat": 0,
                }, ensure_ascii=False) + "\n")
                if a.max_pairs and len(games) >= a.max_pairs:
                    break
            if a.max_pairs and len(games) >= a.max_pairs:
                break

    if not games:
        raise SystemExit("⛔ 一个同状态配对都没有")
    S = np.asarray(S_all, dtype=np.float32); A0 = np.asarray(A0_all, dtype=np.float32)
    A1 = np.asarray(A1_all, dtype=np.float32)
    np.savez(out / "pairs.npz", S=S, A0=A0, A1=A1,
             dy=np.asarray(dyv, dtype=np.float32), y0=np.asarray(y0v, dtype=np.float32),
             y1=np.asarray(y1v, dtype=np.float32), sign=np.asarray(sig, dtype=np.int8),
             game=np.asarray(games, dtype=np.int32), hand=np.asarray(hands, dtype=np.int32),
             step=np.asarray(steps, dtype=np.int32),
             delta_a0=np.asarray(d0v, dtype=np.int32), delta_a1=np.asarray(d1v, dtype=np.int32))
    meta = {"a0_dir": str(d0), "a1_dir": str(d1), "student_a0": stu0, "student_a1": stu1,
            "opponents": p0[1:], "seed_base": s0["seed_base"], "games_per_arm": s0["games"],
            "n_games_seed_mismatch": n_seed_mismatch,
            "n_round_key_mismatch": round_key_mismatch,
            "n_pairs_with_step_drift": step_drift,
            "n_hands_in_both_arms": n_hands, "n_pairs": len(games),
            "pair_rate_of_hands": len(games) / max(1, n_hands),
            "drop_reason": dict(reason), "div_kind_hist": dict(kind_hist),
            "dim_state": int(S.shape[1]), "dim_action": int(A0.shape[1]),
            "dy_mean_pts": float(np.mean(dyv)), "dy_pos_share": float(np.mean(sig)),
            "fantasy_oracle_gain_pts_per_hand": float(np.mean([max(0.0, x) for x in dyv])),
            "a1_always_gain_pts_per_hand": float(np.mean(dyv)),
            "y0_mean_pts": float(np.mean(y0v))}
    (out / "extract-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
