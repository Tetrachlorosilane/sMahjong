#pragma once

// 音效（吃 / 碰 / 杠 / 立直 / 自摸 / 荣和 / 提示 / 摸牌）。
//
// ## 素材从哪来
//
// WAV 是**离线合成**的：`node tools/gen-sfx.mjs` 写 `client/assets/sfx/<名字>.wav`
// （脚本在仓库里 ⇒ 可复现，改一个音符重跑即可，见 AGENTS §9.3）。加载顺序与牌面
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
//   ① `MAHJONG_HAVE_MULTIMEDIA=1`（本机 Qt 装了 Multimedia）→ `QSoundEffect`：
//      低延迟、可叠放、支持循环与音量，且**不需要 FFmpeg 那 20MB**（WAV 解码器内置）。
//   ② 否则 Windows → `winmm` 的 `PlaySound(SND_MEMORY|SND_ASYNC)`：零依赖、系统自带。
//   ③ 其余情况 → 静默（`available()` 返回 false，界面上的音效开关置灰）。
//
// 音效**只影响听感**，任何失败都不该影响对局 —— 所有接口都不抛异常、不阻塞。

#include <QByteArray>
#include <QHash>
#include <QObject>
#include <QString>

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
     * @param allowOverlap 允许叠放（同一个音效上次还没放完就再触发时）。默认允许：
     *        连续摸牌 / 连续鸣牌都要每次出声，被"正在播"挡掉会显得丢音。
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

private:
    Player();
    ~Player() override;

    /** 取某个音效的 WAV 字节（带缓存）；找不到返回空。 */
    const QByteArray& data(const QString& sfx);
    /** 按三档后端真正把字节放出去。 */
    void emitSound(const QString& sfx, const QByteArray& bytes);

    bool m_available = false;
    QString m_backend = QStringLiteral("none");
    bool m_enabled = true;
    int m_volume = 70;
    QHash<QString, QByteArray> m_cache;          // 名字 → WAV 字节
    QHash<QString, QSoundEffect*> m_effects;     // 名字 → QSoundEffect（仅 ① 档）
};

} // namespace sound
