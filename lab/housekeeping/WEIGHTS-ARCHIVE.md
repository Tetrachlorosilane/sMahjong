# 权重归档 `v1.17.0-weights`（谱系端点 + 今日实验网）

**一句话**：把 `tools\build\`（`.gitignore` 的 `build/` 挡着、GitHub 上一份都没有）的**谱系端点**与**今日实验网**打成 zip 挂到 release `v1.17.0-weights`。⛔ **不构成新 bot、不构成任何强度声明**（包里只有 `net.bin`+`net.json`，没有 `bot.json`）。

## release 与资产
- release：tag `v1.17.0-weights` · id **411773503** · https://github.com/Tetrachlorosilane/sMahjong/releases/tag/v1.17.0-weights
  ⚠ `prerelease=true` —— 它是权重归档，**不该顶掉 "Latest"**（最新版本仍是 v1.16.0）。tag 指向 `84bdcd4`（本文档提交之前的主线提交）。
- 资产：`v4-weights-20261013.zip` · asset id **637292839** · **254,863,741 B** · sha256 `7458d486e37e66d784e6b19e9def12809d90a4777091da3a5f97924df4f44fa1`
  （远端 `size` 与 `digest: sha256:7458…4fa1` 与本地 zip **逐位一致**；上传 1 次成功、重试 0 次）

## 集合规则（机械，非手挑）
1. **必含（已发布端点）**：`docs/BOT-AI.md` §9 / `NOTES.md` §9.5 的已发布 bot 包源网 —— 用 `bot-ai/<pkg>/net.bin` 的 **sha256 反查** `tools/build/**/net.bin`（命中 5 / 未命中 5）。
2. **必含（今日实验网）**：`bl13-A-r1` `bl13-A-r2` `bl13-B-s01` `rerun-A-r1` `rerun-A-r2` `rerun-B-s01`（各 5,594,680 B）。
3. **谱系端点**：`tools/build/*` 目录名去掉尾部 `-gNN`/`-gNNN`（或 `-cN-gNN`）世代后缀分组，取**每组世代最大者**（无后缀目录自成一组）。
4. ⛔ **不收录**：`smoke-*` · `v4-smoke-*` · `v4-mr0*` · `v4-r8/r9` · `v4-bc-00{3,4}` · `v4-ppo-00{1,2}` · `w1-nearnull` · `v4-p3-001` · `v4-heads-g01` · `v4-camp01-*` · `v4-league-g*` · `v4-league{2..9}-*`（除非反查命中已发布包）。

## 数量 / 体积
- 扫到谱系分组 **75**，机械排除 **26** ⇒ **入档 50 个网**：`net.bin` 279,734,000 B + `net.json` 277,438 B = **280,011,438 B（267.0 MiB）**。
- 体积闸门：目标 300–500 MB、**>600 MB 才裁剪** ⇒ **未触发裁剪**，也**没有任何静默丢弃**（62 条未收录逐条写在 zip 内）。
- zip 内 `MANIFEST.md`（人读）+ `MANIFEST.json`（机读）：逐条 名字 · 相对路径 · 字节 · sha256 · mtime · 谱系 · 世代 · 性质 · 声明；整批指纹 `3499ffece8e9f09d5958f221ae10efb0820bf16b0eafbecae33f4659c330432b`。

## 各端点的口径标注（摘要；全文见 zip 内 MANIFEST）
- **已发布端点 7 个**：`v4-expert-defrep-g10`（P3-01 / BL-13 / BL-14 / rerun 的**基准**；⚠ 无独立强度数字）· `v4-wl-p301-A-g01`（「口径未动」但**溯源不净**：端点 = 最后一次成功导出那片 s11；闸门**不过线** +0.357 [−0.607,+1.348]）· `v4-wl-p301-B-g06`（**「口径已动」**+**「不跨族外推」**；闸门过线 +2.54 [+1.56,+3.51]，n=12,000）· 四风格源网 = `v4-wl-dec6170-4g-c2-g04`（立直型）/ `v4-wl-dama5400c-c1-c1-g01`（默听型）/ `v4-wl-meldtype-c2-g01`（碰型）/ `v4-wl-meld3030-4g-c2-g04`（速攻型）——**风格包，强度未跑闸门**。
- **今日 6 个 + 未过闸门的 37 个谱系端点：⚠ 无强度声明（探索件）**。

## 未收录清单与理由（62 条；逐条在 zip 内 `MANIFEST.md`）
- **反查未命中 5 个**：`ppo-v3-g01-g01`（v3 格式，`tools\build` 里没有）· `v4-bc-004` / `v4-p3-001` / `v4-ppo-001` / `v4-league-g08` —— 同名 `tools\build` 目录**不是**该包（实测 `v4-bc-004`：params 1360168 vs 1360936、3.25% 字节不同）⇒ 按 ⛔ 规则不收；它们**已发布的权重**本身在 v1.10.6 / v1.13.0 / v1.14.1 的资产里。
- **机械排除 26 组**：`smoke-*`(4) · `v4-smoke` · `v4-mr01..04` · `v4-r8` · `v4-r9` · `v4-camp01` · `v4-heads` · `v4-league`(g01–g08) · `v4-league2..9` · `v4-bc-003` · `v4-p3-001` · `v4-ppo-001|002` · `w1-nearnull`。
- `tools/build/_rejected/**` 29 个（按名字即"被拒"）· `v4-golden-net.bin`（**是文件**不是目录，且是前向对拍夹具）· `v4cache-*/ net-parity/ v4-parity/ oldprobe/ tools/`（**无 net.bin**）。
- ⚠ 与 `_rejected` **同名 18 个**目录一律取**非 `_rejected`** 那份 ⇒ 已断言：入档 50 条的 `sourcePath` 里 `_rejected` 计数 = **0**（否则会取到另一份被判拒的权重，且丢 `net.json`）。

## 复现
- 打包方式：**照抄** `tools/make-zip.mjs` 的手写中央目录（`versionMadeBy = Unix(3)` + `externalAttrs` 高位 `0644`、UTF-8 名、`deflateRaw` level 9、条目 mtime 取源文件 mtime ⇒ 同输入同字节）；它不适用之处 = 只吃单一 `srcDir`，而本次来源跨 50 个同级目录，故脚本按**显式文件清单**打包（zip 内为 `MANIFEST.md` / `MANIFEST.json` / `weights/<名字>/{net.bin,net.json}`）。
- 清单/打包脚本与成品（**仓库外**，⛔ 不入 git）：`S:\mahjong-training\weights-archive\{MANIFEST.md, MANIFEST.json, v4-weights-20261013.zip, work\*.mjs}`。
