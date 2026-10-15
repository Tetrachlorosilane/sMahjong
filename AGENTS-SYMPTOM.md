# AGENTS-SYMPTOM.md —— 症状速查域（高频症状 → 首先怀疑）

> 本文件是 `AGENTS.md` 的**症状速查切片**：报障时最常撞上的那些症状。**全量症状表（约 70 条）**见
> `NOTES.md` §7。⚠ 本文件**只给"先查哪里"**，判据仍在各域切片与主 `AGENTS.md` §2.3。
> ⚠ 不在 harness 自动加载候选表 ⇒ **永不自动加载**，报障时**显式读它**。

## 路由：我要…… ⇒ 读哪一节

| 我要…… | 读这里 |
| --- | --- |
| 报障 → 先怀疑哪里 | §7（本文件） |
| 全量症状表（约 70 条） | `NOTES.md` §7 |
| 判据本身（"代码该怎么写"） | 主 `AGENTS.md` §2.3（十六条坑） |
| 定位到客户端布局问题 | `AGENTS-CLIENT.md` §6.2 |
| 定位到协议/身份/托管/投票问题 | `AGENTS-PROTO.md` §6.4 / §6.8 / §6.9 |
| 定位到服务端行为问题 | `AGENTS-SERVER.md` §6.3 |
| 我要动**别的域** | 回 `AGENTS.md` 顶部路由表 |

> **何时切回主 `AGENTS.md`**（触发条件，非建议）：① 本域任务**收尾**（改动已提交 / 已跑完本域回归）；② 改动**跨出**上表清单；③ 要动的路径**连续 ≥2 个工作轮**全部落在症状速查域（`AGENTS-SYMPTOM.md`）之外；④ 需要确认 §2 铁律 / §4 验证五层 / §8 文档纪律时 —— 满足任一即**先回主文件**读 §2 与 <FOCUS:BEGIN> 块的改指判据。

---

## 7. 常见症状 → 先查哪里

> **本节是"高频速查"**（改代码/报障时最常撞上的那些）。**全量症状表（约 70 条）见 `NOTES.md` §7**。

| 症状 | 首先怀疑 |
| --- | --- |
| 编译不过 / 链接失败 | exe 是否在运行（锁文件）；AUTOMOC 缓存陈旧 → `-Clean` |
| 客户端连不上（`Connection refused`） | 服务端没起 / 端口错 / **WSL 只转发到 `[::1]`**（客户端已自动 IPv4↔IPv6 回退；WSL 填 `localhost`） |
| 大厅按钮是灰的 | `MainWindow::onConnected()` 必须调 `m_lobby->setConnected(true)` |
| **掉线后那一格变成机器人（会吃碰杠/胡牌）**，或掉线就没法继续 | `Table.markAway` 是不是把 `bot` 置真了？托管只该置 `away`（§2.3-13）。定性：`node tools\away-test.mjs` |
| **对局中有人掉线，其他三家突然看到"准备/开始游戏"** | 对局中的 `room` 事件被当成"回等待室"了：`MainWindow` 的 `room` 分支要看 `playing` |
| **掉线的人重连没回原座位 / 每次连上都是"新玩家"** | ① `uuid_ok{issued:true}` 有没有**落盘**（`MainWindow::saveIdentity`）？② 存了 `hello_ok` 的 **`pid`+`token`** 并发 `rejoin` 吗（光有 uuid 接不回托管座位，§6.8）？定性：`node tools\uuid-test.mjs` |
| **牌局卡住、按钮点了没反应、什么提示都没有** | 客户端**收包超时**（60 s 无任何下行**字节**）⇒ `ui.net.recv_timeout` + 断开回「未连接」。⚠ 判据是"**任何**字节"而非"完整报文"；开关 `MAHJONG_RECV_TIMEOUT_MS`（详见 NOTES §6.4.3） |
| **投票通过了牌局还在等出牌/还在打** | §2.3-14：`finishVoteLocked` 要 `wake()`，且**五个检查点**都要看 `voteEnded`。定性：`node tools\vote-test.mjs` |
| 点了「结束对局」没反应 / 一直显示冷却 | 看 `vote_denied.reason`（`not_playing` / `running` / `cooldown`）；冷却**只在服务端**记时 |
| 手牌数量对不上 | `tsumogiri` 用了吗？有没有靠 kind 猜？（§2.3-11） |
| **庄家第一巡点牌却打出另一张 / 张数对但内容差一张（幽灵手牌）** | `round_start.drawn` 有没有发？出牌取牌是不是按**牌码**？客户端对账也按牌码？见 §2.3-11；定性：`node tools\discard-align-test.mjs` |
| 一人牌河两张横置 | `riichi` 事件里是不是又去标"最后一张"了？横置**只认 `discard.sideways`**（§2.3-1） |
| 鸣牌后牌河对不上 | `meld` 的 `called_index` 有没有 `removeAt`（鸣牌是"移动"不是"复制"） |
| 换座后点「准备」没反应 / 把别人设成已准备 | 被换走那家的 `session.seat` 没跟着改 → 必须 `resyncSessionSeats()` + 用 `seatOfSession(this)` 反查（§6.3）。定性：`node tools\seat-swap-test.mjs` |
| **某家"没动就被代打"** | 废包（迟到回包 / 局间残留的 `confirm`）被当成本巡答复：`dropReplies` + `type ∈ option.type` + 认领要确认"是动作"（§2.3-10、§2.3-8） |
| 门前役全不生效 | `menzen[]` 是不是漏了初始化？（§2.3-2） |
| 和了形误判 | 向听 DFS 里 `melds + partials` 没封顶到 4（§2.3-3） |
| 放过一张荣和牌后马上又能荣和同一张 | 振听三种记在哪、有没有被鸣走的舍牌账（§2.3-12） |
| 杠后「岭上」数不减 | `dead_wall_left` 必须**每次摸牌都带**（岭上摸牌不动 `tiles_left`，见 PROTOCOL §3.4） |
| 役满显示成「0 番」 / 结算既画牌又写字 | 界面判据一律看 `yakuman` 字段；图形块与文字块共用 `schematicRenderable()`（§6.4） |
| **界面显示成 `yaku.riichi` 这种裸键** | 语言文件缺 key（`check-i18n` / `i18n-gen --check`）；认不出的码**原样显示码**是刻意的 |
| 中文/玩家名变乱码或 `?` | 显式 UTF-8；截断按**码点**（`Json.clampCodePoints`）；别用 `QLatin1String` 接中文 |
| 改了 `zh_CN.json` 界面还是旧文案 | 客户端要重启（语言文件是**运行时**从 exe 同级 `i18n/` 读的） |
| 新增界面文案忘了搬 | 三件套：字面量 → `i18n-map.mjs` → `i18n-apply.mjs` → `i18n-gen.mjs`（§6.4） |
| 牌河/副露牌太小、盘偏扁、立直棒叠点数 | §6.2 的不变量（比例、区带、固定左缘）；改完**必须看图**（L2 出 `table.png`） |
| `dist\` 里的 exe 不是最新 | 带 `-Deploy` 重新构建（`build\` 每次更新，`dist\` 只在 `-Deploy` 时更新） |
| **训练盘（`S:`）采集/训练中途报 `AccessDeniedException`、读报 `ERROR_IO_DEVICE`** | **先去查盘符还在不在**：卷掉了也长这样，⛔ 别误诊成沙箱权限去放宽沙箱或改路径。见 `NOTES.md` §6.5 |
| **网页版复盘器「只有分析某一家时报错」** | 那一家做过**天鳳规则不允许**的动作（最常见**立直时牌山不足 4 张**或**持点不足 1000**，§2.3-15）：`node tools\tenhou-log-check.mjs` 第 ⑬ 条 |
