#include "ui/ResultDialog.h"

#include "i18n/Lang.h"
#include "ui/TileFont.h"

#include "model/TableModel.h"
#include "model/Tile.h"
#include "net/Protocol.h"

#include <QJsonArray>
#include <QPushButton>
#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QTextBrowser>
#include <QTimer>
#include <QVBoxLayout>

namespace {

// 示意串的结构（都用字体不会显示的 ASCII 控制字符，避免和牌码冲突）：
//   行间      U+001F
//   行内标签  U+001E     —— 「手牌」「宝牌指示牌」…
//   同行的旁挂 U+001D    —— 手牌与和了牌之间（渲染时插一个整牌位的空档）
constexpr ushort kRowSep = 0x1F;
constexpr ushort kLabelSep = 0x1E;
constexpr ushort kAsideSep = 0x1D;

/**
 * 示意串里是否**至少有一行能画出来**（字体可得 + 至少一段牌码合法）。
 * 构造函数用它决定要不要显示图形块；`agariHtml()` 用它决定要不要退回文字版，
 * 两边必须是同一判据 —— 否则会出现「文字删了、图形也没画」的信息真空。
 */
bool schematicRenderable(const QString& schematic)
{
    if (schematic.isEmpty() || !tilefont::available())
        return false;
    for (const QString& rowText : schematic.split(QChar(kRowSep))) {
        for (const QString& seg : rowText.section(QChar(kLabelSep), 1).split(QChar(kAsideSep))) {
            if (!seg.isEmpty() && tilefont::isRenderableSchematic(seg))
                return true;
        }
    }
    return false;
}

/**
 * 座位显示名（**未转义**）：只在需要自己处理转义时用（如 `final[].name`）。
 */
QString seatNameRaw(const TableModel* model, int seat)
{
    if (seat < 0 || seat > 3)
        return QStringLiteral("-");
    return model ? model->playerName(seat) : lang::t("ui.result.seat_fallback").arg(seat);
}

/**
 * 进 HTML 的座位显示名：**所有结算 HTML 都从这里取名**，所以 toHtmlEscaped()
 * 也放在这一处。
 *
 * <p>玩家名是用户数据（`seats[].name` / `hello.name`），直接插进 `setHtml()`
 * 的 HTML 里会被当成标记解析：名字叫 `<b>x` 会破坏表格结构，`&` 会吞掉后文
 * （聊天那条路径早就 `toHtmlEscaped()` 了，这里曾经漏掉）。
 */
QString seatName(const TableModel* model, int seat)
{
    return seatNameRaw(model, seat).toHtmlEscaped();
}

/**
 * 协议码 → 显示文案：`codeText("limit.", "mangan", 码为空时的兜底)`。
 *
 * <p>服务端**只发 ASCII 码**（`limit` / `reason` / `yaku[].code`），
 * 中文文案全在客户端语言文件 `i18n/<locale>.json` 里。
 * 认不出的码原样显示（旧服务端发来的中文原文也走这条路），绝不显示成空白。
 */
QString codeText(const QString& prefix, const QString& code, const QString& fallbackWhenEmpty)
{
    if (code.isEmpty())
        return fallbackWhenEmpty;
    return lang::code(prefix, code);
}

/**
 * 「n 倍役满」的文案。
 *
 * <p>役满**没有番数这个量纲** —— 规则是「成立 n 种役满役则基本点 = 8000n」，
 * 符数与普通番数全部失效（见 docs/日本麻将.md）。所以结算界面一律写「n 倍役满」，
 * 绝不能把那 13 番的等价番数当成"役满是多少番"来显示。
 * 写法与 AutoPlay 的日志、tools/e2e-test.mjs 的输出保持一致。
 */
QString yakumanText(int mult)
{
    return lang::t(QStringLiteral("ui.result.yakuman_multi"), QString::number(mult));
}

QString tileList(const QStringList& tiles)
{
    QStringList out;
    for (const QString& t : tiles)
        out << mj::tileLabel(t);
    return out.join(QStringLiteral(" "));
}

QString deltaText(int d)
{
    if (d > 0)
        return QStringLiteral("<span style='color:#2E9E63'>+%1</span>").arg(d);
    if (d < 0)
        return QStringLiteral("<span style='color:#C0392B'>%1</span>").arg(d);
    return QStringLiteral("±0");
}

QString scoreTable(const QJsonObject& ev, const TableModel* model)
{
    const QVector<int> scores = proto::intVector(ev.value(QStringLiteral("scores_after")));
    const QVector<int> delta = proto::intVector(ev.value(QStringLiteral("score_delta")));
    QString html = QStringLiteral("<table cellspacing='0' cellpadding='4' width='100%'>");
    for (int s = 0; s < 4; ++s) {
        const QString name = seatName(model, s);
        const QString score = (s < scores.size()) ? QString::number(scores.at(s)) : QStringLiteral("-");
        const QString d = (s < delta.size()) ? deltaText(delta.at(s)) : QString();
        html += QStringLiteral("<tr><td>%1</td><td align='right'>%2</td><td align='right'>%3</td></tr>")
                    .arg(name, score, d);
    }
    html += QStringLiteral("</table>");
    return html;
}

} // namespace

ResultDialog::ResultDialog(const QString& title, const QString& html, const QString& schematic,
                           QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(title);
    setMinimumSize(420, 360);
    auto* root = new QVBoxLayout(this);

    // 牌面示意：用内嵌的牌面字体渲染牌码串（liga 默认生效）。
    // 字体不可得或串里含非法字元时**整块不显示**，由 agariHtml() 退回汉字/文本表示
    // （两边共用 schematicRenderable()，判据必须一致）。
    if (schematicRenderable(schematic)) {
        // 示意串结构：行间 U+001F；行内「标签 U+001E 牌串」；同行旁挂 U+001D。
        // 除手牌行外每行都是「小字标签 + 牌面」，用同一套内嵌字体渲染。
        QFont tf(tilefont::family());
        tf.setPixelSize(44);
        QFont lf(QStringLiteral("Microsoft YaHei"));
        lf.setPixelSize(16);

        auto* wrap = new QWidget(this);
        auto* col = new QVBoxLayout(wrap);
        col->setContentsMargins(12, 10, 12, 4);
        col->setSpacing(6);
        bool anyRow = false;

        for (const QString& rowText : schematic.split(QChar(kRowSep))) {
            const QString label = rowText.section(QChar(kLabelSep), 0, 0);
            const QString tiles = rowText.section(QChar(kLabelSep), 1);
            const QStringList aside = tiles.split(QChar(kAsideSep));

            bool ok = false;
            for (const QString& seg : aside) {
                if (!seg.isEmpty() && tilefont::isRenderableSchematic(seg)) {
                    ok = true;
                }
            }
            if (!ok) {
                continue;
            }

            auto* row = new QWidget(wrap);
            auto* rowLay = new QHBoxLayout(row);
            rowLay->setContentsMargins(0, 0, 0, 0);
            rowLay->setSpacing(0);

            auto* lab = new QLabel(label, row);
            lab->setFont(lf);
            lab->setStyleSheet(QStringLiteral("color:#666;"));
            lab->setFixedWidth(96);
            rowLay->addWidget(lab);

            bool first = true;
            for (const QString& seg : aside) {
                if (seg.isEmpty() || !tilefont::isRenderableSchematic(seg)) {
                    continue;
                }
                if (!first) {
                    // 旁挂之间空出**一个整牌位**（和了牌、副露都靠它分开）
                    auto* gap = new QWidget(row);
                    gap->setFixedWidth(44);
                    rowLay->addWidget(gap);
                }
                auto* tl = new QLabel(seg, row);
                tl->setFont(tf);
                tl->setTextInteractionFlags(Qt::NoTextInteraction);
                rowLay->addWidget(tl);
                first = false;
            }
            rowLay->addStretch(1);
            col->addWidget(row);
            anyRow = true;
        }
        if (anyRow) {
            root->addWidget(wrap);
        } else {
            delete wrap;
        }
    }

    m_browser = new QTextBrowser(this);
    m_browser->setHtml(html);
    root->addWidget(m_browser);

    // 底部一行：确认按钮 + 局间倒计时文字
    auto* bottom = new QHBoxLayout();
    m_countdown = new QLabel(this);
    m_countdown->setStyleSheet(QStringLiteral("color:#666;"));
    bottom->addWidget(m_countdown);
    bottom->addStretch(1);
    auto* ok = new QPushButton(lang::t("ui.result.ok"), this);
    bottom->addWidget(ok);
    root->addLayout(bottom);
    connect(ok, &QPushButton::clicked, this, &QDialog::accept);

    m_timer = new QTimer(this);
    m_timer->setInterval(200);
    connect(m_timer, &QTimer::timeout, this, [this]() {
        m_leftMs -= m_timer->interval();
        if (m_leftMs <= 0) {
            m_timer->stop();
            accept();   // 到点自动关闭 → finished → MainWindow 发 confirm
            return;
        }
        m_countdown->setText(lang::t("ui.result.auto_next")
                                 .arg((m_leftMs + 999) / 1000));
    });
}

void ResultDialog::startCountdown(int ms)
{
    m_leftMs = qMax(0, ms);
    if (!m_countdown)
        return;
    if (m_leftMs <= 0) {
        accept();
        return;
    }
    m_countdown->setText(lang::t("ui.result.auto_next")
                             .arg((m_leftMs + 999) / 1000));
    m_timer->start();
}

QString ResultDialog::schematicOf(const QJsonObject& ev, const TableModel* model, bool agari)
{
    if (!agari) {
        return QString();   // 流局/终局没有可公开的手牌，不做示意
    }
    const int winner = ev.value(QStringLiteral("winner")).toInt(0);
    const QStringList hand = proto::stringList(ev.value(QStringLiteral("hand")));
    const QString win = ev.value(QStringLiteral("winning_tile")).toString();

    // 手牌行：暗牌 +（空一格）+ 和了牌 +（空一格）+ 各副露
    QString handSeg = hand.join(QStringLiteral(" "));
    if (!win.isEmpty()) {
        handSeg += QChar(kAsideSep) + win;   // 和了牌单独摆在旁边，不并进手牌
    }
    // 副露：被鸣的那张**后置 `-`** → 该字体渲染成横置牌（第三轮探针实测：
    // 任何位置、有无空格、多组相邻副露都成立）。
    //
    // ⚠ 横置位必须用 **meldSidewaysIndex()**（与牌桌共用同一个函数）。
    //    这里曾自己按「牌码 == calledTile」判断，而**碰/杠的三四张牌码完全相同**，
    //    于是整组都被加 `-`（全变横置），只有吃的三张码不同才看起来正常 ——
    //    表现为"横置方向随机"，且只在对子型副露上发作。
    if (model && winner >= 0) {
        for (const Meld& m : model->melds(winner)) {
            if (m.tiles.isEmpty()) {
                continue;
            }
            const int rot = meldSidewaysIndex(m, winner);
            QStringList group;
            for (int i = 0; i < m.tiles.size(); ++i) {
                QString c = m.tiles.at(i);
                if (i == rot) {
                    c += QLatin1Char('-');
                }
                group << c;
            }
            handSeg += QChar(kAsideSep) + group.join(QStringLiteral(" "));
        }
    }

    // 组装各行：每行 = 标签 U+001E 牌串
    QStringList rows;
    rows << lang::t("ui.result.hand") + QChar(kLabelSep) + handSeg;
    const QStringList dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")));
    if (!dora.isEmpty()) {
        rows << lang::t("ui.result.dora_indicator") + QChar(kLabelSep) + dora.join(QStringLiteral(" "));
    }
    const QStringList ura = proto::stringList(ev.value(QStringLiteral("ura_indicators")));
    if (!ura.isEmpty()) {
        rows << lang::t("ui.result.ura_indicator") + QChar(kLabelSep) + ura.join(QStringLiteral(" "));
    }
    const QString s = rows.join(QChar(kRowSep));

    // 逐行、逐段校验：分隔符本身不是牌码，整串直接校验会被它们判非法。
    // 任何一段含非法字元就整块不显示 —— 宁可退回文字，也不让字体画出"看着像牌其实不是"的图。
    for (const QString& row : rows) {
        const QString tiles = row.section(QChar(kLabelSep), 1);
        for (const QString& seg : tiles.split(QChar(kAsideSep))) {
            if (!seg.isEmpty() && !tilefont::isRenderableSchematic(seg)) {
                return QString();
            }
        }
    }
    return s;
}

QString ResultDialog::agariHtml(const QJsonObject& ev, const TableModel* model)
{
    const int winner = ev.value(QStringLiteral("winner")).toInt(0);
    const int from = ev.value(QStringLiteral("from")).toInt(-1);
    const bool tsumo = ev.value(QStringLiteral("tsumo")).toBool(from < 0);

    QString html;
    html += QStringLiteral("<h2>%1 %2</h2>")
                .arg(seatName(model, winner), tsumo ? lang::t("ui.result.tsumo") : lang::t("ui.result.ron"));
    if (!tsumo)
        html += lang::t("ui.result.dealt_in").arg(seatName(model, from));

    // 手牌 / 宝牌指示牌 / 里宝指示牌：**牌面示意已经把它们画出来了**，
    // 能用图形表示时就不再重复一遍文字（用户要求）。
    // 只有图形画不出来（字体缺失或串非法）时，才退回文字 —— 保证信息不丢。
    if (!schematicRenderable(schematicOf(ev, model, true))) {
        const QStringList hand = proto::stringList(ev.value(QStringLiteral("hand")));
        const QString win = ev.value(QStringLiteral("winning_tile")).toString();
        html += lang::t("ui.result.hand_line")
                    .arg(tileList(hand),
                         win.isEmpty() ? QString() : lang::t("ui.result.winning_tile").arg(mj::tileLabel(win)));
        const QStringList dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")));
        const QStringList ura = proto::stringList(ev.value(QStringLiteral("ura_indicators")));
        if (!dora.isEmpty())
            html += lang::t("ui.result.dora_line").arg(tileList(dora));
        if (!ura.isEmpty())
            html += lang::t("ui.result.ura_line").arg(tileList(ura));
    }

    const QJsonArray yakus = ev.value(QStringLiteral("yaku")).toArray();
    if (!yakus.isEmpty()) {
        html += lang::t("ui.result.yaku_header");
        for (const QJsonValue& v : yakus) {
            const QJsonObject y = v.toObject();
            // 服务端只发 ASCII：`code`（+ 参数化役种的 `tile`）。
            // 役牌/场风/自风是**同一套模板 + 一张牌码**，所以文案在客户端拼。
            const QString code = y.value(QStringLiteral("code")).toString();
            // 码为空 = 老服务端（只发中文 `name`）；认不出的码由 yakuText 原样返回。
            // 役牌/场风/自风是「同一套模板 + 一张牌码」，参数由 yakuText 填。
            const QString name = code.isEmpty()
                    ? y.value(QStringLiteral("name")).toString()
                    : lang::yakuText(code, y.value(QStringLiteral("tile")).toString());
            const int ym = y.value(QStringLiteral("yakuman")).toInt();
            // 役满役的量纲是「几倍役满」，不是番。
            // 旧版直接印 han，而服务端当时对役满役发的 han 是 0 →
            // 界面写着「国士无双 0 番」（用户报障）。老服务端也兼容：
            // 只要带了 yakuman 字段就按倍数显示，不看 han。
            if (ym > 0)
                html += QStringLiteral("<li>%1 <b>%2</b></li>").arg(name, yakumanText(ym));
            else
                html += lang::t("ui.result.yaku_row_han")
                            .arg(name)
                            .arg(y.value(QStringLiteral("han")).toInt());
        }
        html += QStringLiteral("</ul>");
    }

    const int yakuman = ev.value(QStringLiteral("yakuman")).toInt();
    if (yakuman > 0) {
        // 役满时番数与符数全部失效，合计只报「n 倍役满」——
        // 不写「0 番 40 符」这种既错又容易误导的数字。
        html += lang::t("ui.result.total_yakuman").arg(yakumanText(yakuman));
    } else {
        html += lang::t("ui.result.total_han_fu")
                    .arg(ev.value(QStringLiteral("han")).toInt())
                    .arg(ev.value(QStringLiteral("fu")).toInt());
        const QString limit = ev.value(QStringLiteral("limit")).toString();
        // 满贯/跳满/…现在是 ASCII 码（mangan/haneman/…），文案在语言文件里；
        // 老服务端发的是中文原文，认不出就原样显示。
        if (!limit.isEmpty())
            html += QStringLiteral(" · %1")
                        .arg(codeText(QStringLiteral("limit."), limit, limit));
        html += QStringLiteral("</p>");
    }

    html += lang::t("ui.result.dora_counts")
                .arg(ev.value(QStringLiteral("dora")).toInt())
                .arg(ev.value(QStringLiteral("aka")).toInt())
                .arg(ev.value(QStringLiteral("ura")).toInt());

    html += lang::t("ui.result.score_table") + scoreTable(ev, model);
    return html;
}

QString ResultDialog::ryuukyokuHtml(const QJsonObject& ev, const TableModel* model)
{
    QString html;
    // 流局原因是 ASCII 码（exhaustive/kyuushu/four_kans/…）；
    // 老服务端发的是中文原文 —— 认不出就原样显示，实在没有才退回事件名。
    html += lang::t("ui.result.ryuukyoku_title")
                .arg(codeText(QStringLiteral("reason."),
                              ev.value(QStringLiteral("reason")).toString(),
                              ev.value(QStringLiteral("type")).toString()));
    const QVector<bool> tenpai = proto::boolVector(ev.value(QStringLiteral("tenpai")));
    html += lang::t("ui.result.tenpai_prefix");
    for (int s = 0; s < 4; ++s) {
        const bool tp = (s < tenpai.size()) ? tenpai.at(s) : false;
        html += lang::t("ui.result.tenpai_cell").arg(seatName(model, s),
                                              tp ? lang::t("ui.result.tenpai_yes") : lang::t("ui.result.tenpai_no"));
    }
    html += QStringLiteral("</p>");

    const QJsonArray hands = ev.value(QStringLiteral("hands")).toArray();
    for (int s = 0; s < hands.size() && s < 4; ++s) {
        if (hands.at(s).isNull() || !hands.at(s).isArray())
            continue;
        html += lang::t("ui.result.hand_of_seat")
                    .arg(seatName(model, s), tileList(proto::stringList(hands.at(s))));
    }

    const QVector<int> nagashi = proto::intVector(ev.value(QStringLiteral("nagashi")));
    for (int s = 0; s < nagashi.size() && s < 4; ++s) {
        if (nagashi.at(s) >= 0)
            html += lang::t("ui.result.nagashi_mangan").arg(seatName(model, nagashi.at(s)));
    }

    html += lang::t("ui.result.score_table") + scoreTable(ev, model);
    return html;
}

QString ResultDialog::gameEndHtml(const QJsonObject& ev, const TableModel* model)
{
    QString html = lang::t("ui.result.game_end_title");
    const QJsonArray final = ev.value(QStringLiteral("final")).toArray();
    // 表头整块是一条文案（含两个半截的 HTML 片段），所以整块换：
    // 只搬中文那两片会让 HTML 结构散在代码里、翻译时看不到全貌。
    html += lang::t(QStringLiteral("ui.result.table_head"));
    for (const QJsonValue& v : final) {
        const QJsonObject o = v.toObject();
        const int seat = o.value(QStringLiteral("seat")).toInt(-1);
        html += lang::t(QStringLiteral("ui.result.rank_row"))
                    .arg(o.value(QStringLiteral("rank")).toInt())
                    // `final[].name` 也是用户数据（玩家名）：这一处没走 seatName()，
                    // 取未转义的缺省值后自己转义一次（别转义两次）。
                    .arg(o.value(QStringLiteral("name")).toString(seatNameRaw(model, seat))
                             .toHtmlEscaped())
                    .arg(o.value(QStringLiteral("score")).toInt())
                    .arg(o.value(QStringLiteral("point")).toDouble(), 0, 'f', 1);
    }
    html += QStringLiteral("</table>");
    return html;
}
