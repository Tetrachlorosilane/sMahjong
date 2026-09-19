package mahjong.ai;

import java.util.Map;

/**
 * 牌桌策略：**服务端唯一的决策接缝**。
 *
 * <p>状态机里只有两处会问"这一手怎么走"（{@code Round.ask} 的自家回合、
 * {@code Round.claimPhase} 的鸣牌段），两处都汇到 {@code Table.decideBot(Decision)}。
 * 生产默认实现是内置牌效机器人 {@code Bot}（{@link Policies#TEACHER}）；
 * 自对弈 / 训练可以按座位注入别的实现（{@code Table.policy[seat]}）。
 *
 * <p>实现约定：
 * <ul>
 *   <li>返回值形状必须与客户端报文一致（{@link Action#toCmd()}）；</li>
 *   <li>抛异常不会炸牌桌线程：{@code Table.decideBot} 会兜底回内置机器人
 *       （一局里异常冒到线程上会让整场半庄静默死亡，见 AGENTS §6.3）；</li>
 *   <li>返回非法动作不会立即报错，但会退化成"默认摸切/过"（状态机各处的兜底），
 *       所以**优先实现 {@link ActionPolicy}**：它由 {@link Policies#fromAction} 校验，
 *       非法动作会被挡在状态机之外。</li>
 * </ul>
 */
public interface Policy {

    Map<String, Object> decide(Decision d);
}
