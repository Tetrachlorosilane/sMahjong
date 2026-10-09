#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style-profile.py —— **风格剖面测量**（描述性），跑一组"风格桌"并把读数汇成一张表。

⛔⛔ **这不是判决判据。** 判决只看 `mahjong_ml/v4/gate.py`（多套新牌山 + 逐场配对差分的 CI 排除 0）。
    本工具产出的是**描述性剖面**：某模型在"某一种对手组成"下的和了率 / 放铳率 / 打点 / 收支 /
    顺位点。它**没有配对**、**没有 CI**、**不做采纳/回滚**，也**不比较两个模型谁更强** ——
    它只回答"这三条风格线在同一张桌上互相怎么影响"。⛔ 不要拿这里的数字去采纳或回滚任何一代。

为什么要它（设计约束逐条对应"为什么"）：

* **必须带 `--rotate-perm`**：`--rotate` 只是循环移位（4 个排列，且**保持循环序** ⇒ 各策略彼此的
  方位在一代之内固定），于是"上家/下家"这种**方位效应**会整份留在某个策略的读数里。`--rotate-perm`
  按场号枚举 4! = 24 个**全排列**（`kPerm24[g % 24]`）⇒ 每份策略在四个座位上出现次数相等，
  方位偏差被平均掉。**这正是"每个模型的均值可以直接读、不需要配对"的前提** —— 所以这里把它做成
  硬断言（见 `check_rotate_perm`），缺了就直接失败，而不是"悄悄跑出一份偏的读数"。
* **按 `seat_hands` 加权合并多个 seed**：一个 seed = 一套牌山。不同的牌山是**共模**误差分量
  （历史上同一候选换牌山会翻号），所以多套牌山只能用"按`seat_hands`加权的平均"来汇总；
  而**权重必须是该策略真正打过的局数**，不是场数（同一张桌上某个策略可能占 2 席 ⇒ 它的局数是别人的 2 倍）。
* **删轨迹、留摘要**：`g*.jsonl` 是唯一的体积来源（每 1,000 场 ≈ 2.8 GB），而本工具读的量**全在
  `summary.json` 的 `by_policy` 里** ⇒ 读完立刻删轨迹（`--prune-traces`，缺省开），只留 `summary.json`。
  这是本仓的既定规矩；`summary.json` 小到可以永久留存、随时重算这张表。
* **不碰 `python/`**：本工具是纯读 `summary.json` 的独立脚本（自带的解释器是 3.12 或任意 3.8+），
  不 import `mahjong_ml`，也不改任何训练侧代码 —— 测量工具不该有权限影响被测量的东西。

用法（完整示例见 `--help`）：

    python tools/style-profile.py \
      --trainer S:\\mahjong-training\\tools\\trainer\\trainer.exe \
      --out     S:\\mahjong-training\\style-profile \
      --games 1000 --seeds 41001101,41002237 --workers 12 \
      --model def7-g10=net:C:\\...\\v4-expert-def7-g10\\net.bin \
      --model teacher=teacher \
      --table mix-end=def7-g10,atk3-g10,win2-g10,teacher \
      --matrix def7-g10 --matrix atk3-g10 --matrix win2-g10

自检（跑不动/读错了就当场失败，绝不"静默给一张偏的表"）：
  ① 每张桌每个策略的 `by_policy[*].games` 必须等于 `场数 × 该策略在这张桌占的席位数`；
  ② 同一 run 里 `Σ by_policy[*].seat_hands` 必须等于 `4 × summary.json["hands"]`（= 场数×4×局数）；
  ③ 命令行里必须有 `--rotate-perm`（缺了 = 读数带方位偏差，直接失败）；
  ④ 跑完必须真的落盘 `summary.json` 且 `games` 与请求相符（工作区里的 trainer.exe 写数据根会被
     **静默吞掉**：跑满时间、零文件落盘、不报错 —— 这条断言就是它的探针）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 口径常量
# ---------------------------------------------------------------------------

#: `--rotate-perm` 是**读数可直读**的前提（见模块 docstring）。这里做成常量而不是散在代码里，
#: 是为了让"忘了带"只可能发生在一个地方，且自检能直接引用同一个字符串。
ROTATE_PERM_FLAG = "--rotate-perm"

#: 一桌四席。训练侧 `selfplay` 也是四席，写死在这里是为了让"席位数"这个概念的来源唯一。
SEATS_PER_TABLE = 4


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """仓库根 = 本文件的上一级目录的上一级（`tools/style-profile.py` ⇒ `<repo>`）。

    不写死绝对路径：本工具要能在别的机器/别的工作区副本上直接跑。
    """
    return Path(__file__).resolve().parent.parent


def default_trainer() -> str:
    """trainer 的解析顺序：`--trainer` → `$MAHJONG_TRAINER` → 仓库内 `trainer/build/trainer.exe`。

    ⚠ 为什么要有 `$MAHJONG_TRAINER`：**工作区内的那份 trainer.exe 写数据根会被静默吞掉**
    （实测跑满 11 分钟、CPU 7,896s、零文件落盘、不报错）⇒ 正式跑必须指向工作区外那份副本
    （`S:\\mahjong-training\\tools\\trainer\\trainer.exe`）。缺省回落到仓库内那份只是"能跑起来"，
    自检 ④ 会在落盘失败时把它抓出来。
    """
    env = os.environ.get("MAHJONG_TRAINER", "").strip()
    if env:
        return env
    return str(repo_root() / "trainer" / "build" / "trainer.exe")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="style-profile.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="风格剖面测量（描述性；⛔ 不是判决判据 —— 判决只看 mahjong_ml/v4/gate.py）",
        epilog=(
            "示例：\n"
            "  python tools/style-profile.py --games 1000 --seeds 41001101,41002237 \\\n"
            "    --model def7-g10=net:C:\\repo\\tools\\build\\v4-expert-def7-g10\\net.bin \\\n"
            "    --table mix-end=def7-g10,atk3-g10,win2-g10,teacher \\\n"
            "    --matrix def7-g10\n"
        ),
    )
    ap.add_argument("--trainer", default=default_trainer(),
                    help="trainer.exe 路径（缺省：$MAHJONG_TRAINER → 仓库内 trainer\\build\\trainer.exe）")
    ap.add_argument("--out", required=True,
                    help="产出根目录（每张桌每个 seed 一个子目录 <桌>-s<seed>/；轨迹读完后删掉）")
    ap.add_argument("--model", action="append", default=[], metavar="NAME=SPEC",
                    help="策略别名：NAME=SPEC。SPEC = `teacher`/`pass`/`first`/`random`，"
                         "或 `net:<net.bin 路径>[@α][#T]`，或裸路径（自动补 `net:`）。可重复。")
    ap.add_argument("--table", action="append", default=[], metavar="NAME=a,b,c,d",
                    help="一张桌 = 名字 + 逗号分隔的 4 个策略名（用 --model 定义）。可重复。")
    ap.add_argument("--games", type=int, required=True, help="每张桌每个 seed 的场数")
    ap.add_argument("--seeds", required=True,
                    help="逗号分隔的基准种子（每个 seed = 一套牌山）；同一张桌的多个 seed 按 seat_hands 加权合并")
    ap.add_argument("--workers", type=int, default=12, help="并行工作线程数（缺省 12）")
    ap.add_argument("--hands", type=int, default=0, help="每场最多 H 小局（0 = 完整半庄；缺省 0）")
    ap.add_argument("--preset", default="", help="规则预设（mleague|tenhou|majsoul|custom）；缺省交给 trainer")
    ap.add_argument("--reuse", action="store_true",
                    help="已有 summary.json 的 run 直接读、不重跑（删过轨迹之后靠它重算这张表）")
    ap.add_argument("--keep-traces", action="store_true",
                    help="保留 g*.jsonl（缺省**删**：本仓规矩 '删轨迹、留摘要'）")
    ap.add_argument("--matrix", action="append", default=[], metavar="NAME",
                    help="矩阵视图：打印该模型出现在**每一张**桌时的同一组指标 + 相对基准桌的位移。可重复。")
    ap.add_argument("--matrix-base", default="mix-end",
                    help="矩阵视图的基准桌（缺省 mix-end）；位移 = 该桌 − 基准桌。")
    ap.add_argument("--json-out", default="", help="把合并后的读数另存一份 JSON（便于二次分析）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令行，不跑")
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# 参数解析：模型别名 与 桌
# ---------------------------------------------------------------------------

def parse_models(specs):
    """`--model NAME=SPEC` → {NAME: 策略串}。SPEC 归一化：裸路径自动补 `net:`（trainer 的要求）。"""
    models = {}
    for s in specs:
        if "=" not in s:
            raise SystemExit(f"⛔ --model 要写成 NAME=SPEC，收到：{s}")
        name, spec = s.split("=", 1)
        name, spec = name.strip(), spec.strip()
        if not name or not spec:
            raise SystemExit(f"⛔ --model 的 NAME/SPEC 都不能为空：{s}")
        # 裸路径（含 `\\` 或 `/` 且没有 `:` 前缀）自动补 `net:` —— 少一个手滑的机会。
        if ":" not in spec and ("\\" in spec or "/" in spec):
            spec = "net:" + spec
        if name in models and models[name] != spec:
            raise SystemExit(f"⛔ 模型别名 {name} 被定义了两次且不同：{models[name]} vs {spec}")
        models[name] = spec
    return models


def parse_tables(specs, models):
    """`--table NAME=a,b,c,d` → [(NAME, [策略串×4], [名字×4])]（顺序即 trainer 的座位序）。"""
    tables = []
    for s in specs:
        if "=" not in s:
            raise SystemExit(f"⛔ --table 要写成 NAME=a,b,c,d，收到：{s}")
        name, seats = s.split("=", 1)
        name = name.strip()
        names = [x.strip() for x in seats.split(",") if x.strip()]
        if len(names) != SEATS_PER_TABLE:
            raise SystemExit(f"⛔ 桌 {name} 需要 {SEATS_PER_TABLE} 席，收到 {len(names)}：{names}")
        pol = []
        for n in names:
            if n not in models:
                raise SystemExit(f"⛔ 桌 {name} 引用了未定义的模型 {n}（先用 --model {n}=SPEC 定义）")
            pol.append(models[n])
        tables.append((name, pol, names))
    if not tables:
        raise SystemExit("⛔ 至少要给一张 --table")
    return tables


def seed_list(raw):
    out = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        out.append(int(x))
    if not out:
        raise SystemExit("⛔ --seeds 为空")
    if len(set(out)) != len(out):
        raise SystemExit(f"⛔ --seeds 里有重复（同一套牌山跑两遍 = 伪重复）：{out}")
    return out


# ---------------------------------------------------------------------------
# 命令行构造 + 自检 ③
# ---------------------------------------------------------------------------

def run_dir(out_root: Path, table: str, seed: int) -> Path:
    return out_root / f"{table}-s{seed}"


def build_argv(trainer: str, table: str, pol: list, games: int, seed: int, workers: int,
               hands: int, preset: str, out_dir: Path) -> list:
    """拼 `trainer selfplay` 的命令行 —— **这里是全工具唯一写 `--rotate-perm` 的地方**。"""
    argv = [trainer, "selfplay", str(games),
            "--workers", str(workers),
            "--rotate",            # 与 perm 同时给时 perm 优先（保住旧口径的意图），实际生效的是 perm
            ROTATE_PERM_FLAG,      # 自检 ③ 的来源：读数可直读的前提
            "--policy", ",".join(pol),
            "--seed", str(seed),
            "--out", str(out_dir)]
    if hands:
        argv += ["--hands", str(hands)]
    if preset:
        argv += ["--preset", preset]
    # 自检 ③：命令行里**必须**有 `--rotate-perm`。缺了 ⇒ 座位只循环移位 ⇒ 方位效应留在读数里
    # ⇒ 下面那张"每个模型的均值可以直接读"的表就是错的。宁可不跑，也不给一张偏的表。
    assert ROTATE_PERM_FLAG in argv, f"自检③失败：命令行缺 {ROTATE_PERM_FLAG}：{argv}"
    return argv


# ---------------------------------------------------------------------------
# 读 summary.json + 自检 ①②④
# ---------------------------------------------------------------------------

def _accumulate(stat):
    """把一个 `by_policy[策略]` 的**比率**还原成**可加累加量**。

    为什么要还原：跨 seed 合并必须按"该策略真正打过的局数"加权，而 `by_policy` 只给比率。
    直接对两个 seed 的比率取算术平均是错的（两个 seed 的局数不同、某个策略还可能占 2 席）。
    这里按定义反解（`win_rate = wins/seat_hands` 等），累加后再相除 —— 单 seed 时逐位等于原值。
    """
    sh = int(stat["seat_hands"])
    g = int(stat["games"])
    wins = sh * float(stat["win_rate"])
    deals = sh * float(stat["deal_in_rate"])
    return {
        "games": g,
        "seat_hands": sh,
        "wins": wins,
        "deals": deals,
        "delta_sum": sh * float(stat["avg_delta"]),
        "win_score_sum": wins * float(stat["avg_win_score"]),
        "place_sum": g * float(stat["avg_place"]),
        "rank_point_sum": g * float(stat["avg_rank_points"]),
    }


def load_run(out_dir: Path, table: str, pol: list, names: list, games: int):
    """读一个 run 的 `summary.json`，跑自检 ①②④，返回 (累加量字典, 原始 summary, 诊断行)。"""
    sp = out_dir / "summary.json"
    # 自检 ④：跑完必须**真的落盘**且场数相符。工作区里的 trainer.exe 写数据根会被静默吞掉
    # （跑满时间、零文件落盘、不报错）—— 这条断言就是那个症状的探针。
    if not sp.is_file():
        raise SystemExit(
            f"⛔ 自检④失败：{sp} 不存在。\n"
            f"   最可能的原因：trainer.exe 用的是**工作区内**那份 ⇒ 写数据根被静默吞掉。\n"
            f"   修法：Copy-Item trainer\\build\\trainer.exe S:\\mahjong-training\\tools\\trainer\\trainer.exe -Force\n"
            f"        并让 --trainer / $MAHJONG_TRAINER 指向 S: 上那份。")
    summary = json.loads(sp.read_text(encoding="utf-8"))
    if int(summary.get("games", -1)) != games:
        raise SystemExit(
            f"⛔ 自检④失败：{sp} 里 games={summary.get('games')}，请求的是 {games} —— 读到的是别的 run 的摘要？")

    by_policy = summary["by_policy"]
    hands = int(summary["hands"])

    # 自检 ②：Σ seat_hands == 4 × 总小局数（= 场数 × 4 席 × 平均局数）。
    # 对不上 ⇒ 我们读错了口径（例如把 `hands` 当成每场局数、或把别家的账算进来）。
    total_seat_hands = sum(int(v["seat_hands"]) for v in by_policy.values())
    want = SEATS_PER_TABLE * hands
    if total_seat_hands != want:
        raise SystemExit(
            f"⛔ 自检②失败：{sp} 里 Σ seat_hands={total_seat_hands}，但 4×hands={want}"
            f"（hands={hands}）—— 读错了口径，别拿这张表下结论。")

    # 自检 ①：每个策略的场数 == 场数 × 它在这张桌占的席位数。
    # 依据是 `selfplay.cpp` 的累加处：`for (座位 i) if 标签匹配 then games++` ⇒ 占 2 席就翻倍。
    acc = {}
    diag = {}
    for name in dict.fromkeys(names):                     # 去重但保序
        seats = names.count(name)
        exp_games = games * seats
        # 标签 = trainer 原样写在轨迹/摘要里的策略串（**完整**策略串，不是短名）
        polstr = pol[names.index(name)]
        if polstr not in by_policy:
            raise SystemExit(f"⛔ 自检①失败：{sp} 的 by_policy 里没有 {polstr!r}；实际有 {list(by_policy)}")
        got = int(by_policy[polstr]["games"])
        if got != exp_games:
            raise SystemExit(
                f"⛔ 自检①失败：桌 {table} 的策略 {name} 在 {sp} 里 games={got}，"
                f"期望 场数{games} × 席位数{seats} = {exp_games}。")
        # 同一策略占多席时，`by_policy` 只有**一条**合并项（标签相同）—— 这是对的，
        # 而且它正是"席位数"这个概念的来源：占 2 席 ⇒ 局数也是别人的 2 倍。
        acc[name] = _accumulate(by_policy[polstr])
        # 原始读数（`by_policy` 里那一条）也留一份：合并是"从什么合出来的"必须可核。
        diag[name] = {"seats": seats, "policy": polstr, **by_policy[polstr]}
    return acc, summary, diag


def merge_runs(accs):
    """把多个 seed 的累加量按 `seat_hands`（对和了/打点则是 `wins`）加权合并成一张读数。

    ⚠ 权重不是场数：`win_rate` / `deal_in_rate` / `avg_delta` 的分母是**局数**（seat_hands），
    `avg_win_score` 的分母是**和了次数**（wins）。全用"场数"加权会在"某席占 2 席"或
    "两个 seed 局数不同"时给出**看似合理但错**的数。
    """
    out = {}
    for name in accs[0]:
        keys = accs[0][name]
        s = {k: 0.0 for k in ("games", "seat_hands", "wins", "deals",
                              "delta_sum", "win_score_sum", "place_sum", "rank_point_sum")}
        for a in accs:
            for k in s:
                s[k] += a[name][k]
        sh, g, w = s["seat_hands"], s["games"], s["wins"]
        out[name] = {
            "games": int(round(g)),
            "seat_hands": int(round(sh)),
            "win_rate": (s["wins"] / sh) if sh else 0.0,
            "deal_in_rate": (s["deals"] / sh) if sh else 0.0,
            "avg_win_score": (s["win_score_sum"] / w) if w else 0.0,
            "avg_delta": (s["delta_sum"] / sh) if sh else 0.0,
            "avg_place": (s["place_sum"] / g) if g else 0.0,
            "avg_rank_points": (s["rank_point_sum"] / g) if g else 0.0,
        }
    return out


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_table_md(rows, title):
    """`桌 | 策略 | 席数 | 和了率 | 放铳率 | 平均打点 | 平均收支 | 平均顺位点`（+ 加权用的两列）。"""
    lines = [f"### {title}", "",
             "| 桌 | 策略 | 席数 | 席位 | 打过的局 | 和了率 | 放铳率 | 平均打点 | 平均收支 | 平均顺位点 | 平均顺位 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        lines.append("| {table} | {name} | {games} | {seats} | {hand} | {wr:.1f}% | {dr:.1f}% | {ws:.0f} | "
                     "{dl:+.0f} | {rp:+.2f} | {ap:.3f} |".format(
                         table=r["table"], name=r["name"], games=r["games"], seats=r["seats"],
                         hand=r["seat_hands"], wr=100 * r["win_rate"], dr=100 * r["deal_in_rate"],
                         ws=r["avg_win_score"], dl=r["avg_delta"], rp=r["avg_rank_points"],
                         ap=r["avg_place"]))
    return lines


def render_matrix(rows, focal, base_table):
    """矩阵视图：只留 focal 一个模型，逐桌列出"对手组成"与相对基准桌的位移。

    这是"某个终点模型面对**单一**对手风格"的影响矩阵 —— 关键列是**对手组成**（除 focal 外三席），
    没有它这张表读不出来（同一个 focal 在不同桌上对手不同）。
    """
    mine = [r for r in rows if r["name"] == focal]
    if not mine:
        return [f"### 矩阵视图：{focal}", "", f"⛔ 读不到：没有任何一张桌包含 {focal}。"]
    base = next((r for r in mine if r["table"] == base_table), None)
    lines = [f"### 矩阵视图：{focal}（位移 = 该桌 − 基准桌 {base_table}）", ""]
    if base is None:
        lines += [f"⚠ 基准桌 {base_table} 上没有 {focal} ⇒ 只列绝对值、不列位移。", ""]
    lines += ["| 桌 | 对手组成（除本模型外 3 席） | 和了率 | Δ和了率 | 放铳率 | Δ放铳率 | 平均打点 | Δ打点 | "
              "平均收支 | Δ收支 | 平均顺位点 | Δ顺位点 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in mine:
        def d(key, scale=1.0, fmt="{:+.2f}"):
            if base is None:
                return "—"
            return fmt.format(scale * (r[key] - base[key]))
        lines.append(
            "| {t} | {opp} | {wr:.1f}% | {dwr} | {dr:.1f}% | {ddr} | {ws:.0f} | {dws} | {dl:+.0f} | {ddl} | "
            "{rp:+.2f} | {drp} |".format(
                t=r["table"], opp=r["opponents"], wr=100 * r["win_rate"],
                dwr=d("win_rate", 100, "{:+.1f}pp"), dr=100 * r["deal_in_rate"],
                ddr=d("deal_in_rate", 100, "{:+.1f}pp"), ws=r["avg_win_score"],
                dws=d("avg_win_score", 1, "{:+.0f}"), dl=r["avg_delta"],
                ddl=d("avg_delta", 1, "{:+.0f}"), rp=r["avg_rank_points"],
                drp=d("avg_rank_points", 1, "{:+.2f}")))
    return lines


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None):
    # ⚠ Windows 上 Python 的 stdout 缺省按**本地代码页**（这里 GBK）编码，而本工具的表头里有 `⛔`/`⇒`
    # 这类字符 ⇒ 会直接 UnicodeEncodeError 崩在打印上。所以**先**把 stdout/stderr 钉成 UTF-8
    # （`errors='replace'`：控制台缺字形时宁可显示 `?`，也不要因为一个装饰字符丢掉整张表）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):        # 3.6 或已被重定向到不支持的对象
            pass

    args = parse_args(argv)
    models = parse_models(args.model)
    tables = parse_tables(args.table, models)
    seeds = seed_list(args.seeds)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    if not Path(args.trainer).is_file():
        raise SystemExit(f"⛔ 找不到 trainer：{args.trainer}")

    # 子进程环境：照抄仓库的既定环境（`MAHJONG_DATA_ROOT` 指数据根）。解释器/路径不由这里管 ——
    # trainer.exe 是 C++ 的，不吃 PYTHONPATH；但桌面上的任何"数据根"约定要保持一致。
    env = dict(os.environ)
    env.setdefault("MAHJONG_DATA_ROOT", r"S:\mahjong-training")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    manifest = {"trainer": args.trainer, "games": args.games, "seeds": seeds,
                "workers": args.workers, "out": str(out_root), "runs": []}
    all_rows = []
    t_all = time.time()

    for table, pol, names in tables:
        accs = []
        for seed in seeds:
            rd = run_dir(out_root, table, seed)
            sp = rd / "summary.json"
            argv_run = build_argv(args.trainer, table, pol, args.games, seed, args.workers,
                                  args.hands, args.preset, rd)
            reused = False
            if args.reuse and sp.is_file():
                reused = True
            else:
                if sp.is_file() and not args.reuse:
                    # 不 reuse 就得重跑 ⇒ 先清掉上一次的同名 run（否则新旧 g*.jsonl 会混在一个目录里）
                    for old in rd.glob("g*.jsonl"):
                        old.unlink()
                    sp.unlink(missing_ok=True)
                rd.mkdir(parents=True, exist_ok=True)
                print(f"[跑] {table} seed={seed} games={args.games} ⇒ {rd}", flush=True)
                if args.dry_run:
                    print("     " + " ".join(argv_run), flush=True)
                    continue
                t0 = time.time()
                # 不 capture 会把 trainer 的中文汇总直接打到终端（好：跑得慢时能看见进度）；
                # 但为了失败时能报出原因，这里收 stderr、stdout 让它走终端。
                p = subprocess.run(argv_run, env=env, stderr=subprocess.PIPE)
                dt = time.time() - t0
                if p.returncode != 0:
                    raise SystemExit(f"⛔ trainer 退出码 {p.returncode}：{' '.join(argv_run)}\n"
                                     f"   stderr: {p.stderr.decode('utf-8', 'replace')[-2000:]}")
                print(f"     用时 {dt:.1f}s", flush=True)
            if args.dry_run:
                continue

            acc, summary, diag = load_run(rd, table, pol, names, args.games)
            accs.append(acc)
            manifest["runs"].append({"table": table, "seed": seed, "dir": str(rd),
                                     "argv": argv_run, "reused": reused,
                                     "hands": summary.get("hands"),
                                     "ryukyoku_rate": summary.get("ryukyoku_rate"),
                                     "policies": pol, "by_policy": diag})

            # 删轨迹、留摘要（本仓既定规矩）：读数**全在** by_policy 里，jsonl 只是体积。
            if not args.keep_traces:
                files = list(rd.glob("g*.jsonl"))
                mb = sum(f.stat().st_size for f in files) / 2 ** 20
                for f in files:
                    f.unlink()
                if files:
                    print(f"     轮换删除 {len(files)} 个 jsonl（{mb:,.0f} MB；保留 summary.json）", flush=True)

        if args.dry_run:
            continue
        merged = merge_runs(accs)
        seats_of = {n: names.count(n) for n in dict.fromkeys(names)}
        for name, st in merged.items():
            all_rows.append({"table": table, "name": name, "seats": seats_of[name],
                             "opponents": "+".join(sorted(n for n in names if n != name)) or "(无)",
                             **st})

    if args.dry_run:
        return 0

    # ---- 输出 -------------------------------------------------------------
    print()
    print("=" * 110)
    print("风格剖面测量（**描述性**；⛔ 不是判决判据 —— 判决只看 mahjong_ml/v4/gate.py）")
    print("=" * 110)
    print(f"trainer = {args.trainer}")
    print(f"场数/桌/seed = {args.games}；seeds = {seeds}（每张桌共 {args.games * len(seeds)} 场）")
    print(f"座位：`--rotate-perm`（24 全排列）⇒ 每份策略在四个座位上次数相等，均值可直读")
    print(f"合并：同张桌的多个 seed 按 **seat_hands** 加权（和了/放铳/收支）与 **wins** 加权（打点）")
    print()

    md = [f"# 风格剖面读数（描述性；⛔ 不是判据）", "",
          f"- trainer：`{args.trainer}`",
          f"- 场数：每桌每 seed {args.games} 场 × {len(seeds)} 个 seed（`{','.join(map(str, seeds))}`）"
          f" = 每桌 {args.games * len(seeds)} 场",
          f"- 座位：`--rotate-perm`（4! = 24 全排列）⇒ 座位偏差被平均掉，均值可直接读",
          f"- 合并口径：同桌多 seed 按 `seat_hands` 加权（和了率/放铳率/收支）与 `wins` 加权（打点）",
          f"- 精度（2,000 场/桌量级）：和了率约 ±0.6pp、顺位点约 ±2、打点约 ±250；"
          f"**2+1+1 桌上只占 1 席的模型精度更差**",
          ""]
    md += render_table_md(all_rows, "全部桌 × 策略")
    md += [""]
    for focal in args.matrix:
        md += render_matrix(all_rows, focal, args.matrix_base)
        md += [""]

    # 终端也打一遍（Markdown 直接可读；表在终端里也排得开）
    for line in md:
        print(line)

    (out_root / "style-profile.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_rows, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"\n[落盘] {out_root / 'style-profile.md'}")
    print(f"[落盘] {out_root / 'manifest.json'}")
    print(f"[总用时] {time.time() - t_all:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
