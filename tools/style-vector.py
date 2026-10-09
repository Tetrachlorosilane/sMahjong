#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style-vector.py —— 从**自对弈轨迹**（`g*.jsonl`）算出每个策略的**风格向量**。

⛔⛔ **这不是判决判据。** 判决只看 `mahjong_ml/v4/gate.py`（多套新牌山 + 逐场配对差分的 CI 排除 0）。
    本工具只回答"这个策略**怎么打**"（立直/副露/巡目/默听取向），供**奖励塑形**与**验收测量**
    共用同一套口径。它**不比较谁更强**、**不做采纳/回滚**，也**没有显著性检验**。
    一句话：`gate.py` 决定"要不要这一代"，本工具决定"这一代的风格漂了多少"。

为什么要它（而不是再读一遍 `summary.json`）：
  * `summary.json` 的 `by_policy` 只有**和了率 / 放铳率 / 打点 / 收支 / 顺位**这几条"结果轴"。
    "立直多不多 / 副露多不多 / 和的早不早 / 会不会默听"这些**过程轴**它一个都没有 —— 而奖励塑形
    恰恰要塑形过程（结果轴已经在 `delta` 里了，再塑形就是重复计分）。
  * 轨迹里**有**这些量：`obs.riichi[4]` / `obs.riichi_turn[4]`（obs v3）/ `obs.melds[4]` /
    `obs.player_draws`，外加 `chosen` 里的 `riichi:*` / `chi:*` / `pon:*` / `kan:*` / `tsumo` / `ron`。
    本工具只从这些字段读，**取不到就写"取不到 + 缺哪个字段"，绝不编、也不拿相近字段冒充**。

口径的三条纪律（每条都对应一个"曾经会踩的坑"）：
  ① **分母 = `seat_hands`（该策略真正打过的小局数），不是场数。** 同一张桌上某个策略可能占 2 席
     ⇒ 它的局数是别人的 2 倍；用场数加权会得到"看似合理但错"的比率（`style-profile.py` 同一条纪律）。
     本工具的分母**按"场号 × 席位标签"构造**（`Σ_games 占该策略的席位数 × 该场小局数`），
     所以自检 ② 的 `Σ seat_hands == 4 × summary.hands` 是**构造出来的**，不是巧合；
     也顺带躲开"某小局某席一次询问都没有"（庄家第 1 张就被荣和等）导致分母缩水的错误。
  ② **和了 / 放铳 / 平均打点必须与 `summary.json` 的 `by_policy` 逐项相等**（自检 ①）——
     本工具与生产者（`trainer/src/selfplay.cpp` 的 `wins` / `winPoints` / `deals`）读的是
     **同一本账**：`wins ⟺ winner == 席`、`avg_win_score ⟺ Σ delta[winner] / wins`
     （收点 + 本场棒 + 供託一起算，别在这里另立"纯打点"口径）。对不上就当场失败，不静默给数。
  ③ **`--rotate-perm` 是读数可直读的前提**（自检 ③）：`--rotate` 只是循环移位（4 个排列且保持循环序）
     ⇒ 方位效应会整份留在某个策略的读数里。所以生产模式下**必须**带 `--rotate-perm`，
     而且这里做成硬断言；读别人的老目录时退化成"数据侧体检"（逐策略的座位直方图，见报告）。

用法（两种模式）：

    # ① 生产模式：自己跑一次自对弈（**自动带 --rotate-perm**），跑完读数、默认删轨迹留摘要
    python tools/style-vector.py --run-out S:\\mahjong-training\\style-vector\\tf-200-s71260201 \\
        --trainer S:\\mahjong-training\\tools\\trainer\\trainer.exe \\
        --games 200 --policy teacher,teacher,first,first --seed 71260201 --workers 8 \\
        --json S:\\mahjong-training\\style-vector\\tf-200-s71260201\\style-vector.json \\
        --md   S:\\mahjong-training\\style-vector\\tf-200-s71260201\\style-vector.md

    # ② 读盘模式：读**已有**目录（可多个，按策略名合并；`--reuse` 语义天然成立）
    python tools/style-vector.py <dir1> [<dir2> ...] --json out.json --md out.md

自检（跑的时候打印；失败即非 0 退出，绝不"静默给一张偏的表"）：
  ① `win_rate` / `deal_in_rate` / `avg_win_score`（+ `games` / `seat_hands`）与
     该目录 `summary.json` 的 `by_policy` **逐项相等**（比率用 `abs(diff) <= 1e-9`，
     和了/放铳**另按整数计数**再对一遍）；
  ② `Σ(每策略 seat_hands) == 4 × summary.hands`；
  ③ 生产模式的命令行**必须**含 `--rotate-perm`（缺了 `assert` 直接炸）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 口径常量
# ---------------------------------------------------------------------------

#: `--rotate-perm` 是"读数可直读"的前提（见模块 docstring）。做成常量是为了让"忘了带"
#: 只可能发生在一个地方，且自检 ③ 与构造命令行的代码引用**同一个字符串**。
ROTATE_PERM_FLAG = "--rotate-perm"

#: 一桌四席。写死在这里是为了让"席位数"这个概念的来源唯一。
SEATS_PER_TABLE = 4

#: 和了 / 放铳 / 打点与 `summary.json` 对账的浮点容差（比率与均值都应该是**同一次除法**的结果，
#: 所以正常就是 0；留 1e-9 只是为了不被 JSON 往返的十进制表示差异误伤）。
CHECK_TOL = 1e-9

#: 策略在做某个动作时用的 `chosen` 前缀（`PROTOCOL.md` §8.3 的动作键文法）。
RIICHI_PREFIX = "riichi:"
MELD_PREFIXES = ("chi:", "pon:", "kan:")
WIN_ACTIONS = ("tsumo", "ron")

#: **风格向量的定义表**（唯一数据源：MD 的定义表与 JSON 的 `dimensions` 都从这里渲染）。
#: 每维写清「分子 / 分母 / 来源字段」—— 报告里必须能一眼看出"这个数是怎么除出来的"。
DIMENSIONS = [
    {
        "key": "riichi_rate",
        "name": "立直率",
        "num": "该策略**宣言过立直**的小局数（同一小局宣言多次只算 1）",
        "den": "该策略打过的小局数（= seat_hands）",
        "src": "obs.riichi[seat] / obs.riichi_turn[seat] > 0 / chosen 以 riichi: 开头（三者取或）",
        "note": "分母含流局与途中流局；宣言后放铳/被荣和照样计入（它确实立直了）。",
    },
    {
        "key": "meld_rate",
        "name": "副露率",
        "num": "该策略**有副露**的小局数（吃 / 碰 / 暗杠 / 加杠 / 大明杠任一，同局多次只算 1）",
        "den": "该策略打过的小局数（= seat_hands）",
        "src": "obs.melds[seat] 非空（四家副露是公开状态）/ chosen 以 chi: / pon: / kan: 开头 / events 里的 meld·kan",
        "note": "暗杠也算「有副露」（口径 = 吃碰杠任一次）；它不是门前清，但门清与否另有 menzen 位。",
    },
    {
        "key": "win_rate",
        "name": "和了率",
        "num": "该策略和了的小局数（多家荣和时只算离放铳者最近那家 = hand.winner 同口径）",
        "den": "该策略打过的小局数（= seat_hands）",
        "src": "hand 行：agari == true 且 winner == 该席",
        "note": "⚠ 必须与 summary.by_policy.win_rate 逐项相等（自检 ①）；流局满贯不计（winner = -1）。",
    },
    {
        "key": "deal_rate",
        "name": "放铳率",
        "num": "该策略放铳的小局数",
        "den": "该策略打过的小局数（= seat_hands）",
        "src": "hand 行：loser == 该席",
        "note": "⚠ 必须与 summary.by_policy.deal_in_rate 逐项相等（自检 ①）。",
    },
    {
        "key": "avg_win_score",
        "name": "平均打点",
        "num": "该策略所有和了小局的 delta[winner] 之和（**含本场棒与供託**，与 summary 同一本账）",
        "den": "该策略的和了次数（= wins，**不是** seat_hands）",
        "src": "hand 行：delta[winner]（= trainer/src/selfplay.cpp 的 winPoints）",
        "note": "⚠ 必须与 summary.by_policy.avg_win_score 逐项相等（自检 ①）；0 次和了时输出 null（不写 0 —— 那是「没有数」不是「打点 0」）。",
    },
    {
        "key": "avg_win_turn",
        "name": "平均和了巡目",
        "num": "每个和了小局的「和了巡目」之和",
        "den": "**能定位到和了巡目的**和了次数（覆盖不到的在报告里单独计数，不塞进分母）",
        "src": "该席在和了那一小局里 chosen ∈ {tsumo, ron} 的决策行的 obs.player_draws",
        "note": "巡目 = 和了者**到和了那一刻为止已摸过几次牌**（PROTOCOL §8.2 与 riichi_turn 同一把尺子）："
                "自摸 = 本次摸牌序号；荣和 = 该家已摸次数（它还没摸下一张）。⛔ 不是「全场第几巡」。",
    },
    {
        "key": "avg_riichi_turn",
        "name": "平均立直巡目",
        "num": "每个立直小局的 riichi_turn 之和",
        "den": "**能取到立直巡目的**立直小局数",
        "src": "obs.riichi_turn[seat]（obs v3 字段；若立直就是本局最后一次询问，回退到宣言那行的 obs.player_draws —— PROTOCOL §8.2 判据 1 保证两者是同一个数）",
        "note": "obs v2 轨迹**没有**这个字段 ⇒ 该维输出 null 并在 unavailable 里写明缺哪个字段（⛔ 不用相近字段冒充）。",
    },
    {
        "key": "dama_rate",
        "name": "默听率（和了但本局未立直）",
        "num": "和了且该小局**未立直**的和了次数（含「没听牌就自摸」的平和手 —— 口径是「未立直」，不是「故意默听」）",
        "den": "该策略的和了次数（= wins）",
        "src": "hand 行 winner == 该席 且 该小局该席 obs.riichi == false",
        "note": "与 riichi_win_rate 配对，用来刻画**默听/立直取向**；0 次和了时输出 null。"
                "⚠ 它量不出「听得见却故意不立直」（那需要逐巡的听牌形，轨迹里没有和了前的听牌集合），"
                "所以它是**上界意义**的默听率：把「没听牌就自摸」也算进去了。",
    },
    {
        "key": "riichi_win_rate",
        "name": "立直后和了率",
        "num": "立直过且**最终和了**的小局数",
        "den": "该策略立直过的小局数（= riichi_rate 的分子）",
        "src": "hand 行 winner == 该席 且 该小局该席 obs.riichi == true",
        "note": "立直小局为 0 时输出 null（分母为 0。⛔ 不写 0.00%：那会被读成「立直了但一次没和」）。",
    },
]

#: 逐策略的整数计数（JSON 的 `counts`）—— 奖励塑形要的是**分子/分母**，不是只有比率。
COUNT_KEYS = (
    "seat_hands", "games", "wins", "win_points", "deals", "deal_points",
    "riichi_hands", "meld_hands", "riichi_wins", "dama_wins",
    "win_turn_sum", "win_turn_known", "win_turn_missing",
    "riichi_turn_sum", "riichi_turn_known", "riichi_turn_missing",
    "hands_no_decisions",
)


def repo_root() -> Path:
    """仓库根 = 本文件的上一级目录的上一级（`tools/style-vector.py` ⇒ `<repo>`）。

    不写死绝对路径：本工具要能在别的机器 / 别的工作区副本上直接跑。
    """
    return Path(__file__).resolve().parent.parent


def default_trainer() -> str:
    """trainer 的解析顺序：`--trainer` → `$MAHJONG_TRAINER` → 仓库内 `trainer/build/trainer.exe`。

    ⚠ 为什么要有 `$MAHJONG_TRAINER`：**工作区内的那份 trainer.exe 写数据根会被静默吞掉**
    （实测跑满 11 分钟、零文件落盘、不报错）⇒ 正式跑必须指向工作区外那份副本
    （`S:\\mahjong-training\\tools\\trainer\\trainer.exe`）。
    """
    env = os.environ.get("MAHJONG_TRAINER", "").strip()
    if env:
        return env
    return str(repo_root() / "trainer" / "build" / "trainer.exe")


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="style-vector.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="风格向量提取（测量/特征工具；⛔ 不是判决判据 —— 判决只看 mahjong_ml/v4/gate.py）",
        epilog=(
            "示例：\n"
            "  # 生产模式（自动 --rotate-perm；默认读完删轨迹、留 summary.json）\n"
            "  python tools/style-vector.py --run-out S:\\\\mahjong-training\\\\style-vector\\\\tf-200 \\\n"
            "    --games 200 --policy teacher,teacher,first,first --seed 71260201 --workers 8 \\\n"
            "    --json out.json --md out.md\n"
            "  # 读盘模式（可多个目录，按策略名合并）\n"
            "  python tools/style-vector.py <dir1> <dir2> --json out.json --md out.md\n"
        ),
    )
    ap.add_argument("dirs", nargs="*", help="自对弈目录（含 g*.jsonl 与 summary.json）；可给多个，按策略名合并")
    ap.add_argument("--json", dest="json_path", default="", help="风格向量的机器可读输出（供奖励塑形读）")
    ap.add_argument("--md", dest="md_path", default="", help="风格向量的人读表（含定义表 + 自检原始输出）")
    ap.add_argument("--out", default="",
                    help="产出**前缀**：一次写出 <前缀>.json 与 <前缀>.md（= 同时给 --json/--md 的简写）")
    # ---- 生产模式（自己跑一次自对弈）
    ap.add_argument("--run-out", default="", help="生产模式：自对弈落地目录（不存在就建）")
    ap.add_argument("--trainer", default=default_trainer(),
                    help="trainer.exe 路径（缺省：$MAHJONG_TRAINER → 仓库内 trainer\\build\\trainer.exe）")
    ap.add_argument("--games", type=int, default=0, help="生产模式：场数")
    ap.add_argument("--policy", default="teacher,teacher,first,first",
                    help="生产模式：四家策略（逗号分隔），如 teacher,teacher,first,first")
    ap.add_argument("--seed", type=int, default=71260201, help="生产模式：基准种子")
    ap.add_argument("--workers", type=int, default=8, help="生产模式：并行线程数")
    ap.add_argument("--preset", default="", help="生产模式：规则预设（mleague|tenhou|majsoul|custom）")
    ap.add_argument("--keep-traces", action="store_true",
                    help="读完**不删** g*.jsonl（缺省删：本仓规矩「删轨迹、留摘要」，摘要留着就能随时重算）")
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# 生产模式：构造命令行 + 自检 ③
# ---------------------------------------------------------------------------

def build_selfplay_argv(trainer: str, games: int, policy: str, seed: int, workers: int,
                        out_dir: Path, preset: str = "",
                        drop_rotate_perm: bool = False) -> list:
    """拼 `trainer selfplay` 的命令行 —— **这里是全工具唯一写 `--rotate-perm` 的地方**。

    `drop_rotate_perm=True` **只给自检 ③ 用**（故意拼一个**不带** `--rotate-perm` 的命令行，
    来证明那条断言是活的）；⛔ 生产路径永远走缺省 `False`。
    """
    argv = [trainer, "selfplay", str(games),
            "--workers", str(workers),
            "--policy", policy,
            "--seed", str(seed)]
    if not drop_rotate_perm:
        argv.append(ROTATE_PERM_FLAG)   # 读数可直读的前提（见模块 docstring）
    argv += ["--out", str(out_dir)]
    if preset:
        argv += ["--preset", preset]
    # 自检 ③：命令行里**必须**有 `--rotate-perm`。缺了 ⇒ 座位只循环移位（4 个排列、保持循环序）
    # ⇒ 方位效应整份留在读数里 ⇒ "每个策略的均值可以直接读"这句话就是错的。宁可不跑，也不给一张偏的表。
    #
    # ⚠ 判据**写死字面量**，刻意不引用 `ROTATE_PERM_FLAG`：引用常量的话，一旦常量被改坏（或拼命令行
    # 的地方漏了它）两边一起变，这条断言就退化成**恒真式**（第一版就是这么写的，活体演示当场抓到）。
    assert "--rotate-perm" in argv, f"自检③失败：命令行缺 --rotate-perm：{argv}"
    return argv


def check_rotate_perm_guard() -> tuple:
    """自检 ③ 的**活体自证**：既证明正常路径拼得出带 `--rotate-perm` 的命令行，
    又证明"故意不带它"时断言真的会炸（工具每次运行都会打印这一条）。"""
    good = build_selfplay_argv("trainer.exe", 1, "teacher", 1, 1, Path("."))
    fired = False
    msg = ""
    try:
        build_selfplay_argv("trainer.exe", 1, "teacher", 1, 1, Path("."), drop_rotate_perm=True)
    except AssertionError as e:
        fired = True
        msg = str(e)
    ok = (ROTATE_PERM_FLAG in good) and fired
    return ok, (f"正常命令行含 {ROTATE_PERM_FLAG}：" + str(ROTATE_PERM_FLAG in good)
                + f"；故意去掉后 assert：{'触发（' + msg + '）' if fired else '⚠ **没有**触发'}")


def run_selfplay(args, out_dir: Path) -> str:
    """跑一次 `trainer selfplay`（生产模式）；返回用过的命令行（原样记进 JSON）。"""
    argv = build_selfplay_argv(args.trainer, args.games, args.policy, args.seed, args.workers,
                               out_dir, args.preset)
    if not Path(args.trainer).is_file():
        raise SystemExit(f"⛔ 找不到 trainer：{args.trainer}\n"
                         f"   （工作区里那份写数据根会被静默吞掉：跑满时间、零文件、不报错。）")
    env = dict(os.environ)
    env.setdefault("MAHJONG_DATA_ROOT", "S:\\mahjong-training")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[跑] {' '.join(argv)}", flush=True)
    p = subprocess.run(argv, env=env)
    if p.returncode != 0:
        raise SystemExit(f"⛔ trainer 退出码 {p.returncode}：{' '.join(argv)}")
    return " ".join(argv)


# ---------------------------------------------------------------------------
# 读轨迹
# ---------------------------------------------------------------------------

def new_counts() -> dict:
    return {k: 0 for k in COUNT_KEYS}


def blank_hand_state() -> dict:
    """一个小局的**公开状态**（四家）。只装本工具要用的量，别把整个 obs 留下来（内存）。"""
    return {
        "riichi": [False] * SEATS_PER_TABLE,        # 这一小局该席是否宣言过立直
        "meld": [False] * SEATS_PER_TABLE,          # 这一小局该席是否有过副露（含暗杠）
        "riichi_turn": [0] * SEATS_PER_TABLE,       # obs.riichi_turn（v3；0 = 未立直）
        "decl_draw": [0] * SEATS_PER_TABLE,         # 立直**宣言那一手**的 player_draws（回退用）
        "win_turn": [0] * SEATS_PER_TABLE,          # chosen ∈ {tsumo, ron} 时的 player_draws
    }


def read_dir(dir_path: Path):
    """读一个自对弈目录，返回 (逐策略计数, 逐策略-座位直方图, 诊断)。

    ⚠ 为什么按"场号 × 席位标签"构造分母（而不是数"有决策的 (场, 小局, 席)"）：
      庄家第一张就被荣和 / 九种九牌之类的小局里，某些席位**根本没有询问** ⇒ 数决策会把分母缩小，
      而 `summary.json` 的 `seat_hands` 不是那么算的（自检 ② 会立刻对不上）。
      所以分母从 `game.policies` + 小局行数推出来 —— 与生产者同一把尺子。
    """
    files = sorted((f for f in dir_path.iterdir() if re.fullmatch(r"g\d+\.jsonl", f.name)),
                   key=lambda f: int(f.name[1:-6]))
    if not files:
        raise SystemExit(f"⛔ 目录里没有 g*.jsonl：{dir_path}")

    counts: dict = {}                        # policy -> counts
    seat_hist: dict = {}                     # policy -> [该策略坐在座位 0..3 的场数]
    policies_of_game: dict = {}              # game -> [四席策略串]
    hands_per_game: dict = {}                # game -> 小局行数
    sampled_every = 1
    obs_min_v = None
    diag = {"decisions": 0, "hands": 0, "games": 0, "riichi_from_decl_fallback": 0,
            "win_turn_missing": 0, "hands_no_decisions": 0, "claimed_melds_unseen": 0}

    for f in files:
        game_labels = None
        states: dict = {}                    # hand_no -> 公开状态
        hand_rows = []
        game_row = None
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                t = row.get("type")
                if t == "decision":
                    diag["decisions"] += 1
                    obs = row["obs"]
                    v = int(obs.get("v", 2))
                    obs_min_v = v if obs_min_v is None else min(obs_min_v, v)
                    st = states.get(row["hand_no"])
                    if st is None:
                        st = states[row["hand_no"]] = blank_hand_state()
                    seat = int(row["seat"])
                    chosen = row["chosen"]
                    # 立直：三个来源取或（公开状态位 / 立直巡数 / 动作键）—— 任何一个漏了都不至于丢账
                    if obs["riichi"][seat] or (obs.get("riichi_turn") or [0] * 4)[seat] > 0 \
                            or chosen.startswith(RIICHI_PREFIX):
                        st["riichi"][seat] = True
                    rt = (obs.get("riichi_turn") or [0] * 4)[seat]
                    if rt > 0:
                        st["riichi_turn"][seat] = max(st["riichi_turn"][seat], int(rt))
                    if chosen.startswith(RIICHI_PREFIX):
                        st["decl_draw"][seat] = int(obs.get("player_draws") or 0)
                    # 副露：四家副露是**公开状态**，所以随便哪一席的 obs 都能读出别家的副露
                    # （`--no-claims` 只丢鸣牌决策行、丢不掉这个数组）
                    for s in range(SEATS_PER_TABLE):
                        if obs["melds"][s]:
                            st["meld"][s] = True
                    if chosen.startswith(MELD_PREFIXES):
                        st["meld"][seat] = True
                    # 和了巡目：只有**和了那一手自己的决策行**才有（别人的行没有这次摸牌序号）
                    if chosen in WIN_ACTIONS and int(row.get("hand_winner", -1)) == seat:
                        pd = int(obs.get("player_draws") or 0)
                        if pd > 0:
                            st["win_turn"][seat] = pd
                elif t == "hand":
                    hand_rows.append(row)
                    diag["hands"] += 1
                elif t == "game":
                    game_row = row
                else:
                    raise SystemExit(f"⛔ 未知行类型 {t}（{f}）—— 这不是本工具认识的轨迹格式")

        g = int((game_row or {}).get("game", (hand_rows[0]["game"] if hand_rows else 0)))
        if game_row:
            sampled_every = max(sampled_every, int(game_row.get("sampled_every", 1) or 1))
            game_labels = list(game_row.get("policies") or [])
        if not game_labels:
            # 兜底：从决策行反推（正常永远用不到；缺 `policies` 的老轨迹也不至于直接崩）
            raise SystemExit(f"⛔ {f} 没有 game 行 / 没有 policies —— 无法把席位映射到策略")
        policies_of_game[g] = game_labels
        hands_per_game[g] = len(hand_rows)
        diag["games"] += 1

        for i, lab in enumerate(game_labels):          # 座位直方图（`--rotate-perm` 的数据侧体检）
            h = seat_hist.setdefault(lab, [0] * SEATS_PER_TABLE)
            if i < SEATS_PER_TABLE:
                h[i] += 1
            # ⚠ `games` 的口径是「**场 × 席**」（占 2 席就翻倍），不是"打过的小局数" ——
            #   它与 summary.by_policy.games 同一个尺子（自检 ① 会逐项对账）。
            #   所以这里**按场**加一次，绝不能放进下面那个"逐小局"的循环里。
            counts.setdefault(lab, new_counts())["games"] += 1

        for row in hand_rows:
            st = states.get(row["hand_no"])
            if st is None:
                diag["hands_no_decisions"] += 1
                st = blank_hand_state()
            agari = bool(row.get("agari"))
            winner = int(row.get("winner", -1))
            loser = int(row.get("loser", -1))
            delta = row.get("delta") or [0] * SEATS_PER_TABLE
            for s in range(SEATS_PER_TABLE):
                lab = game_labels[s]
                c = counts.setdefault(lab, new_counts())
                c["seat_hands"] += 1
                if st["riichi"][s]:
                    c["riichi_hands"] += 1
                    rt = st["riichi_turn"][s] or st["decl_draw"][s]
                    if rt > 0:
                        c["riichi_turn_sum"] += rt
                        c["riichi_turn_known"] += 1
                        if not st["riichi_turn"][s]:
                            diag["riichi_from_decl_fallback"] += 1
                    else:
                        c["riichi_turn_missing"] += 1
                if st["meld"][s]:
                    c["meld_hands"] += 1
            if agari and 0 <= winner < SEATS_PER_TABLE:
                c = counts[game_labels[winner]]
                c["wins"] += 1
                c["win_points"] += int(delta[winner])
                if st["riichi"][winner]:
                    c["riichi_wins"] += 1
                else:
                    c["dama_wins"] += 1
                wt = st["win_turn"][winner]
                if wt > 0:
                    c["win_turn_sum"] += wt
                    c["win_turn_known"] += 1
                else:
                    c["win_turn_missing"] += 1
                    diag["win_turn_missing"] += 1
            if 0 <= loser < SEATS_PER_TABLE:
                c = counts[game_labels[loser]]
                c["deals"] += 1
                c["deal_points"] += -int(delta[loser])

    diag["obs_min_v"] = obs_min_v
    diag["sampled_every"] = sampled_every
    diag["hands_no_decisions"] = diag["hands_no_decisions"]
    return counts, seat_hist, policies_of_game, hands_per_game, diag


# ---------------------------------------------------------------------------
# 比率（分子/分母都在 counts 里，**先累加再除** —— 跨目录合并必须这样）
# ---------------------------------------------------------------------------

def _div(num: int, den: int):
    """分母为 0 时返回 None（= "没有这个数"）。⛔ 不返回 0.0 —— 那会被读成"真的是 0"。"""
    return (num / den) if den else None


def ratios(c: dict, riichi_turn_available: bool) -> dict:
    wins = c["wins"]
    return {
        "riichi_rate": _div(c["riichi_hands"], c["seat_hands"]),
        "meld_rate": _div(c["meld_hands"], c["seat_hands"]),
        "win_rate": _div(c["wins"], c["seat_hands"]),
        "deal_rate": _div(c["deals"], c["seat_hands"]),
        "avg_win_score": _div(c["win_points"], wins),
        "avg_win_turn": _div(c["win_turn_sum"], c["win_turn_known"]),
        "avg_riichi_turn": (_div(c["riichi_turn_sum"], c["riichi_turn_known"])
                            if riichi_turn_available else None),
        "dama_rate": _div(c["dama_wins"], wins),
        "riichi_win_rate": _div(c["riichi_wins"], c["riichi_hands"]),
    }


def unavailable_dims(riichi_turn_available: bool, policies: dict) -> dict:
    """**取不到的维必须点名缺哪个字段**（⛔ 不许编、也不许拿相近字段冒充）。"""
    out = {}
    if not riichi_turn_available:
        out["avg_riichi_turn"] = ("取不到：轨迹里没有 `obs.riichi_turn`（它是 **obs v3** 才有的字段，"
                                  "本目录的 obs.v < 3）⇒ 立直发生在第几巡无从得知")
    for name, st in policies.items():
        r = st["dims"]
        for k in ("avg_win_score", "dama_rate"):
            if r[k] is None:
                out.setdefault(k, {})[name] = "取不到：该策略 0 次和了（分母 wins=0）—— 这是「没有数」，不是 0"
        if r["riichi_win_rate"] is None:
            out.setdefault("riichi_win_rate", {})[name] = "取不到：该策略 0 次立直（分母 riichi_hands=0）"
        if r["avg_win_turn"] is None and st["counts"]["wins"] > 0:
            out.setdefault("avg_win_turn", {})[name] = (
                "取不到：和了那一手的决策行没有 player_draws / 或轨迹被采样过"
                "（缺 `chosen ∈ {tsumo, ron}` 且 hand_winner == 该席 的决策行）")
        if r["avg_riichi_turn"] is None and riichi_turn_available and st["counts"]["riichi_hands"] > 0:
            out.setdefault("avg_riichi_turn", {})[name] = (
                "取不到：立直巡数与宣言那一手的 player_draws 都取不到（缺 `obs.riichi_turn` 与 `obs.player_draws`）")
    return out


# ---------------------------------------------------------------------------
# 自检 ①②
# ---------------------------------------------------------------------------

def check_against_summary(dir_path: Path, counts: dict, hands_per_game: dict) -> tuple:
    """自检 ①（逐项与 summary.by_policy 相等）+ ②（Σ seat_hands == 4 × hands）。

    返回 ([(名称, 是否通过, 明细)], summary 字典或 None)。
    """
    checks = []
    sp = dir_path / "summary.json"
    if not sp.is_file():
        checks.append(("① 与 summary.json 对账", False, f"{sp} 不存在 —— 没有对账基准，拒绝给数"))
        return checks, None
    summary = json.loads(sp.read_text(encoding="utf-8"))
    by = summary.get("by_policy") or {}
    total = sum(int(c["seat_hands"]) for c in counts.values())
    want = SEATS_PER_TABLE * int(summary.get("hands", -1))
    checks.append(("② Σ(每策略 seat_hands) == 4 × summary.hands", total == want,
                   f"Σ seat_hands = {total}，4 × summary.hands({summary.get('hands')}) = {want}"
                   f"（差 {total - want}）"))
    # ②b：本工具**读到的**小局行数必须等于 summary 记的总小局数 —— 它守的是"我读全了"，
    # 而 ② 守的是"分母构造对了"（两者都过，比率才谈得上可信）。
    hands_read = sum(int(n) for n in hands_per_game.values())
    checks.append(("②b Σ(逐场读到的小局行数) == summary.hands", hands_read == int(summary.get("hands", -1)),
                   f"读到 {hands_read} 行小局 vs summary.hands = {summary.get('hands')}"
                   f"（共 {len(hands_per_game)} 场）"))
    for lab, s in sorted(by.items()):
        if lab not in counts:
            checks.append((f"① {lab}", False, "summary 里有这个策略，轨迹里一条决策都没有"))
            continue
        c = counts[lab]
        sh = int(s.get("seat_hands", -1))
        sg = int(s.get("games", -1))
        # 先按**整数计数**对一遍（比率是除出来的，整数对得上才是真的对得上）
        int_wins = round(float(s.get("win_rate", 0.0)) * sh)
        int_deals = round(float(s.get("deal_in_rate", 0.0)) * sh)
        ok_int = (c["seat_hands"] == sh and c["games"] == sg
                  and c["wins"] == int_wins and c["deals"] == int_deals)
        detail = (f"seat_hands {c['seat_hands']} vs {sh}；games {c['games']} vs {sg}；"
                  f"wins {c['wins']} vs {int_wins}；deals {c['deals']} vs {int_deals}")
        checks.append((f"① {lab} 整数计数（wins/deals/games/seat_hands）", ok_int, detail))
        # 再按**浮点逐项**对一遍（判据：abs(diff) <= 1e-9；正常恒等于 0）
        mine_win = _div(c["wins"], c["seat_hands"])
        mine_deal = _div(c["deals"], c["seat_hands"])
        mine_score = _div(c["win_points"], c["wins"])
        t_win = float(s.get("win_rate", 0.0))
        t_deal = float(s.get("deal_in_rate", 0.0))
        t_score = float(s.get("avg_win_score", 0.0))
        d_win = abs((mine_win or 0.0) - t_win)
        d_deal = abs((mine_deal or 0.0) - t_deal)
        score_ok = True
        d_score_txt = "n/a（summary 与轨迹都是 0 次和了）"
        if c["wins"] > 0:
            d_score = abs((mine_score or 0.0) - t_score)
            score_ok = d_score <= CHECK_TOL
            d_score_txt = f"{mine_score!r} vs {t_score!r}，|diff| = {d_score:.3g}"
        checks.append((f"① {lab} win_rate / deal_rate / avg_win_score（≤ {CHECK_TOL:g}）",
                       d_win <= CHECK_TOL and d_deal <= CHECK_TOL and score_ok,
                       f"win_rate {mine_win!r} vs {t_win!r}（|diff| = {d_win:.3g}）；"
                       f"deal_rate {mine_deal!r} vs {t_deal!r}（|diff| = {d_deal:.3g}）；"
                       f"avg_win_score {d_score_txt}"))
    return checks, summary


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def fmt_pct(x):
    return "n/a" if x is None else f"{100 * x:.2f}%"


def fmt_num(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_definition_md() -> list:
    lines = ["### 定义表（每维的分子 / 分母 / 来源字段）", "",
             "| 维度 | 含义 | 分子 | 分母 | 来源字段 | 口径注 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for d in DIMENSIONS:
        lines.append(f"| `{d['key']}` | {d['name']} | {d['num']} | {d['den']} | `{d['src']}` | {d['note']} |")
    return lines


def render_table_md(policies: dict) -> list:
    lines = ["| 策略 | 场·席 | 打过的局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 平均和了巡 | "
             "平均立直巡 | 默听率 | 立直后和了率 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for lab, st in sorted(policies.items(), key=lambda kv: -kv[1]["counts"]["seat_hands"]):
        c, r = st["counts"], st["dims"]
        lines.append(
            f"| {lab} | {c['games']} | {c['seat_hands']} | {fmt_pct(r['riichi_rate'])} | "
            f"{fmt_pct(r['meld_rate'])} | {fmt_pct(r['win_rate'])} | {fmt_pct(r['deal_rate'])} | "
            f"{fmt_num(r['avg_win_score'], 0)} | {fmt_num(r['avg_win_turn'])} | "
            f"{fmt_num(r['avg_riichi_turn'])} | {fmt_pct(r['dama_rate'])} | "
            f"{fmt_pct(r['riichi_win_rate'])} |")
    return lines


def render_counts_md(policies: dict) -> list:
    """分子/分母的**整数**也打一遍：比率对不上时，一眼能看出是分子错还是分母错。"""
    lines = ["| 策略 | wins | win_points | deals | riichi_hands | meld_hands | riichi_wins | dama_wins | "
             "win_turn 已知/缺 | riichi_turn 已知/缺 | 无决策小局 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for lab, st in sorted(policies.items()):
        c = st["counts"]
        lines.append(f"| {lab} | {c['wins']} | {c['win_points']} | {c['deals']} | {c['riichi_hands']} | "
                     f"{c['meld_hands']} | {c['riichi_wins']} | {c['dama_wins']} | "
                     f"{c['win_turn_known']}/{c['win_turn_missing']} | "
                     f"{c['riichi_turn_known']}/{c['riichi_turn_missing']} | {c['hands_no_decisions']} |")
    return lines


def render_seat_hist_md(seat_hist: dict, residual: int) -> list:
    """数据侧体检：`--rotate-perm` 下每个策略在四个座位上次数应当相等。

    ⚠ 判据要说清楚"允许多大抖动"：`--rotate-perm` 按**场号枚举 24 个全排列**，所以场数不是 24 的
    整数倍时，多出来的 `G % 24` 场只能落在部分排列上 ⇒ 每个座位**允许**最多 `residual` 的残余偏差。
    把它写成"必须完全相等"会把一份**正确**的读数判成"方位效应没平均掉"（本工具第一版就犯过）。
    ⚠ 这里只给**迹象**：`--rotate-perm` 的证明在生产模式的自检 ③（命令行硬断言）；轨迹被删掉之后
    数据侧根本无从复核 —— 那不叫"通过"，叫"没有证据"。
    """
    lines = ["| 策略 | 座位0 | 座位1 | 座位2 | 座位3 | 极差 | 理想值/座 | 最大偏差 | 结论 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for lab, h in sorted(seat_hist.items()):
        span = max(h) - min(h)
        ideal = sum(h) / SEATS_PER_TABLE
        dev = max(abs(v - ideal) for v in h)
        if dev == 0:
            verdict = "完全平衡（`--rotate-perm` 的迹象）"
        elif dev <= max(1.0, float(residual)):
            verdict = f"平衡（残余 {dev:g} ≤ 场数余数 {residual}，来自「场数不是 24 的整数倍」）"
        else:
            verdict = f"⚠ 不平衡（偏差 {dev:g} > 允许的 {max(1.0, float(residual)):g}）—— 方位效应可能没被平均掉"
        lines.append(f"| {lab} | {h[0]} | {h[1]} | {h[2]} | {h[3]} | {span} | {ideal:g} | {dev:g} | {verdict} |")
    return lines


def render_checks_md(checks: list) -> list:
    lines = ["| 自检项 | 结果 | 原始输出 |", "| --- | --- | --- |"]
    return lines + [f"| {n} | {'✅ 通过' if ok else '❌ 失败'} | {d} |" for n, ok, d in checks]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None):
    # ⚠ Windows 上 Python 的 stdout 缺省按**本地代码页**（这里 GBK）编码，而本工具的表头里有 `⛔`/`⇒`
    # 这类字符 ⇒ 会直接 UnicodeEncodeError 崩在打印上。所以先把 stdout/stderr 钉成 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)
    if args.out:                       # `--out <前缀>` 是 `--json <前缀>.json --md <前缀>.md` 的简写
        args.json_path = args.json_path or (args.out + ".json")
        args.md_path = args.md_path or (args.out + ".md")
    if not args.dirs and not args.run_out:
        raise SystemExit("⛔ 至少给一个自对弈目录，或用 --run-out 走生产模式（见 --help）")
    if args.run_out and not args.games:
        raise SystemExit("⛔ --run-out 需要 --games（生产模式）")

    produced_argv = ""
    if args.run_out:
        run_out = Path(args.run_out)
        produced_argv = run_selfplay(args, run_out)
        dirs = [run_out]
    else:
        dirs = [Path(d) for d in args.dirs]

    per_dir = []
    merged_counts: dict = {}
    merged_hist: dict = {}
    all_checks = []
    unavailable: dict = {}
    riichi_turn_available = True
    # 自检 ③（命令行必须含 `--rotate-perm`）——每次运行都当场自证一遍，并把结论写进报告。
    guard_ok, guard_detail = check_rotate_perm_guard()
    print(f"[自检③] {guard_detail}", flush=True)
    all_checks.append(("③ 命令行必须含 --rotate-perm（活体自证）", guard_ok, guard_detail))
    # 座位直方图的"允许残余"：`--rotate-perm` 按场号枚举 24 个全排列 ⇒ 场数不是 24 的整数倍时，
    # 多出来的那些场只能落在部分排列上，每个座位因此**允许** ≤ Σ(G % 24) 的残余偏差。
    hist_residual = 0

    for d in dirs:
        if not d.is_dir():
            raise SystemExit(f"⛔ 不是目录：{d}")
        counts, hist, pol_of_game, hands_of_game, diag = read_dir(d)
        # 采样过的轨迹不能用来量巡目（`--sample k` 会丢掉绝大多数决策行）—— 明确拒绝，不给偏的数
        if diag["sampled_every"] > 1:
            raise SystemExit(f"⛔ {d} 是采样轨迹（sampled_every={diag['sampled_every']}）："
                             f"决策行被抽稀过 ⇒ 巡目/立直/副御都量不准。请用完整轨迹重跑。")
        v3 = (diag["obs_min_v"] or 3) >= 3
        riichi_turn_available = riichi_turn_available and v3
        hist_residual += diag["games"] % 24
        checks, summary = check_against_summary(d, counts, hands_of_game)
        all_checks += [(f"[{d.name}] {n}", ok, det) for n, ok, det in checks]
        # 合并：**先累加整数分子/分母**，比率留到最后一次除（跨目录/跨 seed 只能这么合）
        for lab, c in counts.items():
            m = merged_counts.setdefault(lab, new_counts())
            for k in COUNT_KEYS:
                m[k] += c[k]
        for lab, h in hist.items():
            mh = merged_hist.setdefault(lab, [0] * SEATS_PER_TABLE)
            for i in range(SEATS_PER_TABLE):
                mh[i] += h[i]
        r = {lab: {"dims": {k: v for k, v in ratios(c, v3).items()},
                   "counts": dict(c)} for lab, c in counts.items()}
        per_dir.append({"dir": str(d), "summary": summary, "diagnostics": diag,
                        "checks": [{"check": n, "ok": ok, "detail": det} for n, ok, det in checks],
                        "policies": {lab: {"dims": s["dims"], "counts": s["counts"]}
                                     for lab, s in r.items()}})

    policies = {}
    for lab, c in merged_counts.items():
        policies[lab] = {"dims": ratios(c, riichi_turn_available), "counts": dict(c)}
    unavailable = unavailable_dims(riichi_turn_available, policies)

    ok_all = all(ok for _, ok, _ in all_checks)
    payload = {
        "tool": "tools/style-vector.py",
        "warning": "⛔ 这不是判决判据：判决只看 mahjong_ml/v4/gate.py。本文件是测量/特征工具，"
                   "供奖励塑形与验收测量共用同一套口径。",
        "note": "同一个值既供奖励塑形读（counts 里的分子/分母），也供验收测量读（dims 里的比率）——"
                "两边必须用同一套口径，否则「塑形的东西」与「验收的东西」会漂移。",
        "obs_version_min": min((pd["diagnostics"]["obs_min_v"] or 3) for pd in per_dir),
        "producer_argv": produced_argv,
        "dimensions": DIMENSIONS,
        "unavailable": unavailable,
        "policies": policies,
        "seat_histogram": merged_hist,
        "runs": per_dir,
        "checks": [{"check": n, "ok": ok, "detail": det} for n, ok, det in all_checks],
        "checks_all_ok": ok_all,
    }

    md = []
    md += ["# 风格向量读数（从**轨迹**算；⛔ 不是判决判据 —— 判决只看 `mahjong_ml/v4/gate.py`）", ""]
    if produced_argv:
        md += [f"- 生产命令行（自检 ③：含 `{ROTATE_PERM_FLAG}`）：`{produced_argv}`", ""]
    md += [f"- 输入目录：{', '.join(str(d) for d in dirs)}（共 {len(dirs)} 个；按策略名**先累加整数分子/分母再相除**合并）",
           f"- 轨迹 obs 最低版本：{payload['obs_version_min']}（`avg_riichi_turn` 需要 obs v3 的 `riichi_turn`）",
           ""]
    md += render_definition_md()
    md += ["", "### 读数表", ""]
    md += render_table_md(policies)
    md += ["", "### 分子 / 分母（整数；比率对不上时先看这里）", ""]
    md += render_counts_md(policies)
    md += ["", "### 座位直方图（`--rotate-perm` 的数据侧体检）", "",
           f"每个策略在四个座位上出现的场数；「允许残余」= Σ(场数 % 24) = **{hist_residual}**"
           f"（`--rotate-perm` 按场号枚举 24 个全排列，多出来的场只能落在部分排列上）。", ""]
    md += render_seat_hist_md(merged_hist, hist_residual)
    md += ["", "### 自检原始输出", ""]
    md += render_checks_md(all_checks)
    md += ["", f"**总判定：{'✅ 自检全通过' if ok_all else '❌ 有自检失败 —— 这些数不能用'}**", ""]
    if unavailable:
        md += ["### 取不到的维（缺哪个字段）", "", "```json",
               json.dumps(unavailable, ensure_ascii=False, indent=1), "```", ""]

    text = "\n".join(md)
    print(text)
    if args.md_path:
        Path(args.md_path).write_text(text + "\n", encoding="utf-8")
        print(f"\n[落盘] {args.md_path}")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        print(f"[落盘] {args.json_path}")

    # 删轨迹、留摘要（本仓既定规矩）：轨迹是唯一的体积来源，而 summary.json 小到可以永久留存。
    # ⚠ 只在**生产模式**删（读盘模式删别人的目录就是越权）。
    if args.run_out and not args.keep_traces:
        removed = 0
        mb = 0.0
        for f in Path(args.run_out).glob("g*.jsonl"):
            mb += f.stat().st_size / 2 ** 20
            f.unlink()
            removed += 1
        print(f"[删轨迹] {removed} 个 g*.jsonl（{mb:,.0f} MB）；保留 summary.json + 本工具的两份产物")

    if not ok_all:
        print("STYLE-VECTOR FAIL（自检没过）", file=sys.stderr)
        return 1
    print("STYLE-VECTOR PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
