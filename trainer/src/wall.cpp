#include "wall.hpp"

#include <numeric>

#include "java_rand.hpp"
#include "tiles.hpp"

namespace trainer {

// 与 Java `Wall(long seed, Rules rules)` 逐行对应：
//   · Collections.shuffle(all, new Random(seed))  —— RandomAccess 分支
//   · stripRedFives(tiles, rules)                —— aka == 0 时把赤五写成普通五（同 kind copy 1）
//   · System.arraycopy(tiles, 122, dead, 0, 14)
Wall::Wall(int64_t seed, int aka) {
    tiles_.resize(kTileCount);
    std::iota(tiles_.begin(), tiles_.end(), 0);

    JavaRandom rnd(seed);
    javaShuffle(tiles_, rnd);

    if (aka <= 0) {
        for (int& id : tiles_) {
            if (isRedId(id)) {
                id = idOf(kindOf(id), 1);
            }
        }
    }
    for (int i = 0; i < kDeadTiles; i++) {
        dead_[static_cast<size_t>(i)] = tiles_[static_cast<size_t>(kLiveTiles + i)];
    }
}

}  // namespace trainer
