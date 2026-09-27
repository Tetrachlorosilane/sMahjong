package mahjong.train;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * 极简 **`.npz` 写出**（标签侧，训练专用）—— **零第三方依赖**：JDK 自带
 * {@link ZipOutputStream}，`.npy` 的头部是三十行的事。
 *
 * <p>为什么非要自己写：本工程的服务端是"`java -jar` 自包含、不加 Maven/ONNX/PyTorch"
 * （`AGENTS.md` §6.5），而标签要能被 Python 的
 * {@code np.load(...)} 直接读；npz 就是"一个 zip，成员是 `.npy`"，照抄比引依赖便宜。
 *
 * <h2>确定性（这条是判据的一部分）</h2>
 * <ul>
 *   <li>成员**按插入序**写，且全部用 {@link ZipEntry#STORED}（不压缩）——
 *       同一批数据两次写出**逐字节相同**；</li>
 *   <li>zip 条目的时间戳钉成固定值（不取当前时间）—— 否则"同种子产物逐字节相同"这条
 *       在 aux 上就永远不成立（`docs/TRAINER-CPP.md` §2 铁律 2 的同一条精神）；</li>
 *   <li>`.npy` 头一律 v1.0、字典键序固定、按 64 字节对齐补空格（numpy 的硬要求）。</li>
 * </ul>
 *
 * <p>⚠ 标签与输入是**两套文件**：推理路径只读 `g*.feat.bin`（`FEATURES-V4.md` §5.2 的硬闸门）。
 */
final class NpzWriter {

    /** `\x93NUMPY`。 */
    private static final byte[] MAGIC = {(byte) 0x93, 'N', 'U', 'M', 'P', 'Y'};
    /** zip 条目的固定时间戳（DOS 1980-01-01；`ZipEntry.setTime(0)` 写出来就是它）。 */
    private static final long FIXED_TIME = 0L;

    /** 成员名 → `.npy` 字节（`LinkedHashMap`：**写出顺序 = 插入顺序**）。 */
    private final Map<String, byte[]> members = new LinkedHashMap<>();

    /** uint8 数组（shape 可空 = 标量）。 */
    void putU8(String name, byte[] data, int... shape) {
        members.put(name, npy("<u1", shape.length == 0 ? new int[]{data.length} : shape, data));
    }

    /** int8 数组。 */
    void putI8(String name, byte[] data, int... shape) {
        members.put(name, npy("<i1", shape.length == 0 ? new int[]{data.length} : shape, data));
    }

    /** int32 数组（小端）。 */
    void putI32(String name, int[] data, int... shape) {
        final byte[] raw = new byte[data.length * 4];
        for (int i = 0; i < data.length; i++) {
            final int v = data[i];
            raw[i * 4] = (byte) (v & 0xFF);
            raw[i * 4 + 1] = (byte) ((v >> 8) & 0xFF);
            raw[i * 4 + 2] = (byte) ((v >> 16) & 0xFF);
            raw[i * 4 + 3] = (byte) ((v >> 24) & 0xFF);
        }
        members.put(name, npy("<i4", shape.length == 0 ? new int[]{data.length} : shape, raw));
    }

    /**
     * 元数据（UTF-8 JSON）存成 uint8 数组，Python 侧
     * `json.loads(npz["meta"].tobytes().decode())` 读 —— 避免 `.npy` 的字符串 dtype
     * （`<U…` 宽度随内容变，不如字节数组稳）。
     */
    void putJson(String name, String json) {
        final byte[] raw = json.getBytes(StandardCharsets.UTF_8);
        members.put(name, npy("<u1", new int[]{raw.length}, raw));
    }

    /** 打成 zip（STORED、固定时间戳）。 */
    byte[] toBytes() {
        final ByteArrayOutputStream out = new ByteArrayOutputStream(1 << 16);
        try (ZipOutputStream zip = new ZipOutputStream(out)) {
            for (Map.Entry<String, byte[]> e : members.entrySet()) {
                final byte[] body = e.getValue();
                final ZipEntry entry = new ZipEntry(e.getKey() + ".npy");
                entry.setMethod(ZipEntry.STORED);
                entry.setSize(body.length);
                entry.setCompressedSize(body.length);
                final CRC32 crc = new CRC32();
                crc.update(body);
                entry.setCrc(crc.getValue());
                entry.setTime(FIXED_TIME);
                zip.putNextEntry(entry);
                zip.write(body);
                zip.closeEntry();
            }
        } catch (IOException ex) {
            throw new IllegalStateException("npz 写出失败：" + ex, ex);
        }
        return out.toByteArray();
    }

    /** 一个 `.npy`（v1.0）：magic + 头长 + 头（64 字节对齐）+ 原始数据。 */
    static byte[] npy(String descr, int[] shape, byte[] raw) {
        final StringBuilder sb = new StringBuilder();
        sb.append("{'descr': '").append(descr).append("', 'fortran_order': False, 'shape': (");
        for (int i = 0; i < shape.length; i++) {
            if (i > 0) {
                sb.append(", ");
            }
            sb.append(shape[i]);
        }
        if (shape.length == 1) {
            sb.append(',');                    // numpy 的单元素 tuple 要尾逗号
        }
        sb.append("), }");
        final int prefix = MAGIC.length + 2 + 2;      // magic + major/minor + headerLen(uint16)
        int pad = 64 - ((prefix + sb.length() + 1) % 64);
        if (pad == 64) {
            pad = 0;
        }
        for (int i = 0; i < pad; i++) {
            sb.append(' ');
        }
        sb.append('\n');
        final byte[] header = sb.toString().getBytes(StandardCharsets.US_ASCII);
        final byte[] out = new byte[prefix + header.length + raw.length];
        System.arraycopy(MAGIC, 0, out, 0, MAGIC.length);
        out[6] = 1;                       // major
        out[7] = 0;                       // minor
        out[8] = (byte) (header.length & 0xFF);
        out[9] = (byte) ((header.length >> 8) & 0xFF);
        System.arraycopy(header, 0, out, prefix, header.length);
        System.arraycopy(raw, 0, out, prefix + header.length, raw.length);
        return out;
    }

    /** 成员名清单（自检用；不读内容）。 */
    List<String> memberNames() {
        return new ArrayList<>(members.keySet());
    }
}
