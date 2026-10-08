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

/**
 * `Policies.byName(name)` 的等价物。
 *
 * @param err 未知 / 未实现的策略名写在这里（调用方据此报错退出，**不静默降级**）
 */
inline PolicyFactory policyFactoryByName(const std::string &name, std::string &err) {
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
