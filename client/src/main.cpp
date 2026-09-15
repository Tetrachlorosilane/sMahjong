// 立直麻将 Qt6 客户端入口
//   mahjong-client.exe                     正常启动（大厅）
//   mahjong-client.exe --selftest [outdir] 自检模式（不连网，出图 + 单元测试）
//   mahjong-client.exe --autoplay <host> <port> [--name 名] [--timeout 秒]
//                                          联调自走模式：真连服务端打一场东风战

#include "AutoPlay.h"
#include "FontProbe.h"
#include "GenTiles.h"
#include "SelfTest.h"
#include "i18n/Lang.h"
#include "model/Settings.h"
#include "model/Theme.h"
#include "ui/MainWindow.h"
#include "ui/ReplayWindow.h"

#include <QApplication>
#include <QCoreApplication>
#include <QDebug>
#include <QFile>
#include <QFont>
#include <QFontDatabase>
#include <QString>
#include <QPushButton>
#include <QStringList>
#include <QTextStream>

#ifdef Q_OS_WIN
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <cstdio>
#endif

namespace {

// GUI 子系统程序默认没有 stdout；自检时挂到父进程控制台，便于把结果打出来。
// 但如果 stdout 已被重定向到文件/管道（脚本或 CI 调用），就**不要**抢过来改成控制台，
// 否则 `mahjong-client.exe ... > out.txt` 只会得到空文件。
void attachParentConsole()
{
#ifdef Q_OS_WIN
    const HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
    const DWORD type = h ? GetFileType(h) : FILE_TYPE_UNKNOWN;
    if (type == FILE_TYPE_DISK || type == FILE_TYPE_PIPE)
        return; // 已重定向，保持原样
    if (AttachConsole(ATTACH_PARENT_PROCESS)) {
        FILE* f = nullptr;
        freopen_s(&f, "CONOUT$", "w", stdout);
        freopen_s(&f, "CONOUT$", "w", stderr);
    }
#endif
}

} // namespace

/**
 * 命令行整数参数：解析 + 夹到 [lo, hi]，解析失败回退 fallback。
 *
 * <p>**必须**校验：`--after -5` 会得到负的 QTimer 间隔，Qt 直接不启动计时器 ——
 * `--shot` 永远不出图、进程就那么挂着；`--after 99999999` 还会让 `after * 1000`
 * 整数溢出。日志走 qWarning（不进语言文件）。
 */
int boundedIntArg(const QString& text, int lo, int hi, int fallback, const char* what)
{
    bool ok = false;
    const int v = text.toInt(&ok);
    if (!ok) {
        qWarning("mahjong: %s 参数 \"%s\" 不是整数，回退到 %d", what, qUtf8Printable(text),
                 fallback);
        return fallback;
    }
    const int c = qBound(lo, v, hi);
    if (c != v)
        qWarning("mahjong: %s 参数 %d 超出 [%d, %d]，已夹到 %d", what, v, lo, hi, c);
    return c;
}

int main(int argc, char* argv[])
{
    attachParentConsole();

    QApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("mahjong-client"));
    QCoreApplication::setApplicationVersion(QStringLiteral("1.4.0"));

    const QStringList args = QCoreApplication::arguments();

    // 语言文件：**所有用户可见文案**都从这里取（协议里只传 ASCII 码）。
    //   --lang <code>  >  环境变量 DSH_MAHJONG_LANG  >  zh_CN
    // 必须**尽早**载入 —— 任何一种模式（含 --selftest、--autoplay 的日志）都会用到文案。
    // 载入失败不致命：t() 会原样返回 key（界面看得出「没翻译」，但不会崩）。
    {
        QString langCode;
        const int li = args.indexOf(QStringLiteral("--lang"));
        if (li >= 0 && li + 1 < args.size() && !args.at(li + 1).startsWith(QLatin1Char('-')))
            langCode = args.at(li + 1);
        if (!lang::load(langCode)) {
            fprintf(stderr, "warning: 语言文件载入失败（locale=%s），文案将显示为 key\n",
                    qUtf8Printable(langCode.isEmpty() ? QStringLiteral("zh_CN") : langCode));
            fflush(stderr);
        }
    }

    // ---- 个人设置 + 材质包（**尽早**：字体要在任何界面创建之前设好）----
    //   · 设置文件坏/缺 → `Settings::load()` 自己用缺省值**重新生成**，这里不会失败；
    //   · 材质包坏/缺 → 只回退默认素材，设置里那条路径原样保留（用户修好再点「重新载入」）。
    const QString settingsPath = Settings::defaultPath();
    QStringList repairedKeys;
    QString settingsNote;
    const Settings settings = Settings::load(settingsPath, &repairedKeys, &settingsNote);
    if (!settingsNote.isEmpty() || !repairedKeys.isEmpty()) {
        fprintf(stderr, "settings: %s%s%s\n", qUtf8Printable(settingsNote),
                repairedKeys.isEmpty() ? "" : " repaired=",
                qUtf8Printable(repairedKeys.join(QStringLiteral(","))));
        fflush(stderr);
    }
    {
        const Theme::Status ts = Theme::instance().load(settings.pack);
        if (!settings.pack.isEmpty() && !ts.problems.isEmpty()) {
            fprintf(stderr, "theme: %s\n", qUtf8Printable(ts.problems.join(QStringLiteral(", "))));
            fflush(stderr);
        }
        // UI 字体（结算用的麻将字体不在此列，它由 TileFont 单独加载）
        if (!ts.loaded || Theme::instance().fontData().isEmpty()) {
            // 没有材质包字体：什么都不做（用系统默认），保持既有外观
        } else {
            const int id = QFontDatabase::addApplicationFontFromData(Theme::instance().fontData());
            const QStringList fams = id >= 0 ? QFontDatabase::applicationFontFamilies(id) : QStringList();
            if (!fams.isEmpty()) {
                QFont f = QApplication::font();
                f.setFamily(fams.first());
                QApplication::setFont(f);
            }
        }
    }

    // --fontprobe <out.png> [fontpath]
    // 用指定字体渲染一批候选输入串，实测该字体的连字（liga）输入语法。
    const int fp = args.indexOf(QStringLiteral("--fontprobe"));
    if (fp >= 0) {
        const QString outPng = (fp + 1 < args.size() && !args.at(fp + 1).startsWith(QLatin1Char('-')))
                ? args.at(fp + 1)
                : QCoreApplication::applicationDirPath() + QStringLiteral("/fontprobe.png");
        QString fontPath = (fp + 2 < args.size() && !args.at(fp + 2).startsWith(QLatin1Char('-')))
                ? args.at(fp + 2)
                : QString();
        if (fontPath.isEmpty()) {
            const QStringList cands = {
                QStringLiteral("C:/Users/HP/AppData/Local/Microsoft/Windows/Fonts/I.MahjongJP.otf"),
                QStringLiteral("C:/Windows/Fonts/I.MahjongJP.otf"),
            };
            for (const QString& c : cands) {
                if (QFile::exists(c)) {
                    fontPath = c;
                    break;
                }
            }
        }
        if (fontPath.isEmpty()) {
            QTextStream(stdout) << "找不到 I.MahjongJP.otf，请显式传入字体路径\n";
            return 1;
        }
        // 可选第 3 个参数：探针轮次（1 = 基础总览，2 = 组合符放大）
        int suite = 1;
        if (fp + 3 < args.size()) {
            bool ok = false;
            const int v = args.at(fp + 3).toInt(&ok);
            if (ok) {
                suite = v;
            }
        }
        return runFontProbe(outPng, fontPath, suite);
    }

    // --gentiles <outdir> [fontpath]
    // 用本机字体把 Unicode 麻将牌字形轮廓化成 SVG 素材（上色交给美术）。
    const int gt = args.indexOf(QStringLiteral("--gentiles"));
    if (gt >= 0) {
        const QString outDir = (gt + 1 < args.size() && !args.at(gt + 1).startsWith(QLatin1Char('-')))
                ? args.at(gt + 1)
                : QCoreApplication::applicationDirPath() + QStringLiteral("/tiles-out");
        // 缺省字体：本机安装的 I.MahjongJP.otf（用户级字体目录）
        QString fontPath = (gt + 2 < args.size() && !args.at(gt + 2).startsWith(QLatin1Char('-')))
                ? args.at(gt + 2)
                : QString();
        if (fontPath.isEmpty()) {
            const QStringList cands = {
                QStringLiteral("C:/Users/HP/AppData/Local/Microsoft/Windows/Fonts/I.MahjongJP.otf"),
                QStringLiteral("C:/Windows/Fonts/I.MahjongJP.otf"),
            };
            for (const QString& c : cands) {
                if (QFile::exists(c)) {
                    fontPath = c;
                    break;
                }
            }
        }
        if (fontPath.isEmpty()) {
            QTextStream(stdout) << "找不到 I.MahjongJP.otf，请显式传入字体路径\n";
            return 1;
        }
        return generateTileSvgs(outDir, fontPath);
    }

    const int idx = args.indexOf(QStringLiteral("--selftest"));
    if (idx >= 0) {
        QString outDir;
        if (idx + 1 < args.size() && !args.at(idx + 1).startsWith(QLatin1Char('-')))
            outDir = args.at(idx + 1);
        else
            outDir = QCoreApplication::applicationDirPath() + QStringLiteral("/selftest");
        return selftest::run(outDir);
    }

    const int ap = args.indexOf(QStringLiteral("--autoplay"));
    if (ap >= 0) {
        QString host = QStringLiteral("127.0.0.1");
        quint16 port = 10086;
        QString name = QStringLiteral("Qt-自走");   // i18n-keep: 默认玩家名（用户数据）
        QString logPath = QCoreApplication::applicationDirPath() + QStringLiteral("/autoplay.log");
        int timeoutSec = 240;
        const QStringList rest = args.mid(ap + 1);
        int positional = 0;
        for (int i = 0; i < rest.size(); ++i) {
            const QString& a = rest.at(i);
            if (a == QLatin1String("--name") && i + 1 < rest.size()) {
                name = rest.at(++i);
            } else if (a == QLatin1String("--timeout") && i + 1 < rest.size()) {
                // 1…3600 秒：超时秒数最终会乘 1000 交给 QTimer，非法值会让计时器不启动
                // （autoplay 就永远挂着不退出）。
                timeoutSec = boundedIntArg(rest.at(++i), 1, 3600, timeoutSec, "--timeout");
            } else if (a == QLatin1String("--log") && i + 1 < rest.size()) {
                logPath = rest.at(++i);
            } else if (!a.startsWith(QLatin1Char('-'))) {
                if (positional == 0)
                    host = a;
                else if (positional == 1)
                    port = static_cast<quint16>(a.toUShort());
                ++positional;
            }
        }
        AutoPlay play(host, port, name, timeoutSec, logPath);
        QObject::connect(&play, &AutoPlay::finished, &app, &QCoreApplication::quit);
        QTimer::singleShot(0, &play, &AutoPlay::start);
        app.exec();
        return play.exitCode();
    }

    // ---- 回放模式：直接打开回放窗口（可选带 replay id，不带则显示列表）----
    //   mahjong-client.exe --replay <host> <port> [replay-id] [--wall] [--god] [--step N]
    //                       [--result] [--shot png] [--after 秒]
    // 用途：① 用户命令行看回放；② L4 用 `--shot` 出回放界面 / 牌山 / 上帝视角 / 本局结算的实拍图。
    const int rp = args.indexOf(QStringLiteral("--replay"));
    if (rp >= 0) {
        QString host = QStringLiteral("127.0.0.1");
        quint16 port = 10086;
        QString replayId;
        const QStringList rest = args.mid(rp + 1);
        int positional = 0;
        for (const QString& a : rest) {
            if (a.startsWith(QLatin1Char('-')))
                break;
            if (positional == 0)
                host = a;
            else if (positional == 1)
                port = static_cast<quint16>(a.toUShort());
            else if (positional == 2)
                replayId = a;
            ++positional;
        }
        auto* win = new ReplayWindow();
        win->resize(1200, 800);
        win->show();
        win->openReplay(host, port, replayId);

        // 这些开关都要等"记录真的载入完了"才有意义（`replayLoaded` 在 `finishLoad()` 末尾发）。
        // 另配一个兜底定时器：载入失败（服务端没有这条记录）时也要把已请求的开关落下去，
        // 否则截图脚本会一直等到 `--after` 才退，白等一场。
        const bool wantWall = args.contains(QStringLiteral("--wall"));
        const bool wantGod = args.contains(QStringLiteral("--god"));
        const bool wantResult = args.contains(QStringLiteral("--result"));
        // `--export-tenhou <out.txt>`：载入完就把天鳳牌譜导出到文件，然后退出
        //（L4/L3 都能用它做"真路径"验证，不必手点界面）
        const int exIdx = args.indexOf(QStringLiteral("--export-tenhou"));
        const QString exportPath = (exIdx >= 0 && exIdx + 1 < args.size())
                                           ? args.at(exIdx + 1)
                                           : QString();
        const int stepIdx = args.indexOf(QStringLiteral("--step"));
        const int wantStep = (stepIdx >= 0 && stepIdx + 1 < args.size())
                                     ? args.at(stepIdx + 1).toInt()
                                     : -1;
        auto* fallback = new QTimer(win);
        fallback->setSingleShot(true);
        auto apply = [win, fallback, wantWall, wantGod, wantResult, wantStep, exportPath]() {
            fallback->stop();   // 只应用一次（载入成功与兜底定时器谁先到算谁）
            if (!exportPath.isEmpty()) {
                QString err;
                const QString wrote = win->exportTenhou(exportPath, &err);
                if (wrote.isEmpty()) {
                    fprintf(stderr, "TENHOU EXPORT FAILED: %s\n", qUtf8Printable(err));
                } else {
                    fprintf(stdout, "TENHOU EXPORT: %s\n", qUtf8Printable(wrote));
                    fflush(stdout);
                }
                QCoreApplication::quit();
                return;
            }
            if (wantStep >= 0) {
                win->seekStep(wantStep);
            }
            if (wantGod) {
                win->setGodMode(true);
            }
            if (wantResult) {
                win->openRoundResult();
            }
            if (wantWall) {
                win->showWall();
            }
        };
        QObject::connect(win, &ReplayWindow::replayLoaded, win, apply);
        QObject::connect(fallback, &QTimer::timeout, win, apply);
        fallback->start(4000);

        const int shotIdx = args.indexOf(QStringLiteral("--shot"));
        if (shotIdx >= 0 && shotIdx + 1 < args.size()) {
            const QString path = args.at(shotIdx + 1);
            int afterMs = 6000;
            const int ai = args.indexOf(QStringLiteral("--after"));
            if (ai >= 0 && ai + 1 < args.size()) {
                afterMs = args.at(ai + 1).toInt() * 1000;
            }
            QTimer::singleShot(afterMs, win, [win, path]() {
                // 与 --demo 同一套做法：抓**活动顶层窗口**（回放窗口/牌山窗口都是顶层）
                QWidget* target = QApplication::activeWindow();
                if (target == nullptr) {
                    target = win;
                }
                target->grab().save(path);
                fprintf(stdout, "REPLAY SHOT: %s\n", qPrintable(path));
                fflush(stdout);
                QCoreApplication::quit();
            });
        }
        return qApp->exec();
    }

    // ---- UI 回归测试：真的去点大厅里的「建房间」按钮 ----
    //   mahjong-client.exe --lobbytest <host> <port>
    // 退出码 0 = 连接后按钮可用且成功建房；1/2 = 失败
    const int lt = args.indexOf(QStringLiteral("--lobbytest"));
    if (lt >= 0) {
        QString host = QStringLiteral("127.0.0.1");
        quint16 port = 10086;
        const QStringList rest = args.mid(lt + 1);
        int positional = 0;
        for (const QString& a : rest) {
            if (a.startsWith(QLatin1Char('-')))
                continue;
            if (positional == 0)
                host = a;
            else if (positional == 1)
                port = static_cast<quint16>(a.toUShort());
            ++positional;
        }
        MainWindow window;
        window.applySettings(settings, settingsPath);
        window.resize(1600, 1000);
        window.show();

        // t=0：连接前先记录按钮状态（预期：灰的）
        QTimer::singleShot(300, &window, [&window]() {
            QPushButton* create = nullptr;
            for (QPushButton* b : window.findChildren<QPushButton*>()) {
                if (b->text() == lang::t("ui.lobby.create_room"))
                    create = b;
            }
            if (!create) {
                fprintf(stdout, "LOBBYTEST FAIL: 大厅里找不到「建房间」按钮\n");
                fflush(stdout);
                QCoreApplication::exit(2);
                return;
            }
            fprintf(stdout, "LOBBYTEST [连接前] 「建房间」enabled=%d（预期 0：未连接时置灰）\n",
                    int(create->isEnabled()));
            fflush(stdout);
        });
        // t=1500：真正发起连接（bots=0 → 不会自动建房，必须靠 UI 按钮）
        QTimer::singleShot(1500, &window, [&window, host, port]() {
            window.autoStart(host, port, QStringLiteral("UI测试"), 0);   // i18n-keep: 默认玩家名（用户数据）
        });
        // t=7000：连接后必须可用，然后真的点它
        QTimer::singleShot(7000, &window, [&window]() {
            QPushButton* create = nullptr;
            for (QPushButton* b : window.findChildren<QPushButton*>()) {
                if (b->text() == lang::t("ui.lobby.create_room"))
                    create = b;
            }
            if (!create) {
                fprintf(stdout, "LOBBYTEST FAIL: 找不到「建房间」按钮\n");
                fflush(stdout);
                QCoreApplication::exit(2);
                return;
            }
            fprintf(stdout, "LOBBYTEST [连接后] 「建房间」enabled=%d（必须为 1）\n",
                    int(create->isEnabled()));
            if (!create->isEnabled()) {
                fprintf(stdout, "LOBBYTEST FAIL: 连接成功后按钮仍是灰的 —— 用户点不动\n");
                fflush(stdout);
                QCoreApplication::exit(1);
                return;
            }
            fprintf(stdout, "LOBBYTEST 点击「建房间」…\n");
            fflush(stdout);
            create->click();
        });
        // t=14000：应当已进入等待室（窗口标题带房间号）
        QTimer::singleShot(14000, &window, [&window]() {
            const QString title = window.windowTitle();
            if (title.contains(lang::t("ui.main.room"))) {
                fprintf(stdout, "LOBBYTEST PASS: 建房成功，标题 = %s\n", qUtf8Printable(title));
                fflush(stdout);
                QCoreApplication::exit(0);
            } else {
                fprintf(stdout, "LOBBYTEST FAIL: 没进房间，标题 = %s\n", qUtf8Printable(title));
                fflush(stdout);
                QCoreApplication::exit(1);
            }
        });
        return app.exec();
    }

    // 演示 / 联调：--demo <host> <port> [--name 名] [--bots N]
    const int dm = args.indexOf(QStringLiteral("--demo"));
    MainWindow window;
    window.applySettings(settings, settingsPath);
    window.show();
    if (dm >= 0) {
        QString host = QStringLiteral("127.0.0.1");
        quint16 port = 10086;
        QString name = QStringLiteral("Qt-演示");   // i18n-keep: 默认玩家名（用户数据）
        int bots = 3;
        bool noAnswer = false;
        const QStringList rest = args.mid(dm + 1);
        int positional = 0;
        for (int i = 0; i < rest.size(); ++i) {
            const QString& a = rest.at(i);
            if (a == QLatin1String("--name") && i + 1 < rest.size())
                name = rest.at(++i);
            else if (a == QLatin1String("--bots") && i + 1 < rest.size())
                bots = rest.at(++i).toInt();
            else if (a == QLatin1String("--no-answer"))
                noAnswer = true;
            else if (!a.startsWith(QLatin1Char('-'))) {
                if (positional == 0)
                    host = a;
                else if (positional == 1)
                    port = static_cast<quint16>(a.toUShort());
                ++positional;
            }
        }
        QTimer::singleShot(0, &window, [&window, host, port, name, bots, noAnswer]() {
            window.autoStart(host, port, name, bots);
            window.setAutoAnswer(!noAnswer && bots > 0);
        });
        // --shot <png> [--after 秒]：到时把窗口原样渲染成图片后退出（用于验收/文档配图）
        const int sh = args.indexOf(QStringLiteral("--shot"));
        if (sh >= 0 && sh + 1 < args.size()) {
            const QString path = args.at(sh + 1);
            int after = 12;
            const int ai = args.indexOf(QStringLiteral("--after"));
            if (ai >= 0 && ai + 1 < args.size())
                // 1…3600 秒（见 boundedIntArg：负值会让 QTimer 不启动，--shot 永远不出图）
                after = boundedIntArg(args.at(ai + 1), 1, 3600, after, "--after");
            window.resize(1600, 1000);
            QTimer::singleShot(after * 1000, &window, [&window, path]() {
                QWidget* target = &window;
                  // 结算/大厅等是**独立顶层窗口**，不在主窗口渲染树里，
                  // 直接 grab 主窗口拍不到它们 —— 优先拍当前活动顶层窗口。
                  if (QWidget* aw = QApplication::activeWindow()) {
                      if (aw != &window && aw->isVisible()) {
                          target = aw;
                      }
                  }
                  const bool ok = target->grab().save(path);
                fprintf(stdout, "SHOT %s %s\n", ok ? "OK" : "FAIL", qUtf8Printable(path));
                fflush(stdout);
                QCoreApplication::quit();
            });
        }
    }
    return app.exec();
}
