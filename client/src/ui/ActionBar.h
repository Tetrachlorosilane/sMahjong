#pragma once

// 操作栏：收到 ask 事件后弹出对应按钮，带倒计时进度。
// 点击按钮立即发出 {"cmd":"action",...}。

#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QVector>
#include <QWidget>

class QHBoxLayout;
class QLabel;
class QProgressBar;
class QPushButton;

class ActionBar : public QWidget
{
    Q_OBJECT
public:
    explicit ActionBar(QWidget* parent = nullptr);

    void setAsk(const QJsonObject& ask);
    // 附加说明（例如「振听，不能荣和」），显示在标题后面
    void setNote(const QString& note);
    void clearAsk();
    bool hasAsk() const { return m_valid; }

    // 当前询问本体与种类（turn / claim / chankan）。自动开关在询问进行中被点动时，
    // 要拿它重新决策一次（见 MainWindow::onAutoFlagsChanged）。
    const QJsonObject& currentAsk() const { return m_ask; }
    QString currentKind() const { return m_kind; }

    bool canDiscard() const;             // 当前 ask 为 turn 且允许打牌
    bool riichiMode() const { return m_riichiMode; }
    void setRiichiMode(bool on);
    QStringList riichiTiles() const;     // 立直宣言候选牌

    // 本询的 ask_id（没有则 -1）。回包必须原样带回 —— 服务端靠它丢弃过期/重复回包
    // （见 Table.awaitAction 与 AGENTS §2.2）。
    qint64 askId() const;

    // 组包唯一入口：所有动作都从这里产生，才能保证「必带 ask_id」这条不变量
    // 不会因为某条分支忘了加而破功。询问已失效/已超时时返回**空对象**，
    // 调用方据此丢弃这次操作（这是「连点两下发出两条动作」的第二道闸）。
    QJsonObject actionCmd(const QString& type) const;
    QJsonObject riichiCmd(const QString& tile) const;
    // `tsumogiri` 必须显式下发（PROTOCOL §2.2）：服务端要按它**从摸牌位还是暗手**取牌。
    // 只发一个牌码字符串时，"手里已有 5m、摸到的也是 5m、点的是手里那张"这种局面下
    // 服务端只能靠"同 kind 取第一个副本"去猜，取到的可能与玩家点的那张不是同一张
    // （赤五 0m 与普通 5m 同 kind 更是必然猜不出）→ 手牌在两端悄悄错位（幽灵手牌）。
    QJsonObject discardCmd(const QString& tile, bool tsumogiri) const;

    // 自检用
    QString titleTextForTest() const;
    QPushButton* buttonForTest(const QString& label) const;
    /** 当前所有动作按钮的文案（断言失败时打出来，省得靠猜"到底画了哪几个"）。 */
    QStringList buttonTextsForTest() const;

signals:
    void actionReady(const QJsonObject& action);  // 已组装好的完整 cmd
    void riichiModeChanged(bool on);
    void hint(const QString& text);               // 给用户的文字提示

private:
    void rebuild();
    void onTick();
    void sendSimple(const QString& type);
    /** 按**整条选项**发包（会带上 `tiles` = 副露赤宝选择的精确牌码，见 PROTOCOL §3.6）。 */
    void sendOption(const QJsonObject& opt);
    void showChiMenu();
    void showKanMenu();
    QString baseTitle() const;    // 不含倒计时的标题
    void refreshTitle();          // 标题 = 基标题/立直提示 + 倒计时

    QJsonObject m_ask;
    bool m_valid = false;
    QString m_kind;
    QString m_note;
    QVector<QJsonObject> m_options;
    qint64 m_deadlineAbs = 0;
    qint64 m_totalMs = 0;
    bool m_riichiMode = false;
    bool m_expired = false;

    QHBoxLayout* m_layout = nullptr;
    QLabel* m_title = nullptr;
    QPushButton* m_riichiBtn = nullptr;   // 立直是「模式」按钮，做成 checkable 显示状态
    QVector<QPushButton*> m_buttons;
    QVector<QProgressBar*> m_bars;
    QTimer m_tick;
    /** 提示闪烁：交替切换按钮样式（见 ActionBar.cpp 的 actionButtonStyle）。 */
    QTimer m_alertTimer;
    bool m_alertArmed = false;
};
