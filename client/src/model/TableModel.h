#pragma once

// 牌桌数据模型（纯数据 + 一个 changed() 信号）
// 只负责把 docs/PROTOCOL.md §3 的事件写进内存，不做任何规则判定
//（唯一的例外：本地「听牌提示」仅为显示便利，见 waits()）。

#include <QJsonArray>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVector>

// 副露
struct Meld {
    QString kind;        // chi / pon / daiminkan / ankan / kakan
    QStringList tiles;   // 含被鸣的那张
    int from = 0;        // 被鸣者座位；暗杠 = 自己
    QString calledTile;  // 被鸣/被杠的牌
    QVector<bool> aka;   // 每张是否赤宝牌

    bool isKan() const { return kind.endsWith(QLatin1String("kan")); }
    bool isConcealed() const { return kind == QLatin1String("ankan"); }
};

/**
 * 副露里**被鸣的那张**显示在第几个格子（横置位）。-1 = 不横置。
 *
 * 吃 / 碰 / 大明杠用同一套规则：被鸣的牌落在「来源方位」对应的格子上 ——
 *   上家 → 最左、对家 → 中间、下家 → 最右。
 * **与点数顺序无关**：吃了上家的 3s（手里 2s、4s），三张显示成 `3s-2s-4s`（3s 横置在最左），
 * 而不是按点数排成 `2s-3s-4s`。这纯粹是显示顺序，服务端仍按顺子判定。
 *
 * 暗杠不横置（两端画牌背）；加杠横在第二张。
 *
 * ⚠ 牌桌（TableView）与结算界面（ResultDialog）**必须共用本函数**：
 *   结算界面曾按「牌码 == calledTile」自己判断该横置哪一张，而**碰/杠的三四张牌码完全相同**，
 *   于是整组都被横置，表现为"横置方向随机"。
 */
inline int meldSidewaysIndex(const Meld& m, int ownerSeat)
{
    if (m.tiles.isEmpty() || m.kind == QLatin1String("ankan")) {
        return -1;
    }
    if (m.kind == QLatin1String("kakan")) {
        return m.tiles.size() >= 4 ? 1 : 0;
    }
    const int rel = ((m.from - ownerSeat) % 4 + 4) % 4;
    if (rel == 3) {
        return 0;                                  // 上家 → 最左
    }
    if (rel == 2) {
        return 1;                                  // 对家 → 中间
    }
    return qMin(2, m.tiles.size() - 1);            // 下家 → 最右
}

class TableModel : public QObject
{
    Q_OBJECT
public:
    explicit TableModel(QObject* parent = nullptr);

    void reset();
    void applyEvent(const QJsonObject& ev);

    // ---- 基本信息 ----
    bool hasSeat() const { return m_hasSeat; }
    int mySeat() const { return m_mySeat; }
    void setMySeat(int seat);
    int dealer() const { return m_dealer; }
    QString bakaze() const { return m_bakaze; }
    int kyoku() const { return m_kyoku; }
    int honba() const { return m_honba; }
    int riichiSticks() const { return m_riichiSticks; }
    QString roundText() const;          // 例："東1局 0本場"
    QString seatWind(int seat) const;   // 该座位的自风（東/南/西/北）

    // ---- 手牌 ----
    QStringList hand() const { return m_hand; }        // 已排序（不含摸到的牌）
    QString drawnTile() const { return m_drawn; }      // 刚摸到、单独放在最右
    int concealedCount(int seat) const;                // 该家暗牌张数（含摸牌）
    int drawnSeat() const { return m_drawnSeat; }      // 刚摸牌的是哪家（-1 = 无）

    // ---- 桌面 ----
    QStringList discards(int seat) const;
    bool discardSideways(int seat, int index) const;   // 立直宣言牌横置
    QVector<Meld> melds(int seat) const;
    QVector<int> scores() const { return m_scores; }
    int score(int seat) const;
    QString playerName(int seat) const;
    void setPlayerName(int seat, const QString& name);
    void setScores(const QVector<int>& scores);

    QStringList doraIndicators() const { return m_dora; }
    int tilesLeft() const { return m_tilesLeft; }
    int deadWallLeft() const { return m_deadWallLeft; }
    int turn() const { return m_turn; }
    QString phase() const { return m_phase; }
    bool riichi(int seat) const;
    bool furiten(int seat) const;

    // ---- 本地听牌提示（不做役种/振听判定，仅形式听牌）----
    QStringList waits() const;   // 能和的所有牌（去掉红宝牌标记，按 kind 唯一）
    bool isTenpai() const { return !waits().isEmpty(); }
    // ⚠ 振听**没有**本地推断版本：它是规则判定，只认服务端下发的
    //   `state.furiten[]` / `ask.win_note`，见 furiten(seat) 与 AGENTS §2.1。

    // ---- ask 询问 ----
    bool hasAsk() const { return m_askValid; }
    QJsonObject askEvent() const { return m_ask; }
    QString askKind() const;
    QVector<QJsonObject> askOptions() const;
    qint64 askRemainMs() const;   // 剩余毫秒（<0 表示已超时）
    qint64 askTotalMs() const;
    int askFrom() const;
    QString askTile() const;
    void clearAsk();

    // ---- 结算 ----
    QJsonObject lastAgari() const { return m_agari; }
    QJsonObject lastRyuukyoku() const { return m_ryuukyoku; }
    QJsonObject lastRoundEnd() const { return m_roundEnd; }
    QJsonObject lastGameEnd() const { return m_gameEnd; }
    QJsonObject lastError() const { return m_error; }
    QString lastErrorText() const;

signals:
    void changed();
    void chatReceived(int seat, const QString& name, const QString& text);
    // 出牌（用于播放「手切 / 摸切」动画）；riverIndex 为这张牌在牌河中的下标
    void discarded(int seat, const QString& tile, bool tsumogiri, int riverIndex);
    // 鸣牌：某家牌河中的第 riverIndex 张被 bySeat 鸣走（牌是「移动」过去的，不是复制）
    void calledFromRiver(int fromSeat, int riverIndex, int bySeat);

private:
    void sortHand();
    bool takeFromHand(const QString& tile);
    void syncNamesFromSeats(const QJsonArray& seats);
    static bool isWinningForm(const QVector<int>& counts, int meldCount);

    bool m_hasSeat = false;
    int m_mySeat = 0;
    int m_dealer = 0;
    QString m_bakaze = QStringLiteral("E");
    int m_kyoku = 1;
    int m_honba = 0;
    int m_riichiSticks = 0;

    QStringList m_hand;
    QString m_drawn;      // 摸到的牌（单独放最右）
    int m_drawnSeat = -1; // 刚摸牌的座位

    QVector<QStringList> m_discards;                  // 4 家牌河
    QVector<QVector<bool>> m_discardSide;             // 牌河横置标记
    QVector<QVector<Meld>> m_melds;                   // 4 家副露
    QVector<int> m_scores;
    QStringList m_names;
    QVector<bool> m_riichi;
    QVector<bool> m_furiten;

    QStringList m_dora;
    int m_tilesLeft = 0;
    int m_deadWallLeft = 0;
    int m_turn = 0;
    QString m_phase;

    QJsonObject m_ask;
    bool m_askValid = false;
    qint64 m_askDeadlineAbs = 0;
    qint64 m_askTotalMs = 0;

    QJsonObject m_agari;
    QJsonObject m_ryuukyoku;
    QJsonObject m_roundEnd;
    QJsonObject m_gameEnd;
    QJsonObject m_error;
};
