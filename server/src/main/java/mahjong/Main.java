package mahjong;

import java.nio.file.Path;

import mahjong.net.Server;
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
                case "--fast":
                    // 自动化测试用：机器人不思考、局间不停顿（对局几秒钟跑完）
                    mahjong.game.Table.DEFAULT_BOT_DELAY_MS = 0;
                    mahjong.game.Table.DEFAULT_ROUND_DELAY_MS = 0;
                    break;
                case "--selftest":
                case "--test":
                    selftest = true;
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
        ReplayStore.install(new ReplayStore(Path.of(replayDir), replayMax,
                replayMaxMb * 1024 * 1024, replay));
        if (replay) {
            Log.info("回放库：" + Path.of(replayDir).toAbsolutePath()
                    + "（最多 " + replayMax + " 场 / " + replayMaxMb + " MB）");
        } else {
            Log.info("回放已关闭（--no-replay）");
        }
        Server server = new Server(host, port);
        Runtime.getRuntime().addShutdownHook(new Thread(server::stop, "shutdown"));
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
        System.out.println("  --fast             机器人不思考、局间不停顿（自动化测试用）");
        System.out.println("  --selftest         运行规则引擎自测后退出");
        System.out.println("  --help             显示帮助");
    }
}
