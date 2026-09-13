package mahjong.util;

import java.time.LocalTime;
import java.time.format.DateTimeFormatter;

/** 极简日志。 */
public final class Log {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("HH:mm:ss.SSS");
    public static volatile boolean verbose = false;

    private Log() {
    }

    private static String ts() {
        return LocalTime.now().format(FMT);
    }

    public static void info(String msg) {
        System.out.println("[" + ts() + "] INFO  " + msg);
    }

    public static void debug(String msg) {
        if (verbose) {
            System.out.println("[" + ts() + "] DEBUG " + msg);
        }
    }

    public static void warn(String msg) {
        System.out.println("[" + ts() + "] WARN  " + msg);
    }

    public static void error(String msg) {
        System.err.println("[" + ts() + "] ERROR " + msg);
    }

    public static void error(String msg, Throwable t) {
        System.err.println("[" + ts() + "] ERROR " + msg + " :: " + t);
        if (verbose) {
            t.printStackTrace(System.err);
        }
    }
}
