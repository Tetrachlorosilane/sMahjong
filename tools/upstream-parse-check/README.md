# upstream-parse-check — 用**上游真解析器**验导出的牌谱

`tools/tenhou-log-check.mjs` 是我们**照抄规格复刻**的校验器（判据来自 `docs/input-json.md` 与
上游 `convlog` 源码）。复刻得再像也还是两份代码，所以导出格式动过之后，最好再拿
**mjai-reviewer 自己的解析/转换代码**跑一遍 —— 这个目录就是那件事的最小工具。

两条路，任选（推荐 ①，纯解析、不需要引擎）：

| | 用什么 | 验到什么 |
| --- | --- | --- |
| ① 探针 `probe/` | 直接路径依赖上游 `convlog` 的 `tenhou::Log::from_json_str` + `tenhou_to_mjai` | 解析 + 转 mjai 事件流；并检查 `start_kyoku.scores` ↔ `hora/ryukyoku.deltas` 的点数板自洽 |
| ② 上游 CLI | `mjai-reviewer --no-review -a <座位> -i <导出.json> [--mjai-out out.jsonl]` | 与网页版同一个二进制；`--no-review` 只解析+转换，不起引擎（不需要 Mortal） |

## 用法

```powershell
# 0) 前置：本地有 Rust 工具链（rustup 即可）与 mjai-reviewer 源码（把 probe/Cargo.toml 里的
#    convlog 路径改成你的位置）。

# ① 探针
cd tools\upstream-parse-check\probe
cargo build --release            # 首次会拉依赖，见下面「cargo 取不到凭据时」
target\release\tenhou-probe.exe <导出.json> [...]

# ② 上游 CLI（在 mjai-reviewer 源码目录构建）
cargo build --release
mjai-reviewer --version
mjai-reviewer --no-review -a 0 -i <导出.json> --mjai-out out.jsonl
```

退出码 0 = 合规；非 0 会把上游的原话打出来（例如 `invalid naki string: "60"`、
`invalid length 12, expected an array of size 13`）。**拿几份故意改坏的当负向对照**，
确认它真的会拒（别只看"它说 ok"）。

## cargo 取不到凭据时（本机实测踩到）

本机 `cargo` 与 `curl` 走 Windows schannel 时都报
`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`（沙箱下拿不到系统凭据），
而 node（自带 OpenSSL）能正常下载。绕法是把依赖**离线 vendor** 到本地：

```powershell
# 用上游仓库的 Cargo.lock（版本 + 校验和都是精确的）把依赖下载成 vendor 目录
node tools\upstream-parse-check\vendorize.mjs `
     <上游>\Cargo.lock <某个不进仓库的目录>\vendor convlog serde_json
# 然后在 probe/ 放 .cargo/config.toml（见 probe/.cargo/config.toml.example）
# 并在 probe/ 目录里跑：cargo build --release --offline
```

⚠ 两个坑：① `cargo` 的配置按**当前工作目录**向上找 —— 必须在 `probe/` 里跑
（或 `--config <文件>`），光给 `--manifest-path` 不生效；
② Windows 建不出「以点结尾」的文件名（`rustls-webpki` 有个这样的测试证书），
`vendorize.mjs` 会跳过它并**不写进 checksum 清单** —— 那文件只被该 crate 自己的测试用。

## 已知差异（复刻的校验器比上游严一处）

结果对里**成对性**上游不查：`["和了", [四家增减]]`（少了明细那组）上游
`chunks_exact(2)` 直接跳过 → 那一局变成"没有和了家的和了"，**静默通过**；
`tools/tenhou-log-check.mjs` 会报 `和了后必须成对出现 […]`。改导出时两边都跑。
