package mahjong.ai;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * 权重文件的**分派口**：同一个魔数 `MJNN`，按 `format` 字段选加载器。
 *
 * <p>为什么要分派而不是"两个策略串"：机器人 AI 包（`bot-ai/<名字>/bot.json`）里写的是
 * `model: net.bin`，包不关心代数 —— 服务端要能**同时**吃 v3（格式 1，定长 MLP）与
 * v4（格式 2，三塔 + 多头）的权重，且拿错版本时**构造期报错**（不是对局里算出奇怪动作）。
 *
 * <p>判据（`SelfTest.v4ForwardTests` / `botAiTests`）：
 * ① 格式 1 → {@link NeuralPolicy}、格式 2 → {@link V4Policy}；
 * ② 头部被改坏（魔数/版本/特征版本/维度）时抛出**说清原因**的异常（BOT-AI.md §130 的错包口径）。
 */
public final class NetWeights {

    /** 与两个加载器共用的魔数（`NeuralPolicy.MAGIC` / `V4Policy.MAGIC`）。 */
    public static final int MAGIC = 0x4D4A4E4E;
    /** v3 的格式号（定长 MLP）。 */
    public static final int FORMAT_V3 = 1;
    /** v4 的格式号（带块清单）。 */
    public static final int FORMAT_V4 = 2;

    private NetWeights() {
    }

    /** 读文件 → 对应代数的策略（构造期完成全部校验）。 */
    public static LogitPolicy load(Path path) throws IOException {
        return loadBytes(Files.readAllBytes(path), path.toString());
    }

    /** 从内存字节分派（`--bot-ai-reg` / 自检夹具走这条）。 */
    public static LogitPolicy loadBytes(byte[] raw, String what) throws IOException {
        if (raw.length < 8) {
            throw new IOException("权重文件太短：" + what + "（" + raw.length + " B）");
        }
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        int magic = bb.getInt();
        int ver = bb.getInt();
        if (magic != MAGIC) {
            throw new IOException(String.format("权重文件魔数不对：%#x（期望 %#x）", magic, MAGIC));
        }
        if (ver == FORMAT_V3) {
            return NeuralPolicy.loadBytes(raw, what);
        }
        if (ver == FORMAT_V4) {
            return V4Policy.loadBytes(raw, what);
        }
        throw new IOException("权重格式版本 " + ver + " 不认识（v3 = 1，v4 = 2）—— 重新导出权重");
    }

    /** 只读头部（日志/清单用，不做完整加载）。 */
    public static String peek(byte[] raw) {
        if (raw.length < 8) {
            return "太短（" + raw.length + " B）";
        }
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        int magic = bb.getInt();
        int ver = bb.getInt();
        if (magic != MAGIC) {
            return String.format("魔数 %#x（期望 %#x）", magic, MAGIC);
        }
        return switch (ver) {
            case FORMAT_V3 -> "第三代定长 MLP（格式 1）";
            case FORMAT_V4 -> "第四代三塔 + 多头（格式 2）";
            default -> "未知格式 " + ver;
        };
    }
}
