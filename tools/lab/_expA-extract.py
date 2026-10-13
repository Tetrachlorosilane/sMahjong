# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""实验 A · 第 1 步：从采集轨迹里抽出**学生席**的逐决策行（零训练、只解析）。

产物（全部落在 `--work` 下）：
  stu/<arm>/gNNN.jsonl   学生席决策行**原样**重写（喂 `trainer.exe v4net` 用；读完即删）
  rows_<arm>.jsonl       逐决策行的轻量元数据表（与上面**逐行同序**，用于对齐 v4net 输出）
  hands_<arm>.json       逐小局账（每席净得失点 + 学生席座位）
  meta_<arm>.json        自证：学生席策略串 / 行数 / 小局数 / 逐场学生席分布

⛔ 只读公开字段；⛔ 不碰别家手牌/牌山/里宝（轨迹里本来就没有）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def draw_band(n: int) -> int:
    return 0 if n <= 6 else (1 if n <= 12 else 2)


def riichi_band(obs: dict) -> int:
    seat = int(obs["seat"])
    n = sum(1 for i, v in enumerate(obs.get("riichi") or []) if v and i != seat)
    return 0 if n == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_expA-extract.py")
    ap.add_argument("--raw-dir", required=True, help="采集目录（含 g*.jsonl 与 summary.json）")
    ap.add_argument("--arm", required=True)
    ap.add_argument("--student-net", required=True, help="学生臂的 net.bin 绝对路径（⛔ 不靠猜）")
    ap.add_argument("--work", required=True, help="工作目录（实验 A 的中间产物）")
    a = ap.parse_args(argv)

    raw = Path(a.raw_dir)
    work = Path(a.work)
    sump = raw / "summary.json"
    if not sump.is_file():
        raise SystemExit(f"⛔ 缺 summary.json：{sump}")
    sm = json.loads(sump.read_text(encoding="utf-8"))
    # 学生席 = 唯一那个在第 0 场里带 `@` 的策略；本次两臂都是**纯 argmax**（无 @ / #）⇒ 改用
    # "与其它三席都不同的那一条" 来定，判据写死：第 0 场四席里，出现次数为 1、且 != 三条风格桌的串。
    p0 = list(sm["per_game"][0]["policies"])
    style = set(p for p in p0 if p.startswith("net:") and "v4-expert-" in p)
    net = a.student_net
    if not Path(net).is_file():
        raise SystemExit(f"⛔ 学生权重不存在：{net}")
    stu = "net:" + net
    # 自证：这条串必须真的在这一场的策略表里，且**不能**是三条风格桌之一
    if stu not in p0:
        raise SystemExit(f"⛔ 学生串不在第 0 场策略表里：{stu}\n   p0={p0}")
    if stu in style:
        raise SystemExit(f"⛔ 学生串落在风格桌上（三席是固定的，不许换）：{stu}")

    out_stu = work / "stu" / a.arm
    out_stu.mkdir(parents=True, exist_ok=True)
    rows_path = work / f"rows_{a.arm}.jsonl"
    files = sorted((p for p in raw.iterdir() if p.suffix == ".jsonl"),
                   key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)
    if not files:
        raise SystemExit(f"⛔ {raw} 里没有 g*.jsonl")

    hands: list[dict] = []
    seat_of_hand: dict[tuple[int, int], int] = {}
    n_rows = 0
    n_hdrift = 0
    with rows_path.open("w", encoding="utf-8") as rf:
        for f in files:
            keeps: list[str] = []
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    if r.get("type") == "hand":
                        hands.append({"game": int(r["game"]), "hand_no": int(r["hand_no"]),
                                      "hand": r.get("hand"), "delta": r["delta"],
                                      "winner": r.get("winner"), "loser": r.get("loser"),
                                      "agari": bool(r.get("agari")),
                                      "scores_after": r.get("scores_after")})
                        continue
                    if r.get("type") != "decision" or r.get("policy") != stu:
                        continue
                    obs = r["obs"]
                    g, hn = int(r["game"]), int(r["hand_no"])
                    s = int(r["seat"])
                    if (g, hn) in seat_of_hand and seat_of_hand[(g, hn)] != s:
                        n_hdrift += 1
                    seat_of_hand[(g, hn)] = s
                    keeps.append(line if line.endswith("\n") else line + "\n")
                    # obs 指纹：用来判"两臂在这一行是不是**同一状态**"（逐字节同一张公开观测）
                    oh = hashlib.md5(json.dumps(obs, sort_keys=True, separators=(",", ":"))
                                     .encode("utf-8")).hexdigest()[:16]
                    rf.write(json.dumps({
                        "i": n_rows, "game": g, "hand_no": hn, "step": int(r["step"]),
                        "seat": s, "kind": r.get("kind"), "nlegal": len(r.get("legal") or []),
                        "chosen": r.get("chosen"), "chosen_index": int(r.get("chosen_index", -1)),
                        "obs_hash": oh,
                        "player_draws": int(obs.get("player_draws", 0)),
                        "draw_band": draw_band(int(obs.get("player_draws", 0))),
                        "riichi_band": riichi_band(obs),
                        "total_discards": int(obs.get("total_discards", 0)),
                        "tiles_left": int(obs.get("tiles_left", 0)),
                        "menzen": bool(obs.get("menzen", False)),
                    }, ensure_ascii=False) + "\n")
                    n_rows += 1
            if keeps:
                (out_stu / f.name).write_text("".join(keeps), encoding="utf-8")

    # 逐小局账：把学生席那一格标出来
    for h in hands:
        k = (h["game"], h["hand_no"])
        h["student_seat"] = seat_of_hand.get(k, -1)
        if h["student_seat"] < 0:
            h["student_delta"] = None
        else:
            h["student_delta"] = h["delta"][h["student_seat"]]
    (work / f"hands_{a.arm}.json").write_text(
        json.dumps({"arm": a.arm, "student_policy": stu, "hands": hands}, ensure_ascii=False),
        encoding="utf-8")
    seat_hist: dict[str, int] = {}
    for h in hands:
        seat_hist[str(h["student_seat"])] = seat_hist.get(str(h["student_seat"]), 0) + 1
    (work / f"meta_{a.arm}.json").write_text(json.dumps({
        "arm": a.arm, "raw_dir": str(raw), "student_policy": stu,
        "seed_base": sm.get("seed_base"), "games": len(files),
        "n_games_in_summary": sm.get("games"), "n_hands": len(hands),
        "n_student_rows": n_rows, "student_seat_hist": seat_hist,
        "seat_drift_rows": n_hdrift,
        "style_policies": sorted(style),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[extract:{a.arm}] games={len(files)} hands={len(hands)} student_rows={n_rows} "
          f"seat_hist={seat_hist} seat_drift={n_hdrift}")
    print(f"[extract:{a.arm}] student={stu}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
