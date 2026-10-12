// **确定批前向**（opt-in；缺省关）—— 把"每决策一次同步前向"换成"按键成批、一次前向多行"。
//
// 背景与设计规格：`S:\mahjong-training\w-ladder\GPU-SELFPLAY-PLAN.md`（§2 排序键 / §5 接线点），
// 落地说明见 `docs/TRAINER-CPP.md` §6.26。三条硬口径（改这里之前先读它们）：
//
//   ① **键 = `(game, step, seat)`**，`step` = 该场**第几次询问**（0 基、跨小局连续）。
//      ⛔ 键必须在**询问时**就存在（`Table::decideBot` 发号，见 `table.hpp`）——
//      决策之后再发号的话，提交时根本没有键（那正是 `trace.cpp` 旧发号时机的毛病）。
//   ② **成批只由键决定**：一次"波"= **所有存活 worker 都阻塞在自己那一个询问上**（屏障），
//      此时未决集合 = 每个存活 worker 恰好一条；取其中**字典序最小的 min(K, |未决|) 个**。
//      ⛔ 绝不按到达顺序/线程完成时间凑批 —— 那会让"同种子 ⇒ 同批"当场失效。
//      ⚠ 因此本模块的计划**不是**离线 `v4-batch-serve.py` 的 `batch(b)=规范序[bK,(b+1)K)`：
//      离线规范序是**按场串行**的全序（先跑完整场 0 再跑场 1），在线管线里不可能复现
//      （那需要"低场号全部跑完才允许高场号进场"⇒ 批大小恒为 1）。两者**分开命名**：
//        离线 = **规范序计划**（`plan_batches`）；在线 = **屏障最小 K 键**（本文件）。
//      共同点（也是可比的判据）= **确定性与与到达顺序无关**。
//   ③ **尾批**（`|未决| < K`）三选一，缺省 `keep`（与 `tools/v4-batch-serve.py --tail` 同一缺省）：
//        `keep`  尾批照算并进批计划指纹；
//        `drop`  尾批**不进指纹**（记账里单独标 `dropped`）—— ⚠ 在线路径**仍然必须转发**它，
//                因为不转发 = 那条决策拿不到 logits = 死锁（离线工具里"丢"是安全的）；
//        `error` 出现短批即报错退出。
//
// 引擎（`--batch-engine`）：`rowloop` = 进程内逐行 `v4Logits`（**同内容的对照臂**，
// 用来把"并发/会合"的账与"前向本身"的账分开）；GPU/服务引擎见 §6.26 的"下一批"。
//
// ⚠ 与旧路径的关系：本文件的全部代码只在 `batchfw::active()` 为真时被执行；
//    关掉开关（缺省）时 `policies.hpp` 走原来那一行 `v4Logits`，逐位不变。
#pragma once

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "jsonscan.hpp"
#include "v4policy.hpp"

namespace trainer {
namespace batchfw {

// ---------------------------------------------------------------- 键

/** 确定批的排序键：`(game, step, seat)` 字典序（= 计划文档的规范键）。 */
struct Key {
    int game = -1;
    int step = -1;
    int seat = -1;
};

inline bool keyLess(const Key &a, const Key &b) {
    if (a.game != b.game) {
        return a.game < b.game;
    }
    if (a.step != b.step) {
        return a.step < b.step;
    }
    return a.seat < b.seat;
}
inline bool keyEq(const Key &a, const Key &b) {
    return a.game == b.game && a.step == b.step && a.seat == b.seat;
}
/** 自检里要 `vector<Key> == vector<Key>`（`std::equal_to` 走这个）。 */
inline bool operator==(const Key &a, const Key &b) { return keyEq(a, b); }
inline std::string keyText(const Key &k) {
    return std::to_string(k.game) + "/" + std::to_string(k.step) + "/" + std::to_string(k.seat);
}

enum class Tail { Keep, Drop, Error };
enum class Order {
    /** 规范：键字典序最小的 K 个（唯一合法值）。 */
    Canonical,
    /** ⛔ 故意红的负向开关：按**到达顺序**取前 K 个 —— 守卫必须把它拦下。 */
    Arrival,
};

inline const char *tailName(Tail t) {
    return t == Tail::Keep ? "keep" : (t == Tail::Drop ? "drop" : "error");
}
inline const char *orderName(Order o) { return o == Order::Canonical ? "canonical" : "arrival"; }

/** 线程局部：本线程当前场号（`-1` = 不在自对弈里）。批键的 `game` 从这里来。 */
inline thread_local int tlsGame = -1;

// ---------------------------------------------------------------- 纯函数（自检直接测它们）

/**
 * 从未决键集里取批 —— **纯函数**（同样的 `pending` + `k` + `order` ⇒ 同样的结果）。
 *
 * @param pending 未决键（顺序 = 到达顺序，**不许**当排序用）
 * @return 被取走的下标（升序）；`order == Canonical` 时**按键字典序最小的 `min(k,n)` 个**
 */
inline std::vector<size_t> takeBatch(const std::vector<Key> &pending, int k, Order order) {
    std::vector<size_t> idx(pending.size());
    for (size_t i = 0; i < idx.size(); i++) {
        idx[i] = i;
    }
    const size_t n = k <= 0 ? 0 : std::min(static_cast<size_t>(k), pending.size());
    if (order == Order::Canonical) {
        std::stable_sort(idx.begin(), idx.end(), [&](size_t a, size_t b) {
            return keyLess(pending[a], pending[b]);
        });
    }
    idx.resize(n);
    if (order == Order::Canonical) {
        std::sort(idx.begin(), idx.end());          // 返回"被取走的下标"，按原下标升序便于记账
    }
    return idx;
}

/**
 * 守卫（= python 侧 `assert_canonical` 的在线版）：批必须**① 键唯一 ② 键升序
 * ③ 恰好是未决集合里最小的 `count` 个**。任一条不满足 ⇒ 返回 false（调用方必须**硬失败**）。
 *
 * ⛔ 这条守卫是"按到达顺序凑批永远红"的落点：`Order::Arrival` 下它必然失败。
 */
inline bool assertCanonical(const std::vector<Key> &pending, const std::vector<Key> &taken,
                            std::string &why) {
    if (taken.empty()) {
        why = "空批";
        return false;
    }
    for (size_t i = 0; i < taken.size(); i++) {
        for (size_t j = i + 1; j < taken.size(); j++) {
            if (keyEq(taken[i], taken[j])) {
                why = "批内键重复：" + keyText(taken[i]);
                return false;
            }
        }
        if (i > 0 && !keyLess(taken[i - 1], taken[i])) {
            why = "批内键没有严格升序：" + keyText(taken[i - 1]) + " → " + keyText(taken[i]);
            return false;
        }
    }
    // 未决集合里比"批内最大键"更小的键必须**全部**在批内（否则就是把大键提前凑进来了）
    const Key &mx = taken.back();
    size_t lower = 0;
    for (const Key &p : pending) {
        if (keyLess(p, mx)) {
            lower++;
        }
    }
    // lower 含 mx 自己吗？不含（严格小于）⇒ 期望 = taken.size()-1 个小键 + 可能有并列
    if (lower + 1 < taken.size()) {
        why = "批不是未决集合里最小的 " + std::to_string(taken.size()) + " 个键（更小的有 "
              + std::to_string(lower) + " 个在批外）";
        return false;
    }
    for (const Key &p : pending) {
        if (keyLess(p, mx)) {
            bool in = false;
            for (const Key &t : taken) {
                if (keyEq(t, p)) {
                    in = true;
                    break;
                }
            }
            if (!in) {
                why = "未决键 " + keyText(p) + " 比批内最大键 " + keyText(mx) + " 更小却不在批里";
                return false;
            }
        }
    }
    return true;
}

// ---------------------------------------------------------------- 批计划（指纹 / 记账）

struct BatchRecord {
    int index = 0;
    int count = 0;
    bool tail = false;        // `count < K`
    bool included = true;     // `--batch-tail drop` 且是尾批 ⇒ false（不进指纹）
    std::vector<Key> keys;
};

struct Plan {
    std::vector<BatchRecord> batches;
    int rows = 0;             // 被前向的行数（= 全部决策行）
    int dropped = 0;          // `--tail drop` 下不计入指纹的行数
    std::string fingerprint;  // 两个独立 64 位 FNV-1a（128 位十六进制）
    std::string error;        // 空 = 计划成立
};

/** FNV-1a 64（两个不同 offset basis ⇒ 128 位指纹；纯函数、与机器无关）。 */
inline void fnv64(const std::string &s, uint64_t &h1, uint64_t &h2) {
    for (unsigned char c : s) {
        h1 = (h1 ^ c) * 0x100000001B3ULL;
        h2 = (h2 ^ c) * 0x100000001B3ULL;
    }
}
inline std::string planFingerprint(const std::vector<BatchRecord> &bs) {
    std::string text;
    for (const BatchRecord &b : bs) {
        if (!b.included) {
            continue;
        }
        text += "b" + std::to_string(b.index) + "|" + std::to_string(b.count) + "|";
        for (const Key &k : b.keys) {
            text += keyText(k) + ",";
        }
        text += "\n";
    }
    uint64_t h1 = 0xCBF29CE484222325ULL;
    uint64_t h2 = 0x9E3779B97F4A7C15ULL;
    fnv64(text, h1, h2);
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%016llx%016llx", static_cast<unsigned long long>(h1),
                  static_cast<unsigned long long>(h2));
    return std::string(buf);
}

// ---------------------------------------------------------------- 收集器（唯一的新共享点）

/**
 * 未决的一行。`submit` 的调用方（策略实例）阻塞等待它 `done`。
 *
 * ⚠ 生命周期：`shared_ptr` —— 批被取走后槽位仍由等待中的调用方持有。
 */
struct Slot {
    Key key;
    std::string obs;                    // `Observation.toJson()` 的文本（重放同一份输入）
    std::vector<std::string> legal;
    const V4Policy *net = nullptr;      // 权重（只读、全进程共享）
    std::vector<float> logits;
    bool done = false;
    bool failed = false;
    /** 本槽位被**本波**取走（唤醒后要把它从 `signaled_` 里销账，见 `Collector::submit`）。 */
    bool taken = false;
    std::string err;
    size_t arrival = 0;
};

/**
 * **屏障最小 K 键**收集器。
 *
 * <p>波的定义（这是确定性的全部来源）：`blocked_ == live_ && signaled_ == 0`
 * —— 每个存活场都恰好阻塞在一个询问上，且上一波被唤醒的那些**都已经回来**。
 * 这时未决集合 = 全部存活场各一条，取其中键最小的 `min(K, |未决|)` 个。
 *
 * <p>谁成批：最后一个到达屏障的那个 worker **就地**成批并前向（不另起线程）——
 * 屏障时刻所有 worker 都停着，就地做前向不损失任何并行度，也省掉一条线程与一个队列。
 */
class Collector {
public:
    Collector(int k, Order order, Tail tail) : k_(k), order_(order), tail_(tail) {}

    /** 当场数（建 worker 线程之前调一次）。 */
    void setLive(int n) {
        std::lock_guard<std::mutex> lk(mu_);
        live_ = n;
    }

    /** 一场跑完（场上 -1）。**必须**在 worker 退出前调。 */
    void retire() {
        std::unique_lock<std::mutex> lk(mu_);
        if (live_ > 0) {
            live_--;
        }
        if (live_ == 0) {
            allGone_ = true;
            cv_.notify_all();
            return;
        }
        maybeBuild(lk);
    }

    /** 提交一次询问并**阻塞**到本批回来（返回 false ⇒ 硬失败，调用方不许兜底换动作）。 */
    bool submit(const Key &key, std::string obsJson, std::vector<std::string> legal,
                const V4Policy *net, std::vector<float> &out, std::string &err) {
        auto slot = std::make_shared<Slot>();
        slot->key = key;
        slot->obs = std::move(obsJson);
        slot->legal = std::move(legal);
        slot->net = net;
        std::unique_lock<std::mutex> lk(mu_);
        if (!guardError_.empty()) {
            // 守卫已经红过一次：**所有后续行都当场失败**（⛔ 不许继续凑批，更不许等下去）
            slot->done = true;
            slot->failed = true;
            slot->err = guardError_;
            err = guardError_;
            return false;
        }
        slot->arrival = arrival_++;
        pending_.push_back(slot);
        blocked_++;
        maybeBuild(lk);
        cv_.wait(lk, [&] { return slot->done; });
        blocked_--;
        if (slot->taken) {
            // ⚠ 销账必须发生在**本 worker 真的从 submit 回来**的时刻，不能由成批者代劳：
            //   提前销账的话，下一个"屏障"可能在上一个被唤醒的 worker 还没回来时就成立，
            //   于是 `pending_` 少一条、批组成变成**时序依赖**（判据④当场失效）。
            signaled_--;
        }
        if (slot->failed) {
            err = slot->err;
            return false;
        }
        out = slot->logits;
        return true;
    }

    /** 所有 worker 已 join 之后取计划（指纹 / 记账）。 */
    Plan plan() const {
        std::lock_guard<std::mutex> lk(mu_);
        Plan p;
        p.batches = batches_;
        p.rows = rows_;
        p.dropped = dropped_;
        p.error = guardError_;
        p.fingerprint = planFingerprint(batches_);
        return p;
    }

    /** **只给自检用**：当前未决条数（让负向用例能"等到对方真的阻塞了"再提交，不靠 sleep）。 */
    size_t pendingForTest() const {
        std::lock_guard<std::mutex> lk(mu_);
        return pending_.size();
    }

    /** 前向引擎（`rowloop`；GPU 服务在下一批接）。返回 false ⇒ 本批硬失败。 */
    using Engine = bool (*)(const std::vector<std::shared_ptr<Slot>> &rows, std::string &err);

    void setEngine(Engine e) { engine_ = e; }

private:
    /** 屏障判定 → 成批 → 前向 → 唤醒。**必须持锁进入**。 */
    void maybeBuild(std::unique_lock<std::mutex> &lk) {
        if (guardError_.empty() && !pending_.empty() && blocked_ == live_ && signaled_ == 0
            && live_ > 0) {
            // ---- 快照（键序） ----
            std::vector<Key> pendingKeys;
            pendingKeys.reserve(pending_.size());
            for (const auto &s : pending_) {
                pendingKeys.push_back(s->key);
            }
            const std::vector<size_t> pick = takeBatch(pendingKeys, k_, order_);
            std::vector<Key> takenKeys;
            takenKeys.reserve(pick.size());
            for (size_t i : pick) {
                takenKeys.push_back(pendingKeys[i]);
            }
            // 守卫按**键序**看批（`takeBatch` 返回的是"被取走的下标"，顺序是原下标序）
            std::sort(takenKeys.begin(), takenKeys.end(), keyLess);
            // ---- 守卫（⛔ 到达顺序在这里死，且死在"前向/唤醒"之前） ----
            std::string why;
            if (!assertCanonical(pendingKeys, takenKeys, why)) {
                guardError_ = std::string("批组成守卫失败（") + orderName(order_) + "）：" + why;
                for (const auto &s : pending_) {
                    s->done = true;
                    s->failed = true;
                    s->err = guardError_;
                }
                pending_.clear();
                cv_.notify_all();
                return;
            }
            BatchRecord rec;
            rec.index = static_cast<int>(batches_.size());
            rec.count = static_cast<int>(pick.size());
            rec.tail = rec.count < k_;
            rec.included = !(rec.tail && tail_ == Tail::Drop);
            rec.keys = takenKeys;
            if (rec.tail && tail_ == Tail::Error) {
                guardError_ = "尾批 " + std::to_string(rec.count) + " 行 < K=" + std::to_string(k_)
                              + "（--batch-tail error）";
                for (const auto &s : pending_) {
                    s->done = true;
                    s->failed = true;
                    s->err = guardError_;
                }
                pending_.clear();
                cv_.notify_all();
                return;
            }
            // 被取走的槽位：从 pending_ 摘掉（顺序 = 键升序），其余留在 pending_ 里继续等
            std::vector<char> isTaken(pending_.size(), 0);
            for (size_t i : pick) {
                isTaken[i] = 1;
            }
            std::vector<std::shared_ptr<Slot>> taken;
            taken.reserve(pick.size());
            std::vector<std::shared_ptr<Slot>> rest;
            rest.reserve(pending_.size() - pick.size());
            for (size_t i = 0; i < pending_.size(); i++) {
                if (isTaken[i] != 0) {
                    taken.push_back(pending_[i]);
                } else {
                    rest.push_back(pending_[i]);
                }
            }
            // `pick` 是按原下标升序 ⇒ `taken` 不是键序，这里按键重排（前向顺序也进指纹语义）
            std::sort(taken.begin(), taken.end(),
                      [](const std::shared_ptr<Slot> &a, const std::shared_ptr<Slot> &b) {
                          return keyLess(a->key, b->key);
                      });
            pending_ = std::move(rest);
            batches_.push_back(rec);
            rows_ += rec.count;
            if (!rec.included) {
                dropped_ += rec.count;
            }
            signaled_ += static_cast<int>(taken.size());
            for (const auto &s : taken) {
                s->taken = true;
            }
            // ---- 前向在锁外做（屏障时刻没有别人能改状态） ----
            lk.unlock();
            std::string ferr;
            const bool ok = engine_ != nullptr ? engine_(taken, ferr) : false;
            lk.lock();
            if (!ok || engine_ == nullptr) {
                if (ferr.empty()) {
                    ferr = "批前向引擎未装配";
                }
                guardError_ = ferr;
                for (const auto &s : taken) {
                    s->done = true;
                    s->failed = true;
                    s->err = ferr;
                }
                for (const auto &s : pending_) {
                    s->done = true;
                    s->failed = true;
                    s->err = ferr;
                }
                pending_.clear();
            } else {
                for (const auto &s : taken) {
                    s->done = true;
                }
            }
            cv_.notify_all();
        }
    }

public:
    /** 唤醒所有等待者（worker 从 submit 返回之后再调 `retire`）。 */
    void releaseAll() {
        std::lock_guard<std::mutex> lk(mu_);
        allGone_ = true;
        for (const auto &s : pending_) {
            s->done = true;
            s->failed = true;
            s->err = "收集器已收工";
        }
        pending_.clear();
        cv_.notify_all();
    }

private:
    const int k_;
    const Order order_;
    const Tail tail_;
    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::vector<std::shared_ptr<Slot>> pending_;   // 未决（到达顺序；⛔ 不当作排序用）
    std::vector<BatchRecord> batches_;
    Engine engine_ = nullptr;
    int live_ = 0;
    int blocked_ = 0;
    int signaled_ = 0;
    size_t arrival_ = 0;
    int rows_ = 0;
    int dropped_ = 0;
    bool allGone_ = false;
    std::string guardError_;
};

// ---------------------------------------------------------------- 进程级单例

/** 当前收集器（`nullptr` = 开关关着 ⇒ 旧路径）。建线程之前装好，之后只读。 */
inline Collector *g_collector = nullptr;

inline bool active() { return g_collector != nullptr; }

/**
 * **一条 worker 线程（= 一条 lane）的作用域**：进场时 `setLive()` 已经把它登记过，
 * 退出（把分给自己的场全部跑完）时 `retire()` 销账。
 *
 * <p>⛔ 为什么登记/销账的单位是**lane**而不是**场**：一个 lane 会连着跑好几场
 * （`g, g+L, g+2L, …`）。若按"场"销账，lane 跑完第一场就把自己从 `live_` 里减掉 ——
 * ① 屏障会在 lane 还没开始下一场时成立（未决少一条 ⇒ 批组成变了）；
 * ② 所有 lane 都跑完第一场后 `live_` 归零 ⇒ 收集器"收工" ⇒ 后续提交**永久阻塞**。
 * 现场症状：`--batch-games 32 --games 180` 只跑出 **32 场**（每 lane 一场）就收工。
 *
 * <p>⚠ `tlsGame` 每场都要重设（`setGame`）：批键的 `game` 就是它。
 */
struct GameScope {
    GameScope() = default;
    /** 切到下一场（同一条 lane 内，**不**销账）。 */
    void setGame(int game) { tlsGame = game; }
    ~GameScope() {
        if (g_collector != nullptr) {
            g_collector->retire();
        }
    }
    GameScope(const GameScope &) = delete;
    GameScope &operator=(const GameScope &) = delete;
};

// ---------------------------------------------------------------- 引擎

/** `rowloop`：进程内逐行 `v4Logits`（**同内容的对照臂**，不是 GPU）。 */
inline bool engineRowLoop(const std::vector<std::shared_ptr<Slot>> &rows, std::string &err) {
    V4Scratch scr;
    // ⛔ **这条对照臂显式关掉增量事件 carry**（`policies.hpp` 的生产路径是默认开的）：
    //   批路径的行序**不是"同一小局连续"**（批 = 屏障时刻的规范序键），缓存只会一直"陈旧退回"
    //   （每行白付一次 23 KB 窗口拷贝），而 §6.26 的读数就是这条臂量出来的 —— 关掉才能
    //   "关着开关 = 旧路径逐位不变"那条判据继续成立（数值上开不开都逐位相同，见 §6.27 判据①）。
    scr.carryEnabled = false;
    for (const auto &s : rows) {
        if (s->net == nullptr) {
            err = "批前向（rowloop）：槽位没有权重（" + keyText(s->key) + "）";
            return false;
        }
        JVal obs;
        if (!jsonParse(s->obs, obs) || !obs.isObj()) {
            err = "批前向：obs 文本解析不出对象（" + keyText(s->key) + "）";
            return false;
        }
        if (!v4Logits(*s->net, obs, s->logits, err, &scr)) {
            err = "批前向（rowloop）失败于 " + keyText(s->key) + "：" + err;
            return false;
        }
    }
    return true;
}

/** 自检用：只记账、不动数值的引擎（每个候选填 `fill`）。 */
inline bool engineSelfTestFill(const std::vector<std::shared_ptr<Slot>> &rows, std::string &) {
    for (const auto &s : rows) {
        s->logits.assign(s->legal.size(), 1.0f);
    }
    return true;
}

/** 策略侧唯一入口：把一次询问交给批收集器（**键已由 `Table::decideBot` 发好**）。 */
inline bool forwardOne(const V4Policy &net, const Key &key, std::string obsJson,
                       std::vector<std::string> legal, std::vector<float> &out, std::string &err) {
    Collector *c = g_collector;
    if (c == nullptr) {
        err = "批前向开关没开";
        return false;
    }
    return c->submit(key, std::move(obsJson), std::move(legal), &net, out, err);
}

// ---------------------------------------------------------------- 自检

/**
 * 批前向的自检（挂在 `trainer --selftest`）：纯函数 + 一条**真的会合**的线程测试。
 * `check(名称, ok)` 由 main.cpp 传进来（签名与那里的 lambda 同形）。
 */
template <typename Check>
inline void selftest(Check check) {
    auto K = [](int g, int s, int seat) {
        Key k;
        k.game = g;
        k.step = s;
        k.seat = seat;
        return k;
    };

    // ① 键序：game → step → seat（字典序全序）
    {
        const std::vector<Key> ks = {K(1, 0, 0), K(0, 2, 0), K(0, 1, 3), K(0, 1, 0), K(0, 1, 0)};
        check("批键字典序 = (game, step, seat)",
              keyLess(K(0, 1, 0), K(0, 1, 3)) && keyLess(K(0, 1, 3), K(0, 2, 0))
                  && keyLess(K(0, 2, 0), K(1, 0, 0)) && !keyLess(K(0, 1, 0), K(0, 1, 0)));
    }
    // ② 规范取批 = 最小的 min(K, n) 个，且与**到达顺序无关**
    {
        std::vector<Key> a = {K(2, 0, 0), K(0, 5, 1), K(1, 0, 0), K(0, 0, 2), K(0, 7, 0)};
        std::vector<Key> b = {K(0, 7, 0), K(0, 0, 2), K(1, 0, 0), K(0, 5, 1), K(2, 0, 0)};
        const std::vector<size_t> pa = takeBatch(a, 3, Order::Canonical);
        const std::vector<size_t> pb = takeBatch(b, 3, Order::Canonical);
        std::vector<Key> ta, tb;
        for (size_t i : pa) {
            ta.push_back(a[i]);
        }
        for (size_t i : pb) {
            tb.push_back(b[i]);
        }
        std::sort(ta.begin(), ta.end(), keyLess);
        std::sort(tb.begin(), tb.end(), keyLess);
        check("规范取批 = 最小 K 个（且与到达顺序无关）", ta == tb && ta.size() == 3
                  && keyEq(ta[0], K(0, 0, 2)) && keyEq(ta[1], K(0, 5, 1)) && keyEq(ta[2], K(0, 7, 0)));
    }
    // ③ 守卫正向：规范批放行
    {
        const std::vector<Key> pending = {K(0, 0, 0), K(0, 1, 0), K(1, 0, 0)};
        const std::vector<Key> taken = {K(0, 0, 0), K(0, 1, 0)};
        std::string why;
        check("守卫：规范批放行", assertCanonical(pending, taken, why));
    }
    // ④ 守卫负向：乱序 / 跳号 / 重复 —— **必须红**
    {
        const std::vector<Key> pending = {K(0, 0, 0), K(0, 1, 0), K(1, 0, 0)};
        std::string w1, w2, w3;
        const bool unorderedBatch = assertCanonical(pending, {K(1, 0, 0), K(0, 0, 0)}, w1);
        const bool skipped = assertCanonical(pending, {K(0, 0, 0), K(1, 0, 0)}, w2);
        const bool dup = assertCanonical({K(0, 0, 0), K(0, 0, 0)}, {K(0, 0, 0), K(0, 0, 0)}, w3);
        check("守卫负向：① 批内乱序 ② 跳过更小的键 ③ 键重复 —— 三条都必须拦",
              !unorderedBatch && !skipped && !dup);
    }
    // ⑤ 负向：`Order::Arrival` 取批 ⇒ 守卫**必须**红（"按到达顺序凑批永远红"的落点）
    {
        const std::vector<Key> pending = {K(1, 0, 0), K(0, 9, 0), K(0, 1, 0), K(2, 0, 0)};
        const std::vector<size_t> pick = takeBatch(pending, 3, Order::Arrival);
        std::vector<Key> taken;
        for (size_t i : pick) {
            taken.push_back(pending[i]);
        }
        std::string why;
        const bool pass = assertCanonical(pending, taken, why);
        check(("负向：到达顺序取批被守卫拦下（" + why + "）").c_str(), !pass);
    }
    // ⑥ 尾批三选一
    {
        std::vector<Key> lo;
        for (int i = 0; i < 5; i++) {
            lo.push_back(K(0, i, 0));
        }
        const std::vector<size_t> p = takeBatch(lo, 4, Order::Canonical);
        check("尾批：|未决| < K 时取全部（keep 口径，短批照样前向）", p.size() == 4);
        BatchRecord r1, r2, r3;
        r1.tail = r2.tail = r3.tail = true;
        r1.index = r2.index = r3.index = 0;
        r1.count = r2.count = r3.count = 2;
        r1.keys = r2.keys = r3.keys = {K(0, 0, 0), K(0, 1, 0)};
        r1.included = true;
        r2.included = false;                      // drop
        r3.included = true;
        check("尾批 keep/drop/error：drop 不进指纹、keep 进指纹、两者指纹不同",
              planFingerprint({r1}) == planFingerprint({r3})
                  && planFingerprint({r1}) != planFingerprint({r2}));
    }
    // ⑦ 确定性：同一组批 ⇒ 同一指纹（两次独立算）
    {
        BatchRecord r;
        r.index = 0;
        r.count = 2;
        r.keys = {K(0, 0, 0), K(0, 0, 1)};
        check("批计划指纹：同输入两次相同（128 位十六进制）",
              planFingerprint({r}) == planFingerprint({r}) && planFingerprint({r}).size() == 32);
    }
    // ⑧ **真的会合**：4 个 worker（各 2 步）、K=2 ⇒ 4 波 × 2 行；批 = 屏障时刻最小的 2 个键
    //    （低场号会被连续选中 ⇒ 这是**规则本身**的语义，不是调度巧合）
    {
        Collector col(2, Order::Canonical, Tail::Keep);
        col.setEngine(&engineSelfTestFill);
        col.setLive(4);
        std::mutex mu;
        std::vector<std::thread> ts;
        for (int g = 0; g < 4; g++) {
            ts.emplace_back([&, g] {
                for (int step = 0; step < 2; step++) {
                    std::vector<float> out;
                    std::string err;
                    if (!col.submit(K(g, step, 0), "{}", {"a", "b"}, nullptr, out, err)) {
                        std::lock_guard<std::mutex> lk2(mu);
                        std::fprintf(stderr, "[batchfw-selftest] 会合用例失败：%s\n", err.c_str());
                        break;
                    }
                }
                col.retire();
            });
        }
        for (std::thread &t : ts) {
            t.join();
        }
        const Plan p = col.plan();
        bool ok = p.error.empty() && p.batches.size() == 4 && p.rows == 8;
        for (const BatchRecord &b : p.batches) {
            ok = ok && b.count == 2 && b.keys.size() == 2 && keyLess(b.keys[0], b.keys[1]);
        }
        if (ok) {
            ok = keyEq(p.batches[0].keys[0], K(0, 0, 0)) && keyEq(p.batches[0].keys[1], K(1, 0, 0))
                 && keyEq(p.batches[1].keys[0], K(0, 1, 0)) && keyEq(p.batches[1].keys[1], K(1, 1, 0))
                 && keyEq(p.batches[2].keys[0], K(2, 0, 0)) && keyEq(p.batches[2].keys[1], K(3, 0, 0))
                 && keyEq(p.batches[3].keys[0], K(2, 1, 0)) && keyEq(p.batches[3].keys[1], K(3, 1, 0));
        }
        check("会合：4 worker × 2 步 / K=2 ⇒ 4 波 × 2 行，批 = 屏障时刻最小的 2 个键（键序落定）",
              ok);
    }
    // ⑨ 负向（**确定、不靠 sleep**）：到达顺序凑批 ⇒ 守卫必红，且所有行硬失败
    {
        Collector col(1, Order::Arrival, Tail::Keep);
        col.setEngine(&engineSelfTestFill);
        col.setLive(2);
        std::atomic<int> failures{0};
        bool aDone = false;
        // worker A：先到，键**更大**（1/0/0）
        std::thread a([&] {
            std::vector<float> out;
            std::string err;
            if (!col.submit(K(1, 0, 0), "{}", {"x"}, nullptr, out, err)) {
                failures.fetch_add(1);
            }
            col.retire();
            aDone = true;
        });
        // 主线程 = worker B：等 A **真的阻塞住**（未决 = 1）再提交更小的键（0/0/0）
        while (col.pendingForTest() != 1) {
            std::this_thread::yield();
        }
        std::vector<float> out;
        std::string err;
        if (!col.submit(K(0, 0, 0), "{}", {"x"}, nullptr, out, err)) {
            failures.fetch_add(1);
        }
        col.retire();
        a.join();
        const Plan p = col.plan();
        check(("负向：到达顺序凑批 ⇒ 守卫红（" + std::to_string(failures.load())
               + " 行硬失败；" + p.error + "）").c_str(),
              aDone && failures.load() == 2 && p.error.find("守卫失败") != std::string::npos
                  && p.batches.empty());
    }
}

}  // namespace batchfw
}  // namespace trainer
