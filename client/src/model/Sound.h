#pragma once

// 音效（吃 / 碰 / 杠 / 立直 / 自摸 / 荣和 / 提示 / 摸牌）。
//
// ## 素材从哪来
//
// WAV 是**离线合成**的：`node tools/gen-sfx.mjs` 写 `client/assets/sfx/<名字>.wav`
// （脚本在仓库里 ⇒ 可复现，改一个音符重跑即可，见 NOTES §9.3）。加载顺序与牌面
// 素材同一套约定：**材质包 `sfx/` → exe 同级 `sfx/` → qrc → 静音**，所以：
//   · 换音效不用重新编译；
//   · 音效也在材质包的可替换范围内（用户要求，见 docs/THEME.md §2.2）。
//
// ## 为什么不用 MIDI 合成
//
// Qt 没有 MIDI 合成器（QtMultimedia 在 Windows 上只有 WMF/FFmpeg 后端，不能合成 MIDI）；
// 自带 SoundFont 合成器就得引第三方库，破坏本项目「客户端只依赖 Qt」这条原则。
// 所以音效是**预渲染的 PCM**，运行时只负责播放。
//
// ## 播放后端（按构建能力分层，缺哪层都不会编不过）
//
//   ① `MAHJONG_HAVE_MULTIMEDIA=1`（本机 Qt 装了 Multimedia）→ `QSoundEffect` 池：
//      低延迟、可叠放、支持循环与音量，且**不需要 FFmpeg 那 20MB**（WAV 解码器内置）。
//   ② 否则 Windows → `winmm` 的 `PlaySound(SND_MEMORY|SND_ASYNC)`：零依赖、系统自带。
//   ③ 其余情况 → 静默（`available()` 返回 false，界面上的音效开关置灰）。
//
// ⚠ **每个音效是一个小池子（`kPoolPerSound` 个 `QSoundEffect`），不再"一个对象反复
//   stop()+play()"**：报障「只有第一小局有声音」的现场实测是——客户端**每局都在调 play()**
//   （10 局 80 次，Qt 侧 status=Ready、play() 后 isPlaying()=1），所以触发链没问题；
//   剩下的可疑点就是同一个 `QSoundEffect` 被反复 stop/play 之后**声卡侧不再出声**
//   （Qt 不会报错，`isPlaying()` 照样是 true）。池子让"上一次还没放完"时换一个空闲实例，
//   绝大多数情况下**根本不需要 stop**；`allowOverlap=false` 时才真的跳过（见下）。
//
// ⚠⚠ **"还在播"不能只信 `QSoundEffect::isPlaying()`**（2026-09 二次报障：
//   「音效在有副露 / 在可副露时选择不副露之后消失」）。设备异常（驱动切换、睡眠唤醒、
//   独占占用）之后它会**永远为真**，而"上一次还在播就跳过"这条判据会因此把那条音效
//   **永久静音** —— 这正是"某个音效从此再也不响"的唯一机制（而且它在本地可能复现不出来：
//   本机压测 85 次播放里 `isPlaying()` 都能正常回落）。
//   所以内部记**每个实例本次开始播放的时刻**，并把"自称在播"与"WAV 时长"对账：
//     · 时长内 → 真在播；超出时长 + 余量 → 判为卡死，`stop()` 后**复用**（并计数/打日志）；
//   `allowOverlap=false` 的"别叠"只对**真在播**生效，卡死的实例不再有否决权。
//   池子真满时**放弃这一次**（不再 `stop()` 硬插 —— 那条路径正是最可疑的静音来源）。
//   诊断：`MAHJONG_SFX_TRACE=1` 会打出 `pool3(ready/playing/stale/err)` 与每个判定分支。
//
// 音效**只影响听感**，任何失败都不该影响对局 —— 所有接口都不抛异常、不阻塞。

#include <QByteArray>
#include <QHash>
#include <QObject>
#include <QString>
#include <QUrl>
#include <QVector>

class QSoundEffect;
class QTimer;

namespace sound {

/** 音效名（与 `client/assets/sfx/<name>.wav` 一一对应）。 */
namespace name {
inline constexpr const char* Chi = "chi";
inline constexpr const char* Pon = "pon";
inline constexpr const char* Kan = "kan";
inline constexpr const char* Riichi = "riichi";
inline constexpr const char* Tsumo = "tsumo";
inline constexpr const char* Ron = "ron";
inline constexpr const char* Notify = "notify";
inline constexpr const char* Draw = "draw";
} // namespace name

/** 全部音效名（自检与"预载"用）。 */
QStringList allNames();

/**
 * 音效播放器（进程内单例）。
 *
 * 初始化放在 `init()`（`main.cpp` 里尽早调用）：它只探测**可用性**与后端，
 * 真正的 WAV 读取是**首次播放时按需**做的（带缓存），避免启动时读 8 个文件。
 */
class Player : public QObject
{
    Q_OBJECT
public:
    static Player& instance();

    /** 探测后端 + 读取用户设置里的开关/音量。可重复调用（幂等）。 */
    void init();

    /** 本构建/本机能不能出声（三档后端的可用性结论）。 */
    bool available() const { return m_available; }
    /** 后端名字（自检与故障排查用）：`qsoundeffect` / `winmm` / `none`。 */
    QString backendName() const { return m_backend; }

    /** 总开关（玩家设置里的「音效」复选框）。关掉后 `play()` 直接返回。 */
    void setEnabled(bool on);
    bool enabled() const { return m_enabled; }

    /** 音量 0..100（线性）。 */
    void setVolume(int percent);
    int volume() const { return m_volume; }

    /**
     * 播一个音效；名字见 `sound::name::*`。
     *
     * @param allowOverlap 同一个音效**上一次还在响**时怎么办：
     *        - `true`（默认）：换池子里另一个空闲实例**叠放**（连续鸣牌/立直要每次都出声）；
     *        - `false`：这次**跳过**（摸牌音效用它 —— 每巡都响会很吵，而且旧实现
     *          「停掉再从头放」正是把声卡搞哑的那条路径）。
     */
    void play(const QString& sfx, bool allowOverlap = true);

    /** 立刻停掉所有正在播的音效（离开牌桌 / 关闭音效时用）。 */
    void stopAll();

    /** 清掉已缓存的 WAV（换材质包后调用，与 `TileRenderer::clearAssetCache()` 同一时机）。 */
    void clearCache();

    // ---- 自检用 ----
    /** 已成功载入的音效个数（探测加载链是否真的能找到素材）。 */
    int loadedCountForTest();
    /** 某个音效的 WAV 字节数（0 = 找不到）；用来断言"能取到素材"。 */
    int dataSizeForTest(const QString& sfx);
    /** 某个音效的 WAV 原始字节（自检拿去核对时长解析）。 */
    QByteArray dataForTest(const QString& sfx) { return data(sfx); }
    /**
     * 某个音效的 `QSoundEffect` 是否真的**接受了这份素材**（只有多媒体后端有意义）。
     *
     * <p>自检里 `play()` 之后查它：`QSoundEffect::status()` 会随着加载从 `Loading`
     * 变成 `Ready`（或 `Error`）。这是**无头环境下能拿到的最强证据** ——
     * 「文件找到了」不等于「后端认得它」（WAV 头写坏就会被这里抓住），
     * 但它仍然证明不了"扬声器真的响了"，那件事只能由人听。
     */
    bool effectReadyForTest(const QString& sfx);
    /** 某个音效当前是否在播（仅多媒体后端）。 */
    bool effectPlayingForTest(const QString& sfx);
    /** 池子里这个音效有几个实例（自检断言池子真的建起来了）。 */
    int poolSizeForTest(const QString& sfx) const;
    /** 某个音效**实际放出去**的次数（`allowOverlap=false` 被跳过的那些不算）。 */
    int playCountForTest(const QString& sfx) const;
    /** 因为"上一次还在播 + `allowOverlap=false`"而**跳过**的次数。 */
    int overlapSkipCountForTest() const { return m_overlapSkips; }
    /**
     * 因为某个实例**自称在播、但已经超过这个 WAV 可能的最长时长**（判定为卡死）
     * 而把它 `stop()` 后复用的次数。
     *
     * <p>为什么必须这么判：`QSoundEffect::isPlaying()` 在设备异常（驱动切换 / 睡眠唤醒 /
     * 独占占用）之后可能**永远为真**，而那正是"音效从此再也不响"的唯一机制 ——
     * 旧实现拿它当"还在播"的唯一判据，一个假 playing 就能把那条音效永久静音。
     */
    int stuckStopCountForTest() const { return m_stuckStops; }
    /** 池子里的实例**都在真播**、叠放口径下也只能放弃的次数（不再 `stop()` 硬插）。 */
    int exhaustedSkipCountForTest() const { return m_exhaustedSkips; }
    /**
     * 判定"整套音频栈哑掉"而**重建**的次数（用户报障：两个音效撞进竞态后全哑，
     * 连重开一局都不行、必须重启客户端）。见 `rebuildStack()`。
     */
    int rebuildCountForTest() const { return m_rebuilds; }
    /** 当前"连续几次 play 之后立刻 isPlaying=false"的计数（重建后会清零）。 */
    int consecutiveFailuresForTest() const { return m_consecutiveFailures; }
    /**
     * 自检用：手动触发一次"整套音频栈重建"。
     *
     * <p>⚠ 这条钩子存在的意义是钉住一个**极易复发**的坑：`rebuildStack()` 清空池子之后
     * **必须**立刻 `init()` 重建 —— 否则 `emitSound()` 开头那句 `pool.isEmpty() → return`
     * 会让此后**每一次**播放都静默返回，等于把"哑掉"换成"永久静音"（比原 bug 更难查）。
     * 判据写在池子大小上（见自检的音频栈重建组）。
     */
    void rebuildStackForTest() { rebuildStack(); }

    // ---- 诊断 ----
    /**
     * 某个音效的 `QSoundEffect` 状态文本（`Ready` / `Loading` / `Error` / `missing`）。
     *
     * <p>只在开了 `MAHJONG_SFX_TRACE=1` 时才被打印，用来定位"某一局之后就没声音"这类报障。
     */
    QString effectStateForTrace(const QString& sfx) const;

private:
    Player();
    ~Player() override;

    /** 取某个音效的 WAV 字节（带缓存）；找不到返回空。 */
    const QByteArray& data(const QString& sfx);
    /** 按三档后端真正把字节放出去。 */
    void emitSound(const QString& sfx, const QByteArray& bytes, bool allowOverlap);
    /**
     * 把整套音频栈**重建**（销毁所有 `QSoundEffect`、清掉计时；下次 play() 会按当前默认
     * 输出设备重新建池）。这条路径存在的唯一理由：设备被别的进程抢占 / 睡眠唤醒 / 后端
     * 撞竞态之后，`play()` 会**静默失败**（不报错、status 仍 Ready、isPlaying 立刻回落），
     * 而重建效果对象 + 重新解析默认设备能让它恢复 —— **不需要重启客户端**。
     */
    void rebuildStack();

    bool m_available = false;
    QString m_backend = QStringLiteral("none");
    bool m_enabled = true;
    int m_volume = 70;
    QHash<QString, QByteArray> m_cache;                // 名字 → WAV 字节
    QHash<QString, QVector<QSoundEffect*>> m_effects;  // 名字 → 实例池（仅 ① 档）
    QHash<QString, QUrl> m_source;                     // 名字 → 源 URL（Error 自愈要重设）
    QHash<QString, int> m_plays;                       // 名字 → 实际播放次数（自检）
    QHash<QString, int> m_durationMs;                  // 名字 → WAV 时长（判"自称在播"是否说谎）
    QHash<QString, QVector<qint64>> m_startedAt;       // 名字 → 每个实例本次开始播放的时刻
    int m_overlapSkips = 0;                            // 被 allowOverlap=false 跳过的次数
    int m_stuckStops = 0;                              // 判定某个实例"卡在 playing"并复用的次数
    int m_exhaustedSkips = 0;                          // 池子真满而放弃的次数
    int m_consecutiveFailures = 0;                     // 连续"play 之后立刻没在播"的次数
    int m_rebuilds = 0;                                // 整套音频栈被判哑掉并重建的次数
};

/**
 * WAV 时长（毫秒）：解析 RIFF 的 `fmt `/`data` 两块。不是合法 WAV 时返回 0
 * （调用方退回一个保守上限，绝不因为"解析不出来"就把实例当成永远在播）。
 */
int wavDurationMs(const QByteArray& wav);

/**
 * 挑实例的**纯判据**（自检直接调它，不依赖声卡）。
 *
 * @param playing  每个实例**自称**是否在播（`QSoundEffect::isPlaying()`）
 * @param ageMs    每个实例"已经播了多久"（毫秒；没播过 / 未知时给一个很大的值）
 * @param durMs    该音效的 WAV 时长（0 = 未知 → 用保守上限）
 * @return `>=0` 用这个实例（若 `playing[i]` 为真，说明它自称在播但其实早该结束 → 调用方先 `stop()`）；
 *         `-1` 放弃（`allowOverlap=false` 且确实还在响）；`-2` 放弃（池子都在真播，叠放口径下也不硬插）。
 */
int pickSlot(const QVector<bool>& playing, const QVector<qint64>& ageMs, int durMs,
             bool allowOverlap);

/**
 * 连续几次"`play()` 之后立刻 `isPlaying()==false`"就该把整套音频栈重建（纯判据，自检直接调）。
 *
 * <p>为什么以"立刻没在播"为准：后端哑掉时 `play()` **不报错**、`status()` 仍是 `Ready`，
 * 唯一能观测到的就是这一条。K 取 3：单次偶发（设备切换的那一瞬间）不该触发重建，
 * 但"两个音效撞进竞态之后全哑"这种持续性故障必须在下一次播放前就被兜住。
 */
bool shouldRebuildStack(int consecutiveFailures);

/** 连续多少次"按了没响"就重建（见 `shouldRebuildStack`）。 */
constexpr int kRebuildAfterFailures = 3;

/** 「自称在播」最多被容忍多久（时长未知时的兜底，以及给解码/调度留的余量）。 */
constexpr qint64 kStuckMarginMs = 800;
/** 时长未知时的保守上限：超过它就认为那个实例在说谎。 */
constexpr qint64 kUnknownDurationMs = 2000;

} // namespace sound
