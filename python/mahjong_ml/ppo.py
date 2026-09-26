"""P4 · 在线自对弈强化学习（PPO）—— 直接优化"我们真正测量的东西"。

    # 前提：数据集**必须是用当前网络采的**（净 `net:<权重>#<T>` 自对弈），见 `online.py`
    python -m mahjong_ml.ppo --data S:\\mahjong-training\\compact\\gen-01 \\
        --init S:\\mahjong-training\\ckpt\\awr-002\\model.pt --temp 1.0 --label ppo-001

## 与 P3（离线 RL）的口径差别（**别混着说**）

- P3 在**固定的老师数据**上做优势加权 BC：数据分布不随策略变，天花板 = 数据的行为策略。
- P4 的数据**每一代重新采**（`net:<权重文件>[@<α>][#<T>]` 温度采样自对弈，`PROTOCOL.md` §8.4），
  PPO 用 `π_new/π_old` 的**裁剪代理目标**把策略往"实测结果更好的方向"推 —— 这是唯一
  **结构上**能超过数据行为策略的路线（`docs/TRAINING.md` §4 P4）。

## 口径（与 `rewards.py` 一致，不许另立一份）

- episode = **某一家在一小局里的全部决策**（键 `file` + `hand_no` + `seat`）；γ = 1；
  奖励只记在小局末决策上（`hand_delta[seat]`），整场末决策再另加 `rank_weight · 顺位点`。
- 因为 γ=1 且一条 episode 只有一个非零奖励，**λ=1 时 GAE 塌成 `A_t = R − V(s_t)`**
  （`python/selfcheck.py` 里有一条断言钉着这个等价性；实现仍按通用 GAE 写，换奖励口径不用重写）。
  ⚠ **默认 λ 就是 1.0**：奖励是"末决策单点"，λ<1 会让前几步的优势按剩余步数衰减 ——
  那是系统性偏差，不是"少用了一点信息"。要调 λ 请连带改这条断言的预期值。
- **行为策略是带温度的**：Java 侧按 `softmax(z/T)` 采样，所以这里训练的策略就是
  `π_θ = softmax(z_θ/T)` —— **用同一个 T**。⚠ 线上部署是 argmax，而 argmax 对 T 不变，
  所以温度只影响探索、不影响部署动作；但 `--temp` 必须与采集时的那份一致，否则 `π_old` 是错的。
- **策略损失只算"学生行"**（`is_student == 1`，那一行的行为策略就是网络本身）；
  对手座位（teacher / 脚本）的行**仍进价值损失** —— V 与行为策略无关，白扔数据没道理。
- ⚠ **价值网络不导出到 Java**（`nets.ValueNet` 只在训练侧）：线上策略仍然只是
  `CandidateScorer`，所以 `NeuralPolicy.java` 一行都不用改。
"""

from __future__ import annotations

import argparse
import copy
import json
import time

import numpy as np
import torch
import torch.nn.functional as F

from . import bc, dataset as ds, features, guard, nets, paths, rewards


# ------------------------------------------------------------------ 纯函数（selfcheck 直接喂）


def logprobs(model: nets.CandidateScorer, state: torch.Tensor, cand: torch.Tensor,
             mask: torch.Tensor, temp: float = 1.0) -> torch.Tensor:
    """`[B, L]` = `log softmax(logits / temp)`；非法位置补 `-inf`（softmax 后概率恒 0）。

    ⚠ `-inf / temp` 仍是 `-inf`，所以先除温度再补掩码与先补掩码再除温度等价 —— 这里沿用
    `CandidateScorer.forward` 的掩码（`mask=None` 时由调用方保证候选已定长对齐）。
    """
    logits = model(state, cand, mask) / temp
    if mask is not None:
        logits = logits.masked_fill(~mask, float("-inf"))
    return torch.log_softmax(logits, dim=-1)


def chosen_logprob(logp: torch.Tensor, label: torch.Tensor) -> torch.Tensor:
    """`[B]`：数据里那个动作在当前策略下的对数概率。"""
    return logp.gather(1, label[:, None]).squeeze(1)


def entropy_of(logp: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """`[B]`：候选分布上的熵（**只算合法候选**）。

    ⚠ 不能直接写 `-(p·logp).sum()`：被掩码的位置 `logp = -inf`、`p = 0`，`0·(-inf) = NaN`，
    前向看着没事、反向会把整批梯度污染成 NaN。先把掩码位置替换成 `logp = 0`（`exp(0)·0 = 0`）。
    """
    lp = torch.where(mask, logp, torch.zeros_like(logp))
    return -(lp.exp() * lp).sum(dim=-1)


def gae(reward: np.ndarray, nxt: np.ndarray, values: np.ndarray, *,
        gamma: float = 1.0, lam: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """通用 GAE（按 `nxt` 链**逆序**回推；`nxt < 0` = episode 结束，bootstrap 0）。

    @return `(advantage, value_target)`，其中 `value_target = advantage + V`（TD(λ) 回报估计）
    @param nxt `next_index`（`rewards.py`）：同一条 episode 里下一条决策的行号，`-1` = 末尾。
        ⚠ 它恒有 `nxt[i] > i`（行序就是时间序），所以逆序一遍就能把整条链算对。
    """
    v = np.asarray(values, dtype=np.float64)
    r = np.asarray(reward, dtype=np.float64)
    nx = np.asarray(nxt, dtype=np.int64)
    adv = np.zeros(v.size, dtype=np.float64)
    for i in range(v.size - 1, -1, -1):
        j = int(nx[i])
        v_next = v[j] if j >= 0 else 0.0
        delta = r[i] + gamma * v_next - v[i]
        adv[i] = delta + (gamma * lam * adv[j] if j >= 0 else 0.0)
    return adv, adv + v


def normalize_adv(adv: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """只在 `mask`（学生行）上算均值/标准差再归一 —— 对手行的优势不进策略损失，混进来会把尺度带跑。"""
    a = np.asarray(adv, dtype=np.float64)
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return a
    sub = a[m]
    return (a - float(sub.mean())) / (float(sub.std()) + 1e-8)


def clip_fraction(ratio: np.ndarray | torch.Tensor, clip: float) -> float:
    """被裁剪的样本比例（PPO 的体检指标之一：长期贴 1 = 步长太大）。"""
    r = ratio.detach().cpu().numpy() if isinstance(ratio, torch.Tensor) else np.asarray(ratio)
    return float((np.abs(r - 1.0) > clip).mean()) if r.size else 0.0


def approx_kl(logp_old: np.ndarray | torch.Tensor, logp_new: np.ndarray | torch.Tensor) -> float:
    """`E[log π_old − log π_new]`（PPO 论文里那个便宜的 KL 估计，KL 早停看它）。"""
    a = logp_old.detach().cpu().numpy() if isinstance(logp_old, torch.Tensor) else np.asarray(logp_old)
    b = logp_new.detach().cpu().numpy() if isinstance(logp_new, torch.Tensor) else np.asarray(logp_new)
    return float((a - b).mean()) if a.size else 0.0


# ------------------------------------------------------------------ 价值诊断（与 P3 判据①同口径）


def value_report(value: nets.ValueNet, split: dict, tr: dict, device: str,
                 batch: int = 8192) -> dict:
    """价值头的**同一把尺子**（好和 P3 判据① 直接对比）：

    · `mae`：逐决策 `|V − 回报|`（千点），与 `const`（恒等于训练集均值）同粒度；
    · `pearson`：逐决策相关；`ep_pearson`：**按 episode 先平均再相关**（判据① 用的那个口径）。
    """
    n = int(np.asarray(split["state"]).shape[0])
    v = np.zeros(n, dtype=np.float64)
    value.eval()
    with torch.no_grad():
        for s in range(0, n, batch):
            idx = np.arange(s, min(s + batch, n))
            state, _, _, _ = bc._batch(split, idx, device)
            v[idx] = value(state).float().cpu().numpy()
    ret = np.asarray(tr["ret"], dtype=np.float64)
    out = {"n": n, "mae": float(np.abs(v - ret).mean()) if n else 0.0,
           "const_mae": float(np.abs(ret.mean() - ret).mean()) if n else 0.0,
           "pearson": float(np.corrcoef(v, ret)[0, 1]) if n > 1 and v.std() > 0 else 0.0}
    k = rewards.keys(split)
    uk, inv = np.unique(k, return_inverse=True)
    if uk.size > 1:
        vs = np.bincount(inv, weights=v) / np.bincount(inv)
        rs = np.bincount(inv, weights=ret) / np.bincount(inv)
        out["ep_pearson"] = float(np.corrcoef(vs, rs)[0, 1]) if vs.std() > 0 else 0.0
        out["episodes"] = int(uk.size)
    else:
        out["ep_pearson"] = 0.0
        out["episodes"] = int(uk.size)
    return out


# ------------------------------------------------------------------ 探索强度标定


def explore(args) -> int:
    """量一张**训练过的**网在不同温度下的"偏离贪心比例"（P4 选 `#<T>` 的依据）。

    为什么必须实测而不是照抄常数：`SelfTest` 里那条 T 曲线用的是 **golden 夹具**（未训练的小网，
    logit 几乎相等）—— 它给出的"T=1 偏离 97%"是那个网的属性，**不能**代表训练过的网。
    这和 P5b 选 α 是同一条纪律：`α`/`T` 都随**你自己这张网的 logit 尺度**走。

        python -m mahjong_ml.ppo --data <紧凑集> --init <model.pt> --explore-only
    """
    ckpt = args.ckpt or args.init
    if not ckpt:
        raise SystemExit("--explore-only 需要 --ckpt 或 --init 指定权重")
    split = ds.load_split(args.data, "val" if args.split == "val" else "train")
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
    model.load_state_dict(ck["model"])
    model.eval()
    n = min(int(np.asarray(split["state"]).shape[0]), args.rows)
    if n == 0:
        raise SystemExit("切分为空")
    idx = np.arange(n)
    state, cand, mask, label = bc._batch(split, idx, "cpu")
    with torch.no_grad():
        logits = model(state, cand, mask)
        top1 = logits.argmax(dim=1)
        agree = float((top1 == label).float().mean())
        print(f"权重 {ckpt}｜{args.split} 切分 {n} 条；贪心与数据里那个动作一致率 {agree:.4f}")
        print(f"  {'T':>6}{'平均熵':>10}{'贪心那条概率':>14}{'采样偏离贪心':>14}")
        rng = torch.Generator().manual_seed(args.seed)
        for t in (0.25, 0.5, 1.0, 2.0, 4.0):
            p = torch.softmax(logits / t, dim=-1)
            ent = float(-(p * torch.log(p.clamp_min(1e-12))).sum(-1).mean())
            conf = float(p.gather(1, top1[:, None]).mean())
            pick = torch.multinomial(p, 1, generator=rng).squeeze(1)
            dev = float((pick != top1).float().mean())
            print(f"  {t:>6}{ent:>10.3f}{conf:>14.3f}{dev:>14.3f}")
    print("读法：偏离贪心 ≈ 有多少比例的决策在探索。太高（>30%）梯度噪声大、太低（<5%）几乎退回贪心。")
    return 0


# ------------------------------------------------------------------ 训练


def train(args) -> dict:
    paths.ensure_root()
    split = ds.load_split(args.data, "train")
    tr = rewards.transitions(split, rank_weight=args.rank_weight)
    n = int(np.asarray(split["state"]).shape[0])
    if n == 0:
        raise SystemExit("数据集是空的 —— 先跑一轮采集（online.py / --selfplay）")
    student = np.asarray(split["is_student"], dtype=bool)
    threads = guard.apply_cpu_limit(args.threads)
    device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
             ("cpu" if args.device == "auto" else args.device)

    torch.manual_seed(args.seed)
    actor = nets.build(features.state_dim(), features.cand_dim(),
                       hidden=args.hidden, head=args.head).to(device)
    init = None
    if args.init:
        init = torch.load(args.init, map_location="cpu", weights_only=False)
        actor.load_state_dict(init["model"])
    # 冻结快照 = **采集时那份权重**（`--init`）：`π_old` 必须来自它，否则重要性权重是错的
    snapshot = copy.deepcopy(actor).eval()
    for p in snapshot.parameters():
        p.requires_grad_(False)
    value = nets.build_value(features.state_dim(), hidden=args.hidden, head=args.head).to(device)
    # ⚠ **显式 `--init-value` 优先于 `--init` 里继承来的 value**（2026-09-26 改）。
    #   原来 `--init` 带了 value 就跳过 `--init-value` ⇒ 那个显式参数在 P5 续跑里**被静默忽略**
    #   （实测：v3 g02 跑完才发现"价值头初始化自 --init 里的 value"）。判据是二者的校准质量：
    #   IQL critic val MAE 6.09 / episode ρ +0.677 vs 继承来的 7.16 / +0.640 —— 显式指定应当赢。
    if args.init_value:
        # 从 P3 的 IQL critic 里**只取 trunk + v_head**（`CriticScorer` 与 `ValueNet` 这两段同名同形）。
        # 为什么值得这一步：价值头从零开始时，前几代的优势基本是"回报减一个常数"，
        # 策略梯度方差极大；IQL 的 V 已经在同一批状态上训过（判据①：episode 级 ρ≈+0.6）。
        ck = torch.load(args.init_value, map_location="cpu", weights_only=False)
        sub = {k: v for k, v in ck["model"].items() if k.startswith(("trunk.", "v_head."))}
        missing, unexpected = value.load_state_dict(sub, strict=False)
        override = "　（显式 --init-value 覆盖了 --init 继承的 value）" if (
            init is not None and "value" in init) else ""
        print(f"价值头初始化自 {args.init_value}（trunk.* + v_head.*，{len(sub)} 个张量；"
              f"缺 {len(missing)} / 多 {len(unexpected)}）{override}")
    elif init is not None and "value" in init:
        try:
            value.load_state_dict(init["value"])
            print("价值头初始化自 --init 里的 value")
        except Exception as e:                              # noqa: BLE001
            print(f"（提示）--init 里的 value 装不上，价值头从零开始：{e}")

    if args.temp <= 0:
        raise SystemExit("--temp 必须 > 0（行为策略是 softmax(z/T)，T=0 采不出随机数据）")
    print(f"数据：{n} 条决策（学生行 {int(student.sum())} = {student.mean():.3f}）；"
          f"temp={args.temp} γ={args.gamma} λ={args.lam} clip={args.clip} epochs={args.epochs}")
    print(f"策略 {actor.n_params()} 参数 / 价值 {value.n_params()} 参数；device={device}")
    print(rewards.describe(tr))

    # ① 冻结的一遍：`logp_old`（行为策略）与 `V_old` → 优势（此后固定，等价于 A2C 的 advantage）
    t0 = time.perf_counter()
    logp_old = np.zeros(n, dtype=np.float32)
    v_old = np.zeros(n, dtype=np.float32)
    with torch.no_grad():
        for s in range(0, n, args.batch):
            idx = np.arange(s, min(s + args.batch, n))
            state, cand, mask, label = bc._batch(split, idx, device)
            logp_old[idx] = chosen_logprob(logprobs(snapshot, state, cand, mask, args.temp),
                                           label).float().cpu().numpy()
            v_old[idx] = value(state).float().cpu().numpy()
    adv, vtarget = gae(tr["reward"], tr["nxt"], v_old, gamma=args.gamma, lam=args.lam)
    if args.adv_norm:
        adv = normalize_adv(adv, student)
    print(f"优势：mean={adv.mean():+.3f} std={adv.std():.3f}"
          f"（学生行 std={adv[student].std() if student.any() else 0:.3f}）；"
          f"V_old std={v_old.std():.3f}；冻结一遍 {time.perf_counter() - t0:.1f}s")

    opt = torch.optim.Adam([{"params": actor.parameters()}, {"params": value.parameters()}],
                           lr=args.lr)
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None
    steps = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    rng = np.random.default_rng(args.seed)
    history = []
    stop_epoch = None
    wall = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        perm = rng.permutation(n)
        actor.train()
        value.train()
        acc = {"loss": 0.0, "pi": 0.0, "vf": 0.0, "ent": 0.0, "kl": 0.0, "clip": 0.0, "ratio": 0.0}
        seen = 0
        for step in range(steps):
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            state, cand, mask, label = bc._batch(split, idx, device)
            lp = logprobs(actor, state, cand, mask, args.temp)
            lp_a = chosen_logprob(lp, label)
            old = torch.from_numpy(logp_old[idx]).to(device)
            ratio = torch.exp(lp_a - old)
            a = torch.from_numpy(adv[idx].astype(np.float32)).to(device)
            surr = torch.min(ratio * a, torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * a)
            w = torch.from_numpy(student[idx].astype(np.float32)).to(device)
            wsum = w.sum().clamp_min(1.0)
            pi_loss = (-surr * w).sum() / wsum
            ent = (entropy_of(lp, mask) * w).sum() / wsum
            v = value(state)
            vf_loss = F.mse_loss(v, torch.from_numpy(vtarget[idx].astype(np.float32)).to(device))
            loss = pi_loss + args.vf_coef * vf_loss - args.ent_coef * ent
            opt.zero_grad(set_to_none=True)
            loss.backward()
            if args.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(
                    list(actor.parameters()) + list(value.parameters()), args.grad_clip)
            opt.step()
            if dc:
                dc.tick()
            m = w > 0
            kl = approx_kl(old[m], lp_a[m]) if bool(m.any()) else 0.0
            acc["loss"] += float(loss.detach()) * idx.size
            acc["pi"] += float(pi_loss.detach()) * idx.size
            acc["vf"] += float(vf_loss.detach()) * idx.size
            acc["ent"] += float(ent.detach()) * idx.size
            acc["kl"] += kl * idx.size
            acc["clip"] += clip_fraction(ratio[m].detach(), args.clip) * idx.size if bool(m.any()) else 0.0
            acc["ratio"] += float(ratio[m].mean().detach()) * idx.size if bool(m.any()) else 0.0
            seen += idx.size
            if args.max_kl and kl > args.max_kl:
                break
        row = {k: v / max(1, seen) for k, v in acc.items()}
        row["epoch"] = epoch
        history.append(row)
        print(f"epoch {epoch:>2}: loss {row['loss']:+.4f}（策略 {row['pi']:+.4f} / 价值 "
              f"{row['vf']:.4f}）熵 {row['ent']:.3f} KL {row['kl']:+.4f} 裁剪 {row['clip']:.3f} "
              f"ratio {row['ratio']:.3f}")
        if args.max_kl and row["kl"] > args.max_kl:
            stop_epoch = epoch
            print(f"⚠ KL {row['kl']:.4f} > {args.max_kl}：提前停（这一代不再多走 epoch）")
            break
    if mon:
        mon.close()
    wall = time.perf_counter() - wall

    val_split = ds.load_split(args.data, "val")
    vr = {"n": 0}
    if int(np.asarray(val_split["state"]).shape[0]) > 0:
        vtr = rewards.transitions(val_split, rank_weight=args.rank_weight)
        vr = value_report(value, val_split, vtr, device, batch=args.batch)
        print(f"价值诊断（val {vr['n']} 条）：MAE {vr['mae']:.4f}（常数基线 {vr['const_mae']:.4f}）"
              f"逐点 ρ {vr['pearson']:+.3f} / episode ρ {vr['ep_pearson']:+.3f}")
    actor.eval()

    out = paths.allocate("ckpt", args.label, need_bytes=8 * 1024**2)
    torch.save({"model": actor.state_dict(),
                "config": {"state_dim": features.state_dim(), "cand_dim": features.cand_dim(),
                           "hidden": args.hidden, "head": args.head},
                "feature_version": features.FEATURE_VERSION,
                "value": value.state_dict(),
                "ppo": {"temp": args.temp, "gamma": args.gamma, "lam": args.lam,
                        "clip": args.clip, "lr": args.lr, "epochs": args.epochs,
                        "adv_norm": args.adv_norm, "vf_coef": args.vf_coef,
                        "ent_coef": args.ent_coef, "rank_weight": args.rank_weight,
                        "init": str(args.init or ""), "data": str(args.data),
                        "stop_epoch": stop_epoch}},
               out / "model.pt")
    result = {"label": args.label, "data": str(args.data), "init": str(args.init or ""),
              "args": {k: v for k, v in vars(args).items()}, "device": device,
              "torch_threads": threads, "decisions": n, "student_rows": int(student.sum()),
              "wall_seconds": wall, "history": history, "val_value": vr}
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}；指标：{out / 'metrics.json'}（{wall:.1f}s）")
    print("⚠ 判据不是训练日志：强度只能由同牌山配对实战（mahjong_ml.eval）+ Elo 阶梯"
          "（mahjong_ml.league）给出。")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4：在线自对弈 PPO（策略从 BC/AWR 继续往上走）")
    ap.add_argument("--data", required=True, help="紧凑集目录（**必须是用当前网络采的**）")
    ap.add_argument("--init", default=None, help="策略/价值初始化权重（应与采集时那份**同一份**）")
    ap.add_argument("--explore-only", action="store_true",
                    help="只量「温度 → 偏离贪心比例」曲线（不训练）—— 用来选 `#<T>`")
    ap.add_argument("--ckpt", default=None, help="--explore-only 用的权重（缺省用 --init）")
    ap.add_argument("--split", default="val", choices=["val", "train"], help="--explore-only 读哪个切分")
    ap.add_argument("--rows", type=int, default=40000, help="--explore-only 用多少条")
    ap.add_argument("--init-value", default=None,
                    help="价值头冷启动用：P3 的 IQL critic（只取 trunk.* + v_head.*）；"
                         "`--init` 里已经带 value 时忽略它")
    ap.add_argument("--label", default="ppo-smoke")
    ap.add_argument("--temp", type=float, default=1.0,
                    help="行为策略温度（**必须与采集时 `#<T>` 一致**；π_old 靠它才算得对）")
    ap.add_argument("--rank-weight", type=float, default=1.0,
                    help="整场顺位点项权重（与 rewards.transitions 同口径）")
    ap.add_argument("--gamma", type=float, default=1.0, help="折扣（口径同 rewards.py：1.0）")
    ap.add_argument("--lam", type=float, default=1.0,
                    help="GAE λ。⚠ **默认 1.0 是有理由的**：奖励是「每条 episode 只在末决策记一次」，"
                         "λ<1 会让前几步的优势按剩余步数衰减 = 系统性偏差；λ=1 时 GAE 恰好塌成"
                         "`A = R − V`（自检里钉着这条等价性）")
    ap.add_argument("--clip", type=float, default=0.2, help="PPO 裁剪 ε")
    ap.add_argument("--adv-norm", type=int, default=1, choices=[0, 1],
                    help="优势是否按学生行批内标准化（默认 1；关掉就要求 V 的绝对尺度可信）")
    ap.add_argument("--vf-coef", type=float, default=0.5, help="价值损失系数")
    ap.add_argument("--ent-coef", type=float, default=0.01, help="熵奖励系数（防过早塌到贪心）")
    ap.add_argument("--max-kl", type=float, default=0.03, help="KL 早停阈值（0 = 不早停）")
    ap.add_argument("--grad-clip", type=float, default=0.5, help="梯度范数裁剪（0 = 不裁）")
    ap.add_argument("--epochs", type=int, default=4, help="同一批数据的 epoch 数（PPO 一般 3~10）")
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--max-steps", type=int, default=None, help="每 epoch 最多几步（冒烟用）")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--head", type=int, default=128)
    ap.add_argument("--threads", type=int, default=guard.TRAIN_THREADS)
    ap.add_argument("--seed", type=int, default=20260401)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)
    if args.explore_only:
        return explore(args)
    return 0 if train(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
