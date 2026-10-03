"""对手模型（belief / danger 头）的**成色测量** —— 回答"这些头到底有多准"。

为什么要有它（2026-09-30）：策略侧要"把对手模型接进决策"，但**先得知道接进去的是不是有用的东西**。
仓库里此前只有训练时的 `val_loss_belief_tenpai`（一个 BCE 标量，没有基线、没有 AUC），
所以这里量三件事，**每一件都给"相对基线"的对照**（否则数字没有意义）：

1. `belief_tenpai[3]` vs 真值 `aux_opp_tenpai[3]`：**AUC** + BCE，对照"全预测边缘基率"的 BCE；
2. `danger[L,4]` 在**被选中的那个候选**上 vs 真值 `aux_opp_dealin[3]`：**AUC** + BCE。
   ⚠ 标签只落在被选中的候选上（见 `dataset.py` 的表），所以只能这么量，不能拿"未选中候选的
   danger"去和"没放铳"比，那会把样本量虚增几十倍、AUC 虚高。
   ⚠⚠ **但"被选中的候选"这一个限制还不够 —— 两套口径量的是两个不同的问题**（2026-10-03 第六十五轮
   审计暴露，本次修）：

   | 口径 | 行集 | 它实际在问什么 |
   | --- | --- | --- |
   | **S2（主读数）** | **只取"本小局最后一次决策"那一行**（每个 `(场次 × 小局 × 座位)` 一行） | **候选级**："这张牌危不危险" |
   | **P（保留，历史可比）** | 全部决策行 | **状态级**："这一小局我会不会放铳" |

   为什么 P 是状态级：`aux_opp_dealin` 是**小局级**标签、被 `TraceRecorder.rollHand` 回填到该小局
   **每一行**（实测平均 ~15 行/小局 ⇒ 93% 的 `dealin=1` 行**根本不是放铳那一手**）。于是 P 的
   "正样本"里绝大多数是"这一局后来放铳了、但这一手不是那一手"⇒ 它量的是状态而不是候选。
   直接证据：同一个**真手牌 oracle** 在 P 上只有 0.53、在 S2 上是 **0.999**。
   ⛔ **两个口径量纲不同、数值不可互比**（P 的 0.64 与 S2 的 0.8+ 不是"同一个指标的两个版本"）；
   引用历史数字（`0.638~0.650`、`VALVES §3 W4` 的"天花板在标签"基线）时**必须**注明是 P。
   细节见 `NOTES.md` §6.5 第六十五轮、`docs/TRAINING-V4.md` §14.13/§14.13.1。
3. `belief_hand[3,34]`：逐格 BCE，对照"用本切分的每格频率当常数预测"的 BCE；外加 **top-1 命中率**
   （模型给每家 argmax 的那张牌，真的在该家手里的比例）与"该家手里有几张"的边缘基率。

用法（`--data` 是 compact 目录，`--ckpt` 是 ckpt 目录）：

    python -m mahjong_ml.v4.heads_audit --data S:\\mahjong-training\\compact\\v4-heads-g01 \\
        --ckpt S:\\mahjong-training\\ckpt\\v4-heads-g01 --split val

⚠ 它**只读**：不训练、不改权重、不写数据根。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from . import dataset as v4ds
from . import model as M
from . import value_audit as va
from .pretrain import _batch


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """秩和法 AUC（不依赖 sklearn）。`y` 是 0/1；全 0 或全 1 时返回 `nan`。"""
    y = np.asarray(y).astype(np.int64)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1, dtype=np.float64)
    # 并列取平均秩（否则同分候选的顺序会凭空造出 AUC）
    s = np.asarray(score)[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def bce(prob: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(prob, dtype=np.float64), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=np.float64)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def forward_heads(model: M.V4Model, data: dict, n: int, device: str,
                  out_keys: tuple[str, ...], batch: int = 2048) -> dict[str, np.ndarray]:
    """整切分前向一遍，取需要的头（logits 原样返回，概率在各指标处再算）。"""
    acc: dict[str, list[np.ndarray]] = {k: [] for k in out_keys}
    for i0 in range(0, n, batch):
        idx = np.arange(i0, min(i0 + batch, n))
        b = _batch(data, idx, device)
        with torch.no_grad():
            # `h=b.get("h")`：有 `h0` 列（W1b）就喂**窗口之前**的 carry —— 与训练同源；
            # 老紧凑集没有这一列 ⇒ `None` ⇒ 窗口冷启动（只读审计，按老行为退化即可）。
            out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"], h=b.get("h"))
        for k in out_keys:
            acc[k].append(out[k].float().cpu().numpy())
    return {k: np.concatenate(v, axis=0) for k, v in acc.items()}


def last_decision_rows(data: dict) -> np.ndarray | None:
    """**本小局最后一次决策**的行下标（口径 S2 的行集）。

    分组键 = `(game, hand_no, seat)` —— `game` 是场次、`hand_no` 是小局序号、`seat` 是"我"的座位。
    为什么必须带 `seat`：一份紧凑集里**四家的决策行都在**，只按 `(game, hand_no)` 分组会退化成
    "这一局最后一个动作的人"，那就不是"**我**这一局的最后一次决策"了（实测 val：241 个
    `(game, hand_no)` vs **964** 个 `(game, hand_no, seat)`；第六十五轮报告的 `n=964` 就是这个数）。

    为什么它才是**候选级**口径：`aux_opp_dealin` 是小局级标签、被回填到该小局每一行，只有在这一行
    上它才（近似）等于"**这一手**放铳了没有"。⚠ 不假设行是连续排序的（`np.maximum.at` 取每组最大行号）。

    老紧凑集没有 `game` / `hand_no` 列 ⇒ 返回 `None`（**显式缺席**，不猜、不退化成一个假口径）。
    """
    if data.get("game") is None or data.get("hand_no") is None or data.get("seat") is None:
        return None
    game = np.asarray(data["game"], dtype=np.int64)
    hand = np.asarray(data["hand_no"], dtype=np.int64)
    seat = np.asarray(data["seat"], dtype=np.int64)
    n = len(game)
    # 键用乘加而不是元组（`np.unique(axis=0)` 对这几万行没问题，但乘加快一个数量级）
    key = (game * 4096 + hand) * 8 + seat
    _, inv = np.unique(key, return_inverse=True)
    last = np.full(int(inv.max()) + 1, -1, dtype=np.int64)
    np.maximum.at(last, inv.reshape(-1), np.arange(n, dtype=np.int64))
    return np.sort(last[last >= 0])


def report(data_dir: Path, ckpt: Path, split: str, device: str) -> int:
    data = v4ds.load_split(data_dir, split)
    n = int(data["label"].shape[0])
    print(f"== {Path(ckpt).name} on {Path(data_dir).name}/{split}（n={n}）==")
    for k in ("aux_opp_tenpai", "aux_opp_dealin", "aux_opp_hand"):
        if data.get(k) is None:
            print(f"⛔ 这份数据没有 `{k}`（老数据集 / 没带 --aux）⇒ 量不了")
            return 2
    model = va.load_model(ckpt, device)
    heads = forward_heads(model, data, n, device,
                          ("belief_tenpai", "danger", "belief_hand"))
    label = np.asarray(data["label"], dtype=np.int64)
    # ⚠ `belief_hand` 可能以 `[B,102]` 或 `[B,3,34]` 出来（取决于头是否 reshape）——两种都接受
    bh = heads["belief_hand"]
    if bh.ndim == 2:
        bh = bh.reshape(len(bh), 3, 34)
    heads["belief_hand"] = bh
    bt_y = np.asarray(data["aux_opp_tenpai"], dtype=np.float64)
    dl_y = np.asarray(data["aux_opp_dealin"], dtype=np.float64)
    hd_y = np.asarray(data["aux_opp_hand"], dtype=np.float64)

    print("\n-- belief_tenpai（对手是否听牌）--")
    print("  家  基率     AUC     BCE     边缘BCE   相对")
    for j in range(3):
        p = 1.0 / (1.0 + np.exp(-heads["belief_tenpai"][:, j]))
        base = float(bt_y[:, j].mean())
        a = auc(heads["belief_tenpai"][:, j], bt_y[:, j])
        b_model, b_base = bce(p, bt_y[:, j]), bce(np.full(n, base), bt_y[:, j])
        print(f"  {j}  {base:6.3f}  {a:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%")

    rows = last_decision_rows(data)
    print("\n-- danger（★ 主读数：口径 S2 = **仅在本小局最后一次决策的行上**）--")
    print("   ★ 这是**候选级**读数（「这张牌危不危险」）：只有在这一行上，小局级标签才≈「这一手放铳了没有」")
    if rows is None:
        print("   ⛔ 这份数据没有 `game` / `hand_no` / `seat` 列 ⇒ 量不了 S2（不退化成一个假口径）")
    else:
        print(f"  行数 n={len(rows)}（= 本切分的小局×座位数；下一条 P 口径是 n={n}，两者不是同一样本集）")
        print("  家  基率     AUC     BCE     边缘BCE   相对")
        for j in range(3):
            s = heads["danger"][rows, label[rows], j]
            p = 1.0 / (1.0 + np.exp(-s))
            y = dl_y[rows, j]
            base = float(y.mean())
            a = auc(s, y)
            b_model, b_base = bce(p, y), bce(np.full(len(rows), base), y)
            print(f"  {j}  {base:6.3f}  {a:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
                  f"{(1 - b_model / b_base) * 100:+6.1f}%")

    print("\n-- danger（⚠ 口径 P = **全行**）：**状态级**读数，不是候选级危险度 --")
    print("   ⛔ `aux_opp_dealin` 是**小局级**标签、被回填到该小局每一行（93% 的 dealin=1 行不是放铳那一手）")
    print("      ⇒ 这一栏量的是「**这一小局**我会不会放铳」（状态级），不是「**这张牌**危不危险」（候选级）。")
    print("   ⛔ 与上面 S2 量纲不同、**数值不可互比**；历史文档里的 0.638~0.650 说的都是这一栏。")
    print("  家  基率     AUC     BCE     边缘BCE   相对")
    for j in range(3):
        s = heads["danger"][np.arange(n), label, j]          # 只看被选中那一手
        p = 1.0 / (1.0 + np.exp(-s))
        base = float(dl_y[:, j].mean())
        a = auc(s, dl_y[:, j])
        b_model, b_base = bce(p, dl_y[:, j]), bce(np.full(n, base), dl_y[:, j])
        print(f"  {j}  {base:6.3f}  {a:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%")

    print("\n-- belief_hand（逐对手手牌 3×34 格）--")
    print("  家  基率     BCE     边缘BCE   相对    top1命中  top1基率")
    for j in range(3):
        p = 1.0 / (1.0 + np.exp(-heads["belief_hand"][:, j]))
        base_tile = hd_y[:, j].mean(axis=0)                  # 每格频率（本切分）
        b_model = bce(p, hd_y[:, j])
        b_base = bce(np.broadcast_to(base_tile, hd_y[:, j].shape), hd_y[:, j])
        top1 = np.asarray(heads["belief_hand"][:, j]).argmax(axis=1)
        hit = float(hd_y[np.arange(n), j, top1].mean())
        rate = float(hd_y[:, j].mean())
        print(f"  {j}  {rate:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%   {hit:7.3f}  {rate:7.3f}")
    return 0


def danger_baseline(data_dir: Path, split: str) -> int:
    """**危险标签的便宜基线**：逐候选输入列对「任意一家放铳」的 AUC 最高能到多少？

    为什么要有它（2026-09-30）：判决出 `danger` 头 AUC 只有 0.64（被选中候选上的实际放铳）之后，
    必须先分清是「**头没吃满输入**」还是「**标签/信息本身到顶**」—— 这就是杠杆①的"先量再改"。
    实测（`v4-heads-g01` val）：**最好的单列只有 0.558**，而 net 的头是 0.638~0.650
    ⇒ 头已超过任何便宜线性特征 ⇒ 天花板在**标签**（`aux_opp_dealin` 只落在被选中的候选上、
    无反事实）⇒ 该做的是**稠密逐候选标签**，而不是把同一个头再训一遍。

    ⚠ **更正（2026-10-03，第六十五轮 + `TRAINING-V4` §14.13.1）**：上面那句"天花板在标签"**不成立**，
    两个原因：① 这里的 AUC 是**口径 P**（全行、小局级标签）⇒ 它量的是**状态级**问题，本来就不该
    拿来给"候选级危险度"定天花板；② 本函数只扫了 `cand[:, c]`，**没扫 `tile` 的 `danger_*` 通道**
    （`danger_all` / `safety_*` 早就是危险头的输入，实测非零），而那条启发式对实际放铳的 AUC 只有
    0.50~0.54 —— 所以"头已超过便宜特征"这个结论也要按同一批通道重述。**保留本函数只为历史可比。**
    """
    data = v4ds.load_split(data_dir, split)
    n = int(data["label"].shape[0])
    label = np.asarray(data["label"], dtype=np.int64)
    dealin = np.asarray(data["aux_opp_dealin"], dtype=np.float64)
    cand = np.asarray(data["cand"][np.arange(n), label, :], dtype=np.float32)
    y = (dealin.max(axis=1) > 0).astype(np.int64)
    print(f"== danger 便宜基线（{Path(data_dir).name}/{split}，n={n}）==")
    print(f"   任意一家放铳基率 = {float(y.mean()):.4f}")
    rows: list[tuple[float, int]] = []
    flat: list[int] = []
    for c in range(cand.shape[1]):
        if float(np.std(cand[:, c])) == 0.0:
            flat.append(c)
            continue
        a = auc(cand[:, c], y)
        if a == a:
            rows.append((a, c))
    rows.sort(reverse=True)
    for a, c in rows[:5]:
        print(f"   列 {c:3d}  AUC {a:.4f}")
    if flat:
        print(f"   [警告] 常量列（AUC 恒 0.5 = 没给信息）：{flat[:12]}"
              f"{' ...' if len(flat) > 12 else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="量对手模型（belief/danger 头）的成色")
    ap.add_argument("--data", required=True, help="compact 目录")
    ap.add_argument("--ckpt", help="ckpt 目录（含 model.pt）；只跑 --danger-baseline 时可省")
    ap.add_argument("--split", default="val", choices=["train", "val"])
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--danger-baseline", action="store_true",
                    help="只跑「危险标签的便宜基线」（不需要 --ckpt）")
    args = ap.parse_args(argv)
    if args.danger_baseline:
        return danger_baseline(Path(args.data), args.split)
    if not args.ckpt:
        ap.error("量三个头必须给 --ckpt（只跑 --danger-baseline 时才可省）")
    return report(Path(args.data), Path(args.ckpt), args.split, args.device)


if __name__ == "__main__":
    raise SystemExit(main())
