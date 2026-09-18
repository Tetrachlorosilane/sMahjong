#pragma once

// 结算弹窗：和牌 / 流局 / 终局顺位。

#include <QDialog>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVector>

class QLabel;
class QPushButton;
class QTextBrowser;
class QTimer;
class TableModel;

class ResultDialog : public QDialog
{
    Q_OBJECT
public:
    ResultDialog(const QString& title, const QString& html,
                  const QString& schematic = QString(), QWidget* parent = nullptr);

    /**
     * 局间倒计时：服务端最多等 5 秒（或所有人确认）。
     * 到点**自动关闭**本弹窗（关闭即视为确认），不必等玩家点按钮。
     * 由 MainWindow 在收到 `round_wait` 时调用。
     */
    void startCountdown(int ms);

    /**
     * 允许"看本局回放"：把这一场的回放 ID 传进来，底部按钮才会出现。
     * 服务端没给 `replay_id`（关掉了回放 / 老服务端）时保持隐藏。
     */
    void enableReplay(const QString& replayId);

signals:
    void replayRequested(const QString& replayId);

public:
    // 组装文本
    static QString agariHtml(const QJsonObject& ev, const TableModel* model);
    static QString ryuukyokuHtml(const QJsonObject& ev, const TableModel* model);
    static QString gameEndHtml(const QJsonObject& ev, const TableModel* model);

    // 牌面示意串（喂给内嵌牌面字体）；拿不到可用数据时返回空串
    static QString schematicOf(const QJsonObject& ev, const TableModel* model, bool agari = true);

    /**
     * 「依次浮现」用到的顶层块切分（自检用）。
     *
     * <p>暴露它是为了钉住一条真踩过的坑：`agariHtml()` 里**役满分支**的合计文案
     * 曾经是一个**只有 `</p>` 没有 `<p>`** 的孤立闭合标签，块扫描于是把后面所有内容
     * （点数收支表）都当成嵌套在未闭合的 `<p>` 里，最终结算界面只剩标题和牌面。
     * 自检必须同时验证「块的条数」与「每块里标签配平」。
     */
    static QStringList htmlBlocksForTest(const QString& html);

private:
    /** 露出下一块（一行牌面示意 / 一段 HTML）。 */
    void revealNextBlock();
    /** 立刻露完全部内容（倒计时开始前必须保证信息完整）。 */
    void revealAll();
    /** 正文浏览器滚回顶部（setHtml 不重置滚动位置，见实现里的说明）。 */
    void scrollBrowserToTop();

    QTextBrowser* m_browser = nullptr;
    QLabel* m_countdown = nullptr;
    QPushButton* m_replayBtn = nullptr;
    QString m_replayId;
    QTimer* m_timer = nullptr;
    int m_leftMs = 0;

    // ---- 「依次浮现」：只有**牌面示意行**逐行露出（构造时先建好、隐藏）----
    // 文字正文一次性给全：`setHtml()` 变长内容后不重置滚动位置，分块露出会让
    // 后露的块滚到可视区外（真机上结算弹窗只剩标题，判据见 SelfTest 里的出图断言）。
    QVector<QWidget*> m_revealWidgets;   // 尚未露出的牌面行
    QStringList m_htmlBlocks;            // 文字部分的顶层块（一次给全，分块只为可测/将来用）
    int m_htmlShown = 0;                 // 已露出的文字块数
    QTimer* m_revealTimer = nullptr;
};
