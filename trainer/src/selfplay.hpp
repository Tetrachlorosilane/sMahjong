// 无网络自对弈 runner（CLI）—— 与 Java `mahjong.train.SelfPlay` + `Main --selfplay` 同口径。
//
// 三条硬性质（"结果可信"的前提，照抄 Java 的类注释）：
//   ① **同种子逐事件可复现**：每场种子由 `seedBase + 场号` **预先**算出（`seedFor`），
//      每个座位的策略按 `(座位, 种子)` 新建实例，桌面固定 `debugDeterministicSeed`；
//   ② **座位轮转**（`--rotate`）：同一批牌山下让每个策略把四个座位都坐一遍；
//   ③ **奖励事后回填**：见 `trace.hpp`。
#pragma once

namespace trainer {

/** CLI：`trainer selfplay <games> [--workers K] [--policy P] [--seed S] [--hands H] [--out DIR]
 *  [--rotate] [--sample K] [--no-claims] [--preset NAME]`。 */
int selfplayCli(int argc, char **argv);

}  // namespace trainer
