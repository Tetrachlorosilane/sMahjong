// Java 侧**只读探针**：把牌山/配牌/指示牌/岭上按固定 JSON 打出来，供 C++ 对拍。
//
//   java -cp server\build\mahjong-server.jar tools\WallProbe.java <seed> [aka] [dealer]
//
// 它**不修改服务端任何东西**（只 import jar 里的公开 API：`Wall.debugAllTiles()` 等），
// 输出格式与 `trainer wall` 完全一致 —— 见 docs/TRAINER-CPP.md §4。
//
// ⚠ 配牌顺序必须与 `Round.setup()` 同序：13 巡 × 4 家（从庄家起）→ 庄家第 14 张 → finishDealing → 排序。
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import mahjong.core.Rules;
import mahjong.core.Tiles;
import mahjong.core.Wall;

public final class WallProbe {

    /** 与 `Round.compareTile` 逐字一致：先 kind，再"赤五在前"，最后比 id。 */
    private static int compareTile(int a, int b) {
        int ka = Tiles.kind(a);
        int kb = Tiles.kind(b);
        if (ka != kb) {
            return ka - kb;
        }
        boolean ra = Tiles.isRedId(a);
        boolean rb = Tiles.isRedId(b);
        if (ra != rb) {
            return ra ? -1 : 1;
        }
        return a - b;
    }

    private static String array(int[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(v[i]);
        }
        return sb.append(']').toString();
    }

    private static String list(List<Integer> v) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < v.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(v.get(i));
        }
        return sb.append(']').toString();
    }

    public static void main(String[] args) {
        long seed = Long.parseLong(args[0]);
        int aka = args.length > 1 ? Integer.parseInt(args[1]) : 3;
        int dealer = args.length > 2 ? Integer.parseInt(args[2]) : 0;

        Rules rules = Rules.defaults();
        rules.aka = aka;
        Wall wall = new Wall(seed, rules);

        int[] all = wall.debugAllTiles();

        List<List<Integer>> hands = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            hands.add(new ArrayList<>());
        }
        for (int r = 0; r < 13; r++) {
            for (int s = 0; s < 4; s++) {
                hands.get((dealer + s) % 4).add(wall.deal());
            }
        }
        // 庄家的第 14 张随配牌一起发（`Round.setup()`：openingTile 进庄家手牌，第一巡不再摸）
        hands.get(dealer).add(wall.deal());
        for (List<Integer> h : hands) {
            h.sort(WallProbe::compareTile);
        }
        wall.finishDealing();

        List<Integer> dora = wall.doraIndicators();
        List<Integer> ura = wall.uraIndicators();
        List<Integer> rinshan = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            rinshan.add(wall.drawRinshan());
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{\"seed\":").append(seed).append(",\"aka\":").append(aka)
          .append(",\"dealer\":").append(dealer).append(',');
        sb.append("\"wall\":").append(array(all)).append(',');
        sb.append("\"hands\":[");
        for (int i = 0; i < 4; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(list(hands.get(i)));
        }
        sb.append("],");
        sb.append("\"dora\":").append(list(dora)).append(',');
        sb.append("\"ura\":").append(list(ura)).append(',');
        sb.append("\"rinshan\":").append(list(rinshan));
        sb.append('}');
        System.out.println(sb);
    }

    private WallProbe() {
    }
}
