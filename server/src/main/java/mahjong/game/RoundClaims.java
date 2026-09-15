package mahjong.game;

import java.util.Collection;
import java.util.Map;
import java.util.Set;

/**
 * 鸣牌仲裁的**收工判据**（纯函数，可单独单测）。
 *
 * <p>从 {@link Round} 抽出来的第三步。鸣牌询问要同时等多家应答，
 * 而"什么时候可以不等了"有这几条容易写错的规则：
 *
 * <ol>
 *   <li>被问的人**都答完了**（{@code asked} 空）——没什么可等的；</li>
 *   <li>**所有可能荣和的人都已应答**——荣和要收齐（多荣和），
 *       而荣和之下的人再等也没意义（否则会平白拖满整个时限）；</li>
 *   <li>**已经到手的最优鸣牌没人能压过**——这是「胡 &gt; 杠 = 碰 &gt; 吃」的另一半：
 *       高优先级一旦成立，**低优先级的等待就必须立刻结束**，
 *       不能因为「还有一家没点跳过/吃」而拖到时限；</li>
 *   <li>**超时**——必须兜底，否则一家挂机会卡死整桌。</li>
 * </ol>
 *
 * <p>⚠ 第 3 条里的「压过」必须与 {@link Round} 实际的仲裁顺序**逐字一致**
 * （同等级看座次：离打牌者近的赢），否则提前收工会**改变赢家**。
 * 这也是第 2 条里 {@code !ronCapable.isEmpty()} 的原因：谁都不能荣和时该条不成立，
 * 否则会立刻收工、把碰/吃的机会整片丢掉。
 */
public final class RoundClaims {

    private RoundClaims() {
    }

    /** 鸣牌等级；数值越大越优先，与 Round 的仲裁顺序一一对应。 */
    public static final int RANK_NONE = -1;
    public static final int RANK_CHI = 0;
    public static final int RANK_PON = 1;
    public static final int RANK_KAN = 2;
    public static final int RANK_RON = 3;

    /**
     * 动作类型 → 鸣牌等级。
     *
     * <p>⚠ 这个顺序就是「荣和 &gt; 杠 &gt; 碰 &gt; 吃」。改它等于改规则，
     * 必须同时改 {@link Round} 里的仲裁循环。
     */
    public static int rankOf(String type) {
        if ("ron".equals(type)) {
            return RANK_RON;
        }
        if ("kan".equals(type)) {
            return RANK_KAN;
        }
        if ("pon".equals(type)) {
            return RANK_PON;
        }
        if ("chi".equals(type)) {
            return RANK_CHI;
        }
        return RANK_NONE;      // pass / 认不出的类型
    }

    /**
     * 一条候选鸣牌：等级 + 座次距离（离打牌者几步，1..3）。
     *
     * <p>同等级时**距离小者优先**，所以比较必须同时看这两个字段。
     */
    public record Claim(int rank, int seatDist) {
    }

    /**
     * 一条「等级 rank、座次距离 seatDist」的候选能否压过当前最优 {@code best}。
     *
     * @param best 当前最优；{@code null} 表示还没有任何有效鸣牌，谁都能"压过"
     */
    public static boolean canBeat(Claim best, int rank, int seatDist) {
        if (rank <= RANK_NONE) {
            return false;                    // pass / 兑现不了的动作，永远压不过任何人
        }
        if (best == null) {
            return true;
        }
        if (rank != best.rank()) {
            return rank > best.rank();
        }
        return seatDist < best.seatDist();   // 同级：离打牌者近的赢
    }

    /**
     * 是否可以结束本轮鸣牌询问。
     *
     * @param asked         仍在等待应答的座位；空表示都答完了
     * @param ronCapable    本轮**有荣和选项**的座位（没选项的座位不该拖住别人）
     * @param answeredSeats 已收集到应答的座位
     * @param remainMs      距离全局时限还剩多少毫秒（{@code <= 0} 表示已超时）
     * @param best          已到手的**最优、且能兑现**的鸣牌；{@code null} 表示还没有
     * @param askedBestRank 仍在等待的座位 → 它**最高能做的**鸣牌等级
     * @param seatDist      座位 → 距打牌者的步数（1..3）
     */
    public static boolean shouldStop(Collection<Integer> asked,
                                     Collection<Integer> ronCapable,
                                     Set<Integer> answeredSeats,
                                     long remainMs,
                                     Claim best,
                                     Map<Integer, Integer> askedBestRank,
                                     Map<Integer, Integer> seatDist) {
        if (asked.isEmpty()) {
            return true;                       // 都答完了
        }
        if (remainMs <= 0) {
            return true;                       // 超时兜底
        }
        if (allRonAnswered(ronCapable, answeredSeats)) {
            return true;                       // 荣和都收齐了，剩下的等着也没用
        }
        // 还有人在等：只要其中**任何一个**能压过已到手的最优，就得继续等
        for (int s : asked) {
            if (ronCapable.contains(s)) {
                return false;                  // 还有人可能荣和 → 必须等他（多荣和要收齐）
            }
            final Integer rank = askedBestRank.get(s);
            if (rank == null) {
                // 没有该座位的优先级信息 → **保守地继续等**。
                // 真实调用点一定会填（eligible 的座位必有非 pass 选项）；
                // 缺信息时"继续等"只是慢一点，"收工"却可能丢掉他的碰/杠。
                return false;
            }
            if (canBeat(best, rank, seatDist.getOrDefault(s, Integer.MAX_VALUE))) {
                return false;
            }
        }
        return true;                           // 没人能翻盘 → 立刻收工，不再等下家
    }

    /**
     * 是否所有可能荣和的人都已应答。
     *
     * <p>没有荣和者时返回 {@code false}：此时不该收工，还得等碰/吃。
     */
    public static boolean allRonAnswered(Collection<Integer> ronCapable,
                                         Set<Integer> answeredSeats) {
        if (ronCapable.isEmpty()) {
            return false;
        }
        for (int s : ronCapable) {
            if (!answeredSeats.contains(s)) {
                return false;
            }
        }
        return true;
    }

    /**
     * 这个回包该不该认领。
     *
     * <p>三种情况必须丢弃，否则会把**上一轮的迟到回包**当成这一轮的应答，
     * 表现为"莫名其妙按了别家上一巡的选择出牌"：
     *
     * <ul>
     *   <li>该座位本轮没被问（{@code seatIsAsked == false}）；</li>
     *   <li>该座位当前没有挂起的询问（{@code pendingAskId == null}）；</li>
     *   <li>回包带的 {@code ask_id} 与当前挂起的对不上（过期回包）。</li>
     * </ul>
     *
     * <p>回包**没带** {@code ask_id} 时按座位认领（兼容老客户端）；
     * 这类回包**没法**被 ask_id 识别，所以询问被取消时必须在队列上把它们摘掉
     * （见 {@link Table#dropReplies}）。
     *
     * @param seatIsAsked  该座位是否在本轮被问过
     * @param pendingAskId 该座位当前挂起的询问号；无则 {@code null}
     * @param hasReplyId   回包是否带了 {@code ask_id}
     * @param replyAskId   回包带的询问号（{@code hasReplyId} 为 false 时忽略）
     */
    public static boolean acceptsReply(boolean seatIsAsked, Long pendingAskId,
                                       boolean hasReplyId, long replyAskId) {
        if (!seatIsAsked || pendingAskId == null) {
            return false;
        }
        if (!hasReplyId) {
            return true;
        }
        return replyAskId == pendingAskId;
    }
}
