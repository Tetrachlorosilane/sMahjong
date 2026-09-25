package mahjong;

import java.nio.file.Path;

import mahjong.net.Server;
import mahjong.player.PlayerStore;
import mahjong.replay.ReplayStore;
import mahjong.test.SelfTest;
import mahjong.util.Log;

/** 服务端入口。 */
public final class Main {

    private Main() {
    }

    public static void main(String[] args) throws Exception {
        String host = "0.0.0.0";
        int port = 10086;
        boolean selftest = false;
        // 回放：**默认开启并落盘**（`./replays`）—— 需求是"重启后还能看"。
        // 容量双上限（场数 / 总字节）由 ReplayStore 强制，超出淘汰最旧的并删文件。
        boolean replay = true;
        String replayDir = "replays";
        int replayMax = 50;
        long replayMaxMb = 96;
        // 玩家档案（uuid）：同样**默认开启并落盘**（`./players`）。
        // TTL 是需求里的"2 个月"，可用 `--uuid-ttl-days` 覆盖（0 = 只留本进程登录过的，便于自检/运维）。
        boolean playerStore = true;
        String playerDir = "players";
        long uuidTtlDays = PlayerStore.DEFAULT_TTL_MS / 86400000L;
        // ---- 训练接口（自对弈）：见 mahjong.train.SelfPlay
        int selfplay = -1;
        long selfplaySeed = 20260101L;
        int selfplayWorkers = 0;
        String selfplayPolicy = "teacher,teacher,teacher,teacher";
        boolean selfplayRotate = false;
        String selfplayOut = null;
        int selfplaySample = 1;
        boolean selfplayClaims = true;
        int selfplayHands = 0;
        boolean selfplayTeacherLabel = false;
        String preset = null;
        // ---- 训练接口（派生特征富化）：见 mahjong.train.TraceFeatures
        String featuresDir = null;
        // ---- 机器人用哪一代 AI：见 mahjong.ai.BotAis（服务端白名单，客户端只选名字）
        String botAiDefault = null;
        java.util.List<String> botAiReg = new java.util.ArrayList<>();
        java.util.List<String> botAiDirs = new java.util.ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--port":
                case "-p":
                    if (i + 1 < args.length) {
                        port = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--host":
                    if (i + 1 < args.length) {
                        host = args[++i];
                    }
                    break;
                case "--verbose":
                case "-v":
                    Log.verbose = true;
                    break;
                case "--replay-dir":
                    if (i + 1 < args.length) {
                        replayDir = args[++i];
                    }
                    break;
                case "--replay-max":
                    if (i + 1 < args.length) {
                        replayMax = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--replay-max-mb":
                    if (i + 1 < args.length) {
                        replayMaxMb = Long.parseLong(args[++i]);
                    }
                    break;
                case "--no-replay":
                    replay = false;
                    break;
                case "--player-dir":
                    if (i + 1 < args.length) {
                        playerDir = args[++i];
                    }
                    break;
                case "--uuid-ttl-days":
                    if (i + 1 < args.length) {
                        uuidTtlDays = Long.parseLong(args[++i]);
                    }
                    break;
                case "--no-player-store":
                    playerStore = false;
                    break;
                case "--fast":
                    // 自动化测试用：机器人不思考、局间不停顿（对局几秒钟跑完）
                    mahjong.game.Table.DEFAULT_BOT_DELAY_MS = 0;
                    mahjong.game.Table.DEFAULT_ROUND_DELAY_MS = 0;
                    break;
                case "--selftest":
                case "--test":
                    selftest = true;
                    break;
                case "--selfplay":
                    // 训练接口：无网络自对弈 / 评测（4 个机器人座位，策略可注入）
                    if (i + 1 < args.length) {
                        selfplay = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--features":
                    // 训练接口：给轨迹目录补派生特征 sidecar（`g*.feat.bin`），轨迹本身不动
                    if (i + 1 < args.length) {
                        featuresDir = args[++i];
                    }
                    break;
                case "--seed":
                    if (i + 1 < args.length) {
                        selfplaySeed = Long.parseLong(args[++i]);
                    }
                    break;
                case "--workers":
                    if (i + 1 < args.length) {
                        selfplayWorkers = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--policy":
                    if (i + 1 < args.length) {
                        selfplayPolicy = args[++i];
                    }
                    break;
                case "--rotate":
                    selfplayRotate = true;
                    break;
                case "--out":
                    if (i + 1 < args.length) {
                        selfplayOut = args[++i];
                    }
                    break;
                case "--sample":
                    if (i + 1 < args.length) {
                        selfplaySample = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--no-claims":
                    selfplayClaims = false;
                    break;
                case "--teacher-label":
                    // DAgger：对学生座位额外记一次老师的动作（`teacher` / `teacher_index`）
                    selfplayTeacherLabel = true;
                    break;
                case "--hands":
                    if (i + 1 < args.length) {
                        selfplayHands = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--preset":
                    if (i + 1 < args.length) {
                        preset = args[++i];
                    }
                    break;
                case "--bot-ai":
                    // 默认机器人 AI（注册表里的名字）：房间不指定 `bot_ai` 时用它
                    if (i + 1 < args.length) {
                        botAiDefault = args[++i];
                    }
                    break;
                case "--bot-ai-reg":
                    // 注册一个可选 AI：`<名字>=<策略串>`（可重复；名字会出现在客户端的下拉框里）
                    if (i + 1 < args.length) {
                        botAiReg.add(args[++i]);
                    }
                    break;
                case "--bot-ai-dir":
                    // 扫描目录：每个含 net.bin 的一级子目录按**目录名**注册（各代 checkpoint）
                    if (i + 1 < args.length) {
                        botAiDirs.add(args[++i]);
                    }
                    break;
                case "--help":
                case "-h":
                    printHelp();
                    return;
                default:
                    Log.warn("未知参数: " + args[i]);
                    break;
            }
        }
        if (selftest) {
            int code = SelfTest.run();
            System.exit(code);
        }
        // 机器人 AI 注册表：**启动期一次建好并校验**（权重坏了/名字写错都在这里炸，
        // 而不是"开了房间打到一半才发现机器人不动"）。自对弈不装它（那边用 `--policy`）。
        if (selfplay < 0 && featuresDir == null) {
            try {
                mahjong.ai.BotAis.clear();
                for (String dir : botAiDirs) {
                    mahjong.ai.BotAis.scanDir(java.nio.file.Path.of(dir));
                }
                for (String kv : botAiReg) {
                    int eq = kv.indexOf('=');
                    if (eq <= 0 || eq == kv.length() - 1) {
                        throw new IllegalArgumentException("--bot-ai-reg 要写成 <名字>=<策略串>：" + kv);
                    }
                    mahjong.ai.BotAis.register(kv.substring(0, eq), kv.substring(eq + 1));
                }
                if (botAiDefault != null) {
                    mahjong.ai.BotAis.setDefault(botAiDefault);
                }
                Log.info("机器人 AI：" + String.join(", ", mahjong.ai.BotAis.names())
                        + "（默认 " + mahjong.ai.BotAis.defaultAi() + "）");
            } catch (RuntimeException e) {
                Log.error("机器人 AI 配置有误", e);
                System.exit(2);
                return;
            }
        }
        if (selfplay >= 0) {            // 自对弈**不装回放库**：训练局没必要占 replay 配额（ReplayStore.current() 为 null
            // 时 Table 的录制开销为零）。
            mahjong.train.SelfPlay.Config cfg = new mahjong.train.SelfPlay.Config();
            cfg.games = selfplay;
            cfg.seedBase = selfplaySeed;
            if (selfplayWorkers > 0) {
                cfg.workers = selfplayWorkers;
            }
            cfg.seatPolicy = selfplayPolicy.split(",");
            cfg.rotatePolicies = selfplayRotate;
            cfg.outDir = selfplayOut;
            cfg.sampleEvery = selfplaySample;
            cfg.recordClaims = selfplayClaims;
            cfg.maxHands = selfplayHands;
            cfg.teacherLabel = selfplayTeacherLabel;
            cfg.preset = preset;
            Log.quiet = !Log.verbose;
            mahjong.train.SelfPlay.Summary sum;
            try {
                sum = mahjong.train.SelfPlay.run(cfg);
            } catch (RuntimeException e) {
                Log.error("自对弈失败", e);
                System.exit(2);
                return;
            }
            System.out.print(mahjong.train.SelfPlay.format(sum));
            mahjong.train.SelfPlay.writeSummary(sum, selfplayOut);
            if (selfplayOut != null) {
                System.out.println("轨迹目录：" + java.nio.file.Path.of(selfplayOut).toAbsolutePath());
            }
            System.exit(0);
        }
        if (featuresDir != null) {
            // 离线富化：给轨迹补派生特征 sidecar（`g*.feat.bin`）—— 轨迹格式不变，可重跑可缓存
            Log.quiet = !Log.verbose;
            try {
                mahjong.train.TraceFeatures.Report rep =
                        mahjong.train.TraceFeatures.run(featuresDir, selfplayWorkers);
                System.out.print(rep);
            } catch (java.io.IOException e) {
                Log.error("派生特征富化失败", e);
                System.exit(2);
                return;
            }
            System.exit(0);
        }
        ReplayStore.install(new ReplayStore(Path.of(replayDir), replayMax,
                replayMaxMb * 1024 * 1024, replay));
        if (replay) {
            Log.info("回放库：" + Path.of(replayDir).toAbsolutePath()
                    + "（最多 " + replayMax + " 场 / " + replayMaxMb + " MB）");
        } else {
            Log.info("回放已关闭（--no-replay）");
        }
        // 玩家档案：装库 + **启动时清一次过期**（超过 TTL 没登录的），之后每 6 小时一次。
        // 维护线程是守护线程：它只做"清理 + 补落盘"，进程退出时不该被它拖住。
        PlayerStore store = new PlayerStore(Path.of(playerDir),
                uuidTtlDays * 86400000L, playerStore);
        PlayerStore.install(store);
        if (playerStore) {
            int purged = store.purge(System.currentTimeMillis());
            Log.info("玩家档案：" + store.summary() + (purged > 0 ? "，启动时清理 " + purged + " 份" : ""));
            Thread upkeep = new Thread(() -> {
                long lastPurge = System.currentTimeMillis();
                while (true) {
                    try {
                        Thread.sleep(60_000);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                    store.flush();      // 节流落盘的补写
                    if (System.currentTimeMillis() - lastPurge >= 6 * 3600_000L) {
                        lastPurge = System.currentTimeMillis();
                        store.purge(lastPurge);
                    }
                }
            }, "player-upkeep");
            upkeep.setDaemon(true);
            upkeep.start();
        } else {
            Log.info("玩家档案已关闭（--no-player-store）：uuid 握手照常，只是不落盘");
        }
        Server server = new Server(host, port);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            store.flush();          // 退出前把节流掉的档案补上
            server.stop();
        }, "shutdown"));
        server.start();
    }

    private static void printHelp() {
        System.out.println("立直麻将服务端");
        System.out.println("用法: java -jar mahjong-server.jar [选项]");
        System.out.println("  --port <n>         监听端口（默认 10086）");
        System.out.println("  --host <addr>      监听地址（默认 0.0.0.0）");
        System.out.println("  --verbose          输出调试日志");
        System.out.println("  --replay-dir <dir> 对局记录目录（默认 replays）");
        System.out.println("  --replay-max <n>   最多保留多少场（默认 50，超出淘汰最旧）");
        System.out.println("  --replay-max-mb <n> 记录总字节上限（默认 96 MB）");
        System.out.println("  --no-replay        不记录对局");
        System.out.println("  --player-dir <dir> 玩家档案目录（uuid → 昵称/登录时间，默认 players）");
        System.out.println("  --uuid-ttl-days <n> 多久没登录就清理档案（默认 60 = 2 个月）");
        System.out.println("  --no-player-store  不落盘玩家档案（uuid 握手照常，只是服务端不记得人）");
        System.out.println("  --fast             机器人不思考、局间不停顿（自动化测试用）");
        System.out.println("  --selftest         运行规则引擎自测后退出");
        System.out.println("  --help             显示帮助");
        System.out.println();
        System.out.println("机器人 AI（房间可以选\"机器人用哪一代\"，客户端只传**名字**）：");
        System.out.println("  --bot-ai <名字>        默认用哪个（缺省 teacher = 内置牌效）");
        System.out.println("  --bot-ai-reg <名=串>   注册一个可选 AI，可重复；串 = teacher|first|pass|random"
                + "|net:<权重文件>[@<α>][#<T>]");
        System.out.println("  --bot-ai-dir <目录>    扫描目录：每个含 net.bin 的一级子目录按目录名注册"
                + "（各代 checkpoint）");
        System.out.println();
        System.out.println("训练接口（无网络自对弈 / 评测）：");
        System.out.println("  --selfplay <n>     跑 n 场半庄（4 个机器人座位；不监听端口）");
        System.out.println("  --seed <n>         基准种子（默认 20260101；同种子逐事件可复现）");
        System.out.println("  --workers <k>      并行线程数（默认 = CPU 核数；不影响结果）");
        System.out.println("  --policy a,b,c,d   四家策略：teacher|first|pass|random|net:<权重文件>[@<α>][#<T>]"
                + "（默认全 teacher）");
        System.out.println("  --rotate           按局轮转座位（评测用：每个策略把四个座位都坐一遍）");
        System.out.println("  --out <dir>        轨迹输出（每场 g<序号>.jsonl + summary.json）");
        System.out.println("  --sample <k>       每 k 次决策记 1 条（默认 1 = 全记）");
        System.out.println("  --no-claims        不记录鸣牌决策");
        System.out.println("  --hands <n>        每场最多打 n 个小局（0 = 完整半庄；冒烟测试用）");
        System.out.println("  --preset <name>    规则预设：mleague|tenhou|majsoul|custom");
        System.out.println("  --teacher-label    DAgger：对学生座位额外记一次老师的动作（teacher/teacher_index）");
        System.out.println("  --features <dir>   给轨迹目录补**派生特征** sidecar（g*.feat.bin；轨迹本身不动）");
    }
}
