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

    /**
     * 写出到文件（`.txt`，内容就是那一行链接 —— 与参考项目一致）。
     * @param path 目标文件；父目录不存在会创建
     */
    static bool writeFile(const Result& r, const QString& path, QString* err = nullptr);
};
