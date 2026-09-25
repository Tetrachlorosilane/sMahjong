package mahjong.ai;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import mahjong.util.Json;
import mahjong.util.Log;

/**
 * **机器人 AI 注册表**：让服务端的机器人座位能选"用哪一代 AI"（内置牌效 / 各代训练网络）。
 *
 * <p>为什么必须是**服务端白名单**而不是"房间传策略串"：策略串里 {@code net:<路径>} 是
 * **服务器本机的文件路径** —— 让客户端直接传串，等于把"读服务器上任意文件"开放出去
 * （{@code net:/etc/passwd}、{@code net:C:\...\机密} 都会被当成权重文件去读）。
 * 所以客户端只能传**注册表里的名字**，路径只由服务端的启动参数决定
 * （{@code --bot-ai-reg} / {@code --bot-ai-dir}）或**自动挂载的包目录**（{@link #DEFAULT_DIR}）。
 *
 * <p>**装包不需要参数**：启动时自动扫 {@code bot-ai/}（与 {@code replays}/{@code players} 同层，
 * 相对**启动目录**），每个一级子目录 = 一个 AI 包（清单 {@code bot.json} + 载荷）。
 * 深度模型与启发搜索**同一套接口**（见 {@link #loadPackage}），格式说明见 {@code docs/BOT-AI.md}。
 *
 * <p>注册在**启动时**完成并立即校验（{@link Policies#byName} 会当场加载权重、校验魔数与特征维度）：
 * 名字写错、权重文件不在、维度不符都在启动期炸掉，而不是"开了房间、打到一半才发现机器人不动"。
 *
 * <p>⚠ 名字→策略串的映射**进程级唯一**（静态注册表），与 {@code PlayerStore}/{@code ReplayStore}
 * 一个套路：它是服务端配置，不属于任何一张桌子。测试用 {@link #clear()} 复原。
 */
public final class BotAis {

    /** 有序：客户端下拉框的顺序必须稳定（LinkedHashMap，不是 HashMap）。 */
    private static final Map<String, String> REG = new LinkedHashMap<>();

    /** 默认 AI 的**名字**（缺省 {@code teacher} = 改造前的行为）。 */
    private static String defaultName = "teacher";

    private BotAis() {
    }

    /** 清空并恢复出厂状态（自检用；生产只在启动时调一次）。 */
    public static synchronized void clear() {
        REG.clear();
        defaultName = "teacher";
        // 内置的四种脚本/老师永远可选：这样"一个都不配"时客户端也有得选，
        // 而且 `teacher` 必须在列表里 —— 它是"改造前行为"的锚点。
        REG.put("teacher", "teacher");
        REG.put("first", "first");
        REG.put("pass", "pass");
        REG.put("random", "random");
    }

    /**
     * 注册一个可选 AI。
     *
     * @param name 给客户端看/传的名字（唯一；重复注册同一名字**覆盖**，方便脚本反复调）
     * @param spec 策略串（`teacher` / `first` / `pass` / `random` / `net:<权重文件>[@<α>][#<T>]`）
     * @throws IllegalArgumentException 名字为空、或策略串解析/加载失败（启动期就该炸，别等开局）
     */
    public static synchronized void register(String name, String spec) {
        String n = name == null ? "" : name.trim();
        String s = spec == null ? "" : spec.trim();
        if (n.isEmpty()) {
            throw new IllegalArgumentException("机器人 AI 的名字不能为空");
        }
        if (s.isEmpty()) {
            throw new IllegalArgumentException("机器人 AI `" + n + "` 没有策略串");
        }
        if (n.indexOf(',') >= 0 || n.indexOf('#') >= 0 || n.indexOf('@') >= 0) {
            // 这三个字符在策略串文法里有含义（`--policy a,b,c,d` / `@α` / `#T`），
            // 放进名字会让日志与命令行没法一眼看懂是"名字"还是"文法"。
            throw new IllegalArgumentException("机器人 AI 的名字不许含 `,` `@` `#`：" + n);
        }
        for (int i = 0; i < n.length(); i++) {
            if (n.charAt(i) < 0x20 || n.charAt(i) > 0x7E) {
                // 名字会进**报文**（`hello_ok.bot_ais[].name` / `room.bot_ai`），而 AGENTS §6.4 的纪律是
                // "报文里只有 name/text/msg 允许非 ASCII"（e2e 的 ASCII 审计会当场红）。
                throw new IllegalArgumentException("机器人 AI 的名字必须是可打印 ASCII（报文纪律）：" + n);
            }
        }
        Policies.byName(s);            // 立刻校验（会加载权重 → 坏路径/坏魔数/坏维度在这里就报）
        REG.put(n, s);
    }

    /**
     * 扫描目录：把每个**一级子目录**当成一个 AI 包注册（见 {@link #loadPackage}）。
     *
     * <p>这正是"各代 AI"的用法：训练侧的 checkpoint 目录形如
     * {@code S:\mahjong-training\ckpt\ppo2-g04\net.bin}（没有 {@code bot.json}），
     * 于是按目录名注册成 {@code ppo2-g04} —— 老目录可以直接指过来用；
     * 而"可分发"的包则带上 {@code bot.json}（能改名、能写 α/T、能声明内置启发搜索）。
     *
     * @return 注册成功的个数（坏包跳过，不抛）
     */
    public static synchronized int scanDir(Path dir) {
        if (dir == null || !Files.isDirectory(dir)) {
            throw new IllegalArgumentException("机器人 AI 目录不存在：" + dir);
        }
        int n = 0;
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir)) {
            List<Path> subs = new ArrayList<>();
            for (Path p : ds) {
                if (Files.isDirectory(p)) {
                    subs.add(p);                // 只认**一级子目录** = 一个包
                }
            }
            subs.sort(java.util.Comparator.comparing(p -> p.getFileName().toString()));
            for (Path p : subs) {
                if (loadPackage(p)) {
                    n++;
                }
            }
        } catch (IOException e) {
            throw new IllegalArgumentException("扫描机器人 AI 目录失败：" + dir + " —— " + e.getMessage(), e);
        }
        return n;
    }

    /**
     * 载入**一个 AI 包**（一个一级子目录）。返回是否注册成功。
     *
     * <p>包的统一接口 = 目录里的 `bot.json`（清单）+ 载荷。两种形态同一套接口：
     * <pre>
     *   bot-ai/ppo2-g04/bot.json   {"kind":"net",     "model":"net.bin", "alpha":4, "temp":1}
     *   bot-ai/ppo2-g04/net.bin    ← 深度模型权重（纯 Java 前向）
     *   bot-ai/teacher/bot.json    {"kind":"builtin", "policy":"teacher"}       ← 启发搜索（内置牌效）
     * </pre>
     * 也可以**不写** `bot.json`、直接放 `net.bin`（老写法，按目录名注册成 net）—— 训练侧那些
     * 「一代一个子目录、里面一个 net.bin」的 checkpoint 目录就能直接指过来用。
     * ⚠ 注释里别写 `ckpt` 加斜杠星号那种路径通配（`星号+斜杠` 会把 javadoc 提前结束，
     * 后面的中文全变成"非法字符"——这次就这么炸过一次）。
     *
     * <p>坏包（清单读不出 / 未知 kind / 载荷缺失 / 权重坏了 / 名字撞内置）一律
     * **WARN + 跳过这一个**：一个半截包不该让整个服务起不来（与"点名的 AI 坏了"不同，
     * 那是启动参数，错了要当场炸）。
     */
    private static boolean loadPackage(Path dir) {
        final String dirName = dir.getFileName().toString();
        final Path manifest = dir.resolve("bot.json");
        String name = dirName;
        String kind = null;
        String model = "net.bin";
        String policy = null;
        double alpha = 0.0;
        double temp = 0.0;
        String note = "";
        if (Files.isRegularFile(manifest)) {
            try {
                Map<String, Object> m = Json.asObj(Json.parse(Files.readString(manifest)));
                if (m == null) {
                    throw new IllegalArgumentException("bot.json 不是对象");
                }
                name = Json.str(m, "name", dirName);
                kind = Json.str(m, "kind", "");
                model = Json.str(m, "model", "net.bin");
                policy = Json.str(m, "policy", "");
                alpha = Json.i(m, "alpha", 0) * 1.0;
                temp = Json.i(m, "temp", 0) * 1.0;
                // α/T 允许小数：整数读法会丢掉 0.5 —— 优先按 double 再退整数
                alpha = num(m, "alpha", alpha);
                temp = num(m, "temp", temp);
                note = Json.str(m, "note", "");
            } catch (Exception e) {                                  // noqa: BLE001
                Log.warn("跳过机器人 AI 包 `" + dirName + "`：bot.json 读不出来 —— " + e.getMessage());
                return false;
            }
        }
        if (kind == null || kind.isEmpty()) {
            kind = Files.isRegularFile(dir.resolve(model)) || Files.isRegularFile(dir.resolve("net.bin"))
                    ? "net" : "builtin";        // 没写 kind 时按载荷猜：有权重就是网，否则看 policy
        }
        String spec;
        if ("net".equals(kind)) {
            Path w = dir.resolve(model);
            if (!Files.isRegularFile(w)) {
                Log.warn("跳过机器人 AI 包 `" + dirName + "`：找不到权重文件 " + model);
                return false;
            }
            spec = "net:" + w.toAbsolutePath();
            if (alpha > 0) {
                spec += "@" + trim(alpha);
            }
            if (temp > 0) {
                spec += "#" + trim(temp);
            }
        } else if ("builtin".equals(kind)) {
            if (policy == null || policy.isEmpty()) {
                Log.warn("跳过机器人 AI 包 `" + dirName + "`：kind=builtin 必须给 `policy`"
                        + "（teacher/first/pass/random）");
                return false;
            }
            spec = policy;
        } else {
            Log.warn("跳过机器人 AI 包 `" + dirName + "`：未知 kind=`" + kind + "`（只认 net / builtin）");
            return false;
        }
        if (BUILTIN_NAMES.contains(name)
                && !("builtin".equals(kind) && name.equals(policy))) {
            // 内置那四个是"改造前行为"的锚点（也是 `--bot-ai` 的缺省）：**深度模型包**不许占用这些名字
            // （那会把锚点悄悄换成一个网络）。但"内置包"允许同名同义地声明一遍 ——
            // 这样"启发搜索"与"深度模型"就是**同一套包格式**，不用为前者开特例。
            Log.warn("跳过机器人 AI 包 `" + dirName + "`：名字 `" + name
                    + "` 与内置策略同名（只有 kind=builtin 且 policy 同名的包才能这么写；"
                    + "换成别的名字，或用 --bot-ai-reg 显式覆盖）");
            return false;
        }
        try {
            register(name, spec);
            Log.info("挂载机器人 AI 包 `" + name + "`（kind=" + kind
                    + (note.isEmpty() ? "" : "，" + note) + "）");
            return true;
        } catch (RuntimeException e) {
            Log.warn("跳过机器人 AI 包 `" + dirName + "`：" + e.getMessage());
            return false;
        }
    }

    /** 清单里的数值字段（`alpha`/`temp` 允许小数）。 */
    private static double num(Map<String, Object> m, String key, double def) {
        Object v = m.get(key);
        if (v instanceof Number) {
            return ((Number) v).doubleValue();
        }
        if (v instanceof String) {
            try {
                return Double.parseDouble(((String) v).trim());
            } catch (NumberFormatException ignored) {
                return def;
            }
        }
        return def;
    }

    /** 数值 → 策略串里的写法（不要 `0.30000000000000004` 那种尾巴）。 */
    private static String trim(double v) {
        if (v == Math.rint(v) && Math.abs(v) < 1e9) {
            return String.valueOf((long) v);
        }
        return String.valueOf(v);
    }

    /** 四个内置策略名：包不许占用它们（见 {@link #loadPackage}）。 */
    private static final java.util.Set<String> BUILTIN_NAMES =
            new java.util.LinkedHashSet<>(List.of("teacher", "first", "pass", "random"));

    /**
     * 扫**默认目录**（`bot-ai`，与 `replays`/`players` 同一层 = 相对**启动目录**）。
     *
     * <p>目录不存在不算错（部署里可以一个包都不放）：只打一条提示。
     */
    public static synchronized int scanDefault() {
        Path dir = Path.of(DEFAULT_DIR);
        if (!Files.isDirectory(dir)) {
            Log.info("未发现 " + DEFAULT_DIR + "/（可选：把各代 AI 包放进去，服务端启动时自动挂载）");
            return 0;
        }
        return scanDir(dir);
    }

    /** 默认包目录名（与 `replays`/`players` 同层）。 */
    public static final String DEFAULT_DIR = "bot-ai";

    /** 设默认 AI（不传 `bot_ai` 的房间用它）。名字必须已注册。 */
    public static synchronized void setDefault(String name) {
        String n = name == null ? "" : name.trim();
        if (!REG.containsKey(n)) {
            throw new IllegalArgumentException("默认机器人 AI 没注册：" + n
                    + "（可用：" + String.join(", ", REG.keySet()) + "）");
        }
        defaultName = n;
    }

    public static synchronized String defaultAi() {
        return defaultName;
    }

    public static synchronized List<String> names() {
        return new ArrayList<>(REG.keySet());
    }

    /** 名字 → 策略串；未注册返回 {@code null}（调用方据此拒绝，而不是猜一个）。 */
    public static synchronized String spec(String name) {
        return name == null ? null : REG.get(name.trim());
    }

    public static synchronized boolean has(String name) {
        return name != null && REG.containsKey(name.trim());
    }

    /** 该名字的**工厂**（每局一份实例，见 {@link PolicyFactory}）；未注册返回 {@code null}。 */
    public static synchronized PolicyFactory factory(String name) {
        String s = spec(name);
        return s == null ? null : Policies.byName(s);
    }

    /**
     * 给客户端的清单 `[{name, default}]`（顺序稳定 = 注册顺序）。
     *
     * <p>⚠ **故意不发策略串**：`spec` 里是**服务器本机的绝对路径**（可能带中文目录名），
     * 发出去既没必要（客户端只选名字）、又违反"报文里只有 name/text/msg 允许非 ASCII"的纪律，
     * 还白送一条服务器目录结构的情报。要看串的只有服务端自己的日志。
     */
    public static synchronized List<Object> json() {
        List<Object> out = new ArrayList<>();
        for (Map.Entry<String, String> e : REG.entrySet()) {
            out.add(Json.obj("name", e.getKey(), "default", e.getKey().equals(defaultName)));
        }
        return out;
    }
}
