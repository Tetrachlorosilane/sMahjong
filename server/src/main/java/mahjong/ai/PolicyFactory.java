package mahjong.ai;

/**
 * 每个座位、每局一份策略实例的工厂。
 *
 * <p>为什么不是直接给 {@link Policy} 数组：自对弈要**并行**跑，而并行会打乱"哪一局先跑"。
 * 只要策略实例跨局携带状态（随机数发生器、计数器、模型里的临时缓存），同一个种子就不再
 * 产出同一份轨迹 —— 而"同种子可复现"正是配对评测（同一副牌山比两个策略）的前提。
 * 所以约定：**实例只在单局内使用**，跨局的状态由 {@code gameSeed} 显式重建。
 */
public interface PolicyFactory {

    /**
     * @param seat     座位（0..3）
     * @param gameSeed 本局的种子，用来派生确定性的随机源
     */
    Policy create(int seat, long gameSeed);
}
