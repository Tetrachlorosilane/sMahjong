// Java 侧**只读探针**：把动作键 / 固定头下标 / 回包 / 落位打出来，供 C++ 对拍。
//
//   java -cp server\build\mahjong-server.jar tools\ActionProbe.java <corpus> <out>
//
// 语料格式与 `trainer action` 一致（每行一个类型 + 参数，见 trainer/src/main.cpp 的注释）。
// 回包用**规范化 token 串**表示（`type=…;tile=…;kind=…;tsumogiri=1;tiles=a+b`，字段顺序固定）——
// 真正的 JSON 序列化属协议层，与本层无关，token 串只是让两边能逐字符比。
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import mahjong.ai.Action;
import mahjong.util.Json;

public final class ActionProbe {

    /**
     * 回包 → 规范化 token 串。
     *
     * ⚠ 字段顺序**按类型固定**（与 `Action.toCmd()` 的插入顺序一致，也与 C++ 侧一致）：
     *   discard → type;tile[;tsumogiri] · riichi → type;tile · kan → type;kind;tile[;tiles] ·
     *   pon → type[;tiles] · chi → type;tiles · 其余 → type
     * 顺序不同会让"同一份回包"在两边打印出不同文本，从而把纯粹的顺序差异误报成不一致。
     */
    private static String tokens(Map<String, Object> cmd) {
        if (cmd == null) {
            return "";
        }
        final String type = Json.str(cmd, "type", "");
        StringBuilder sb = new StringBuilder("type=").append(type);
        if (type.equals(Action.DISCARD) || type.equals(Action.RIICHI)) {
            sb.append(";tile=").append(Json.str(cmd, "tile", ""));
            if (Json.bool(cmd, "tsumogiri", false)) {
                sb.append(";tsumogiri=1");
            }
        } else if (type.equals(Action.KAN)) {
            sb.append(";kind=").append(Json.str(cmd, "kind", ""));
            sb.append(";tile=").append(Json.str(cmd, "tile", ""));
            List<String> tiles = Json.strList(cmd, "tiles");
            if (tiles != null && !tiles.isEmpty()) {
                sb.append(";tiles=").append(String.join("+", tiles));
            }
        } else if (type.equals(Action.PON)) {
            List<String> tiles = Json.strList(cmd, "tiles");
            if (tiles != null && !tiles.isEmpty()) {
                sb.append(";tiles=").append(String.join("+", tiles));
            }
        } else if (type.equals(Action.CHI)) {
            List<String> tiles = Json.strList(cmd, "tiles");
            if (tiles != null && !tiles.isEmpty()) {
                sb.append(";tiles=").append(String.join("+", tiles));
            }
        }
        return sb.toString();
    }

    /** token 串 → 回包（与 C++ `actionFromCmdTokens` 同一套解析）。 */
    private static Map<String, Object> fromTokens(String text) {
        Map<String, Object> cmd = new LinkedHashMap<>();
        for (String field : text.split(";", -1)) {
            int eq = field.indexOf('=');
            if (eq < 0) {
                continue;
            }
            String k = field.substring(0, eq);
            String v = field.substring(eq + 1);
            if (k.equals("tsumogiri")) {
                cmd.put("tsumogiri", v.equals("1") || v.equals("true"));
            } else if (k.equals("tiles")) {
                cmd.put("tiles", new ArrayList<Object>(List.of(v.split("\\+", -1))));
            } else {
                cmd.put(k, v);
            }
        }
        return cmd.isEmpty() ? null : cmd;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("用法：java -cp server\\build\\mahjong-server.jar tools\\ActionProbe.java "
                             + "<corpus> <out>");
            System.exit(2);
        }
        long rows = 0;
        try (BufferedReader in = new BufferedReader(new FileReader(args[0]), 1 << 20);
             BufferedWriter out = new BufferedWriter(new FileWriter(args[1]), 1 << 20)) {
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                int sp = line.indexOf(' ');
                String kind = sp < 0 ? line : line.substring(0, sp);
                String rest = sp < 0 ? "" : line.substring(sp + 1);
                StringBuilder sb = new StringBuilder(128);
                if (kind.equals("key")) {
                    Action a = Action.fromCmd(fromTokens(rest));
                    sb.append(a == null ? "- - -" : a.key() + " " + a.index() + " " + tokens(a.toCmd()));
                } else if (kind.equals("parse")) {
                    Action a = Action.parse(rest);
                    sb.append(a == null ? "- - -" : a.key() + " " + a.index() + " " + tokens(a.toCmd()));
                } else if (kind.equals("tile")) {
                    int idx = Action.tileIndex(rest);
                    String code = Action.tileCode(idx);
                    sb.append(idx).append(' ').append(code == null ? "-" : code);
                } else if (kind.equals("resolve")) {
                    int sp2 = rest.indexOf(' ');
                    String cmdText = sp2 < 0 ? rest : rest.substring(0, sp2);
                    String legalText = sp2 < 0 ? "-" : rest.substring(sp2 + 1);
                    List<Action> legal = new ArrayList<>();
                    if (!legalText.equals("-")) {
                        for (String k : legalText.split(",", -1)) {
                            Action a = Action.parse(k);
                            if (a != null) {
                                legal.add(a);
                            }
                        }
                    }
                    Action r = Action.resolve(fromTokens(cmdText), legal);
                    sb.append(r == null ? "-" : r.key());
                } else {
                    System.err.println("认不出的语料行：" + line);
                    System.exit(2);
                }
                sb.append('\n');
                out.write(sb.toString());
                rows++;
            }
        }
        System.err.printf("[ActionProbe] 语料 %d 行%n", rows);
    }

    private ActionProbe() {
    }
}
