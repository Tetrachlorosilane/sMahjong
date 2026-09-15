#pragma once

// **天鳳牌譜导出**（tenhou.net/6 的 `#json=` 形式）。
//
// 格式参考 <https://github.com/wuye999/tenhou>（`生成2.py` 的 `TenhouLog` 类）：
//
//   {
//     "title": ["<房间名>", "<GMT 时间>"],
//     "name":  ["东家名","南家名","西家名","北家名"],     // 按**座位**顺序（seat 0..3）
//     "rule":  {"disp":"...","aka53":1,"aka52":1,"aka51":1},
//     "log": [ [ 小局0 ], [ 小局1 ], ... ]
//   }
//
// 每个小局 = 17 个元素，顺序固定：
//   [0] [场局次, 本场, 供託]       场局次：0..3 = 東1..東4，4..7 = 南1..南4，8..11 = 西1..西4
//   [1] [四家点数]
//   [2] 表ドラ指示牌（含杠后追加的）
//   [3] 裏ドラ指示牌
//   [4..7]  四家**配牌**（各 13 张；庄家的第 14 张算作他的第一次摸牌）
//   [8..11] 四家**取牌**（按摸牌先后）
//   [12..15] 四家**出牌**：牌号 = 手切；`60` = 摸切；`"r60"` = 立直宣言且摸切；`"r<牌号>"` = 立直宣言手切
//   [16] 结果（见下）
//
// 牌号用天鳳的两位数编码：11..19 = 1m..9m、21..29 = 1p..9p、31..39 = 1s..9s、
// 41..47 = 東南西北白發中、**51/52/53 = 赤五 m/p/s**。
//
// ⚠ 两点必须说清楚（都是**这个格式本身**的限制，不是我们的取舍）：
//   ① **没有副露的表示**：参考项目也只把副露用来推导摸/打序列，导出的牌谱里看不到鸣牌。
//      所以有鸣牌的小局，导出的牌河与手牌数会与真实牌局不完全一致。要连副露一起保留，
//      需要天鳳的 `mjlog` XML（本项目暂未实现）。
//   ② 最后一个元素（结果）参考项目里是**写死的** `["全員聴牌"]`；我们按已知的结算事件尽量填
//      （和了 → `["和了", 和了家, 放銃家]`；流局 → `["流局"]`），但仍以展示牌河为主。

#include <QString>
#include <QStringList>

class ReplayModel;

class TenhouLog
{
public:
    struct Result
    {
        bool ok = false;
        QString json;          // 牌谱 JSON（可直接拼进链接）
        QString url;           // https://tenhou.net/6/#json=<json>
        QStringList problems;  // 导出过程中发现的问题（小局级，不致命）
        int rounds = 0;        // 导出的小局数
    };

    /** 把我们记录的整场导出成天鳳牌譜。任何小局级的小毛病都不致命，只会记进 `problems`。 */
    static Result build(const ReplayModel& rp);

    /** 牌码 → 天鳳牌号（"1m"→11 … "0s"→53）；认不出的返回 -1。 */
    static int tileNumber(const QString& code);

    // ================= 完整天鳳牌谱（mjlog XML）=================
    //
    // `#json=` 那份是**查看器用的瘦格式**（没有副露）；这一份是天鳳原生的 `mjlog` XML，
    // **完整**：配牌、每一张摸牌/打牌、鸣牌（吃碰杠/暗杠/加杠）、立直宣言、杠宝牌、
    // 和了与流局全部带着。标签与位域编码都按公开实现核对过（mjlog2mjai / mjlog2json /
    // tenhou 的 `tehai.js` 反解公式），关键几条：
    //   · 摸牌/打牌把**牌号写在标签名里**：`<T{id}/>`…`<W{id}/>` = 0..3 家摸牌，
    //     `<D{id}/>`…`<G{id}/>` = 0..3 家打牌（`id` 是 0..135 的**唯一**牌号）。
    //     所以**摸切的牌号必须与刚摸到的那张相同** —— 我们按这个不变量分配牌号。
    //   · `<N who m/>` 的 `m` 是位域打包：`m&0x3` = 被鸣者相对座位（0=上家/3=自己=暗杠），
    //     `0x4` 吃 / `0x8` 碰 / `0x10` 加杠 / `0x20` 拔北；吃是 `(pattern<<10)|三家副本偏移`，
    //     碰/加杠是 `(pattern<<9)|(缺的那张副本<<5)`，`pattern = 种类*3 + 被鸣牌在其中的序号`；
    //     杠是 `(代表牌号<<8)|相对座位`。
    //   · 立直：`<REACH who step="1"/>` 在**宣言牌之前**、`step="2"` 在其后。
    //
    // ⚠ 两处**如实说明**的限制（不是漏做，是数据/格式本身如此）：
    //   ① **不写 `<SHUFFLE>`（牌山）**：天鳳把整副牌山打包成一个 seed，那个编码没有公开规范；
    //      而本格式里每一次摸牌都带牌号，**牌山本来就是冗余的**，不写不影响任何消费方还原牌局。
    //   ② **不写 `yaku` 属性**：天鳳的役种是数字 id，那张表我无法在此环境核对；写错等于报错役。
    //      （和了/流局的家、手牌、和了牌、宝牌/里宝、符数/点数/收支都在。）
    struct MjlogResult
    {
        bool ok = false;
        QString xml;
        QStringList problems;
        int rounds = 0;
        int tiles = 0;   // 分配出去的牌号数（自检用来核"每个牌号最多用一次"）
    };

    /** 生成完整天鳳牌谱（mjlog XML）。 */
    static MjlogResult buildMjlog(const ReplayModel& rp);

    /** 写出 mjlog 文件（UTF-8，带 XML 声明）。 */
    static bool writeMjlogFile(const MjlogResult& r, const QString& path, QString* err = nullptr);

    /**
     * 解一条 `m`（**照抄参考实现的解码公式**，只给自检用）：
     * 返回被鸣的几张牌号 + 被鸣者相对座位；`kind` 回填 "chi"/"pon"/"kakan"/"kan"。
     * 这是"我编的位域必须能被公开实现解回来"的判据。
     */
    static QVector<int> decodeMeldForTest(int m, QString* kind, int* dir);

    /**
     * 写出到文件（`.txt`，内容就是那一行链接 —— 与参考项目一致）。
     * @param path 目标文件；父目录不存在会创建
     */
    static bool writeFile(const Result& r, const QString& path, QString* err = nullptr);
};
