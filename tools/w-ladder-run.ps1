# w-ladder-run.ps1 —— 剂量-反应实验（`tools/w-ladder.py`）的**环境包装**：跑链 + 逐代读数 + 臂对臂闸门。
#
# 为什么要这个包装（而不是直接敲两行）：环境变量是**踩过坑的**那套（见任务书与
# `S:\mahjong-training\style-reward\REPORT.md` 的抬头）——
#   ① 解释器必须是 `S:\mahjong-training\tools\py312\python.exe`（仓库 `.venv` 不能在工作区外建目录）；
#   ② `MAHJONG_DATA_ROOT` / `MAHJONG_TRAINER` 必须指 S:（工作区里的 trainer.exe 写数据根会被**静默吞掉**）；
#   ③ `MAHJONG_STYLE_BONUS` 是**自变量**、`MAHJONG_STYLE_AUDIT_DIR` 是审计账的**持久**落点
#      （缺它账会落在 `compact\<tag>`，被 `run-league.ps1` 每代轮换删掉）。
# ⛔ 本脚本不碰 `tools\run-league.ps1`（那是哈希基线），只调 `tools\w-ladder.py`。
#
# 用法：
#   pwsh -File tools\w-ladder-run.ps1 run          # 六条链（顺序：w0-c1/c2 → w300-c1/c2 → w2800-c1/c2）
#   pwsh -File tools\w-ladder-run.ps1 measure      # 逐代风格向量 + 臂表 + 噪声地板
#   pwsh -File tools\w-ladder-run.ps1 gate         # 臂对臂闸门（4 组配对 × 4 套新牌山 × 3000 场）
#   pwsh -File tools\w-ladder-run.ps1 report       # 拼 Markdown（由 w-ladder.py 出）
#   pwsh -File tools\w-ladder-run.ps1 probe -Weight 14000 -Label v4-wl-w14000-c1
#       ↑ **判别性探针**（单链 1 代、指定 w；`CONTINGENCY.md` §7 台阶 2）。⚠ 这里刻意用 `-Weight`
#         而不是 `--w`：`pwsh -File` 会把 `--w` 当成**参数名**去绑（`-w` 还与 `-WarningAction`
#         歧义，报 ambiguous），所以探针的旋钮在本包装器里是**具名参数**，由它转成 `--w` 传给 python。
#   pwsh -File tools\w-ladder-run.ps1 probe -Weight 6170 -Mode decision -Label v4-wl-dec6170-c1
#       ↑ **逐决策付奖**的判别性实验（`--style-bonus-mode decision`：奖励只落在真的做了那个动作的
#         那一行）。`-Mode` 同样由本包装器转成 `--mode` 传给 python。
param([Parameter(Position = 0)][string]$Step = 'status',
      [double]$Weight = 0, [string]$Label = '', [int]$Seed = 24310041,
      [string]$Mode = 'hand', [switch]$DryRun)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\HP\source\games\mahjong'
$S    = 'S:\mahjong-training'
$py   = Join-Path $S 'tools\py312\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = (Join-Path $root 'python') + ';' + (Join-Path $root 'python\.venv\Lib\site-packages')
$env:MAHJONG_DATA_ROOT = $S
$env:MAHJONG_TRAINER   = Join-Path $S 'tools\trainer\trainer.exe'
# ⚠ 这两个由 `tools\w-ladder.py` 每条链自己覆盖；这里给的是"忘了覆盖时也别落错地方"的兜底。
$env:MAHJONG_STYLE_AUDIT_DIR = Join-Path $S 'w-ladder\audit'
$log = Join-Path $S "w-ladder\log-$Step.txt"
if ($Step -eq 'probe') {
  if ($Weight -le 0) { throw "probe 需要 -Weight <w>（>0）" }
  $tag = if ($Label) { $Label } else { "v4-wl-probe-w$Weight-c1" }
  $dry = [bool]$DryRun
  if ($dry) {
    & $py (Join-Path $root 'tools\w-ladder.py') probe --w $Weight --label $tag --seed $Seed --mode $Mode --dry-run 2>&1 |
      Tee-Object -FilePath $log -Append
  } else {
    & $py (Join-Path $root 'tools\w-ladder.py') probe --w $Weight --label $tag --seed $Seed --mode $Mode 2>&1 |
      Tee-Object -FilePath $log -Append
  }
} else {
  & $py (Join-Path $root 'tools\w-ladder.py') $Step 2>&1 | Tee-Object -FilePath $log -Append
}
exit $LASTEXITCODE
