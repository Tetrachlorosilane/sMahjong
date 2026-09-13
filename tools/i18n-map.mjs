/**
 * 界面文案映射表：**C++ 源码里的中文字面量 → 语言文件的 key**。
 *
 * 这张表是「搬运」的**唯一数据源**：`tools/i18n-apply.mjs` 按它把
 * `QStringLiteral("建房间")` 换成 `lang::t("ui.lobby.create_room")`，
 * 并按 `key → 字面量` 生成 `client/assets/i18n/zh_CN.json` 的条目。
 * 所以**两边不会漂移**：值就是搬运前的那个字面量。
 *
 * 约定：
 *   - key 一律 `ui.<区域>.<语义>`；协议码那几族（`yaku.` / `tile.` / `limit.` / `reason.` / `error.`）
 *     由 `YakuCodes.java` 决定，不在这张表里（见 tools/check-i18n.mjs）。
 *   - 字面量按**源码原文**写（含 `\\n`、`%1`、HTML 标签），保证替换能命中。
 *   - 用户数据（默认玩家名、房间名）与**字形**（牌面「萬」「東」、立直标记「立」、
 *     隐藏的测量按钮）**不搬**：它们的行上有 `// i18n-keep` 标记，见 tools/i18n-scan.mjs。
 */

export const MAP = {
  'client/src/ui/MainWindow.cpp': {
    '立直麻将 Mahjong Client': 'ui.main.window_title',
    '未连接': 'ui.main.disconnected',
    '正在连接 %1:%2 …': 'ui.main.connecting',
    '房间': 'ui.main.room',
    '未加入房间': 'ui.main.not_in_room',
    '座位': 'ui.main.seat',
    '座位 %1：空': 'ui.main.seat_empty',
    '准备': 'ui.main.ready',
    '取消准备': 'ui.main.cancel_ready',
    '加机器人': 'ui.main.add_bot',
    '移除机器人': 'ui.main.remove_bot',
    '开始游戏': 'ui.main.start_game',
    '离开房间': 'ui.main.leave_room',
    '发送聊天…': 'ui.main.chat_placeholder',
    '发送': 'ui.main.send',
    '没有可移除的机器人': 'ui.main.no_bot_to_remove',
    '聊天': 'ui.main.chat',
    '回车发送': 'ui.main.enter_to_send',
    '房间 %1  %2': 'ui.main.room_status',
    '座位 %1：%2   分数 %3   %4%5': 'ui.main.seat_row',
    '已准备': 'ui.main.is_ready',
    '未准备': 'ui.main.not_ready',
    '  [机器人] ': 'ui.main.bot_tag',
    '只有房主可以开始游戏': 'ui.main.host_only_start',
    '<p>余牌 %1 · 岭上 %2</p>': 'ui.main.tiles_left_html',
    '<p>听牌：%1%2</p>': 'ui.main.waits_html',
    '（振听）': 'ui.main.furiten_suffix',
    '<b>%1</b>：%2': 'ui.main.wait_line_html',
    '未连接服务器': 'ui.main.no_server',
    '已连接': 'ui.main.connected',
    '已连接，正在握手…': 'ui.main.connected_handshake',
    '连接已断开': 'ui.main.connection_lost',
    '连接断开': 'ui.main.connection_lost_title',
    '与服务器的连接已断开，返回大厅。': 'ui.main.connection_lost_detail',
    '连接被拒绝': 'ui.main.connect_refused',
    '无法连接': 'ui.main.connect_failed',
    '无法解析服务器地址': 'ui.main.bad_host',
    '无法连接服务器': 'ui.main.connect_failed_title',
    '立直：点击要打出的宣言牌': 'ui.main.riichi_prompt',
    '这张牌不能作为立直宣言牌': 'ui.main.riichi_bad_tile',
    '现在不能打牌': 'ui.main.cannot_discard',
    '自动连接 %1:%2 …': 'ui.main.auto_connecting',
    '握手完成，正在获取房间列表…': 'ui.main.handshake_done',
    '演示房': 'ui.main.demo_room',
    '立直麻将 · 房间 %1': 'ui.main.room_window_title',
    '观战中': 'ui.main.spectating',
    '对局开始': 'ui.main.game_started',
    '振听：不能荣和，只能自摸': 'ui.main.win_note_furiten',
    '和了形但无役，不能和牌': 'ui.main.win_note_no_yaku',
    '本巡 %1s + 额外 %2s': 'ui.main.clock_both',
    '本巡 %1s': 'ui.main.clock_base',
    '轮到你出牌': 'ui.action.your_turn',
    '可以鸣牌': 'ui.action.can_claim',
    '抢杠机会': 'ui.action.chankan',
    '%1 打出 %2': 'ui.main.log_discard',
    '%1 立直！': 'ui.main.log_riichi',
    '新宝牌指示牌': 'ui.main.log_new_dora',
    '%1 和牌！': 'ui.main.log_agari',
    '和牌': 'ui.result.title_agari',
    '流局': 'ui.result.title_ryuukyoku',
    '下一局：%1%2局': 'ui.main.next_round',
    '本局结束 · 最多 %1 秒后开始下一局（关闭结算可提前）': 'ui.main.round_end_hint',
    '终局': 'ui.result.title_game_end',
    '服务端错误：%1': 'ui.main.server_error',
    '错误：%1': 'ui.main.error_prefix',
  },

  'client/src/ui/LobbyDialog.cpp': {
    '立直麻将 · 大厅': 'ui.lobby.window_title',
    '连接服务器': 'ui.lobby.connect_group',
    '玩家': 'ui.lobby.col_player',
    '连接': 'ui.lobby.connect',
    '地址': 'ui.lobby.host',
    '端口': 'ui.lobby.port',
    '昵称': 'ui.lobby.nickname',
    '房间列表': 'ui.lobby.room_list',
    '刷新列表': 'ui.lobby.refresh',
    '创建房间': 'ui.lobby.create_group',
    '我的房间': 'ui.lobby.my_room',
    '半庄（东+南）': 'ui.lobby.rule_hanchan',
    '东风战': 'ui.lobby.rule_tonpuu',
    '无赤宝牌 (0)': 'ui.lobby.aka_0',
    '赤宝牌 3 张': 'ui.lobby.aka_3',
    '赤宝牌 4 张': 'ui.lobby.aka_4',
    '每巡 %1 秒 + 额外 %2 秒': 'ui.lobby.clock_both',
    '每巡 %1 秒（无额外）': 'ui.lobby.clock_base',
    '建房间': 'ui.lobby.create_room',
    '房间名': 'ui.lobby.room_name',
    '规则': 'ui.lobby.rules',
    '赤宝牌': 'ui.lobby.aka',
    '思考时间': 'ui.lobby.clock',
    '补机器人': 'ui.lobby.fill_bots',
    '加入房间': 'ui.lobby.join_group',
    '房间号，例如 AB12': 'ui.lobby.room_id_placeholder',
    '加入': 'ui.lobby.join',
    '断开': 'ui.lobby.disconnect',
    '未连接': 'ui.main.disconnected',
    '已连接': 'ui.main.connected',
    '  [对局中]': 'ui.lobby.playing_tag',
    '暂无房间，可以自己建一个': 'ui.lobby.no_rooms',
    '请输入房间号，或在列表中双击选择房间': 'ui.lobby.enter_room_id',
  },

  'client/src/ui/ResultDialog.cpp': {
    '座位%1': 'ui.result.seat_fallback',
    '确定': 'ui.result.ok',
    '%1 秒后自动开始下一局（点「确定」可提前）': 'ui.result.auto_next',
    '手牌': 'ui.result.hand',
    '宝牌指示牌': 'ui.result.dora_indicator',
    '里宝指示牌': 'ui.result.ura_indicator',
    '自摸': 'ui.result.tsumo',
    '荣和': 'ui.result.ron',
    '<p>放铳：%1</p>': 'ui.result.dealt_in',
    '<p>手牌：%1%2</p>': 'ui.result.hand_line',
    ' 和了牌 <b>%1</b>': 'ui.result.winning_tile',
    '<p>宝牌指示牌：%1</p>': 'ui.result.dora_line',
    '<p>里宝指示牌：%1</p>': 'ui.result.ura_line',
    '<h3>役种</h3><ul>': 'ui.result.yaku_header',
    '<li>%1 <b>%2</b> 番</li>': 'ui.result.yaku_row_han',
    '<p>合计 <b>%1</b></p>': 'ui.result.total_yakuman',
    '<p>合计 <b>%1</b> 番 %2 符': 'ui.result.total_han_fu',
    '<p>宝牌 %1 · 赤宝 %2 · 里宝 %3</p>': 'ui.result.dora_counts',
    '<h3>点数收支</h3>': 'ui.result.score_table',
    '<h2>流局 · %1</h2>': 'ui.result.ryuukyoku_title',
    '<p>听牌：': 'ui.result.tenpai_prefix',
    '%1 %2　': 'ui.result.tenpai_cell',
    '<b>听</b>': 'ui.result.tenpai_yes',
    '不听': 'ui.result.tenpai_no',
    '<p>%1：%2</p>': 'ui.result.hand_of_seat',
    '<p>流局满贯：%1</p>': 'ui.result.nagashi_mangan',
    '<h2>对局结束</h2>': 'ui.result.game_end_title',
  },

  'client/src/ui/ActionBar.cpp': {
    '等待其他玩家…': 'ui.action.waiting',
    '轮到你出牌': 'ui.action.your_turn',
    '可以鸣牌': 'ui.action.can_claim',
    '抢杠机会': 'ui.action.chankan',
    '请选择动作': 'ui.action.choose',
    '立直：请点击要打出的宣言牌': 'ui.action.riichi_prompt',
    '%1（%2 秒）': 'ui.action.title_with_seconds',
    '打牌（点手牌）': 'ui.action.discard_hint',
    '立直': 'ui.action.riichi',
    '自摸': 'ui.action.tsumo',
    '荣和': 'ui.action.ron',
    '碰': 'ui.action.pon',
    '吃': 'ui.action.chi',
    '杠': 'ui.action.kan',
    '九种九牌': 'ui.action.kyuushu',
    '跳过': 'ui.action.pass',
    '请点击自己的手牌出牌': 'ui.action.discard_click',
    '可宣言的牌：%1': 'ui.action.riichi_tiles',
    '暗杠': 'ui.action.ankan',
    '加杠': 'ui.action.kakan',
    '大明杠': 'ui.action.daiminkan',
    '已超时（由服务端代打）': 'ui.action.timed_out',
  },

  'client/src/ui/AutoBar.cpp': {
    '自动胡了': 'ui.auto.win',
    '能自摸就自摸、能荣和就荣和（优先于「自动摸切」与「不吃碰杠」）': 'ui.auto.win_tip',
    '不吃碰杠': 'ui.auto.no_call',
    '别人的舍张一律不叫（吃 / 碰 / 杠都不自动应答）；能荣和时不会替你跳过': 'ui.auto.no_call_tip',
    '自动摸切': 'ui.auto.tsumogiri',
    '轮到自己就把刚摸到的那张打出去；摸到的牌能自摸时绝不打出，留给「自动胡了」或你决定': 'ui.auto.tsumogiri_tip',
  },

  'client/src/ui/TableView.cpp': {
    '未连接牌局': 'ui.table.not_connected',
    '余牌 %1 · 供託 %2': 'ui.table.tiles_left_offering',
    '  立直': 'ui.table.riichi_suffix',
  },

  'client/src/model/TableModel.cpp': {
    '自家': 'ui.table.seat_self',
    '下家': 'ui.table.seat_right',
    '对家': 'ui.table.seat_opposite',
    '上家': 'ui.table.seat_left',
    '東': 'ui.table.wind_east',
    '南': 'ui.table.wind_south',
    '西': 'ui.table.wind_west',
    '北': 'ui.table.wind_north',
    '%1%2局 %3本場': 'ui.table.round_text',
  },

  'client/src/net/NetClient.cpp': {
    '无法解析服务器地址：%1': 'ui.net.bad_host',
    '未连接到服务器，发送失败': 'ui.net.not_connected',
    '报文超过 1 MiB 上限，已丢弃': 'ui.net.tx_too_large',
    '无法连接 %1:%2（%3）——已尝试 %4': 'ui.net.connect_failed',
    '下行报文超过 1 MiB 上限，连接已断开': 'ui.net.rx_too_large',
    '收到无法解析的报文：%1': 'ui.net.bad_packet',
  },

  // main.cpp 里只有这三处是**界面文案**（按钮文字查找 / 标题断言），
  // 其余中文是默认玩家名（用户数据），见源码里的 `// i18n-keep` 标记。
  'client/src/main.cpp': {
    '建房间': 'ui.lobby.create_room',
    '房间': 'ui.main.room',
  },
};

/**
 * 需要**整块**搬运的文案（源码里是若干相邻字面量拼起来的）。
 *
 * 这些不放进 `MAP`：它们不是一个完整的 `QStringLiteral("…")`，替换器匹配不到；
 * 而且**本来就该当成一条文案** —— 拆成多条 key 的话，换语言时行数与语序就没法调整了。
 * 对应源码由人工改成 `lang::t("…")`（见 `HANDLED`）。
 */
export const EXTRA = {
  // 大厅：地址输入框的 tooltip（三行）
  'ui.lobby.conn_hint':
    '本机服务端填 127.0.0.1 或 localhost。\n'
    + '服务端跑在 WSL / 虚拟机 / 别的机器上时，填那台机器的 IP。\n'
    + '客户端会自动在 IPv4 与 IPv6 之间回退，所以 127.0.0.1 与 ::1 只要有一个通就能连上。',
  // 大厅：思考时间下拉的 tooltip（五行）
  'ui.lobby.clock_hint':
    '写法为「额外+每巡」，例如 20+5 =\n'
    + '总额外 20 秒 + 每巡基本 5 秒。\n'
    + '每巡先给基本时长，这段内出牌不消耗额外时长；\n'
    + '超出部分从总额外时长里扣，扣减按 1 秒离散，\n'
    + '剩余量向上去整（对玩家有利）。',
  // 连接被拒时的整段排查提示（含换行，一条文案）
  'ui.net.refused_help':
    '连接被拒绝（%1:%2）——已尝试 %3。\n'
    + '常见原因：\n'
    + '  1) 服务端没启动（在服务器上执行 bash server/run.sh）\n'
    + '  2) 端口不对（默认 10086）\n'
    + '  3) 服务端在 WSL 里：请把地址填 localhost，并确认它监听 0.0.0.0',
  // 结算：对局结束表格的表头（含 `<table>` 开标签，整块一条）
  'ui.result.table_head':
    "<table cellspacing='0' cellpadding='4' width='100%'>"
    + "<tr><th align='left'>顺位</th><th align='left'>玩家</th>"
    + "<th align='right'>点数</th><th align='right'>精算</th></tr>",
  // 结算：对局结束表格的一行（含 `%4` 那半截，整块一条）
  'ui.result.rank_row':
    "<tr><td>%1位</td><td>%2</td><td align='right'>%3</td>"
    + "<td align='right'>%4</td></tr>",
  // 大厅：昵称留空时的默认名（源码里已由人工改为 lang::t，避免与分组标题共用同一个 key）
  'ui.lobby.default_name': '玩家',
  // 结算：役满的倍数（「n倍役满」；从旧的 `result.*` 族并入 `ui.result.*`，族名统一）
  'ui.result.yakuman_multi': '%1倍役满',
};

/**
 * 已由人工结构化改写、所以替换器**不报未命中**的字面量：
 *   - TableModel 的 `kWinds` 数组（`const char*`，改成 key 数组）；
 *   - 上面 `EXTRA` 覆盖的那些相邻字面量。
 */
export const HANDLED = {
  'client/src/model/TableModel.cpp': ['西', '北'],
  'client/src/ui/LobbyDialog.cpp': ['本机服务端填 127.0.0.1 或 localhost。\\n'],
  'client/src/ui/ResultDialog.cpp': ["<tr><th align='left'>顺位</th><th align='left'>玩家</th>"],
  'client/src/net/NetClient.cpp': ['连接被拒绝（%1:%2）——已尝试 %3。\\n'],
};

/** 供生成 JSON 用：key → 文案（同一 key 在多处出现时取首次出现的那份字面量）。 */
export function keyToText() {
  const out = { ...EXTRA };
  for (const file of Object.keys(MAP)) {
    for (const [text, key] of Object.entries(MAP[file])) {
      if (!(key in out)) out[key] = text;
      else if (out[key] !== text) {
        throw new Error(`同一个 key 被映射到两份不同文案：${key}\n  ${JSON.stringify(out[key])}\n  ${JSON.stringify(text)}`);
      }
    }
  }
  return out;
}
