package mahjong.ai;

/**
 * v4 **增量事件缓存的状态**（L2 表示级；`docs/FEATURES-V4.md` §5.3、`v4/cache.py` 的
 * `EventStream` 在 Java 侧的对应物）。
 *
 * <p>一个**槽 = 一个小局 + 一个座位**（策略实例可能被多个座位共用 —— 采集时同一份
 * `net:<文件>` 常挂在两个学生席上，所以按座位分槽，避免互相把对方的窗口顶掉）。
 *
 * <p>槽里存三样（都是**逐行**的，值只依赖"那条事件 + 自己这个座位"，所以可以整体平移复用）：
 * <ul>
 *   <li>{@link Slot#tok}：窗口的 token 行（[K][C_EVT]，前部是零 padding）——
 *       **校验用**：与下一决策 `V4Features.eventMatrix` 的对应行逐元素比对（见 {@link #overlapOk}）；</li>
 *   <li>{@link Slot#enc}：事件编码器（`event.enc` 两层 MLP）的输出行；</li>
 *   <li>{@link Slot#qkv}：注意力 `in_proj`（对 `LayerNorm1(enc)` 的结果）——逐行可缓存，
 *       省下的是事件 Transformer 里最大的一块（60 行 × 576×192）。</li>
 * </ul>
 *
 * <p>⚠ **本类只管状态与校验，不算任何数**（编码要权重，那是 {@link V4Policy} 的事）——
 * 这样"缓存"与"前向"的职责不混，判据（{@code 增量 == 全量}）也容易在自检里单独钉。
 *
 * <p>⚠ 最危险的失败模式是"缓存陈旧但结果看起来正常"（设计文档 §5.3）：所以 {@link #overlapOk}
 * 做的是**逐行逐元素**比对（60×96 次比较，相对 38M 次乘加可忽略），任何不一致都退回全量重算。
 */
final class V4Cache {

    /** 一份缓存槽。字段刻意包内可见：只有 {@link V4Policy} 会填它。 */
    static final class Slot {
        String handKey;
        /** 已编码进缓存的**绝对**事件条数（= 上一次 obs 里 `events[]` 的长度）。 */
        int seen;
        /** 窗口里真实事件的行数（≤ K）。 */
        int len;
        float[][] tok;
        float[][] enc;
        float[][] qkv;
        /** 增量 GRU 隐状态（本小局内 carry；跨局清零 —— 设计文档 §5.3 纪律①）。 */
        float[] h;
        boolean valid;

        void clear() {
            handKey = null;
            seen = 0;
            len = 0;
            valid = false;
        }
    }

    private final int k;
    private final Slot[] slots = new Slot[4];

    // 计数器（自检/基准要看得见"到底命中没有"—— 静默全 miss 的缓存等于没写）
    long hits;
    long cold;          // 首次见到这个小局（或换局）⇒ 必须从零建
    long stale;         // 前缀对不上（换局/篡改/多桌交错）⇒ 丢掉重建
    long rowsEncoded;
    long rowsReused;

    V4Cache(int kEvt) {
        this.k = kEvt;
        for (int i = 0; i < slots.length; i++) {
            slots[i] = new Slot();
        }
    }

    Slot slot(int seat) {
        return slots[Math.floorMod(seat, slots.length)];
    }

    int window() {
        return k;
    }

    void clearAll() {
        for (Slot s : slots) {
            s.clear();
            s.tok = null;
            s.enc = null;
            s.qkv = null;
            s.h = null;
        }
    }

    void resetStats() {
        hits = 0;
        cold = 0;
        stale = 0;
        rowsEncoded = 0;
        rowsReused = 0;
    }

    String stats() {
        return "命中 " + hits + " / 建槽 " + cold + " / 陈旧退回 " + stale
                + "；编码行 " + rowsEncoded + "，复用行 " + rowsReused;
    }

    /**
     * **前缀校验**：把槽里旧窗口的行与新 obs 的窗口行在重叠区间上逐元素比对。
     *
     * <p>重叠区间（绝对事件下标）：`[max(E-K, seen-len), min(seen, E))`；新位置 `i-(E-K)`、
     * 旧位置 `i-(seen-len)`。任何一格不等 ⇒ 返回 false（调用方丢掉整个槽、从零重建）。
     *
     * @param fresh 本决策的窗口 token 行（`V4Features.eventMatrix` 的输出，长度 == K）
     */
    boolean overlapOk(Slot s, float[][] fresh, int events) {
        // ⚠ 窗口里绝对下标 i 的**行位置**恒为 `i - seen + K`（窗口贴着事件流尾巴：
        //   前部是 padding、新事件在尾部）—— 曾经写成 `i - (seen - len)`，只在窗口满时才对，
        //   于是每小局的前 K 条事件全部误判成"陈旧"、缓存形同虚设（实测 600 决策里退回 533 次）。
        int lo = Math.max(0, events - k);
        int hi = Math.min(s.seen, events);
        for (int i = lo; i < hi; i++) {
            float[] a = s.tok[i - s.seen + k];
            float[] b = fresh[i - events + k];
            if (a == null || b == null) {
                return false;
            }
            for (int c = 0; c < a.length; c++) {
                if (Float.floatToIntBits(a[c]) != Float.floatToIntBits(b[c])) {
                    return false;                                // 逐位比：NaN 也不会被放过
                }
            }
        }
        return true;
    }

    /**
     * 窗口整体左移 `delta` 行（引用级移动，不搬浮点），空出来的尾部由调用方填新行。
     *
     * <p>为什么恒是"左移 delta"：窗口**总是贴着事件流的尾巴**（新事件在尾部、前部是 padding），
     * 所以 `E` 变 `E+delta` 时，绝对下标 `i` 的行从 `i-(seen-len)` 移到 `i-(E-len')`，
     * 而两种窗口长度下这个位移都等于 `-delta`（不管窗口有没有满）。
     */
    static void shiftRows(float[][] rows, int delta) {
        if (delta <= 0) {
            return;
        }
        if (delta >= rows.length) {
            java.util.Arrays.fill(rows, null);
            return;
        }
        System.arraycopy(rows, delta, rows, 0, rows.length - delta);
        for (int i = rows.length - delta; i < rows.length; i++) {
            rows[i] = null;
        }
    }
}
