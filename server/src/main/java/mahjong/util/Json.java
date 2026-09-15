package mahjong.util;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 极简 JSON 解析/序列化（零依赖）。
 *
 * <p>值模型：{@code Map<String,Object>} / {@code List<Object>} / {@code String} /
 * {@code Long} / {@code Double} / {@code Boolean} / {@code null}。
 */
public final class Json {

    private Json() {
    }

    // ---------------------------------------------------------------- 构造

    public static Map<String, Object> obj() {
        return new LinkedHashMap<>();
    }

    /** 交替传入 key, value 构造对象。 */
    public static Map<String, Object> obj(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return m;
    }

    public static List<Object> arr(Object... items) {
        List<Object> l = new ArrayList<>(items.length);
        for (Object o : items) {
            l.add(o);
        }
        return l;
    }

    /**
     * {@code int[]} → JSON 数组（元素装箱成 Integer）。
     *
     * <p>放在这里是因为「把领域里的定长数组翻成报文里的数组」属于**报文构造**，与规则无关。
     * 两个发送方（{@code Round} 与 {@code Table}）曾经各写一份一模一样的实现 ——
     * 现在只有这一份，两边都用 {@code import static mahjong.util.Json.intList} 引用。
     */
    public static List<Object> intList(int[] a) {
        List<Object> l = new ArrayList<>(a.length);
        for (int v : a) {
            l.add(v);
        }
        return l;
    }

    // ---------------------------------------------------------------- 读取

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObj(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : null;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArr(Object o) {
        return o instanceof List ? (List<Object>) o : null;
    }

    public static String str(Map<String, Object> m, String k, String def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof String ? (String) v : def;
    }

    public static int i(Map<String, Object> m, String k, int def) {
        Object v = m == null ? null : m.get(k);
        if (v instanceof Number) {
            return ((Number) v).intValue();
        }
        if (v instanceof String) {
            try {
                return Integer.parseInt((String) v);
            } catch (NumberFormatException ignored) {
                return def;
            }
        }
        return def;
    }

    public static long l(Map<String, Object> m, String k, long def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof Number ? ((Number) v).longValue() : def;
    }

    public static boolean bool(Map<String, Object> m, String k, boolean def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof Boolean ? (Boolean) v : def;
    }

    public static Map<String, Object> map(Map<String, Object> m, String k) {
        return asObj(m == null ? null : m.get(k));
    }

    public static List<Object> list(Map<String, Object> m, String k) {
        return asArr(m == null ? null : m.get(k));
    }

    /** 取字符串列表。 */
    public static List<String> strList(Map<String, Object> m, String k) {
        List<String> out = new ArrayList<>();
        List<Object> l = list(m, k);
        if (l != null) {
            for (Object o : l) {
                if (o instanceof String) {
                    out.add((String) o);
                }
            }
        }
        return out;
    }

    // ---------------------------------------------------------------- 解析

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWs();
        Object v = p.value();
        p.skipWs();
        if (p.pos < p.src.length()) {
            throw new JsonException("trailing content at " + p.pos);
        }
        return v;
    }

    /** 解析失败返回 null，不抛异常。 */
    public static Object tryParse(String text) {
        try {
            return parse(text);
        } catch (RuntimeException e) {
            return null;
        } catch (StackOverflowError e) {
            // 深度上限（见 Parser.MAX_DEPTH）已经挡住正常路径；这里兜底，
            // 绝不让 Error 冒到读线程外面去（StackOverflowError 不是 RuntimeException，
            // 只 catch RuntimeException 的话读线程会被直接带走，见 AUDIT F12）。
            return null;
        }
    }

    public static final class JsonException extends RuntimeException {
        public JsonException(String msg) {
            super(msg);
        }
    }

    private static final class Parser {
        /**
         * 嵌套深度上限。报文是四人牌桌的控制流，正常不超过 6 层；不设限的话，
         * 一条 20 KB 的 `[[[[…` 就能把递归压爆栈（每层两个栈帧）。
         */
        static final int MAX_DEPTH = 32;

        final String src;
        int pos;
        int depth;

        Parser(String src) {
            this.src = src;
        }

        void skipWs() {
            while (pos < src.length()) {
                char c = src.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    break;
                }
            }
        }

        char peek() {
            if (pos >= src.length()) {
                throw new JsonException("unexpected end");
            }
            return src.charAt(pos);
        }

        void expect(char c) {
            if (pos >= src.length() || src.charAt(pos) != c) {
                throw new JsonException("expected '" + c + "' at " + pos);
            }
            pos++;
        }

        Object value() {
            skipWs();
            char c = peek();
            switch (c) {
                case '{':
                case '[':
                    if (depth >= MAX_DEPTH) {
                        throw new JsonException("nesting too deep at " + pos);
                    }
                    depth++;
                    try {
                        return c == '{' ? object() : array();
                    } finally {
                        depth--;
                    }
                case '"':
                    return string();
                case 't':
                    literal("true");
                    return Boolean.TRUE;
                case 'f':
                    literal("false");
                    return Boolean.FALSE;
                case 'n':
                    literal("null");
                    return null;
                default:
                    return number();
            }
        }

        void literal(String lit) {
            if (!src.startsWith(lit, pos)) {
                throw new JsonException("bad literal at " + pos);
            }
            pos += lit.length();
        }

        Map<String, Object> object() {
            expect('{');
            Map<String, Object> m = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') {
                pos++;
                return m;
            }
            while (true) {
                skipWs();
                String k = string();
                skipWs();
                expect(':');
                Object v = value();
                m.put(k, v);
                skipWs();
                char c = peek();
                if (c == ',') {
                    pos++;
                    continue;
                }
                if (c == '}') {
                    pos++;
                    return m;
                }
                throw new JsonException("bad object at " + pos);
            }
        }

        List<Object> array() {
            expect('[');
            List<Object> l = new ArrayList<>();
            skipWs();
            if (peek() == ']') {
                pos++;
                return l;
            }
            while (true) {
                l.add(value());
                skipWs();
                char c = peek();
                if (c == ',') {
                    pos++;
                    continue;
                }
                if (c == ']') {
                    pos++;
                    return l;
                }
                throw new JsonException("bad array at " + pos);
            }
        }

        String string() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (pos >= src.length()) {
                    throw new JsonException("unterminated string");
                }
                char c = src.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c == '\\') {
                    if (pos >= src.length()) {
                        throw new JsonException("unterminated escape");
                    }
                    char e = src.charAt(pos++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            if (pos + 4 > src.length()) {
                                throw new JsonException("bad \\u");
                            }
                            sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                            pos += 4;
                            break;
                        default:
                            throw new JsonException("bad escape \\" + e);
                    }
                } else {
                    sb.append(c);
                }
            }
        }

        Object number() {
            int start = pos;
            if (pos < src.length() && (src.charAt(pos) == '-' || src.charAt(pos) == '+')) {
                pos++;
            }
            boolean dec = false;
            while (pos < src.length()) {
                char c = src.charAt(pos);
                if (c >= '0' && c <= '9') {
                    pos++;
                } else if (c == '.' || c == 'e' || c == 'E' || c == '-' || c == '+') {
                    dec = dec || c == '.' || c == 'e' || c == 'E';
                    pos++;
                } else {
                    break;
                }
            }
            String s = src.substring(start, pos);
            if (s.isEmpty()) {
                throw new JsonException("bad number at " + start);
            }
            if (dec) {
                return Double.parseDouble(s);
            }
            try {
                return Long.parseLong(s);
            } catch (NumberFormatException e) {
                return Double.parseDouble(s);
            }
        }
    }

    // ---------------------------------------------------------------- 序列化

    /**
     * 按**码点**截断字符串（不是按 UTF-16 码元）。
     *
     * <p>报文全程 UTF-8，而 Java 的 {@code substring} 是按 UTF-16 码元切的：
     * 直接 {@code substring(0, 24)} 可能把代理对（CJK 扩展汉字如 𠮷、emoji 如 🀄）
     * 切成**半个字符**，写出时那个孤立代理会被编码成 {@code ?} —— 静默损坏用户输入。
     * 长度上限按"几个字符"算也更符合直觉（一个 emoji 算 1，不是 2）。
     */
    public static String clampCodePoints(String s, int maxCodePoints) {
        if (s == null) {
            return "";
        }
        if (maxCodePoints <= 0) {
            return "";
        }
        if (s.codePointCount(0, s.length()) <= maxCodePoints) {
            return s;
        }
        return s.substring(0, s.offsetByCodePoints(0, maxCodePoints));
    }

    public static String write(Object o) {
        StringBuilder sb = new StringBuilder(256);
        writeTo(sb, o);
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    private static void writeTo(StringBuilder sb, Object o) {
        if (o == null) {
            sb.append("null");
        } else if (o instanceof String) {
            escape(sb, (String) o);
        } else if (o instanceof Boolean) {
            sb.append(o.toString());
        } else if (o instanceof Double || o instanceof Float) {
            double d = ((Number) o).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                sb.append((long) d);
            } else {
                sb.append(d);
            }
        } else if (o instanceof Number) {
            sb.append(o.toString());
        } else if (o instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> e : ((Map<String, Object>) o).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                escape(sb, e.getKey());
                sb.append(':');
                writeTo(sb, e.getValue());
            }
            sb.append('}');
        } else if (o instanceof Iterable) {
            sb.append('[');
            boolean first = true;
            for (Object v : (Iterable<Object>) o) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeTo(sb, v);
            }
            sb.append(']');
        } else if (o instanceof int[]) {
            sb.append('[');
            int[] a = (int[]) o;
            for (int k = 0; k < a.length; k++) {
                if (k > 0) {
                    sb.append(',');
                }
                sb.append(a[k]);
            }
            sb.append(']');
        } else {
            escape(sb, o.toString());
        }
    }

    private static void escape(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }
}
