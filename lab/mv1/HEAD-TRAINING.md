# 多头训练体系重优化（第一百二十一轮 · 2026-10-20；⛔ 未采集/未跑闸门/未发布）
> 问题：7 头共用一套注册损失权重与一个头级 lr（×3.0），二者从未扫过。本轮**只加旋钮 + 12 点小规模扫描**（1,200 步/点，同 `.tmp/mv1-s37/compact` / 同 `--init` A8 / 单 seed 20261201）；判据**跑前冻写** = `.tmp/mv1-s40/FROZEN.md`。
## 1 旋钮审计（已有 → 新增；`文件:行`）
| 旋钮 | 缺省 | 位置 |
| --- | --- | --- |
| 逐头 loss 权重（policy 1.0 / value 0.5 / placement 0.3 / belief_hand 0.2 / belief_tenpai 0.2 / danger 0.3 / effect 0.3） | 注册值 | `model.py:1189`（`loss_weights()`）；表 = `model.py:381-390` |
| `--head-lr-mult` | 3.0 | `pretrain.py:3483` |
| `--only-heads` / `--drop-heads` / `--freeze-trunk` | 不传 / 关 | `pretrain.py:3495` / `3616` / `3493` |
| `--stage-c-lr-mult` + `STAGE_WEIGHTS` | 1.0；a/b/c 三阶段 | `pretrain.py:3501` / `1632` |
| ★ `--head-loss-weights k=v,...` | **不传 ⇒ 逐位不变** | 声明 `3486` · 解析 `1662` · 叠加 `_step_weights` `2380`（阶段权重之上；缺省返回原对象） |
| ★ `--head-lr-groups policy/value/belief/aux=` | **不传 ⇒ 仍原来的两组** | 声明 `3488` · 组定义 `1654`（policy+`policy_gate`；value+placement；belief_hand+belief_tenpai；danger+effect+ssl+own_win）· 解析 `1692` · 建组处**全覆盖断言**（漏一个头参数即 `SystemExit`，fail-closed） |
| ★ `--belief-hand-unseen-weight α` | **0 = 关** | 声明 `3490` · 加权 `1369`（`w=1+α·unseen/4`，整批归一；`unseen` = 公开 `tile` 第 34 通道 = `4−visible`） |
- **缺省逐位不变**：同命令 154 步，改动前/后 `model` 段 sha 同为 `e285a3a6…`；1,200 步基线 `base.bin` = **`7EB4954B…`**，与**改动前**第一百一十九轮 `const.bin` **逐字节相同**（同 top1 0.6888 / KL 0.0904）。负向对照：`A-policy2.bin` = `EE64BCC8…` ≠ 基线。
- **显式中性值也逐位相同**：七头=注册权重（`--head-loss-weights`）与四组=×3（`--head-lr-groups`）两跑 154 步均 = 基线 sha。⚠ 这条比 sha 抓到过真 bug：`_group_params` 起初漏了 `policy_gate` ⇒ 该参数**整轮不更新**（1 步后 `max|Δ|` 恰 = 一步 lr），已补进 policy 组并加全覆盖断言。
- **台账**：三旋钮**非缺省才**进 `metrics.json` 的 `args` 与 ckpt 的 `recipe`（`_recorded_args` `1907` / `_resume_recipe` `1847`，旧存档照旧可续）。**缓存键不适用**：只改损失/优化器，不碰数据与 `--cache-ref-logits` 的内容。
## 2 点位 × 读数（12 点；`lr 3e-3 constant`；C8-v2 = 六腿）
| 点 | 旋钮（相对基线只改这一处） | `KL/100step` | top1 学生/init（比） | C8 | `frac(u==0)` | stop |
| --- | --- | ---: | --- | --- | ---: | --- |
| base | 现默认（`head-lr 3.0` + 注册权重） | 0.0904 | 0.6888/0.6948（0.991） | PASS | 0.0000 | 空 |
| floor50 | lr floor .5（对照） | 0.0751 | 0.6899/0.6948（0.993） | PASS | 0.0000 | 空 |
| A-policy2 | policy ×2 = 2.0 | 0.0952 | 0.6921/0.6948（0.996） | PASS | 0.0000 | 空 |
| A-value025 | value ×0.5 = 0.25 | 0.0526 | 0.6991/0.6948（1.006） | PASS | 0.0000 | 空 |
| **A-value2** | **value ×2 = 1.0** | **0.1489** | 0.6894/0.6948（**0.992**） | PASS | 0.0000 | 空 |
| A-belief2 | belief_tenpai ×2 = 0.4 | 0.0953 | 0.6888/0.6948（0.991） | PASS | 0.0000 | 空 |
| A-danger2 | danger ×2 = 0.6 | 0.0578 | 0.6980/0.6948（1.005） | PASS | 0.0000 | 空 |
| A-effect05 | effect ×0.5 = 0.15 | 0.0848 | 0.6818/0.6948（**0.981**） | PASS | 0.0000 | 空 |
| B-policy3 | lr policy 3 / 其余 1 | 0.0800 | 0.6867/0.6948（**0.988**） | PASS | 0.0000 | 空 |
| B-v3b3 | lr policy 1 / value 3 / belief 3 / aux 1 | 0.0608 | 0.6850/0.6948（**0.986**） | PASS | 0.0000 | 空 |
| B-aux6 | lr aux 6（其余 3） | 0.0849 | 0.6861/0.6948（**0.988**） | PASS | 0.0000 | 空 |
| C-unseen1 | `belief_hand` 公开 unseen 加权 α=1 | 0.0823 | 0.6845/0.6948（**0.985**） | PASS | 0.0000 | 空 |
- 六腿 12/12 PASS、无早停；每点训练相 150–152 s + 导出/探针/C8 ≈7 s；**12 点首轮总墙钟 32 min 42 s**（含一次 torch 段错误重试）；B 组因 `policy_gate` 分组 bug 重测 3 点 **+7 min 55 s**（扫描相合计 40 min 37 s）。`A-effect05`/`B-policy3`/`B-v3b3`/`B-aux6`/`C-unseen1` 因 **top1 比 <0.99** 落选。
## 3 逐头指标（只报不清算；init → trained，1,851 验证行）
- ⚠ **`danger` AUC（任一家、实际打出的候选）**：init **0.5001** → base 0.5328 / **选中 0.4712**；其余 0.393–0.544。⚠ 该切片仅 **186 正例**、头输出幅度窄（σ≈0.044）⇒ **≈0.5 = 只学到先验**；本轮不据此改任何东西。
- `belief_tenpai` AUC 0.9708 → 0.9747（base）/ 0.9708（选中）；`belief_hand` AUC 0.6265 → 0.6085 / 0.6076（全部 ≤0.614）。
- `value` ECE/MSE 3.406/— → 3.851/154.2（base）→ **3.268/138.8**（选中，变好），但 `value` CE 8.38（base 7.83，变差）；`placement` ECE 0.058 → 0.390/0.395、`effect` MSE 0.0021 → 0.0036/0.0039（全部大幅变差）⇒ 1,200 步内多数辅助头**变差**，只记账、不归因。
## 4 选定 = `--head-loss-weights value=1.0`（其余默认、`head-lr 3.0`）
1. 主判据：六腿 PASS ∧ top1 比 0.992 ≥0.99 ∧ KL 0.1489 ≥0.020；**满足者中 KL 最高**（次高 A-belief2 0.0953；B 组三点修 `policy_gate` 后全部跌破比 0.99）。
2. 代价如实报：`danger` AUC 0.471、`placement` ECE 0.395、`effect` MSE 0.0039 ⇒ **不是"全面更好"**，只是本判据下最优。
3. ⛔ 不归因（E）：只说明"这份数据/这 1,200 步上，value 权重 ×2 给出最大信任域位移且未塌"；**不是强度结论**。下批长训以默认（value 0.5）作同数据对照臂，预注册"top1 比跌破 0.99 就回退默认"。
## 5 复现
```powershell
python -m mahjong_ml.v4.pretrain --data .tmp\mv1-s37\compact --label mv1-s40-A-value2 --epochs 16 --max-steps 75 --batch 512 `
  --lr 3e-3 --lr-schedule constant --head-lr-mult 3.0 --seed 20261201 --objective ppo --behaviour .tmp\mv1-s38\nets\A8.bin `
  --behaviour-temp 1.0 --value-target final --stage-a 0 --stage-b 0 --grad-clip 0.5 --kl-early-stop 1.0 --threads 8 `
  --arch a- --init .tmp\mv1-s38\nets\A8.bin --no-carry --head-loss-weights value=1.0
# 读数：tools\criterion8-check.py --run <ckpt> --net <net.bin> --probe <probe.json>；探针 = .tmp\mv1-s40\probe_heads.py
```
