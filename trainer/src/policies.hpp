// 内置策略（训练接口的"标准件"）—— 与 Java `mahjong.ai.Policies` 的
// `pass` / `first` / `random` 三个**逐语义**一致。
//
// 复现性的两条硬口径（AGENTS §6.5）：
//   ① **每局每席一份实例**（`PolicyFactory.create(seat, gameSeed)`）—— 策略跨局带状态
//      （`random` 的随机源）会让同 seed 不再产出同一轨迹；
//   ② `random` 的随机源必须逐位等于 `new java.util.Random(gameSeed * 31 + seat)`
//      —— 所以走 `java_rand.hpp`，不用 `std::mt19937`。
//
// ⚠ **teacher / net: 这一轮没实现**：本层只提供"不需要 Bot 兜底"的三种策略。
//   Java 的 `Policies.fromAction` 在"回包非法 / 返回 null / 抛异常"时会退回**内置机器人**；
//   这三种策略只在 `legal` 为空时可能触发那条路（`Action.of(PASS)` 不在空 legal 里），
//   而 `legal` 在实局里恒非空（自家回合至少有打牌、鸣牌段至少有 pass）——
//   所以训练端**不需要** Bot，但一旦真触发就报错，绝不悄悄换一个动作（那等于伪造标签）。
#pragma once

#include <functional>
#include <memory>
#include <string>

#include "action.hpp"
#include "java_rand.hpp"
#include "observation.hpp"

namespace trainer {

/** 一次**决策机会**的信息集部分（Java `Decision` 的 `obs` / `kind`；不含 `Round`）。 */
struct Decision {
    const Observation *obs = nullptr;
    std::string kind;                 // "turn" / "claim"
};

/** 策略回包（等价于 Java 的 `Map<String,Object> cmd`）。 */
struct Cmd {
    bool valid = false;
    Action action;
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
 * `Policies.byName(name)` 的等价物。
 *
 * @param err 未知 / 未实现的策略名写在这里（调用方据此报错退出，**不静默降级**）
 */
inline PolicyFactory policyFactoryByName(const std::string &name, std::string &err) {
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
        err = "策略 `teacher`（内置牌效机器人 Bot.decide）在训练端**这一轮还没实现**"
              "（见 docs/TRAINER-CPP.md §5 的 M3）；本轮只支持 pass / first / random";
        return nullptr;
    }
    if (n.rfind("net:", 0) == 0) {
        err = "策略 `net:`（神经网络前向）在训练端**这一轮还没实现**（M3）；"
              "本轮只支持 pass / first / random";
        return nullptr;
    }
    err = "未知策略名: " + name;
    return nullptr;
}

}  // namespace trainer
