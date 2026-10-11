// 内置策略（训练接口的"标准件"）—— 与 Java `mahjong.ai.Policies` 的
// `pass` / `first` / `random` / `teacher` 四个**逐语义**一致。
//
// 复现性的两条硬口径（AGENTS §6.5）：
//   ① **每局每席一份实例**（`PolicyFactory.create(seat, gameSeed)`）—— 策略跨局带状态
//      （`random` 的随机源）会让同 seed 不再产出同一轨迹；
//   ② `random` 的随机源必须逐位等于 `new java.util.Random(gameSeed * 31 + seat)`
//      —— 所以走 `java_rand.hpp`，不用 `std::mt19937`。
//
// `teacher`（= Java `Policies.TEACHER` → `Bot.decide`）的**本体在 `bot.cpp`**：它要读 `Round`
//   （teacher 是训练侧唯一允许读 `Round` 的策略，AGENTS §6.5 的例外），而 `Round` 又包含本
//   头文件 —— 包含关系只能是 `bot.hpp → policies.hpp` 这一个方向。所以这里只**前置声明**
//   `makeTeacherPolicy()`（定义在 `bot.cpp`），避免循环包含。
//
// ⚠ `net:` 的 `@α` 先验要调 `Bot.decide`（现在是有的），但先验那一支仍未接（见下）。
#pragma once

#include <cstdio>
#include <cstdlib>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>

#include "action.hpp"
#include "java_rand.hpp"
#include "net.hpp"
#include "observation.hpp"
#include "v4policy.hpp"

namespace trainer {

class Round;

/** 一次**决策机会**的信息集部分（Java `Decision` 的 `obs` / `kind`；不含 `Round`）。 */
struct Decision {
    const Observation *obs = nullptr;
    std::string kind;                 // "turn" / "claim"

    // ---- 以下三项对应 Java `Decision` 的 `round` / `options` / `extra` ----
    // ⚠ 只有 **teacher** 允许读 `round`（Java `Decision.round` 的注释：外部策略尤其 ML 策略
    //   不许读它，那是绕过信息集作弊）。`options` 是服务端下发的**原始选项**
    //   （Java `Decision.options`），`first`/`pass`/`random`/`net` 都只用 `obs.legal`。
    /** 本局的 `Round`（**只读**；Java `Decision.round`）。 */
    const Round *round = nullptr;
    /** 本次询问的原始选项（Java `Decision.options`）。 */
    const std::vector<Option> *options = nullptr;
    /** 鸣牌询问的"被鸣那张牌 id"（= Java `extra.get("tile")` 解析回来的 kind 的来源）。 */
    int calledTileId = -1;
};

/** 策略回包（等价于 Java 的 `Map<String,Object> cmd`）。 */
struct Cmd {
    bool valid = false;
    Action action;
    /**
     * 这个回包**来自内置 Bot**（= Java `Policies.TEACHER` 那条路）。
     *
     * <p>为什么需要它：Java 的"回包必须在本次 `legal` 里"这条校验只活在
     * `Policies.fromAction`（`ActionPolicy` 的适配器）里 —— `TEACHER` 根本不经过它，
     * `Table.decideBot` 直接把它交给状态机（合法性由 `Round` 自己判：`discardAllowed` /
     * `pickAuto` / 杠校验）。所以训练端的漏斗必须**同样**对内置 Bot 的回包放行，
     * 否则会出现 Java 没有的 `fatal`（例：teacher 在"吃之后食替锁死全部可打牌"时回 `pass`，
     * 而本次询问还带着 `kan` 选项 → `legal` 非空却找不到 `pass`，见 docs/TRAINER-CPP.md §6.15）。
     */
    bool fromBot = false;
};

using Policy = std::function<Cmd(const Decision &)>;

/** `Policies.passFirst()`：能过就过，否则选第一个合法动作（"不鸣牌"基线）。 */
inline Policy makePassPolicy() {
    return [](const Decision &d) {
        Cmd c;
        c.valid = true;
        for (const Action &a : d.obs->legal) {
            if (a.type == kActPass) {
                c.action = a;
                return c;
            }
        }
        c.action = d.obs->legal.empty() ? actionOf(kActPass) : d.obs->legal[0];
        return c;
    };
}

/** `Policies.firstLegal()`：恒选第一个合法动作（最小的可跑基线）。 */
inline Policy makeFirstPolicy() {
    return [](const Decision &d) {
        Cmd c;
        c.valid = true;
        c.action = d.obs->legal.empty() ? actionOf(kActPass) : d.obs->legal[0];
        return c;
    };
}

/**
 * `Policies.random(seed)`：均匀随机（可复现）。
 *
 * <p>随机源在**构造时**给定：自对弈里每局新建一个实例，否则同一 seed 在并行 worker 下的
 * 取数顺序会变，可复现立刻失效。
 */
inline Policy makeRandomPolicy(int64_t seed) {
    auto rng = std::make_shared<JavaRandom>(seed);
    return [rng](const Decision &d) {
        Cmd c;
        c.valid = true;
        const int n = static_cast<int>(d.obs->legal.size());
        c.action = n == 0 ? actionOf(kActPass)
                          : d.obs->legal[static_cast<size_t>(rng->nextInt(n))];
        return c;
    };
}

/** 每局一份策略实例的工厂（Java `PolicyFactory`）。 */
using PolicyFactory = std::function<Policy(int seat, int64_t gameSeed)>;

/**
 * `teacher` / `bot` 策略：Java `Policies.TEACHER` = `Bot.decide(d.round, d.obs.seat, d.kind,
 * d.options, d.extra)`。
 *
 * <p>**定义在 `bot.cpp`**（那里才拿得到 `Round` 的完整定义）；本头文件只前置声明，见文件顶部。
 */
Policy makeTeacherPolicy();

/**
 * `net:<权重文件>` 的策略本体（= Java `NeuralPolicy.choose` / `chooseSampled`，**不含** `@α` 先验）。
 *
 * `temp <= 0` ⇒ argmax（并列取最小下标）；`temp > 0` ⇒ 从 `softmax(logits / T)` 采样。
 * `rng` 按值捕获 —— 调用方每局每席新建一个（`netMixSeed(gameSeed, seat)`），
 * 跨局共享会让"同种子可复现"失效（AGENTS §6.5）。
 *
 * ⚠ 失败（obs 解析不出 / 权重维度不符 / 键解析不出）时返回 `valid = false`：上层
 * `Table::decideBot` 会据此**报错退出**，绝不悄悄换一个动作（训练端没有 Bot 可退）。
 */
inline Policy makeNetPolicy(std::shared_ptr<const Net> net, float temp, JavaRandom rng) {
    return [net, temp, rng](const Decision &d) mutable {
        Cmd c;
        if (d.obs == nullptr) {
            return c;
        }
        // Java `chooseWithPrior`：`legal` 为空 → `Action.of(PASS)`
        // （那一条随后会被判"不在 legal 里" → 引擎按 `defaultDiscardId` 兜底，见 docs/TRAINER-CPP.md §6.15）
        std::vector<std::string> keys = d.obs->legalKeys();
        if (keys.empty()) {
            c.valid = true;
            c.action = actionOf(kActPass);
            return c;
        }
        // 与 Java 同一条拼装路径：`Observation.toJson()` → `Features.state/candidate`
        JVal obsJson;
        if (!jsonParse(d.obs->toJson(), obsJson) || !obsJson.isObj()) {
            return c;
        }
        std::vector<float> logits;
        std::string err;
        if (!netLogits(*net, obsJson, keys, logits, err)) {
            return c;
        }
        const int pick = temp > 0.f ? netSampleSoftmax(logits, temp, rng) : netArgmax(logits);
        bool ok = false;
        const Action a = pick < 0 ? Action{} : actionParse(keys[static_cast<size_t>(pick)], ok);
        if (pick < 0) {
            c.valid = true;
            c.action = actionOf(kActPass);
            return c;
        }
        if (!ok) {
            return c;
        }
        c.valid = true;
        c.action = a;
        return c;
    };
}

/**
 * `net:<权重文件>` 的**进程级权重注册表**：同一个路径在本进程里只加载一次。
 *
 * <p>为什么必须有它：`--policy "net:X,net:X,net:X,net:X"` 会给四个座位各要一个工厂 ——
 * 没有注册表就是**四份独立加载**（每个座位一份 `V4Policy`）。g08 是 5.4 MB，加上加载期建的
 * 转置副本是 10.9 MB ⇒ 一份策略串就吃掉 44 MB，**远超 L3（i9-14900HX = 36 MB）**，
 * 前向的内存带宽立刻崩掉。注册表把它压回**一份只读权重**，四个座位 `shared_ptr` 共享
 * （`V4Policy::forwardAll` 是 `const` 且不改状态，多线程同时前向本来就是安全的）。
 *
 * <p>生命周期：注册表**持有**这些 `shared_ptr`（进程级，不释放）—— 策略实例可能在任何
 * 时候被销毁，权重必须活得比它们久。加载发生在建线程池**之前**，但仍在锁内做，
 * 免得将来别处并发调用工厂时出现两份。
 */
inline std::shared_ptr<const V4Policy> sharedV4Policy(const std::string &path, std::string &err) {
    static std::mutex mu;
    static std::map<std::string, std::shared_ptr<const V4Policy>> cache;
    const std::lock_guard<std::mutex> lock(mu);
    const auto it = cache.find(path);
    if (it != cache.end()) {
        return it->second;
    }
    std::shared_ptr<V4Policy> loaded = std::make_shared<V4Policy>();
    std::string loadErr;
    if (!v4LoadPolicy(path, *loaded, loadErr)) {
        err = "加载 v4 神经网络权重失败：" + path + " —— " + loadErr;
        return nullptr;
    }
    const std::shared_ptr<const V4Policy> shared = loaded;
    cache.emplace(path, shared);
    return shared;
}

/** `net:<权重文件>` 且权重是 **v4（格式 2）** 时的策略本体（= Java `V4Policy.chooseIndex/SampleIndex`）。
 *
 * <p>与 v3 那条（`makeNetPolicy`）**同一套语义**：`temp <= 0` ⇒ argmax（并列取最小下标）、
 * `temp > 0` ⇒ 从 `softmax(logits / T)` 采样；失败返回 `valid = false`（不悄悄换动作）。
 * 权重按 `shared_ptr` 共享 —— `V4Policy::forwardAll` 是 `const` 且不改任何状态，
 * 所以多个自对弈 worker 线程同时前向是安全的（见 `v4policy.hpp` 的说明）。
 */
inline Policy makeV4NetPolicy(std::shared_ptr<const V4Policy> net, float temp, JavaRandom rng) {
    // **每策略实例一份**前向缓冲（`V4Scratch`）：一个实例只服务一个座位、一场、一个线程，
    // 所以拿它当热路径的 scratch 是安全的（权重是共享只读的，缓冲绝不能挂在那儿）。
    // 有了它，每决策的堆分配从 ~800 次降到 0（实测前向 17.34 ms → 1.70 ms/决策）。
    auto scr = std::make_shared<V4Scratch>();
    return [net, temp, rng, scr](const Decision &d) mutable {
        Cmd c;
        if (d.obs == nullptr) {
            return c;
        }
        // Java `chooseWithPrior`：`legal` 为空 → `Action.of(PASS)`（与 v3 同一条兜底）
        std::vector<std::string> keys = d.obs->legalKeys();
        if (keys.empty()) {
            c.valid = true;
            c.action = actionOf(kActPass);
            return c;
        }
        JVal obsJson;
        if (!jsonParse(d.obs->toJson(), obsJson) || !obsJson.isObj()) {
            return c;
        }
        std::vector<float> logits;
        std::string err;
        if (!v4Logits(*net, obsJson, logits, err, scr.get())) {
            return c;
        }
        const int pick = temp > 0.f ? netSampleSoftmax(logits, temp, rng) : netArgmax(logits);
        bool ok = false;
        const Action a = pick < 0 ? Action{} : actionParse(keys[static_cast<size_t>(pick)], ok);
        if (pick < 0) {
            c.valid = true;
            c.action = actionOf(kActPass);
            return c;
        }
        if (!ok) {
            return c;
        }
        c.valid = true;
        c.action = a;
        return c;
    };
}

/** ASCII 空白裁剪（Java `String.trim()` 的等价物，只处理 ≤ 0x20 的字符）。 */
inline std::string trimAscii(const std::string &s) {
    size_t b = 0;
    size_t e = s.size();
    while (b < e && static_cast<unsigned char>(s[b]) <= 0x20) {
        b++;
    }
    while (e > b && static_cast<unsigned char>(s[e - 1]) <= 0x20) {
        e--;
    }
    return s.substr(b, e - b);
}

/** 严格解析 float（整串都被吃掉才算数；Java `Float.parseFloat(trimmed)` 的口径）。 */
inline bool parseFloatStrict(const std::string &s, float &out) {
    if (s.empty()) {
        return false;
    }
    char *end = nullptr;
    const float v = std::strtof(s.c_str(), &end);
    if (end == s.c_str() || end == nullptr || *end != '\0') {
        return false;
    }
    out = v;
    return true;
}

// ---------------------------------------------------------------------------------------------
// 域掩码策略组合（P2-11「共享主干 + 域条件化分叉」的最小可执行件）
//
// 策略串：`net:<A>!net:<B>@<cond>`（A = 基座、B = 域内替换网、cond = **只读公开观测**的条件）
//   · cond 为真（**域内**）⇒ 用 B 在**同一次询问、同一个 `legal`** 上的 argmax；
//   · cond 为假（**域外**）⇒ **逐位**返回 A 的回包（连 `valid` / `fromBot` 一起原样返回）。
//
// 三条硬判据（写成代码就是下面几条分支；契约见 docs/PROTOCOL.md §8.4）：
//   ① **域外逐位等于 A** —— 这是"同到可辨状态再分歧"的定义，不是巧合；
//   ② 域内 B 的回包**必须在本次 legal 里**，不在就**整场报错退出**（⛔ 绝不静默退回 A：
//      静默退回会把"B 没接线"伪装成"两臂无分歧"）；
//   ③ 条件**只许读 `Observation`**（该席的合法观测）—— 别家手牌 / 牌山顺序 / 里宝指示牌
//      一律取不到 ⇒ 未知字段**报错**，不当假（AGENTS §6.5 反作弊口径）。
// ⚠ `!` 是结构分隔符，所以 cond 里**没有**逻辑非；要用非就用 `!=` 或德摩根。
// ---------------------------------------------------------------------------------------------

/** 域掩码条件：编译成 `Observation -> bool`（**构造期**就把未知字段 / 未知运算判死）。 */
struct DomainCond {
    std::function<bool(const Observation &)> pred;
    /** 原串（报错 / 日志用）。 */
    std::string text;
};

namespace conddetail {

using CondPred = std::function<bool(const Observation &)>;

/** 跳过 ASCII 空白（与 `trimAscii` 同一口径）。 */
inline void skipWs(const std::string &s, size_t &i) {
    while (i < s.size() && static_cast<unsigned char>(s[i]) <= 0x20) {
        i++;
    }
}

/** 是不是标识符首 / 后续字符。 */
inline bool isIdentStart(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
}
inline bool isIdentChar(char c) {
    return isIdentStart(c) || (c >= '0' && c <= '9');
}

/** 整型字段表（值 = 该字段在 `Observation` 上的读数；名字必须**逐字**与文档一致）。 */
inline bool intField(const std::string &n, const Observation &o, long long &v) {
    if (n == "draw") { v = o.playerDraws; return true; }
    if (n == "tiles_left") { v = o.tilesLeft; return true; }
    if (n == "dead_wall_left") { v = o.deadWallLeft; return true; }
    if (n == "total_discards") { v = o.totalDiscards; return true; }
    if (n == "kan_count") { v = o.kanCount; return true; }
    if (n == "kyoku") { v = o.kyoku; return true; }
    if (n == "honba") { v = o.honba; return true; }
    if (n == "riichi_sticks") { v = o.sticks; return true; }
    if (n == "seat") { v = o.seat; return true; }
    // ---- 点棒 / 顺位（P2-11「点棒/顺位」域；**全部公开共见** —— 四家持点本来就摆在自己面前）----
    //   ⚠ 取值口径必须与 Python 侧 `_p2-11c-cond.py` 的 `FIELDS` **逐字同义**：域判定由 C++ 给真值，
    //     但读数据侧（覆盖率 / D 行选取 / 逐行自证）走 Python 的等价求值器；两边不一致 = 静默判宽/判窄。
    //   ⚠ 命名定死：**不**登记 `my_score` / `my_rank` 别名（两语言同名是硬要求，别名一律报错）。
    const int s0 = (o.seat >= 0 && o.seat < 4) ? o.seat : 0;   // `isIntFieldName` 会拿空观测来探名
    const int self = o.scores[static_cast<size_t>(s0)];
    if (n == "score") { v = self; return true; }
    if (n == "rank") {
        long long r = 1;
        for (int s = 0; s < 4; s++) {
            if (s != o.seat && o.scores[static_cast<size_t>(s)] > self) {
                r++;
            }
        }
        v = r;
        return true;
    }
    if (n == "gap_above") {
        // 与**紧邻上一名**的差（自家第一 = 0 ⇒ 其余 ≤ 0）：`above` 取严格高于自家的最小持点。
        long long prev = 0;
        bool has = false;
        for (int s = 0; s < 4; s++) {
            const int x = o.scores[static_cast<size_t>(s)];
            if (s != o.seat && x > self && (!has || x < prev)) {
                prev = x;
                has = true;
            }
        }
        v = has ? static_cast<long long>(self) - prev : 0;
        return true;
    }
    if (n == "lead") {
        // 与**除自家外最高持点**的差（可正可负）。
        long long top = 0;
        bool has = false;
        for (int s = 0; s < 4; s++) {
            const int x = o.scores[static_cast<size_t>(s)];
            if (s != o.seat && (!has || x > top)) {
                top = x;
                has = true;
            }
        }
        v = has ? static_cast<long long>(self) - top : 0;
        return true;
    }
    return false;
}

/** 布尔字段表。⚠ `opp_riichi` **不含自家**（自家立直另有 `self_riichi`）。 */
inline bool boolField(const std::string &n, const Observation &o, bool &v) {
    if (n == "opp_riichi") {
        v = false;
        for (int s = 0; s < 4; s++) {
            if (s != o.seat && o.riichi[static_cast<size_t>(s)]) {
                v = true;
            }
        }
        return true;
    }
    if (n == "self_riichi") { v = o.selfRiichi; return true; }
    if (n == "menzen") { v = o.menzen; return true; }
    if (n == "furiten") { v = o.furiten; return true; }
    if (n == "any_call") { v = o.anyCall; return true; }
    if (n == "haitei") { v = o.haitei; return true; }
    if (n == "houtei") { v = o.houtei; return true; }
    if (n == "rinshan") { v = o.rinshan; return true; }
    if (n == "has_drawn") { v = o.hasDrawn; return true; }
    /** 自家是不是亲（庄）。公开共见；`Observation` 里本来就有 `dealer`。 */
    if (n == "is_oya") { v = (o.seat == o.dealer); return true; }
    return false;
}

inline bool isIntFieldName(const std::string &n) {
    Observation dummy;
    long long v = 0;
    return intField(n, dummy, v);
}
inline bool isBoolFieldName(const std::string &n) {
    Observation dummy;
    bool v = false;
    return boolField(n, dummy, v);
}

inline bool fail(std::string &err, const std::string &msg, const std::string &src) {
    err = "域掩码条件语法错误：" + msg + "（cond=\"" + src + "\"）";
    return false;
}

inline bool parseOr(const std::string &s, size_t &i, CondPred &out, std::string &err);
inline bool parseAnd(const std::string &s, size_t &i, CondPred &out, std::string &err);

/** 谓词：`<字段><运算><字面量>`，或裸布尔字段。 */
inline bool parsePred(const std::string &s, size_t &i, CondPred &out, std::string &err) {
    const size_t b = i;
    while (i < s.size() && isIdentChar(s[i])) {
        i++;
    }
    const std::string name = s.substr(b, i - b);
    if (name.empty()) {
        return fail(err, "这里要一个字段名（kind / draw / opp_riichi / …）", s);
    }
    skipWs(s, i);
    std::string op;
    if (i < s.size()) {
        const char c = s[i];
        if (c == '<' || c == '>' || c == '=' || c == '!') {
            op.push_back(c);
            i++;
            if (i < s.size() && s[i] == '=') {
                op.push_back('=');
                i++;
            } else if (c == '!') {
                // `!` 单独出现非法（`!` 是域掩码的结构分隔符）；`=` 单独出现合法（等同 `==`）
                return fail(err, "`!` 只能写成 `!=`（`!` 本身是域掩码的结构分隔符）", s);
            }
        }
    }
    const bool isInt = isIntFieldName(name);
    const bool isBool = isBoolFieldName(name);
    const bool isStr = (name == "kind");
    if (!isInt && !isBool && !isStr) {
        return fail(err, "未知字段 `" + name + "`（可读到的只有该席合法观测里的字段；"
                         "别家手牌 / 牌山 / 里宝一律取不到）", s);
    }
    if (op.empty()) {
        if (!isBool) {
            return fail(err, "字段 `" + name + "` 是" + (isInt ? "整数" : "字符串")
                             + "字段，必须带比较运算（如 `" + name + "<7` / `" + name + "=claim`）", s);
        }
    }
    skipWs(s, i);
    // ---- 字面量 ----
    if (isBool) {
        bool want = true;
        if (!op.empty()) {
            const size_t vb = i;
            while (i < s.size() && (isIdentChar(s[i]))) {
                i++;
            }
            const std::string lit = s.substr(vb, i - vb);
            if (lit == "true" || lit == "1") {
                want = true;
            } else if (lit == "false" || lit == "0") {
                want = false;
            } else {
                return fail(err, "布尔字段 `" + name + "` 的值只认 true/false/1/0，收到 `" + lit + "`", s);
            }
            if (op != "=" && op != "==" && op != "!=") {
                return fail(err, "布尔字段 `" + name + "` 只支持 = / == / !=，收到 `" + op + "`", s);
            }
        }
        if (op == "!=") {
            out = [name, want](const Observation &o) {
                bool v = false;
                boolField(name, o, v);
                return v != want;
            };
        } else {
            out = [name, want](const Observation &o) {
                bool v = false;
                boolField(name, o, v);
                return v == want;
            };
        }
        return true;
    }
    if (isStr) {
        const size_t vb = i;
        while (i < s.size() && isIdentChar(s[i])) {
            i++;
        }
        const std::string lit = s.substr(vb, i - vb);
        if (lit != "claim" && lit != "turn") {
            return fail(err, "字段 `kind` 的值只认 claim / turn，收到 `" + lit + "`", s);
        }
        if (op == "=" || op == "==") {
            out = [lit](const Observation &o) { return o.kind == lit; };
        } else if (op == "!=") {
            out = [lit](const Observation &o) { return o.kind != lit; };
        } else {
            return fail(err, "字符串字段 `kind` 不支持运算 `" + op + "`（只有 = / == / !=）", s);
        }
        return true;
    }
    // ---- 整数 ----
    const size_t nb = i;
    if (i < s.size() && s[i] == '-') {
        i++;
    }
    while (i < s.size() && s[i] >= '0' && s[i] <= '9') {
        i++;
    }
    const std::string lit = s.substr(nb, i - nb);
    if (lit.empty() || lit == "-") {
        return fail(err, "字段 `" + name + "` 的比较值要一个整数", s);
    }
    const long long want = std::strtoll(lit.c_str(), nullptr, 10);
    if (op == "=" || op == "==") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v == want;
        };
    } else if (op == "!=") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v != want;
        };
    } else if (op == "<") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v < want;
        };
    } else if (op == "<=") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v <= want;
        };
    } else if (op == ">") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v > want;
        };
    } else if (op == ">=") {
        out = [name, want](const Observation &o) {
            long long v = 0;
            intField(name, o, v);
            return v >= want;
        };
    } else {
        return fail(err, "整数字段 `" + name + "` 不支持运算 `" + op + "`", s);
    }
    return true;
}

/** `(` expr `)` 或一个谓词。 */
inline bool parseAtom(const std::string &s, size_t &i, CondPred &out, std::string &err) {
    skipWs(s, i);
    if (i < s.size() && s[i] == '(') {
        i++;
        if (!parseOr(s, i, out, err)) {
            return false;
        }
        skipWs(s, i);
        if (i >= s.size() || s[i] != ')') {
            return fail(err, "少一个 `)`", s);
        }
        i++;
        return true;
    }
    return parsePred(s, i, out, err);
}

/** `and`：`&` 比 `|` 结合得紧（左结合）。 */
inline bool parseAnd(const std::string &s, size_t &i, CondPred &out, std::string &err) {
    CondPred lhs;
    if (!parseAtom(s, i, lhs, err)) {
        return false;
    }
    while (true) {
        skipWs(s, i);
        if (i < s.size() && s[i] == '&') {
            i++;
            CondPred rhs;
            if (!parseAtom(s, i, rhs, err)) {
                return false;
            }
            CondPred acc = lhs;
            lhs = [acc, rhs](const Observation &o) { return acc(o) && rhs(o); };
            continue;
        }
        break;
    }
    out = lhs;
    return true;
}

inline bool parseOr(const std::string &s, size_t &i, CondPred &out, std::string &err) {
    CondPred lhs;
    if (!parseAnd(s, i, lhs, err)) {
        return false;
    }
    while (true) {
        skipWs(s, i);
        if (i < s.size() && s[i] == '|') {
            i++;
            CondPred rhs;
            if (!parseAnd(s, i, rhs, err)) {
                return false;
            }
            CondPred acc = lhs;
            lhs = [acc, rhs](const Observation &o) { return acc(o) || rhs(o); };
            continue;
        }
        break;
    }
    out = lhs;
    return true;
}

}  // namespace conddetail

/**
 * 解析 `cond`（文法：`or := and ('|' and)*` · `and := atom ('&' atom)*` ·
 * `atom := '(' or ')' | 谓词`）。**任何**认不出的东西都写 `err` 并返回 false ——
 * ⛔ 绝不把"取不到的字段"当假（那会把实现缺口伪装成"域是空的"）。
 */
inline bool condParse(const std::string &src, DomainCond &out, std::string &err) {
    const std::string s = trimAscii(src);
    if (s.empty()) {
        err = "域掩码条件为空（写法 net:<A>!net:<B>@<cond>）";
        return false;
    }
    size_t i = 0;
    conddetail::CondPred p;
    if (!conddetail::parseOr(s, i, p, err)) {
        return false;
    }
    conddetail::skipWs(s, i);
    if (i != s.size()) {
        return conddetail::fail(err, "多余的内容 `" + s.substr(i) + "`", s);
    }
    out.pred = p;
    out.text = s;
    return true;
}

/**
 * 域掩码合成：`base`（域外）/ `dom`（域内），`cond` 为真走 `dom`。
 *
 * <p>用 `Policy` 级组合（不是 `ActionPolicy`）：它只要 `Decision`，**拿不到 `Round`** ——
 * 与 `net:` 同一条反作弊口径（`teacher` 是唯一例外，见 `Decision` 的注释）。
 */
inline Policy makeDomainMaskPolicy(Policy base, Policy dom, DomainCond cond) {
    auto c = std::make_shared<DomainCond>(std::move(cond));
    return [base, dom, c](const Decision &d) -> Cmd {
        if (d.obs == nullptr) {
            return Cmd{};                      // 与 `net:` 同：拿不到观测 → 上层报错
        }
        if (!c->pred(*d.obs)) {
            return base(d);                    // 域外：**逐位**等于 A（连 valid / fromBot 一起）
        }
        Cmd cmd = dom(d);                      // 域内：B 的 argmax
        if (!cmd.valid || cmd.fromBot) {
            return cmd;                        // B 自己失败 / 内置 Bot 回包 → 原样上交（上层报错）
        }
        if (d.obs->legal.empty()) {
            // §6.15 的退化局面（自家回合每一种牌都被禁止）：`net:` 本身也回 PASS，引擎兜底。
            // 这里放行是**与漏斗同口径**，不是"退回 A"。
            return cmd;
        }
        if (d.obs->indexOfKey(cmd.action.key()) < 0) {
            // ⛔ 绝不静默退回 A：那会把"两网在看不同的 legal"伪装成"域内无分歧"。
            std::fprintf(stderr,
                         "[trainer] 域掩码策略：域内 B 的回包不在本次 legal 里 —— 键 %s，"
                         "legal=%zu 条，kind=%s，seat=%d，cond=\"%s\"（训练端不退回 A，直接报错）\n",
                         cmd.action.key().c_str(), d.obs->legal.size(), d.obs->kind.c_str(),
                         d.obs->seat, c->text.c_str());
            return Cmd{};
        }
        return cmd;
    };
}

/** `net:` 前缀（大小写不敏感）。 */
inline bool hasNetPrefix(const std::string &s) {
    return s.size() >= 4 && (s[0] == 'n' || s[0] == 'N') && (s[1] == 'e' || s[1] == 'E')
           && (s[2] == 't' || s[2] == 'T') && s[3] == ':';
}

/**
 * 把 `net:<A>!net:<B>[@α][#T]@<cond>` 切成三段。
 *
 * 触发条件**刻意收紧**：串必须 `net:` 开头、且含**紧接 `net:` 的 `!`**（`!net:`）——
 * 于是所有老串（`net:<路径>[@α][#T]` / `pass` / `first` / `random` / `teacher`）**永不**进这一支，
 * 逐位不变（自检钉着）。
 *
 * @return true = 是本支（`err` 非空 ⇒ 语法错，`err` 空 ⇒ 三段已填好）；false = 不是本支（`err` 不动）。
 */
inline bool domainMaskSplit(const std::string &name, std::string &specA, std::string &specB,
                            std::string &condText, std::string &err) {
    if (!hasNetPrefix(name)) {
        return false;
    }
    size_t bang = std::string::npos;                     // 第一个 `!net:`（`!` 的绝对下标）
    for (size_t k = 4; k + 5 <= name.size(); k++) {
        if (name[k] == '!' && hasNetPrefix(name.substr(k + 1, 4))) {
            bang = k;
            break;
        }
    }
    if (bang == std::string::npos) {
        return false;
    }
    const size_t at = name.rfind('@');
    if (at == std::string::npos || at <= bang + 4 || at + 1 >= name.size()) {
        err = "域掩码策略缺 `@<cond>`：写法 net:<A>!net:<B>@<cond>（例：net:A!net:B@kind=claim）—— "
              + name;
        return true;
    }
    condText = trimAscii(name.substr(at + 1));
    float probe = 0.f;
    if (parseFloatStrict(condText, probe)) {
        err = "域掩码策略的 `@` 后面那一段是个数（`" + condText + "`）—— 那看着像给 B 的 `@α`；"
              "域掩码必须给 `@<cond>`（例：net:A!net:B@kind=claim）—— " + name;
        return true;
    }
    specA = "net:" + name.substr(4, bang - 4);
    specB = "net:" + name.substr(bang + 5, at - (bang + 5));
    if (specA.size() <= 4 || specB.size() <= 4) {
        err = "域掩码策略的 A / B 有一侧没写权重路径：写法 net:<A>!net:<B>@<cond> —— " + name;
        return true;
    }
    return true;
}

/**
 * `Policies.byName(name)` 的等价物。
 *
 * @param err 未知 / 未实现的策略名写在这里（调用方据此报错退出，**不静默降级**）
 */
inline PolicyFactory policyFactoryByName(const std::string &name, std::string &err) {
    // ---- 域掩码（P2-11）：`net:<A>!net:<B>@<cond>` ----
    // ⚠ 只在串里出现 `!net:` 时触发 ⇒ 老串走下面的原路，**逐位不变**（自检钉着）。
    {
        std::string specA, specB, condText;
        if (domainMaskSplit(name, specA, specB, condText, err)) {
            if (!err.empty()) {
                return nullptr;
            }
            DomainCond cond;
            if (!condParse(condText, cond, err)) {
                return nullptr;
            }
            PolicyFactory fa = policyFactoryByName(specA, err);
            if (!fa) {
                return nullptr;
            }
            PolicyFactory fb = policyFactoryByName(specB, err);
            if (!fb) {
                return nullptr;
            }
            return [fa, fb, cond](int seat, int64_t gameSeed) {
                return makeDomainMaskPolicy(fa(seat, gameSeed), fb(seat, gameSeed), cond);
            };
        }
    }
    // `net:<权重文件>[@<α>][#<T>]` —— 与 Java `Policies.byName` 同一套解析：
    // 前缀大小写不敏感、**先剥 `#T` 再剥 `@α`**（顺序固定：Windows 路径里可能带 `#`），
    // 权重路径保持原样大小写；权重在**构造期**加载并校验魔数/版本/维度（坏了立刻报错）。
    if (name.size() >= 4 && (name[0] == 'n' || name[0] == 'N') && (name[1] == 'e' || name[1] == 'E')
            && (name[2] == 't' || name[2] == 'T') && name[3] == ':') {
        std::string rest = name.substr(4);
        float temp = 0.f;
        const size_t hash = rest.rfind('#');
        if (hash != std::string::npos && hash > 0 && hash < rest.size() - 1) {
            const std::string t = trimAscii(rest.substr(hash + 1));
            if (!parseFloatStrict(t, temp)) {
                err = "采样温度不是数：" + t + "（写法 net:<权重文件>[@<α>][#<T>]）";
                return nullptr;
            }
            rest = rest.substr(0, hash);
        }
        float alpha = 0.f;
        const size_t at = rest.rfind('@');
        if (at != std::string::npos && at > 0 && at < rest.size() - 1) {
            const std::string a = trimAscii(rest.substr(at + 1));
            if (!parseFloatStrict(a, alpha)) {
                err = "先验权重不是数：" + a + "（写法 net:<权重文件>@<α>）";
                return nullptr;
            }
            rest = rest.substr(0, at);
        }
        if (alpha > 0.f) {
            err = "策略 `net:<权重文件>@<α>`（P5b 的 teacher 先验）在训练端**尚未接线**：teacher 本体"
                  "（`Bot::decide`）已经移植（见 bot.hpp / bot.cpp），但先验那一支要按 Java "
                  "`Policies.hybrid` 把老师动作落位到本次 legal 再改 logit，本轮没做 —— 要跑混合臂"
                  "请先用 Java 生产者（**不静默降级**）。纯网络（`@0` 或缺省）与 `#<T>` 温度采样已支持。";
            return nullptr;
        }
        // ⚠ **按格式分派**（= Java `mahjong.ai.NetWeights.loadBytes`）：两个加载器共用魔数 `MJNN`，
        //   靠头部的 `format` 区分 —— 1 = v3 定长 MLP、2 = v4（三塔 + 多头）。拿错版本一律
        //   加载期报错，**不"尽力而为"**。格式 1 走的还是原来那条 `loadNet`（行为一位不变）。
        int format = 0;
        std::string peekErr;
        if (!v4WeightFormatOf(rest, format, peekErr)) {
            err = "加载神经网络权重失败：" + rest + " —— " + peekErr;
            return nullptr;
        }
        if (format == 2) {
            const std::shared_ptr<const V4Policy> sharedV4 = sharedV4Policy(rest, err);
            if (!sharedV4) {
                return nullptr;
            }
            return [sharedV4, temp](int seat, int64_t gameSeed) {
                return makeV4NetPolicy(sharedV4, temp, JavaRandom(netMixSeed(gameSeed, seat)));
            };
        }
        std::shared_ptr<Net> loaded = std::make_shared<Net>();
        std::string loadErr;
        if (!loadNet(rest, *loaded, loadErr)) {
            err = "加载神经网络权重失败：" + rest + " —— " + loadErr;
            return nullptr;
        }
        const std::shared_ptr<const Net> shared = loaded;
        return [shared, temp](int seat, int64_t gameSeed) {
            return makeNetPolicy(shared, temp, JavaRandom(netMixSeed(gameSeed, seat)));
        };
    }
    std::string n = name;
    for (char &c : n) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c - 'A' + 'a');       // Java 的 `switch (n.toLowerCase())`
        }
    }
    if (n == "pass") {
        return [](int, int64_t) { return makePassPolicy(); };
    }
    if (n == "first") {
        return [](int, int64_t) { return makeFirstPolicy(); };
    }
    if (n == "random") {
        return [](int seat, int64_t gameSeed) {
            // Java：`gameSeed * 31 + seat` 是 long 运算（溢出回绕有定义）→ C++ 用无符号域算
            const uint64_t z = static_cast<uint64_t>(gameSeed) * 31ULL
                    + static_cast<uint64_t>(static_cast<int64_t>(seat));
            return makeRandomPolicy(static_cast<int64_t>(z));
        };
    }
    if (n == "teacher" || n == "bot") {
        // Java `Policies.byName` 的 `case "teacher": case "bot": return teacher();`
        // —— `teacher()` 返回 `(seat, gameSeed) -> TEACHER`（**无状态**、与座位/种子无关；
        //    九种九牌那条随机源在 `Table.botRng()` 上，整场共享）。
        return [](int, int64_t) { return makeTeacherPolicy(); };
    }
    err = "未知策略名: " + name;
    return nullptr;
}

}  // namespace trainer
