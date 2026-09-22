package mahjong.core;

/**
 * 牌的工具集合。
 *
 * <p>内部牌 id 为 {@code 0..135}：{@code kind = id / 4}，{@code copy = id % 4}。
 * kind 0..8 = 1m..9m，9..17 = 1p..9p，18..26 = 1s..9s，27..33 = 1z..7z
 * （1z东 2z南 3z西 4z北 5z白 6z发 7z中）。
 *
 * <p>赤宝牌：kind ∈ {4(5m), 13(5p), 22(5s)} 且 copy == 0。
 */
public final class Tiles {

    public static final int KIND_COUNT = 34;
    public static final int TILE_COUNT = 136;

    /** 赤 5m / 5p / 5s 的 kind。 */
    public static final int AKA_M = 4;
    public static final int AKA_P = 13;
    public static final int AKA_S = 22;

    private static final String[] HONOR_NAMES = {"1z", "2z", "3z", "4z", "5z", "6z", "7z"};

    private Tiles() {
    }

    public static int kind(int id) {
        return id >> 2;
    }

    public static int copy(int id) {
        return id & 3;
    }

    public static int id(int kind, int copy) {
        return (kind << 2) | (copy & 3);
    }

    public static boolean isRedId(int id) {
        int k = kind(id);
        return (k == AKA_M || k == AKA_P || k == AKA_S) && (id & 3) == 0;
    }


    /** id → "5m" / "0p" 等。 */
    public static String toStr(int id) {
        return kindToStr(kind(id), isRedId(id));
    }

    public static String kindToStr(int kind) {
        return kindToStr(kind, false);
    }

    public static String kindToStr(int kind, boolean red) {
        if (kind < 0 || kind >= KIND_COUNT) {
            return "??";
        }
        if (kind < 27) {
            int suit = kind / 9;
            int num = kind % 9 + 1;
            char sc = suit == 0 ? 'm' : suit == 1 ? 'p' : 's';
            if (red && num == 5) {
                return "0" + sc;
            }
            return num + String.valueOf(sc);
        }
        return HONOR_NAMES[kind - 27];
    }

    /** 解析 kind 字符串（可带赤：0m/0p/0s）。非法返回 -1。 */
    public static int parseKind(String s) {
        if (s == null || s.length() != 2) {
            return -1;
        }
        char c0 = s.charAt(0);
        char c1 = s.charAt(1);
        if (c0 == '0') {
            if (c1 == 'm') {
                return AKA_M;
            }
            if (c1 == 'p') {
                return AKA_P;
            }
            if (c1 == 's') {
                return AKA_S;
            }
            return -1;
        }
        if (c0 < '1' || c0 > '9') {
            return -1;
        }
        int n = c0 - '0';
        switch (c1) {
            case 'm':
                return n - 1;
            case 'p':
                return 9 + n - 1;
            case 's':
                return 18 + n - 1;
            case 'z':
                if (n >= 1 && n <= 7) {
                    return 27 + n - 1;
                }
                return -1;
            default:
                return -1;
        }
    }

    /** 是否请求赤宝牌：字符串形式为 0m/0p/0s。 */
    public static boolean isRedStr(String s) {
        return s != null && s.length() == 2 && s.charAt(0) == '0';
    }

    /** 花色 0=m 1=p 2=s，字牌返回 -1。 */
    public static int suit(int kind) {
        return kind < 27 ? kind / 9 : -1;
    }

    /** 数字 1..9；字牌 1..7。 */
    public static int num(int kind) {
        return kind < 27 ? kind % 9 + 1 : kind - 26;
    }

    public static boolean isHonor(int kind) {
        return kind >= 27;
    }

    public static boolean isWind(int kind) {
        return kind >= 27 && kind <= 30;
    }

    public static boolean isDragon(int kind) {
        return kind >= 31 && kind <= 33;
    }

    public static boolean isTerminal(int kind) {
        return kind < 27 && (kind % 9 == 0 || kind % 9 == 8);
    }

    /** 幺九牌：老头牌 + 字牌。 */
    public static boolean isYaochu(int kind) {
        return isHonor(kind) || isTerminal(kind);
    }

    /** 中张牌 2..8。 */
    public static boolean isSimple(int kind) {
        return !isYaochu(kind);
    }

    /** 绿一色用牌：2s3s4s6s8s + 发(6z)。 */
    public static boolean isGreen(int kind) {
        if (kind == 19 || kind == 20 || kind == 21 || kind == 23 || kind == 25) {
            return true; // 2s 3s 4s 6s 8s
        }
        return kind == 32; // 发
    }

    /** 指示牌 → 宝牌 kind。 */
    public static int doraFrom(int indicatorKind) {
        if (indicatorKind < 27) {
            int suit = indicatorKind / 9;
            int num = indicatorKind % 9;
            return suit * 9 + (num + 1) % 9;
        }
        if (indicatorKind < 31) {
            return 27 + (indicatorKind - 27 + 1) % 4;
        }
        return 31 + (indicatorKind - 31 + 1) % 3;
    }

    /** kind 数组 → 34 长度计数数组。 */
    public static int[] counts(int[] kinds) {
        int[] c = new int[KIND_COUNT];
        for (int k : kinds) {
            if (k >= 0 && k < KIND_COUNT) {
                c[k]++;
            }
        }
        return c;
    }


    /** 计数数组求和。 */
    public static int sum(int[] c) {
        int s = 0;
        for (int v : c) {
            s += v;
        }
        return s;
    }

    /** 中文名（日志/调试用）。 */
    public static String cnName(int kind) {
        if (kind < 0 || kind >= KIND_COUNT) {
            return "?";
        }
        String[] nums = {"一", "二", "三", "四", "五", "六", "七", "八", "九"};
        if (kind < 9) {
            return nums[kind % 9] + "万";
        }
        if (kind < 18) {
            return nums[kind % 9] + "筒";
        }
        if (kind < 27) {
            return nums[kind % 9] + "索";
        }
        String[] honors = {"东", "南", "西", "北", "白", "发", "中"};
        return honors[kind - 27];
    }
}
