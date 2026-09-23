package mahjong.ai;

import java.util.List;
import java.util.Map;

import mahjong.core.Tiles;
import mahjong.util.Json;

/**
 * 观测 → 网络输入：**基础段**的拼装（与 `python/mahjong_ml/features.py` 逐段同规格）。
 *
 * <p>输入统一是**观测的 JSON 形态**（`Observation.toJson()` / 轨迹里的 `obs` / golden 夹具）——
 * 三条路径共用同一份拼装代码，进程内推理只是多一次 `toJson()`（几十微秒，可忽略）。
 * 这是 B 形态（进程内推理）与离线训练之间的**唯一接缝**，所以分段顺序、归一化系数、
 * one-hot 槽位映射全集中在这里；Python 侧由 `features.describe()` 打印同一张表。
 * 一致性由 `SelfTest.neuralForwardTests` 用 Python 导出的 golden 夹具**逐元素**钉住（容差 1e-4）。
 *
 * <h2>布局（state 607 = 539 基础 + 68 派生；cand 96 = 88 基础 + 8 派生）</h2>
 * <pre>
 *   state: hand/34(×0.25) · hand_red/34 · meld_kind/4×5 · meld_tiles/4×34(×0.25)
 *          · river/4×34(×0.25) · dora/34(×0.25) · riichi/4 · ippatsu/4
 *          · scores/4(相对 25000，/1000) · round/5 · state/5 · self/4 · ctx/4
 *          · visible/34(×0.25) · drawn/37 · called_tile/37 · from/4 · win_note/3
 *          · danger 68（{@link ObsFeatures#perDecision}，/100）
 *   cand:  type/9 · tile/37 · tiles/37 · tsumogiri/1 · kan_kind/3 · noarg/1
 *          · derived 8（{@link ObsFeatures#perCandidate}，除以 {@link #DERIVED_CAND_SCALE}）
 * </pre>
 */
public final class Features {

    /** 特征版本（与 `features.FEATURE_VERSION` 同号；变了要两边一起改 + 重建数据集）。 */
    public static final int VERSION = 2;

    public static final int BASE_STATE = 539;
    public static final int BASE_CAND = 88;
    public static final int STATE = BASE_STATE + ObsFeatures.PER_DECISION;      // 607
    public static final int CAND = BASE_CAND + ObsFeatures.PER_CANDIDATE;       // 96

    public static final float DERIVED_DANGER_SCALE = 100f;
    /** 与 Python `DERIVED_SCALE_CAND` **逐位一致**。 */
    public static final float[] DERIVED_CAND_SCALE = {8f, 34f, 136f, 34f, 136f, 34f, 136f, 4f};

    static final String[] ACTION_TYPES = {"discard", "riichi", "pon", "chi", "kan",
                                          "tsumo", "ron", "pass", "kyuushu"};
    static final String[] KAN_KINDS = {"ankan", "kakan", "daiminkan"};
    static final String[] MELD_KINDS = {"chi", "pon", "ankan", "kakan", "daiminkan"};
    static final String[] WIN_NOTES = {"furiten", "no_yaku"};
    static final int TILE_SLOTS = 37;
    static final int KIND_COUNT = Tiles.KIND_COUNT;
    /** 场风字母表（`Observation.toJson()` 与轨迹里都是字母）。 */
    static final String WINDS = "ESWN";

    private Features() {
    }

    // ------------------------------------------------------------------ 状态（607）

    /** `obs`（JSON 形态）→ 607 维状态向量；`v` 是同一 obs 的派生特征视图。 */
    public static float[] state(Map<String, Object> obs, ObsFeatures.View v) {
        float[] f = new float[STATE];
        int i = 0;
        final int seat = Json.i(obs, "seat", 0);
        int[] hand = ObsFeatures.ints(obs.get("hand"), KIND_COUNT);
        for (int k = 0; k < KIND_COUNT; k++) {
            f[i++] = hand[k] * 0.25f;
        }
        List<Object> handRed = Json.list(obs, "hand_red");
        for (int k = 0; k < KIND_COUNT; k++) {
            f[i++] = boolAt(handRed, k) ? 1f : 0f;
        }
        // 四家副露：种类计数 + 牌种计数（只统计"牌种"，与 Python 同）
        float[] meldKind = new float[4 * MELD_KINDS.length];
        float[] meldTiles = new float[4 * KIND_COUNT];
        List<Object> melds = Json.list(obs, "melds");
        if (melds != null) {
            for (int s = 0; s < 4 && s < melds.size(); s++) {
                for (Object mo : Json.asArr(melds.get(s)) == null ? List.of() : Json.asArr(melds.get(s))) {
                    Map<String, Object> m = Json.asObj(mo);
                    if (m == null) {
                        continue;
                    }
                    int ki = indexOf(MELD_KINDS, Json.str(m, "kind", ""));
                    if (ki >= 0) {
                        meldKind[s * MELD_KINDS.length + ki] += 1f;
                    }
                    for (Object code : Json.list(m, "tiles")) {
                        int k = Tiles.parseKind(String.valueOf(code));
                        if (k >= 0) {
                            meldTiles[s * KIND_COUNT + k] += 1f;
                        }
                    }
                }
            }
        }
        i = put(f, i, meldKind);
        for (int x = 0; x < meldTiles.length; x++) {
            meldTiles[x] *= 0.25f;
        }
        i = put(f, i, meldTiles);
        float[] river = new float[4 * KIND_COUNT];
        List<Object> discards = Json.list(obs, "discards");
        if (discards != null) {
            for (int s = 0; s < 4 && s < discards.size(); s++) {
                List<Object> row = Json.asArr(discards.get(s));
                if (row == null) {
                    continue;
                }
                for (Object code : row) {
                    int k = Tiles.parseKind(String.valueOf(code));
                    if (k >= 0) {
                        river[s * KIND_COUNT + k] += 1f;
                    }
                }
            }
        }
        for (int x = 0; x < river.length; x++) {
            river[x] *= 0.25f;
        }
        i = put(f, i, river);
        float[] dora = new float[KIND_COUNT];
        for (Object code : Json.list(obs, "dora_indicators")) {
            int k = Tiles.parseKind(String.valueOf(code));
            if (k >= 0) {
                dora[k] += 0.25f;
            }
        }
        i = put(f, i, dora);
        List<Object> riichi = Json.list(obs, "riichi");
        List<Object> ippatsu = Json.list(obs, "ippatsu");
        for (int s = 0; s < 4; s++) {
            f[i++] = boolAt(riichi, s) ? 1f : 0f;
        }
        for (int s = 0; s < 4; s++) {
            f[i++] = boolAt(ippatsu, s) ? 1f : 0f;
        }
        List<Object> scores = Json.list(obs, "scores");
        for (int s = 0; s < 4; s++) {
            f[i++] = (numAt(scores, s, 25000f) - 25000f) / 1000f;
        }
        Map<String, Object> rnd = Json.map(obs, "round");
        f[i++] = windIndex(rnd == null ? "E" : Json.str(rnd, "bakaze", "E")) / 4f;
        f[i++] = (rnd == null ? 1f : Json.i(rnd, "kyoku", 1)) / 4f;
        f[i++] = (rnd == null ? 0f : Json.i(rnd, "honba", 0)) / 10f;
        f[i++] = (((rnd == null ? 0 : Json.i(rnd, "dealer", 0)) - seat + 4) % 4) / 3f;
        f[i++] = (rnd == null ? 0f : Json.i(rnd, "riichi_sticks", 0)) / 4f;
        f[i++] = Json.i(obs, "tiles_left", 0) / 70f;
        f[i++] = Json.i(obs, "dead_wall_left", 0) / 4f;
        f[i++] = Json.i(obs, "total_discards", 0) / 72f;
        f[i++] = Json.i(obs, "kan_count", 0) / 4f;
        f[i++] = Json.bool(obs, "any_call", false) ? 1f : 0f;
        f[i++] = Json.i(obs, "player_draws", 0) / 18f;
        f[i++] = Json.bool(obs, "menzen", false) ? 1f : 0f;
        f[i++] = Json.bool(obs, "self_riichi", false) ? 1f : 0f;
        f[i++] = Json.bool(obs, "furiten", false) ? 1f : 0f;
        f[i++] = "turn".equals(Json.str(obs, "kind", "turn")) ? 1f : 0f;
        f[i++] = Json.bool(obs, "haitei", false) ? 1f : 0f;
        f[i++] = Json.bool(obs, "houtei", false) ? 1f : 0f;
        f[i++] = Json.bool(obs, "rinshan", false) ? 1f : 0f;
        int[] visible = ObsFeatures.ints(obs.get("visible"), KIND_COUNT);
        for (int k = 0; k < KIND_COUNT; k++) {
            f[i++] = visible[k] * 0.25f;
        }
        i = putSlot(f, i, str(obs.get("drawn")));
        i = putSlot(f, i, str(obs.get("called_tile")));
        if ("claim".equals(Json.str(obs, "kind", "turn"))) {
            int from = Json.i(obs, "from", -1);
            if (from >= 0) {
                f[i + ((from - seat + 4) % 4)] = 1f;
            }
        }
        i += 4;
        int wn = indexOf(WIN_NOTES, str(obs.get("win_note")));
        f[i + (wn >= 0 ? wn : WIN_NOTES.length)] = 1f;
        i += WIN_NOTES.length + 1;
        int[] dec = ObsFeatures.perDecision(v);
        for (int x = 0; x < ObsFeatures.PER_DECISION; x++) {
            f[i++] = dec[x] / DERIVED_DANGER_SCALE;
        }
        if (i != STATE) {
            throw new IllegalStateException("状态维度拼装错误：" + i + " != " + STATE);
        }
        return f;
    }

    // ------------------------------------------------------------------ 候选（96）

    /** 单个动作键 → 候选向量（含派生量，**已归一化**：与训练时喂给网络的一致）。 */
    public static float[] candidate(String key, int[] derived) {
        float[] f = new float[CAND];
        int i = 0;
        Action a = Action.parse(key);
        String type = a == null ? "" : a.type;
        int ti = indexOf(ACTION_TYPES, type);
        if (ti >= 0) {
            f[i + ti] = 1f;
        }
        i += ACTION_TYPES.length;
        if (a != null && a.tile != null) {
            int s = tileSlot(a.tile);
            if (s >= 0) {
                f[i + s] = 1f;
            }
        }
        i += TILE_SLOTS;
        if (a != null && a.tiles != null) {
            for (String code : a.tiles) {
                int k = Tiles.parseKind(code);
                if (k >= 0) {
                    f[i + k] += 1f;
                }
            }
        }
        i += TILE_SLOTS;
        f[i++] = key != null && key.endsWith("/tsumogiri") ? 1f : 0f;
        int ki = a == null ? -1 : indexOf(KAN_KINDS, a.kanKind);
        if (ki >= 0) {
            f[i + ki] = 1f;
        }
        i += KAN_KINDS.length;
        f[i++] = ("tsumo".equals(type) || "ron".equals(type) || "pass".equals(type)
                || "kyuushu".equals(type)) ? 1f : 0f;
        for (int x = 0; x < ObsFeatures.PER_CANDIDATE; x++) {
            int v = derived == null || x >= derived.length ? 0 : derived[x];
            f[i++] = v / DERIVED_CAND_SCALE[x];
        }
        if (i != CAND) {
            throw new IllegalStateException("候选维度拼装错误：" + i + " != " + CAND);
        }
        return f;
    }

    /** 一次询问的全部候选（顺序与 `legal` 一致，掩码用它）。 */
    public static float[][] candidates(ObsFeatures.View v, List<String> legal) {
        float[][] out = new float[legal.size()][];
        for (int i = 0; i < legal.size(); i++) {
            out[i] = candidate(legal.get(i), ObsFeatures.perCandidate(v, legal.get(i)));
        }
        return out;
    }

    // ------------------------------------------------------------------ 小工具

    private static int put(float[] f, int i, float[] src) {
        System.arraycopy(src, 0, f, i, src.length);
        return i + src.length;
    }

    private static int putSlot(float[] f, int i, String code) {
        if (code != null) {
            int s = tileSlot(code);
            if (s >= 0) {
                f[i + s] = 1f;
            }
        }
        return i + TILE_SLOTS;
    }

    /** 牌码 → 槽位（0..36；赤五占 34/35/36）—— 与 `Action.tileIndex` 同一把尺子。 */
    public static int tileSlot(String code) {
        int k = Tiles.parseKind(code);
        if (k < 0) {
            return -1;
        }
        if (code.length() == 2 && code.charAt(0) == '0') {
            return 34 + k / 9;
        }
        return k;
    }

    static float windIndex(String bakaze) {
        int idx = bakaze == null || bakaze.isEmpty() ? 0 : WINDS.indexOf(bakaze.toUpperCase().charAt(0));
        return Math.max(0, Math.min(3, idx < 0 ? 0 : idx));
    }

    private static boolean boolAt(List<Object> l, int i) {
        return l != null && i < l.size() && Boolean.TRUE.equals(l.get(i));
    }

    private static float numAt(List<Object> l, int i, float def) {
        if (l == null || i >= l.size() || !(l.get(i) instanceof Number)) {
            return def;
        }
        return ((Number) l.get(i)).floatValue();
    }

    private static String str(Object o) {
        return o instanceof String s && !s.isEmpty() ? s : null;
    }

    static int indexOf(String[] arr, String s) {
        if (s == null) {
            return -1;
        }
        for (int i = 0; i < arr.length; i++) {
            if (arr[i].equals(s)) {
                return i;
            }
        }
        return -1;
    }

    /** 布局自述（与 `python -m mahjong_ml.features` 对得上）。 */
    public static String describe() {
        return "Features v" + VERSION + " state=" + STATE + "（base " + BASE_STATE
                + " + derived " + ObsFeatures.PER_DECISION + "） cand=" + CAND + "（base "
                + BASE_CAND + " + derived " + ObsFeatures.PER_CANDIDATE + "）";
    }
}
