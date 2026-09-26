"""把各代 AI 打成服务端**即用的独立包**（`bot-ai/<名字>/`）。

    # 把各代网络 + 启发搜索（内置牌效）都打成包，放到服务端的 bot-ai/
    python -m mahjong_ml.packbot --out bot-ai \
        --from-dir S:\\mahjong-training\\ckpt --include ppo2-g05,ppo2-g04,awr-002,bc-003 \
        --builtin teacher,first --zip release\\bot-ai

⚠ **发布资产里的深度模型包不带 teacher 先验**（`bot.json` 里就没有 `alpha` 字段 = 纯网络，α=0）：
`--alpha auto` / `--alpha <数值>` 是**实验与评测臂**用的（P5b 混合），别拿它去打发布包 ——
理由：带了先验的包强度基准就变成 teacher（约 95% 决策听老师），而"这一代比上一代强多少"
要由**纯网络**的同牌山配对来量（见 `docs/BOT-AI.md` §6 与 `docs/TRAINING.md` §4 P5）。

## 包的统一接口（服务端 `mahjong/ai/BotAis.java` 读它）

一个包 = 一个目录，目录里的 `bot.json` 是**唯一接口**，两种形态同一套：

    bot-ai/ppo2-g04/          深度模型（纯 Java 手写前向）
      bot.json   {"kind":"net","name":"ppo2-g04","model":"net.bin","alpha":4,"temp":0,"note":"…"}
      net.bin    权重（由 checkpoint 导出，见 `export.py`）

    bot-ai/teacher/           启发搜索（内置牌效机器人）
      bot.json   {"kind":"builtin","policy":"teacher"}

- `kind`：`net`（深度模型）/ `builtin`（内置 `teacher|first|pass|random`）。**没写 kind 时**按载荷猜：
  有权重就是 `net`，否则看 `policy`。
- `alpha` / `temp`：深度模型的两个旋钮（P5b 的 teacher 先验 α、P4 的采样温度 T）。
  **发布包一律 α=0（纯网络，`bot.json` 里不写 `alpha`）**；`--alpha auto` 按 `hybrid.py` 的
  **让位曲线**选"约 95% 决策听老师"的那个 α —— 那属于**实验/评测臂**（混合体既保留网络主见、
  又不会比老师差，可证），不要写进发布资产。
- 名字必须是**可打印 ASCII**（它会进报文 `hello_ok.bot_ais` / `room.bot_ai`），
  且不许含 `,` `@` `#`（那是策略串的文法）。`teacher`/`first`/`pass`/`random` 这四个是内置锚点，
  **深度模型包不许占用这些名字**；只有「`kind=builtin` 且 `policy` 同名」的包可以
  —— 那正是把启发搜索按同一套格式声明一遍（所以 `bot-ai/teacher/` 是合法的）。
- 服务端**启动时自动挂载** `bot-ai/`（与 `replays/` 同层）里的每个包，不需要任何参数；
  坏包只跳过它一个（WARN），不会让服务起不来。

## 为什么做成"目录 + 清单"而不是"一个权重文件"

因为服务端要挂的**不只是深度模型**：启发搜索（内置牌效）没有权重文件，但它同样是一个"可选的机器人 AI"。
把两者收进同一个接口（`kind` + 载荷），客户端那边就只有一个下拉框、一套 `bot_ai` 名字，
服务端也只有一处注册表 —— 加一种新形态（比如以后的搜索增强 P6）只多一个 `kind`，不用动协议。
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np
import torch

from . import export, features, guard, hybrid, nets

#: 内置（启发搜索/脚本）策略：它们不需要权重文件，`policy` 直接写名字
BUILTIN_POLICIES = ("teacher", "first", "pass", "random")

#: 清单文件名（服务端只认这个名字）
MANIFEST = "bot.json"

#: 权重文件名（`kind=net` 的缺省 `model`）
DEFAULT_MODEL = "net.bin"


def _ascii_name(name: str, *, kind: str = "", policy: str = "") -> str:
    """名字的校验与服务端同一把尺子（可打印 ASCII + 内置名只在"内置包同义声明"时可用）。"""
    if not name:
        raise SystemExit("包名不能为空")
    if any(c < " " or c > "~" for c in name):
        raise SystemExit(f"包名必须是可打印 ASCII（会进报文）：{name!r}")
    if any(c in name for c in ",@#"):
        raise SystemExit(f"包名不许含 `,` `@` `#`（它们是策略串的文法）：{name!r}")
    if name in BUILTIN_POLICIES and not (kind == "builtin" and policy == name):
        raise SystemExit(
            f"包名不许叫内置策略名 {name!r} —— 只有「kind=builtin 且 policy 同名」的包可以"
            f"（那是把启发搜索按同一套格式声明一遍；深度模型包占用这些名字会顶掉锚点）")
    return name


def export_net(ckpt: Path, out_bin: Path) -> int:
    """checkpoint（`model.pt`）→ Java 侧权重文件；返回字节数。已存在就直接用。"""
    if out_bin.is_file():
        return out_bin.stat().st_size
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    if cfg["state_dim"] != features.state_dim() or cfg["cand_dim"] != features.cand_dim():
        raise SystemExit(f"{ckpt} 的特征维度 ({cfg['state_dim']},{cfg['cand_dim']}) != 代码 "
                         f"({features.state_dim()},{features.cand_dim()})")
    model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
    model.load_state_dict(ck["model"])
    export.save_weights(model, out_bin)
    return out_bin.stat().st_size


def alpha_for(ckpt: Path, data: Path, split: str, target: float) -> tuple[float, dict]:
    """`--alpha auto`：按让位曲线选"约 target 的决策听老师"的 α（α=0 = 纯网络）。"""
    rep = hybrid.analyse(ckpt, data, split=split, target=target)
    return float(rep["recommend_alpha"] or 0.0), rep


def write_package(out_root: Path, name: str, *, kind: str, model: str | None = None,
                  policy: str | None = None, alpha: float = 0.0, temp: float = 0.0,
                  note: str = "", force: bool = False) -> Path:
    """写一个包目录（`<out_root>/<name>/bot.json` + 载荷）。"""
    _ascii_name(name, kind=kind, policy=policy or "")
    d = out_root / name
    if d.exists() and not force:
        raise SystemExit(f"包已存在（要覆盖加 --force）：{d}")
    d.mkdir(parents=True, exist_ok=True)
    man: dict = {"kind": kind, "name": name}
    if kind == "net":
        man["model"] = model or DEFAULT_MODEL
        if alpha > 0:
            man["alpha"] = alpha
        if temp > 0:
            man["temp"] = temp
    elif kind == "builtin":
        if policy not in BUILTIN_POLICIES:
            raise SystemExit(f"未知的内置策略：{policy}（可用 {', '.join(BUILTIN_POLICIES)}）")
        man["policy"] = policy
    else:
        raise SystemExit(f"未知 kind：{kind}")
    if note:
        man["note"] = note
    (d / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return d


def write_readme(out_root: Path) -> None:
    """在包目录里放一份**给部署者看的**格式说明（服务端不读它）。"""
    (out_root / "README.txt").write_text(
        "这个目录是服务端的「机器人 AI 包」目录（与 replays/ players/ 同层）。\n"
        "服务端**启动时自动挂载**这里的每个一级子目录，不需要任何参数。\n"
        "\n"
        "一个包 = 一个目录，里面的 bot.json 是唯一接口：\n"
        "\n"
        "  深度模型（纯 Java 手写前向）：\n"
        "    <名字>/bot.json   {\"kind\":\"net\",\"name\":\"<名字>\",\"model\":\"net.bin\",\n"
        "                       \"alpha\":4,\"temp\":0,\"note\":\"...\"}\n"
        "    <名字>/net.bin    权重\n"
        " 启发搜索（内置牌效机器人）：\n"
        "    <名字>/bot.json   {\"kind\":\"builtin\",\"policy\":\"teacher\"}\n"
        "                      policy ∈ teacher | first | pass | random\n"
        "\n"
        "要点：\n"
        "  · 名字必须是可打印 ASCII（会进报文），且不许含 , @ #（策略串的文法）。\n"
        "    teacher/first/pass/random 是内置锚点：深度模型包不许占用，\n"
        "    只有 kind=builtin 且 policy 同名的包可以（见上面「启发搜索」那条）。\n"
        "  · alpha = teacher 先验权重（P5b 混合）：>0 时这个包就是「启发搜索 + 深度模型」的混合体，\n"
        "    越大越听老师（α 极大 ⇒ 逐决策等同老师，所以不会比老师差）。\n"
        "  · temp = 采样温度（P4 探索）：0 = 贪心。\n"
        "  · 坏包（清单读不出 / 未知 kind / 权重缺失或损坏 / 名字撞内置）只会跳过它一个，\n"
        "    其余包照常用（服务端启动日志里能看到 WARN）。\n"
        "  · 生成这些包用：python -m mahjong_ml.packbot（见仓库 docs/BOT-AI.md）。\n",
        encoding="utf-8")


def zip_packages(out_root: Path, zip_dir: Path) -> list[Path]:
    """把每个包压成**独立可分发的 zip**（解压到 `bot-ai/` 下即可用）。"""
    zip_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for d in sorted(p for p in out_root.iterdir() if p.is_dir()):
        if not (d / MANIFEST).is_file() and not (d / DEFAULT_MODEL).is_file():
            continue
        z = zip_dir / f"{d.name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as fh:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    fh.write(f, str(Path(out_root.name) / d.name / f.relative_to(d)))
        made.append(z)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把各代 AI 打成服务端即用的 bot-ai 包")
    ap.add_argument("--out", default="bot-ai", help="包目录（服务端启动目录下的 bot-ai/）")
    ap.add_argument("--from-dir", default=None,
                    help="checkpoint 根目录（每个子目录含 model.pt；配合 --include）")
    ap.add_argument("--include", default="", help="从 --from-dir 里挑哪几代（逗号分隔）")
    ap.add_argument("--from-ckpt", action="append", default=[],
                    help="直接给一个 checkpoint 目录（可重复）")
    ap.add_argument("--builtin", default="", help="要打哪些内置（启发搜索）包：" + "/".join(BUILTIN_POLICIES))
    ap.add_argument("--alpha", default="0", help="深度模型的 teacher 先验 α：数值 或 `auto`（让位曲线）")
    ap.add_argument("--temp", type=float, default=0.0, help="深度模型的采样温度（0 = 贪心）")
    ap.add_argument("--data", default=None, help="`--alpha auto` 用的紧凑数据集")
    ap.add_argument("--split", default="val", choices=["val", "train"])
    ap.add_argument("--target", type=float, default=0.95, help="`--alpha auto` 的让位目标")
    ap.add_argument("--zip", default=None, help="同时把每个包压成 <dir>/<名字>.zip（独立分发包）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的包")
    args = ap.parse_args(argv)

    guard.apply_cpu_limit()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ① 内置（启发搜索）包
    for p in [x.strip() for x in args.builtin.split(",") if x.strip()]:
        d = write_package(out, p, kind="builtin", policy=p,
                          note="内置启发搜索（牌效机器人）", force=args.force)
        print(f"内置包：{d}")

    # ② 深度模型包
    ckpts: list[Path] = [Path(p) for p in args.from_ckpt]
    if args.from_dir:
        root = Path(args.from_dir)
        names = [x.strip() for x in args.include.split(",") if x.strip()]
        if not names:
            raise SystemExit("--from-dir 要配 --include（用 `all` 表示全部含 model.pt 的子目录）")
        if names == ["all"]:
            names = sorted(p.name for p in root.iterdir()
                           if p.is_dir() and (p / "model.pt").is_file())
        for nm in names:
            ckpts.append(root / nm)
    alpha_mode = args.alpha.strip().lower()
    any_alpha = False                                   # 有没有包带了 teacher 先验（发版时该报警）
    for ck in sorted(set(ckpts)):
        name = ck.name
        _ascii_name(name, kind="net")
        model_pt = ck / "model.pt"
        if not model_pt.is_file():
            print(f"跳过 {ck}：没有 model.pt")
            continue
        alpha = 0.0
        note = f"来源 {ck}（打包 {time.strftime('%Y-%m-%d')}）"
        if alpha_mode == "auto":
            if not args.data:
                raise SystemExit("--alpha auto 需要 --data <紧凑数据集>")
            alpha, rep = alpha_for(model_pt, Path(args.data), args.split, args.target)
            note += (f"；α=auto（让位 {rep['defer_curve'].get(alpha, 0) * 100:.1f}%，"
                     f"数据集 {Path(args.data).name}）")
        else:
            alpha = float(alpha_mode or 0)
        if alpha > 0:
            any_alpha = True
        d = out / name
        if d.exists() and not args.force:
            print(f"跳过 {name}：包已存在（要覆盖加 --force）")
            continue
        d.mkdir(parents=True, exist_ok=True)
        size = export_net(model_pt, d / DEFAULT_MODEL)
        write_package(out, name, kind="net", model=DEFAULT_MODEL, alpha=alpha,
                      temp=args.temp, note=note, force=True)
        print(f"深度模型包：{d}（net.bin {size/1024:.0f} KB，α={alpha:g}，T={args.temp:g}）")

    write_readme(out)
    print(f"\n包目录：{out.resolve()}（服务端启动时自动挂载）")
    if args.zip and any_alpha:
        print("⚠ 发布资产按惯例**不带** teacher 先验（α=0）：上面有包带了 α>0 —— 那种包只适合"
              "实验/评测臂，发版请用 `--alpha 0`（见 docs/BOT-AI.md §6）")
    if args.zip:
        made = zip_packages(out, Path(args.zip))
        print(f"独立分发包 {len(made)} 个 → {Path(args.zip).resolve()}")
        for z in made:
            print(f"  {z.name}  {z.stat().st_size/1024:.1f} KB")
    print("提示：服务端跑起来后看启动日志里的「机器人 AI：…」，或客户端建房间时的「机器人 AI」下拉框。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
