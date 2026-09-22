#pragma once

// 对局记录（回放）的**纯数据模型**：解析服务端的 `replay_list` / `replay_get`，
// 建好"按小局 / 按巡 / 按操作"的索引与"牌山每张牌归谁、何时被拿走"的索引。
//
// 设计取舍（都对应需求里的"性能与内存管理"）：
//   · 服务端按 2000 条一页下发，这里**一次拉完**（一场半庄约 1~3 MB JSON，可接受）；
//     页与页之间只做追加，不重复解析、不重复拷贝。
//   · 建索引是**一趟线性扫描**：小局 / 巡 / 牌山归属都在 `build()` 里算完，
//     之后"下一巡 / 跳转第 N 巡 / 上一小局"都是查表（O(1)~O(log n)），
//     不在绘制或按键路径上扫事件。
//   · 牌山只放**下标 → 归属**的紧凑结构（136 条），不复制牌面字符串。
//
// 与渲染的分工：本类**不做绘制**；`ReplayWindow` 拿 `seek()` 结果去喂 `TableModel`。

#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVector>

struct ReplayEntry
{
    int seq = 0;
    qint64 t = 0;          // 相对开局的毫秒
    int to = -1;           // 收件座位；-1 = 广播
    QJsonObject body;

    QString ev() const { return body.value(QStringLiteral("ev")).toString(); }
    int seat() const { return body.value(QStringLiteral("seat")).toInt(-1); }
    bool isChat() const { return ev() == QLatin1String("chat"); }
};

class ReplayModel
{
public:
    /** 一小局：边界条目下标 + 局况 + 牌山（136 张，按抓牌顺序）。 */
    struct Round
    {
        int start = 0;
        QString bakaze;
        int kyoku = 1;
        int honba = 0;
        int dealer = 0;
        QVector<int> wall;
    };

    /** 牌山某一张的归属：谁、什么时候（哪条 entry）拿走；以及它是不是王牌。 */
    struct WallSlot
    {
        int takenAt = -1;      // entries 下标；-1 = 还没被拿走
        int seat = -1;         // 归属座位（配牌按抓牌顺序算，摸牌按 draw 事件）
        int order = 0;         // 是该家的第几张（0 起，含配牌）
        bool dead = false;     // 王牌 14 张（岭上 / 表宝牌 / 里宝）
        bool movedByKan = false;   // 被开杠移进王牌补位（从此摸不到）
    };

    void reset();
    /** 头（`replay_get.meta`）：id / 名字 / 规则 / 每小局牌山 / 小局边界。 */
    bool setMeta(const QJsonObject& meta);
    /** 追加一页 `entries`。 */
    void addEntries(const QJsonArray& arr);
    /** 数据齐了以后建索引（小局 / 巡 / 牌山归属）。 */
    void build();

    bool hasMeta() const { return m_hasMeta; }
    bool empty() const { return m_entries.isEmpty(); }
    int total() const { return m_total; }
    int loaded() const { return m_entries.size(); }
    bool complete() const { return m_total > 0 && m_entries.size() >= m_total; }

    QString replayId() const { return m_id; }
    QStringList names() const { return m_names; }
    QJsonObject rules() const { return m_rules; }
    QString preset() const { return m_rules.value(QStringLiteral("preset")).toString(); }

    const QVector<ReplayEntry>& entries() const { return m_entries; }
    const QVector<Round>& rounds() const { return m_rounds; }
    int roundCount() const { return m_rounds.size(); }
    /** 第 round 小局的牌山归属（越界返回空）。 */
    const QVector<WallSlot>& wallSlots(int round) const;

    QString playerName(int seat) const;
    QString roundText(int round) const;

    int roundOf(int index) const;
    /** 该 entry 属于本小局第几巡（1 起）。巡 = 四家各摸一次。 */
    int turnOf(int index) const;
    int maxTurn(int round) const;
    int turnStart(int round, int turn) const;      // 该巡第一条 entry（找不到 = -1）
    int nextTurnStart(int index) const;
    int prevTurnStart(int index) const;
    int roundStart(int round) const;               // 小局第一条 entry
    int nextRoundStart(int index) const;
    int prevRoundStart(int index) const;
    /**
     * 该小局的**结算事件**下标（`agari` / `ryuukyoku`，取该小局最后一条）。
     *
     * <p>回放里「每小局结束都给一次结算界面」要看这条：结算界面的 HTML 由那条事件 + 当时
     * 的模型状态渲染，所以必须能定位到"是哪一条"，而不是靠 `round_end` 里的布尔字段。
     *
     * @return entry 下标；`-1` = 该小局还没打完（记录里没有结算事件）。
     */
    int roundResultEntry(int round) const;

    // ---- 「步」= 一个逻辑操作 ----
    // 记录里一条事件可能有多份（`round_start` 按座位各发一份），所以**步**才是
    // "上一步 / 下一步"的单位：`stepEntry()` 给出每一步的代表 entry。实测真机跑下来的
    // 第一版就是没做这件事 —— 一次摸牌要按四次"下一步"、一次配牌要按四次。
    int stepCount() const { return m_steps.size(); }
    int stepEntry(int step) const;
    int stepOfEntry(int entry) const;
    int nextStepEntry(int entry) const;
    int prevStepEntry(int entry) const;
    /**
     * 该小局「配牌完成」那一步的 entry 下标（= 最后一条 `round_start`）。
     *
     * <p>跳小局时落在这里而不是边界条目：边界上四家的手牌还没下发，
     * 画面会是"四个空手牌"（真机截图就踩过），落在配牌完成处才是玩家心里的"一局开头"。
     */
    int roundOpenEnd(int round) const;

    /** 该 entry 的"说明"（界面上的操作列表用），例 "东家 摸 5p" / "乙：碰！"。 */
    QString describe(int index) const;

    /** 上帝视角的四家手牌（**按牌码**，已扣掉打出与副露的牌）。 */
    struct GodState
    {
        QStringList hands[4];                            // **含**刚摸到的那张（与 `round_start` + `draw` 的叠加一致）
        QString drawn[4];                                // 刚摸到、还没打出的那张（`TableView::setGodHands` 要单列一格）
        QStringList rivers[4];
        QVector<QPair<QString, QStringList>> melds[4];   // kind, tiles
        QVector<int> scores;
        QString roundText;
        bool valid = false;
    };
    /** 从第 `index` 条（含）往前推出来的四家状态（用于牌山视图的"这张牌现在在哪"）。 */
    const GodState& godState(int index) const;

    /** 牌山下标的静态坐标：0..47 配牌三轮、48..51 补第 13 张、52 庄家第 14 张、53.. 摸牌、122.. 王牌。 */
    static int dealRoundOf(int k) { return k / 16; }
    static int dealSeatOf(int k) { return (k % 16) / 4; }
    static int dealCopyOf(int k) { return k % 4; }
    static bool isDeadWallIndex(int k) { return k >= 122 && k < 136; }
    /** 牌山总张数。 */
    static constexpr int kWallSize = 136;

private:
    bool m_hasMeta = false;
    QString m_id;
    QStringList m_names;
    QJsonObject m_rules;
    int m_total = 0;
    QVector<ReplayEntry> m_entries;
    QVector<Round> m_rounds;
    QVector<QVector<WallSlot>> m_walls;
    QVector<int> m_roundOf;         // entry -> 小局
    QVector<int> m_turnOf;          // entry -> 巡（1 起）
    QVector<QVector<int>> m_turnStart;   // 小局 -> 巡 -> entry 下标
    QVector<int> m_steps;           // 步 -> 代表 entry
    QVector<int> m_stepOf;          // entry -> 步
    mutable int m_godAt = -1;
    mutable GodState m_god;

    void buildRoundTurn();
    void buildWall(int round);
    void buildGod(int index) const;
};
