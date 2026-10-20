# lr 配方小规模扫描（第一百一十九轮 · 2026-10-20；⛔ 未采集/未跑闸门/未发布）

> 问题：`lr 3e-3 + floored-cosine(0.75)` 在阶段 3 后段 `KL/100step` 跌破 ε、top1 比掉到 0.977。本轮**只改 lr**（⛔ 不动结构、⛔ 不加历史网），**一次一个变量**。

## 1 口径
- 数据 = `.tmp/mv1-s37/compact`（**唯一完整既有 compact**：39,709 训练决策 / 55 场 / 1,851 验证，`dataset v2 · feature v4 · aux`）；阶段 3 的 8 份只剩 `meta.json`。
- 起始网 = `.tmp/mv1-s38/nets/A8.bin`（链尖 `F060B7AA…`），`--init` == `--behaviour` == A8、温度 1.0；`--epochs 16 --max-steps 75 --batch 512` = **1,200 步**；`--objective ppo --value-target final --stage-a 0 --stage-b 0 --grad-clip 0.5 --kl-early-stop 1.0 --arch a- --seed 20261201`。
- ⚠ 偏离：该 compact 无 `h0` ⇒ 6 点全部 `--no-carry`（同条件；只影响与阶段 3 的绝对值对照）；`--lr-warmup-steps` 为本轮新增 lr 旋钮（缺省 0 = 逐位不变）。

## 2 读数（C8-v2 六腿 + 冻写四判据；每点墙钟 ≈2.6 min，训练相 150–153 s）

| 候选 | lr / 调度 | `KL/100step` | top1 学生/init（比） | `frac(u==0)` | `tile_id` | `stop_reason` | C8-v2 | ①②③④ |
| --- | --- | ---: | --- | ---: | ---: | --- | --- | --- |
| 对照 | 3e-3 · floored 0.75 | **0.1101** | 0.6861/0.6948（**0.988**） | 0.0000 | 1.1175 | 空 | PASS | ✓**✗**✓✓ |
| ① | 2e-3 · floored 0.75 | 0.0598 | 0.6921/0.6948（0.996） | 0.0000 | 1.1982 | 空 | PASS | ✓✓✓✓ |
| ② | 4e-3 · floored 0.75 | 0.0961 | 0.6829/0.6948（**0.983**） | 0.0000 | 1.1422 | 空 | PASS | ✓**✗**✓✓ |
| ③ | 3e-3 · floored 0.50 | 0.0751 | 0.6899/0.6948（0.993） | 0.0000 | 1.1934 | 空 | PASS | ✓✓✓✓ |
| ④ | 3e-3 · constant | **0.0904** | 0.6888/0.6948（0.991） | 0.0000 | 1.1530 | 空 | PASS | ✓✓✓✓ |
| ⑤ | 3e-3 · floored 0.75 + warmup 100 | 0.0616 | 0.6791/0.6948（**0.977**） | 0.0000 | 1.1510 | 空 | PASS | ✓**✗**✓✓ |

- 六腿（M1 `KL/100step` / M2 1200-1200 / M3 无早停 / M4 top1≥0.8×init / M5 `frac(u==0)`<10% / M6 `tile_id`≥0.01）**6/6 PASS**；⚠ 冻写 ② 比 M4（0.8×）**严** ⇒ "C8-v2 PASS" ≠ "② PASS"。
- KL 末窗 W=150 的 p50（同序）：0.0981 / 0.0670 / 0.1243 / 0.0957 / 0.1045 / 0.0655。逐点墙钟：156.9 / 158.4 / 155.9 / 155.7 / 156.2 / 155.9 s ⇒ **总 15.6 min ≤ 40 min**。

## 3 选定 = `lr 3e-3 · constant`（`KL/100step` 0.0904，四条全过：无早停 / 比 0.991 / KL≥0.020 / 结构未动只做回归）
1. ③ floor0.5 与 ① 2e-3 也四条全过，但 KL 只有 0.0751 / 0.0598 ⇒ 按冻写出口落选。
2. ⚠ ④ 原是"已知会早停"的**负向对照，这次没早停也没塌**（0.991）⇒ 负向标签在本数据/本步数**失效**；它与 ③ 的 top1 差在单 seed 噪声级，只有 KL 差（0.0904 vs 0.0751）可读。
3. ⛔ 不许当成"长训也会更好"：阶段 3 的 constant 崩溃是 1,200 步 × 8 轮量级。下批长训**同时挂 ③ 作对照臂**，并预注册"④ 的比跌破 0.99 就回退 ③"。
4. ② 4e-3 与 ⑤ warmup100 双双跌破 0.99（0.983 / 0.977）⇒ **不采纳**；⑤ 说明 warmup 在这份数据上把 top1 压得更低。

## 4 复现
```powershell
# 只改 --lr / --lr-schedule / --lr-floor-frac / --lr-warmup-steps；其余同 §1
python -m mahjong_ml.v4.pretrain --data ..\.tmp\mv1-s37\compact --label mv1-s119-const --epochs 16 --max-steps 75 `
  --batch 512 --lr 3e-3 --lr-schedule constant --head-lr-mult 3.0 --seed 20261201 --objective ppo `
  --behaviour <A8.bin> --behaviour-temp 1.0 --value-target final --stage-a 0 --stage-b 0 --grad-clip 0.5 `
  --kl-early-stop 1.0 --kl-min-steps 100 --threads 8 --arch a- --init <A8.bin> --no-carry --cache-ref-logits <dir>
# 读数：tools\criterion8-check.py --run <ckpt> --net <net.bin> --probe <probe.json> --json <out>
# 驱动器/探针：`.tmp/mv1-s119\{scan.ps1,probe.py}`（gitignored；逐点从干净 ckpt/refcache 起跑）
```
