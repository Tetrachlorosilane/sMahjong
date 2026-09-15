#pragma once

// 结算弹窗：和牌 / 流局 / 终局顺位。

#include <QDialog>
#include <QJsonObject>
#include <QString>

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

private:
    QTextBrowser* m_browser = nullptr;
    QLabel* m_countdown = nullptr;
    QPushButton* m_replayBtn = nullptr;
    QString m_replayId;
    QTimer* m_timer = nullptr;
    int m_leftMs = 0;
};
