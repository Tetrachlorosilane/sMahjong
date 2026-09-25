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
 * （{@code --bot-ai-reg} / {@code --bot-ai-dir}）。
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
     * 扫描目录：把每个**含 `net.bin` 的一级子目录**按目录名注册。
     *
     * <p>这正是"各代 AI"的用法：训练侧的 checkpoint 目录形如
     * {@code S:\mahjong-training\ckpt\ppo2-g04\net.bin}，于是名字就是 {@code ppo2-g04}。
     *
     * @return 注册成功的个数
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
                    subs.add(p);
                }
            }
            subs.sort(java.util.Comparator.comparing(p -> p.getFileName().toString()));
            for (Path p : subs) {
                Path bin = p.resolve("net.bin");
                if (!Files.isRegularFile(bin)) {
                    continue;                   // 没有 net.bin 的子目录直接跳过（ckpt 下也有别的目录）
                }
                String nm = p.getFileName().toString();
                try {
                    register(nm, "net:" + bin.toAbsolutePath());
                    n++;
                } catch (RuntimeException e) {
                    // 单个权重坏了**不让整个服务起不来**：打一条 WARN 跳过它，
                    // 但其余 AI 照常可用（否则一个半截文件就能让服务器开不了机）。
                    Log.warn("跳过机器人 AI `" + nm + "`：" + e.getMessage());
                }
            }
        } catch (IOException e) {
            throw new IllegalArgumentException("扫描机器人 AI 目录失败：" + dir + " —— " + e.getMessage(), e);
        }
        Log.info("机器人 AI 注册表：" + String.join(", ", REG.keySet()) + "（默认 " + defaultName + "）");
        return n;
    }

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
