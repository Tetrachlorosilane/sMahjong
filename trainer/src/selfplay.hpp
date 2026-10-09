// 无网络自对弈 runner（CLI）—— 与 Java `mahjong.train.SelfPlay` + `Main --selfplay` 同口径。
//
// 三条硬性质（"结果可信"的前提，照抄 Java 的类注释）：
//   ① **同种子逐事件可复现**：每场种子由 `seedBase + 场号` **预先**算出（`seedFor`），
//      每个座位的策略按 `(座位, 种子)` 新建实例，桌面固定 `debugDeterministicSeed`；
//   ② **座位轮转**：`--rotate` 按场循环移位（只覆盖 4 个排列、**保持循环序**）；
//      `--rotate-perm` 按场枚举 **24 个全排列**（一次训练里 4! 种座次关系都平均到，两者同时给时 perm 优先）；
//   ③ **奖励事后回填**：见 `trace.hpp`。
#pragma once

namespace trainer {

/** CLI：`trainer selfplay <games> [--workers K] [--policy P] [--seed S] [--hands H] [--out DIR]
 *  [--rotate] [--rotate-perm] [--sample K] [--no-claims] [--preset NAME]`。 */
int selfplayCli(int argc, char **argv);

}  // namespace trainer
