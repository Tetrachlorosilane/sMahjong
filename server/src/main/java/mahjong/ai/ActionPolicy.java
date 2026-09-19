package mahjong.ai;

/**
 * **干净**的策略接口：只能看见合法信息集（{@link Decision#obs}），拿不到 {@code Round}。
 *
 * <p>训练侧一律实现这个接口（而不是 {@link Policy}）：
 * <ul>
 *   <li>结构上不可能读别家手牌 / 牌山 / 里宝 —— 想作弊都没有入口；</li>
 *   <li>返回值由 {@link Policies#fromAction} 校验在本次 {@link Observation#legal} 之内，
 *       非法动作不会漏进状态机；</li>
 *   <li>抛异常会被兜底成内置机器人，不会打断对局。</li>
 * </ul>
 */
public interface ActionPolicy {

    Action choose(Decision d);
}
