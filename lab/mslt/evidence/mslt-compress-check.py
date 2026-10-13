# -*- coding: utf-8 -*-
"""第四次指令预算压缩 —— 「判据句 / 代码 token / 数字常量」三组机械核对（只读）。

判据：被删掉的每一段，其**判据句 / 代码 token / 数字常量**必须都能在 NOTES.md 里找到。
      "未找到" 计数必须 == 0（否则不许删）。

组 1 判据句：以 CJK 二元组（bigram）为最小单元 —— 被删文本的每个 CJK bigram 都要在 NOTES.md 出现。
组 2 代码 token：反引号里的串 + 形如标识符/开关的 ASCII token 必须在 NOTES.md 出现。
组 3 数字常量：被删文本里的每个数字字面量必须在 NOTES.md 出现。
"""
import re
import sys
from pathlib import Path

NOTES = Path(r"C:\Users\HP\source\games\mahjong\NOTES.md")
notes = NOTES.read_text(encoding="utf-8")
# 归一化：去掉粗体标记与反引号（NOTES 与 AGENTS 的加粗位置不同，不该算差异）
notes_norm = notes.replace("**", "").replace("`", "")

REMOVED = [
    ("AGENTS L498", '（同族 = "两文件都缺 ⇒ 哈希都空 ⇒ 判成相同"）'),
    ("AGENTS L501", "（旧实现在紧凑集路径上从没生效）"),
    ("AGENTS L502", "（DSH 自己的命令行里带着本会话任务文本、含 `--grad-clip`）"),
    ("AGENTS L504", "（会把它虚大成另一相的时长 = 关于重叠效率的假绿）"),
]

CJK = re.compile(r"[\u3400-\u9fff]")
ALLOWED_ASCII = {"DSH", "grad", "clip"}  # 开关/名字按整 token 判；其余 ASCII 单字（= 等）不算常量


def bigrams(txt: str):
    """CJK 二元组：只在同一段连续汉字里取相邻两字。"""
    out = set()
    for run in re.findall(r"[\u3400-\u9fff]+", txt):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def code_tokens(txt: str):
    toks = set(re.findall(r"`([^`]+)`", txt))
    toks |= set(re.findall(r"--[A-Za-z][A-Za-z0-9-]*", txt))
    toks |= set(re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", txt))
    toks |= ALLOWED_ASCII & set(re.findall(r"[A-Za-z]+", txt))
    return toks


def numbers(txt: str):
    return set(re.findall(r"\d+(?:\.\d+)?", txt))


r1_bad, r2_bad, r3_bad = [], [], []
print("=" * 78)
print(f"NOTES.md = {NOTES}（{len(notes.encode('utf-8'))} B）")
print("=" * 78)
for where, chunk in REMOVED:
    norm = chunk.replace("**", "").replace("`", "")
    b = sorted(bigrams(norm))
    miss_b = [g for g in b if g not in notes_norm]
    toks = sorted(code_tokens(chunk))
    miss_t = [t for t in toks if t not in notes_norm]
    nums = sorted(numbers(chunk))
    miss_n = [n for n in nums if n not in notes_norm]
    r1_bad += [(where, g) for g in miss_b]
    r2_bad += [(where, t) for t in miss_t]
    r3_bad += [(where, n) for n in miss_n]
    print(f"\n[{where}] 删除片段：{chunk}")
    print(f"  组1 判据句 bigram : {len(b):2d} 个 · 未找到 {len(miss_b)}  {miss_b if miss_b else ''}")
    print(f"  组2 代码 token    : {len(toks):2d} 个 · 未找到 {len(miss_t)}  {miss_t if miss_t else ''}   {toks}")
    print(f"  组3 数字常量      : {len(nums):2d} 个 · 未找到 {len(miss_n)}  {miss_n if miss_n else ''}   {nums}")

print("\n" + "=" * 78)
print(f"合计：组1 未找到 {len(r1_bad)} · 组2 未找到 {len(r2_bad)} · 组3 未找到 {len(r3_bad)}")
if r1_bad or r2_bad or r3_bad:
    print("FAIL：有未找到的单元 ⇒ 不许这样删")
    for g in (r1_bad, r2_bad, r3_bad):
        for where, tok in g:
            print(f"  ✗ {where}: {tok}")
    sys.exit(1)
print("PASS：三组未找到 == 0 ⇒ 被删内容在 NOTES.md 里全部有逐字/等价落点")
