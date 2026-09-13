package mahjong;

import mahjong.net.Server;
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
        Server server = new Server(host, port);
        Runtime.getRuntime().addShutdownHook(new Thread(server::stop, "shutdown"));
        server.start();
    }

    private static void printHelp() {
        System.out.println("立直麻将服务端");
        System.out.println("用法: java -jar mahjong-server.jar [选项]");
        System.out.println("  --port <n>     监听端口（默认 10086）");
        System.out.println("  --host <addr>  监听地址（默认 0.0.0.0）");
        System.out.println("  --verbose      输出调试日志");
        System.out.println("  --selftest     运行规则引擎自测后退出");
        System.out.println("  --help         显示帮助");
    }
}
