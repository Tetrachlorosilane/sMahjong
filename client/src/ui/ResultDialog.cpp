#include "ui/ResultDialog.h"

#include "i18n/Lang.h"
#include "ui/TileFont.h"

#include "model/TableModel.h"
#include "model/Tile.h"
#include "net/Protocol.h"

#include <QJsonArray>
#include <QFile>
#include <QPushButton>
#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QScrollBar>
#include <QTextBrowser>
#include <QTextCursor>
#include <QTextDocument>
#include <QTextStream>
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
 * 「文字依次浮现」的步进（毫秒）：10 秒的结算窗口里露完 3~5 块，
 * 既不拖沓也不会一闪而过（用户要求：结算动画时间太短）。
 */
constexpr int kRevealIntervalMs = 220;

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

/**
 * 把一段 HTML 切成**顶层块**（`<h2>` / `<h3>` / `<p>` / `<table>` …），
 * 供结算界面「文字依次浮现」逐块显示（用户要求：结算动画时间太短，改成依次浮现）。
 *
 * <p>只做一层扫描：遇到块级开标签就深入找到配对的闭合标签，把整段当作一块；
 * 其余零散文本也算一块。**不追求通用 HTML 解析** —— 这里的输入是我们自己
 * `agariHtml()` 生成的固定结构，够用且不会错。
 */
QStringList splitHtmlBlocks(const QString& html)
{
    QStringList out;
    QString pending;
    int i = 0;
    const int n = html.size();
    while (i < n) {
        if (html.at(i) != QLatin1Char('<')) {
            pending += html.at(i);
            ++i;
            continue;
        }
        const int close = html.indexOf(QLatin1Char('>'), i);
        if (close < 0) {
            pending += html.mid(i);
            break;
        }
        const QString tag = html.mid(i, close - i + 1);
        // 块级开标签：深入找配对闭合
        if (tag.startsWith(QLatin1String("<h2"))
            || tag.startsWith(QLatin1String("<h3"))
            || tag.startsWith(QLatin1String("<p"))
            || tag.startsWith(QLatin1String("<table"))
            || tag.startsWith(QLatin1String("<div"))
            || tag.startsWith(QLatin1String("<ul"))) {
            // 标签名 = 从 '<' 之后到第一个空白 / '>' 之前
            int e = 1;
            while (e < tag.size()) {
                const QChar c = tag.at(e);
                if (c.isSpace() || c == QLatin1Char('>') || c == QLatin1Char('/'))
                    break;
                ++e;
            }
            const QString name = tag.mid(1, e - 1);
            if (!name.isEmpty()) {
                const QString openTag = QStringLiteral("<%1").arg(name);
                const QString closeTag = QStringLiteral("</%1>").arg(name);
                int depth = 1;
                int scan = close + 1;
                while (scan < n && depth > 0) {
                    const int lt = html.indexOf(QLatin1Char('<'), scan);
                    if (lt < 0) {
                        break;
                    }
                    if (html.mid(lt, openTag.size()).compare(openTag, Qt::CaseInsensitive) == 0) {
                        depth++;
                        scan = lt + openTag.size();
                    } else if (html.mid(lt, closeTag.size()).compare(closeTag, Qt::CaseInsensitive) == 0) {
                        depth--;
                        scan = lt + closeTag.size();
                    } else {
                        scan = lt + 1;
                    }
                }
                if (!pending.trimmed().isEmpty()) {
                    out << pending;
                }
                pending.clear();
                out << html.mid(i, qMin(n, scan) - i);
                i = qMin(n, scan);
                continue;
            }
        }
        pending += tag;
        i = close + 1;
    }
    if (!pending.trimmed().isEmpty()) {
        out << pending;
    }
    return out;
}

ResultDialog::ResultDialog(const QString& title, const QString& html, const QString& schematic,
                           QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(title);
    // 结算信息量固定（牌面示意 + 役种 + 合计 + 点数收支），给一个够用的下限；
    // 真正的高度由下面「正文最小高度」+ 布局自己算。
    setMinimumSize(460, 420);
    // ⚠ **限制最大宽度**：牌面示意那一行（14 张手牌 + 和了牌 + 副露）会很长，
    //   而 QLabel 的 sizeHint 会把它算进窗口宽度 → 窗口被撑到 977px 宽、
    //   高度反被挤掉，正文又只剩标题（真机上实测）。
    //   限宽之后示意行由布局换行/裁切，正文拿到稳定的一整块高度。
    setMaximumWidth(720);
    setSizeGripEnabled(false);
    auto* root = new QVBoxLayout(this);

    // 牌面示意：用内嵌的牌面字体渲染牌码串（liga 默认生效）。
    // 字体不可得或串里含非法字元时**整块不显示**，由 agariHtml() 退回汉字/文本表示
    // （两边共用 schematicRenderable()，判据必须一致）。
    if (schematicRenderable(schematic)) {
        // 示意串结构：行间 U+001F；行内「标签 U+001E 牌串」；同行旁挂 U+001D。
        // 除手牌行外每行都是「小字标签 + 牌面」，用同一套内嵌字体渲染。
        // 牌面示意的字号：44/34 都试过 —— 三行示意会把弹窗宽度与高度预算吃光
        // （14 张手牌那一行可达 700px 宽），正文只剩两三行，真机上就是
        // "结算界面看不全"。24 在 1080p 下仍看得清牌面，一行也收得进 720px。
        QFont tf(tilefont::family());
        tf.setPixelSize(24);
        QFont lf(QStringLiteral("Microsoft YaHei"));
        lf.setPixelSize(14);

        auto* wrap = new QWidget(this);
        auto* col = new QVBoxLayout(wrap);
        col->setContentsMargins(12, 8, 12, 2);
        // 行距压到 2px：默认 6px × 3 行看着像"空了一大片"，而结算界面每一点高度
        // 都应该留给正文（真机上"结算界面看不全"就是这么来的）。
        col->setSpacing(2);
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
            row->setVisible(false);            // 等「依次浮现」（见下面的 m_revealTimer）
            col->addWidget(row);
            m_revealWidgets.append(row);
            anyRow = true;
        }
        if (anyRow) {
            root->addWidget(wrap);
        } else {
            delete wrap;
        }
    }

    m_browser = new QTextBrowser(this);
    // 正文给一个稳定的最小高度：它是本弹窗**唯一**可拉伸的控件，而牌面示意那一行
    // 会跟着手牌长度变化 —— 不给下限时，长手牌会把高度预算吃光，正文只剩一行。
    m_browser->setMinimumHeight(260);
    // ⚠ 压掉 QTextDocument 给块级元素的默认外边距：默认值下 `<h2>` 与 `<h3>` 之间会空出
    //   近 100px，结算弹窗看着"上面一大片空、内容却显示不全"（真机上就是这么表现的）。
    //   用文档自带样式表设置，不改任何 HTML 文本（自检断言的是 HTML 字符串本身）。
    m_browser->document()->setDefaultStyleSheet(
        QStringLiteral("h2, h3, p, ul, li, table { margin: 0; padding: 0; }"
                       " ul { margin-left: 18px; }"
                       " li { margin: 0; }"));
    m_htmlBlocks = splitHtmlBlocks(html);
    m_htmlShown = m_htmlBlocks.size();
    m_browser->setHtml(m_htmlBlocks.isEmpty() ? html : m_htmlBlocks.join(QStringLiteral("<br>")));
    root->addWidget(m_browser);
    scrollBrowserToTop();

    m_revealTimer = new QTimer(this);
    m_revealTimer->setInterval(kRevealIntervalMs);
    connect(m_revealTimer, &QTimer::timeout, this, [this]() { revealNextBlock(); });
    // ⚠ **只让牌面行依次浮现，文字正文一次性给全**。
    //   曾经两边都分块露出，结果真机上结算弹窗只剩标题：`setHtml()` 变长内容后
    //   浏览器**保留旧滚动位置**，后露出的块被推到可视区之外（自检查 toPlainText
    //   是查不出来的）。牌面行是独立 QLabel，没有滚动这回事，慢慢露很安全。
    if (!m_revealWidgets.isEmpty())
        m_revealTimer->start();

    // 底部一行：看回放按钮（拿到 replay_id 才显示）+ 局间倒计时文字 + 确认
    auto* bottom = new QHBoxLayout();
    m_replayBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.watch_this")), this);
    m_replayBtn->setVisible(false);   // `enableReplay()` 里才显示（老服务端没有 replay_id）
    connect(m_replayBtn, &QPushButton::clicked, this, [this]() { emit replayRequested(m_replayId); });
    bottom->addWidget(m_replayBtn);
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

    // 最小高度按**布局真正需要的量**给：内容（牌面示意 + 役种 + 合计 + 收支表）
    // 因局而异，写死一个数就会在某些局里把底部按钮行挤出窗口（真机上正文与
    // 「确定」叠在一起）。这里用 layout 自己的 sizeHint，再兜一个下限 420。
    root->setSizeConstraint(QLayout::SetMinimumSize);
    if (QLayout* lay = layout()) {
        const QSize need = lay->sizeHint();
        setMinimumSize(qMax(460, need.width()), qMax(420, need.height()));
    }
    // ⚠ 最大宽度必须**在 setMinimumSize 之后**再钉一次：`setMinimumSize` 会按布局的
    //   sizeHint 重设尺寸约束，把先前那句 setMaximumWidth 顶掉 —— 于是窗口又被那行
    //   十几张牌的面示意撑到 833px 宽（真机上实测）。
    setMaximumWidth(720);
}

/**
 * 露出下一块（一行牌面示意 / 一段 HTML）。
 *
 * <p>两边的顺序是「先牌面行、后文字块」：牌面是这一局的结论，文字是明细。
 * 全部露完就停表；`startCountdown()` 会直接调 `revealAll()` 跳过动画
 * —— 局间倒计时一旦开始，玩家的注意力应该落在"还剩几秒"，不该再等文字。
 */
void ResultDialog::revealNextBlock()
{
    for (QWidget* w : m_revealWidgets) {
        if (w && !w->isVisible()) {
            w->setVisible(true);
            return;
        }
    }
    m_revealTimer->stop();
}

/**
 * 把正文浏览器滚回顶部。
 *
 * <p>⚠ `setHtml()` **不会**重置滚动位置：内容变长后仍停在原来的位置，
 * 于是"依次浮现"露出来的后续块全被推到可视区之外 —— 界面看上去永远只有第一行
 * （真机上就是这么表现的：结算弹窗只剩标题，点数收支表"不见了"）。
 * 每次换内容后必须显式回到顶部。
 */
void ResultDialog::scrollBrowserToTop()
{
    if (!m_browser)
        return;
    m_browser->moveCursor(QTextCursor::Start);
    if (QScrollBar* bar = m_browser->verticalScrollBar())
        bar->setValue(0);
}
QStringList ResultDialog::htmlBlocksForTest(const QString& html)
{
    return splitHtmlBlocks(html);
}

/** 立刻露完全部内容（倒计时开始 / 玩家点确认前都要保证信息完整）。 */
void ResultDialog::revealAll(){
    if (m_revealTimer)
        m_revealTimer->stop();
    for (QWidget* w : m_revealWidgets) {
        if (w)
            w->setVisible(true);
    }
    m_htmlShown = m_htmlBlocks.size();
    if (m_browser && !m_htmlBlocks.isEmpty()) {
        m_browser->setHtml(m_htmlBlocks.join(QStringLiteral("<br>")));
        scrollBrowserToTop();
    }
}

void ResultDialog::enableReplay(const QString& replayId)
{
    m_replayId = replayId.trimmed();
    if (m_replayBtn != nullptr) {
        m_replayBtn->setVisible(!m_replayId.isEmpty());
    }
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
    // 倒计时一开始就把内容全部露出来：玩家只有这几秒，不该再等浮现动画
    revealAll();
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
    const bool tsumo = ev.value(QStringLiteral("tsumo")).toBool();
    QStringList hand = proto::stringList(ev.value(QStringLiteral("hand")));
    const QString win = ev.value(QStringLiteral("winning_tile")).toString();

    // ⚠ **自摸时 `hand` 里已经含和了牌**（自摸和了形是 14 张，服务端把和了牌算在手牌里），
    //   而 `winning_tile` 又单独发一份 —— 直接把两者都画出来，界面上的和了牌会出现两次
    //   （报障：自摸张在手牌里一次、在和了牌位又一次，看着像多了一张牌）。
    //   所以自摸时先从手牌里摘掉**一张**同牌码的和了牌，再把它单独摆到和了牌位；
    //   荣和时手牌是 13 张（不含和了牌），不必摘。
    if (tsumo && !win.isEmpty()) {
        const int dup = hand.indexOf(win);
        if (dup >= 0) {
            hand.removeAt(dup);
        }
    }

    // 手牌行：暗牌 +（空一格）+ 和了牌 +（空一格）+ 各副露
    QString handSeg = hand.join(QStringLiteral(" "));
    if (!win.isEmpty()) {
        // 和了牌的朝向要区分自摸与荣和（用户要求）：
        //   自摸 → **竖置**（牌码本身，字体默认竖着画）；
        //   荣和 → **横置**（后置 `-`，与副露里"被鸣的那张"同一套语法）。
        // 两者都靠 U+001D 旁挂分隔，渲染时插入固定一个牌位的间隔，所以是"空一小段距离"。
        handSeg += QChar(kAsideSep) + (tsumo ? win : win + QLatin1Char('-'));
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
