// 一条**公开事件**（Java `Round.Event` 的逐字段镜像）+ 它的 JSON 渲染。
//
// 为什么单独一个头（Java 把它嵌在 `Round` 里）：`Observation` 也要持有事件流
// （决策那一刻的**快照**），而 `round.hpp` 已经 include 了 `observation.hpp` ——
// 嵌在 `Round` 里就成环了。字段、工厂、JSON 的键序与取舍**逐条照抄** Java，别"顺手整理"。
//
// ⚠ 只装客户端看得见的东西（牌码 / 谁做的 / 被鸣那张来自谁 / 巡目）—— 反作弊口径与 Java 同：
//   别家手牌、牌山顺序、里宝一律不进这里（`docs/PROTOCOL.md` §8.2）。
#pragma once

#include <string>
#include <vector>

#include "jsonw.hpp"
#include "meld.hpp"
#include "tiles.hpp"

namespace trainer {

/** Java `Round.Event`：`type` ∈ discard / meld / kan / riichi / dora_flip（其余三型刻意不发）。 */
struct Event {
    std::string type;
    /** 主牌牌码（`discard` 是打出的那张；`meld`/`kan` 是**被鸣那张**；`dora_flip` 是新翻的指示牌）。 */
    bool hasTile = false;
    std::string tile;
    /** 被鸣那张（与 `tile` 相同；加杠时是加上的第 4 张）。 */
    bool hasCalledTile = false;
    std::string calledTile;
    /** 谁做的（**绝对**座位；`-1` = 无）。 */
    int actor = -1;
    /** 被鸣那张来自谁（绝对座位；`-1` = 无）。 */
    int from = -1;
    /** `chi`/`pon`/`ankan`/`kakan`/`daiminkan`（空 = 非副露）。 */
    std::string meldKind;
    /** 整副副露的牌码（`meld`/`kan` 才有）—— 增量缓存靠它累加 `meld_count`。 */
    bool hasTiles = false;
    std::vector<std::string> tiles;
    /** 该 actor 的第几次摸牌（0 = 还没摸过）。 */
    int turn = 0;
    /** 舍牌是否摸切（只对 `discard` 有意义）。 */
    bool tsumogiri = false;
    /** 是否立直宣言那张（横置）；顺延牌**不算**。 */
    bool sideways = false;
    /** 该事件是否发生在"该 actor 立直**之后**"；立直宣言那张算 `false`（它才是分界）。 */
    bool ripPhase = false;

    // ---------------------------------------------------------------- 工厂（与 Java 一一对应）

    static Event discard(int seat, int id, int turn, bool tsumogiri, bool sideways, bool ripPhase) {
        Event e;
        e.type = "discard";
        e.hasTile = true;
        e.tile = tileToStr(id);
        e.actor = seat;
        e.turn = turn;
        e.tsumogiri = tsumogiri;
        e.sideways = sideways;
        e.ripPhase = ripPhase;
        return e;
    }

    static Event meld(int seat, const Meld &m, int turn, bool ripPhase) {
        Event e;
        // 杠是**另一种** `type`（它翻宝牌、给岭上牌）；`meld_kind` 两者都带。
        e.type = m.isKan() ? "kan" : "meld";
        e.hasTile = true;
        e.tile = tileToStr(m.calledId);
        e.hasCalledTile = true;
        e.calledTile = e.tile;
        e.actor = seat;
        e.from = m.from;
        e.meldKind = meldKindWire(m.kind);
        e.hasTiles = true;
        for (int i = 0; i < m.tileCount; i++) {
            e.tiles.push_back(tileToStr(m.tiles[static_cast<size_t>(i)]));
        }
        e.turn = turn;
        e.ripPhase = ripPhase;
        return e;
    }

    static Event riichi(int seat, int turn) {
        Event e;
        e.type = "riichi";
        e.actor = seat;
        e.turn = turn;
        return e;
    }

    static Event doraFlip(int kind, int seat, int turn) {
        Event e;
        e.type = "dora_flip";
        e.hasTile = true;
        e.tile = kindToStr(kind, false);         // 指示牌按**牌种**公开（赤五信息在牌山那边就没了）
        e.actor = seat;
        e.turn = turn;
        return e;
    }

    /**
     * 这条事件是否**从牌河里拿走**了一张（吃 / 碰 / 大明杠）—— 校验器与自检靠它重建牌河。
     *
     * 暗杠不从河里拿（`from == actor`），加杠拿的是**手里的**第 4 张（`meld_kind == kakan`）。
     */
    bool takesFromRiver() const {
        return (type == "meld" || type == "kan") && from != actor
               && (meldKind == "chi" || meldKind == "pon" || meldKind == "daiminkan");
    }
};

/**
 * `Observation.toJson()` 里 `events` 那一坨 —— 与 Java `Observation.eventJson` **同键序、同取舍**：
 * 没有的字段整个键都不出现（`tile`/`called_tile`/`tiles`/`meld_kind`/`from`），
 * `tsumogiri`/`sideways` 只在 `discard` 上，`rip_phase` 只在为真时出现。
 */
inline void eventsJson(std::string &out, const std::vector<Event> &evs) {
    out.push_back('[');
    for (size_t i = 0; i < evs.size(); i++) {
        if (i > 0) {
            out.push_back(',');
        }
        const Event &e = evs[i];
        out += "{\"type\":";
        jsonStr(out, e.type);
        if (e.hasTile) {
            out += ",\"tile\":";
            jsonStr(out, e.tile);
        }
        if (e.hasCalledTile) {
            out += ",\"called_tile\":";
            jsonStr(out, e.calledTile);
        }
        if (e.hasTiles) {
            out += ",\"tiles\":";
            jsonStrArray(out, e.tiles);
        }
        if (!e.meldKind.empty()) {
            out += ",\"meld_kind\":";
            jsonStr(out, e.meldKind);
        }
        out += ",\"actor\":";
        out += std::to_string(e.actor);
        if (e.from >= 0) {
            out += ",\"from\":";
            out += std::to_string(e.from);
        }
        out += ",\"turn\":";
        out += std::to_string(e.turn);
        if (e.type == "discard") {
            out += ",\"tsumogiri\":";
            jsonBool(out, e.tsumogiri);
            out += ",\"sideways\":";
            jsonBool(out, e.sideways);
        }
        if (e.ripPhase) {
            out += ",\"rip_phase\":true";
        }
        out.push_back('}');
    }
    out.push_back(']');
}

}  // namespace trainer
