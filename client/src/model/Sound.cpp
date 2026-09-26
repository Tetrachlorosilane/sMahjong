// `MAHJONG_HAVE_MULTIMEDIA` **总是**由 CMake 定义成 0/1（与 MAHJONG_HAVE_ZIP 同一约定），
// 所以这里一律用 `#if` 而不是 `#ifdef` —— 写成 `#ifdef` 时"定义了但是 0"也会走进
// Qt Multimedia 那条分支，于是没链 Multimedia 的构建会报 `QSoundEffect: No such file`。
#ifndef MAHJONG_HAVE_MULTIMEDIA
#define MAHJONG_HAVE_MULTIMEDIA 0
#endif

#include "model/Sound.h"

#include "i18n/Lang.h"
#include "model/Settings.h"
#include "model/Theme.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QTemporaryFile>
#include <QTimer>
#include <QUrl>

#include <cstdio>

#if MAHJONG_HAVE_MULTIMEDIA
#include <QAudioDevice>
#include <QMediaDevices>
#include <QSoundEffect>
#endif

#ifdef Q_OS_WIN
#include <windows.h>
#include <mmsystem.h>
#endif

namespace sound {
namespace {

/** 每个音效建几个 `QSoundEffect` 实例（叠放用；见 Sound.h 顶部说明）。 */
constexpr int kPoolPerSound = 3;

/** exe 同级 `sfx/<名字>.wav`。 */
QString besidePath(const QString& sfx)
{
    return QCoreApplication::applicationDirPath() + QStringLiteral("/sfx/") + sfx
            + QStringLiteral(".wav");
}

/** qrc 兜底 `:/sfx/<名字>.wav`。 */
QString resourcePath(const QString& sfx)
{
    return QStringLiteral(":/sfx/") + sfx + QStringLiteral(".wav");
}

/**
 * 按三档顺序找 WAV 字节：材质包 → exe 同级 → qrc。
 *
 * 与牌面素材**同一套约定**（AGENTS §6.1）：材质包优先，其次随程序分发的目录，
 * 最后才是编进 exe 的兜底 —— 删掉 `sfx/` 目录也不会"没声音还崩"。
 */
QByteArray loadWav(const QString& sfx)
{
    const QByteArray fromPack = Theme::instance().packSfx(sfx);
    if (!fromPack.isEmpty()) {
        return fromPack;
    }
    const QString beside = besidePath(sfx);
    if (QFile::exists(beside)) {
        QFile f(beside);
        if (f.open(QIODevice::ReadOnly)) {
            return f.readAll();
        }
    }
    const QString res = resourcePath(sfx);
    if (QFile::exists(res)) {
        QFile f(res);
        if (f.open(QIODevice::ReadOnly)) {
            return f.readAll();
        }
    }
    return QByteArray();
}

#ifdef Q_OS_WIN
/** 供 PlaySound 使用的临时文件句柄（`SND_MEMORY` 要求内存在调用期间一直有效）。 */
QHash<QString, QTemporaryFile*> g_winTemp;
#endif

/**
 * 诊断开关：设了环境变量 `MAHJONG_SFX_TRACE=1` 时，把**每次播放的判定依据**打到 stderr。
 *
 * <p>为什么要留下来：报障「没有声音」只看代码是查不出来的（静默路径太多：开关关了 /
 * 音量 0 / 后端 none / 素材取不到 / 效果还没 Ready / 正在播被吞）。这一行把六个原因
 * 一次列全，跑一局就能定位。默认关闭，零开销（一个 `qEnvironmentVariableIsSet`）。
 */
bool traceEnabled()
{
    static const bool on = qEnvironmentVariableIsSet("MAHJONG_SFX_TRACE");
    return on;
}

void trace(const QString& msg)
{
    if (!traceEnabled()) {
        return;
    }
    std::fprintf(stderr, "[sfx] %s\n", qPrintable(msg));
    std::fflush(stderr);
}

// ⚠ `wavDurationMs` / `pickSlot` 定义在**匿名 namespace 之外**（就在下面 `} // namespace`
// 之后）：它们要在头文件里公开给自检，放匿名 namespace 里会与头文件那份声明撞成二义。
} // namespace

int wavDurationMs(const QByteArray& wav)
{
    if (wav.size() < 44 || wav.left(4) != QByteArrayLiteral("RIFF")
        || wav.mid(8, 4) != QByteArrayLiteral("WAVE")) {
        return 0;
    }
    auto u16 = [&wav](int at) {
        return int(quint8(wav.at(at))) | (int(quint8(wav.at(at + 1))) << 8);
    };
    auto u32 = [&wav](int at) {
        return qint64(quint32(quint8(wav.at(at))) | (quint32(quint8(wav.at(at + 1))) << 8)
                      | (quint32(quint8(wav.at(at + 2))) << 16)
                      | (quint32(quint8(wav.at(at + 3))) << 24));
    };
    int rate = 0;
    int ch = 0;
    int bits = 0;
    qint64 dataLen = 0;
    int i = 12;
    while (i + 8 <= wav.size()) {
        const QByteArray id = wav.mid(i, 4);
        const qint64 sz = u32(i + 4);
        // 块长必须落在文件内（坏文件里它可能是天文数字，直接当解析失败）
        if (sz < 0 || i + 8 + sz > wav.size()) {
            if (id == QByteArrayLiteral("data")) {
                dataLen = wav.size() - (i + 8);     // 头写坏但数据在：按剩下的算
            }
            break;
        }
        if (id == QByteArrayLiteral("fmt ") && sz >= 16) {
            ch = u16(i + 10);
            rate = int(u32(i + 12));
            bits = u16(i + 22);
        } else if (id == QByteArrayLiteral("data")) {
            dataLen = sz;
            break;
        }
        i += 8 + int(sz) + (int(sz) & 1);
    }
    const int bytesPerSample = bits / 8;
    if (rate <= 0 || ch <= 0 || bytesPerSample <= 0 || dataLen <= 0) {
        return 0;
    }
    return int(dataLen * 1000 / (qint64(rate) * ch * bytesPerSample));
}

int pickSlot(const QVector<bool>& playing, const QVector<qint64>& ageMs, int durMs,
             bool allowOverlap)
{
    const qint64 limit = (durMs > 0 ? qint64(durMs) : kUnknownDurationMs) + kStuckMarginMs;
    // 「真在播」= 自称在播 **且** 还没超过这个 WAV 可能的最长时长。
    // 只看 `isPlaying()` 是不够的：设备异常之后它会永远为真，那条音效就再也放不出来了。
    auto live = [&](int i) {
        return playing.value(i) && ageMs.value(i) >= 0 && ageMs.value(i) < limit;
    };
    if (!allowOverlap) {
        for (int i = 0; i < playing.size(); ++i) {
            if (live(i)) {
                return -1;      // 不叠口径：真的还在响 → 这次跳过
            }
        }
    }
    // 第一轮：**真正空闲**的实例（`play()` 之前不用 stop，最干净的一条路）。
    for (int i = 0; i < playing.size(); ++i) {
        if (!playing.value(i)) {
            return i;
        }
    }
    // 第二轮：自称在播、但已经超过这个 WAV 可能的最长时长 —— 判为卡死，调用方 stop 后复用。
    // 没有这一轮，一个假 playing 就能让这条音效**永久静音**（正是二次报障的机制）。
    for (int i = 0; i < playing.size(); ++i) {
        if (!live(i)) {
            return i;
        }
    }
    return -2;                  // 都在真播：叠放口径下也不再 stop() 硬插（那条路径最可疑）
}

bool shouldRebuildStack(int consecutiveFailures)
{
    return consecutiveFailures >= kRebuildAfterFailures;
}

QStringList allNames()
{
    return { QLatin1String(name::Chi),    QLatin1String(name::Pon),
             QLatin1String(name::Kan),    QLatin1String(name::Riichi),
             QLatin1String(name::Tsumo),  QLatin1String(name::Ron),
             QLatin1String(name::Notify), QLatin1String(name::Draw) };
}

Player& Player::instance()
{
    static Player p;
    return p;
}

Player::Player()
    : QObject(nullptr)
{
}

Player::~Player()
{
    stopAll();
    for (QVector<QSoundEffect*>& pool : m_effects) {
        qDeleteAll(pool);
    }
    m_effects.clear();
#ifdef Q_OS_WIN
    qDeleteAll(g_winTemp);
    g_winTemp.clear();
#endif
}

void Player::init()
{
    // 后端探测：只做一次，重复调用无副作用。
    if (m_backend == QLatin1String("none")) {
#if MAHJONG_HAVE_MULTIMEDIA
        m_backend = QStringLiteral("qsoundeffect");
        m_available = true;
#elif defined(Q_OS_WIN)
        m_backend = QStringLiteral("winmm");
        m_available = true;
#else
        m_backend = QStringLiteral("none");
        m_available = false;
#endif
    }
    // 关掉开关就**不探测**素材（省掉 8 次文件探测）。
#if MAHJONG_HAVE_MULTIMEDIA
    if (m_available) {
        for (const QString& n : allNames()) {
            if (m_effects.contains(n)) {
                continue;
            }
            const QByteArray bytes = data(n);
            if (bytes.isEmpty()) {
                continue;
            }
            // ⚠ `QSoundEffect::setSource()` 只吃 URL，没有 setData()：
            //   把 WAV 落到临时文件再喂给它（临时文件是 Player 的子对象、生命周期内不删）。
            auto* tmp = new QTemporaryFile(
                QDir::tempPath() + QStringLiteral("/mahjong-sfx-XXXXXX.wav"), this);
            if (!tmp->open()) {
                delete tmp;
                continue;
            }
            tmp->write(bytes);
            tmp->flush();
            const QUrl url = QUrl::fromLocalFile(tmp->fileName());
            QVector<QSoundEffect*> pool;
            pool.reserve(kPoolPerSound);
            // ⚠ **显式绑当前默认输出设备**（不是让它自己取默认）：设备被别的进程抢占、或
            //   睡眠唤醒之后，效果对象仍记得那个已经死掉的设备；`rebuildStack()` 会把这些
            //   对象整个重建一遍，那时这里会重新解析一次默认设备 —— 这就是"不用重启客户端"的凭据。
            const QAudioDevice dev = QMediaDevices::defaultAudioOutput();
            for (int i = 0; i < kPoolPerSound; ++i) {
                auto* eff = dev.isNull() ? new QSoundEffect(this) : new QSoundEffect(dev, this);
                eff->setSource(url);
                eff->setVolume(m_volume / 100.0);
                pool.append(eff);
            }
            m_source.insert(n, url);
            m_effects.insert(n, pool);
            // 时长用来判"自称在播"是不是在说谎（见 `pickSlot`）。解析不出来就记 0，
            // `pickSlot` 会退回保守上限，绝不因此把实例当成永远在播。
            m_durationMs.insert(n, wavDurationMs(bytes));
        }
    }
#endif
}

void Player::setEnabled(bool on)
{
    m_enabled = on;
    if (!on) {
        stopAll();
    }
}

void Player::setVolume(int percent)
{
    m_volume = qBound(0, percent, 100);
#if MAHJONG_HAVE_MULTIMEDIA
    for (const QVector<QSoundEffect*>& pool : m_effects) {
        for (QSoundEffect* e : pool) {
            if (e) {
                e->setVolume(m_volume / 100.0);
            }
        }
    }
#endif
}

const QByteArray& Player::data(const QString& sfx)
{
    static const QByteArray empty;
    auto it = m_cache.find(sfx);
    if (it != m_cache.end()) {
        return it.value();
    }
    const QByteArray bytes = loadWav(sfx);
    if (bytes.isEmpty()) {
        // 缓存"没有"这件事也用空字节表示；`m_cache` 的键存在即代表探测过了。
        return *m_cache.insert(sfx, empty);
    }
    return *m_cache.insert(sfx, bytes);
}

void Player::play(const QString& sfx, bool allowOverlap)
{
    if (!m_enabled || !m_available || m_volume <= 0) {
        trace(QStringLiteral("play %1 → 跳过（开关 %2 / 可用 %3 / 音量 %4）")   // i18n-keep
                  .arg(sfx)
                  .arg(m_enabled ? 1 : 0)
                  .arg(m_available ? 1 : 0)
                  .arg(m_volume));
        return;
    }
    const QByteArray& bytes = data(sfx);
    if (bytes.isEmpty()) {
        trace(QStringLiteral("play %1 → 素材为空（材质包/目录/qrc 三档都没取到）").arg(sfx));   // i18n-keep
        return;   // 素材找不到：静默（绝不因为"没声音"影响对局）
    }
    trace(QStringLiteral("play %1 → 后端 %2，效果 %3")   // i18n-keep
              .arg(sfx, m_backend, effectStateForTrace(sfx)));
    emitSound(sfx, bytes, allowOverlap);
}

/** 诊断用：某个音效池子当前的状态（非多媒体后端返回 `-`）。 */
QString Player::effectStateForTrace(const QString& sfx) const
{
#if MAHJONG_HAVE_MULTIMEDIA
    const QVector<QSoundEffect*> pool = m_effects.value(sfx);
    if (pool.isEmpty()) {
        return QStringLiteral("missing");
    }
    int playing = 0;
    int stale = 0;
    int ready = 0;
    int errors = 0;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    const qint64 limit = (m_durationMs.value(sfx, 0) > 0 ? qint64(m_durationMs.value(sfx, 0))
                                                        : kUnknownDurationMs)
            + kStuckMarginMs;
    const QVector<qint64> starts = m_startedAt.value(sfx);
    for (int i = 0; i < pool.size(); ++i) {
        QSoundEffect* e = pool.at(i);
        if (e == nullptr) {
            continue;
        }
        if (e->isPlaying()) {
            // 自称在播但已经超过这个 WAV 可能的最长时长 = 卡死（设备异常后 isPlaying 会永远为真）
            const qint64 started = starts.value(i, 0);
            if (started > 0 && now - started >= limit) {
                ++stale;
            } else {
                ++playing;
            }
        }
        if (e->status() == QSoundEffect::Ready) {
            ++ready;
        } else if (e->status() == QSoundEffect::Error) {
            ++errors;
        }
    }
    return QStringLiteral("pool%1(ready %2/playing %3/stale %4/err %5)")
            .arg(pool.size())
            .arg(ready)
            .arg(playing)
            .arg(stale)
            .arg(errors);
#else
    Q_UNUSED(sfx);
    return QStringLiteral("-");
#endif
}

void Player::emitSound(const QString& sfx, const QByteArray& bytes, bool allowOverlap)
{
#if MAHJONG_HAVE_MULTIMEDIA
    Q_UNUSED(bytes);
    const QVector<QSoundEffect*> pool = m_effects.value(sfx);
    if (pool.isEmpty()) {
        return;
    }
    // ① 先把"池子现状"翻成判据要的形状：谁自称在播、已经播了多久。
    //    ⚠ 只看 `isPlaying()` 是不行的（见 `pickSlot` 的注释）：这里额外带上年龄，
    //      于是"声称在播但其实早该结束"的实例会被识别出来并复用 —— 这条判据修的是
    //      「某个音效从此再也不响」（报障：音效在有副露 / 选择不副露之后消失）。
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    const QVector<qint64> starts = m_startedAt.value(sfx);
    QVector<bool> playing;
    QVector<qint64> ageMs;
    playing.reserve(pool.size());
    ageMs.reserve(pool.size());
    for (int i = 0; i < pool.size(); ++i) {
        playing.append(pool.at(i) != nullptr && pool.at(i)->isPlaying());
        const qint64 started = starts.value(i, 0);
        // 没记录过开始时刻（或从没播过）：给一个很大值 = "不像是在播"
        ageMs.append(started > 0 ? (now - started) : (kUnknownDurationMs + kStuckMarginMs + 1));
    }
    const int idx = pickSlot(playing, ageMs, m_durationMs.value(sfx, 0), allowOverlap);
    if (idx < 0) {
        if (idx == -1) {
            ++m_overlapSkips;
            trace(QStringLiteral("  ↳ %1 仍在播，allowOverlap=false → 跳过").arg(sfx));   // i18n-keep
        } else {
            ++m_exhaustedSkips;
            trace(QStringLiteral("  ↳ %1 池子 %2 个实例都在真播 → 放弃这一次（不再 stop 硬插）")   // i18n-keep
                      .arg(sfx)
                      .arg(pool.size()));
        }
        return;
    }
    QSoundEffect* e = pool.at(idx);
    if (playing.value(idx)) {
        // 自称在播、但已经超过这个 WAV 可能的最长时长 → 卡死（设备异常后 isPlaying 会永远为真）。
        // 这是**唯一**还会 stop() 的路径，而且停的是一个本来就没在响的实例。
        ++m_stuckStops;
        trace(QStringLiteral("  ↳ %1 实例 %2 卡在 playing（%3 ms ≥ 上限 %4 ms）→ stop 后复用")   // i18n-keep
                  .arg(sfx)
                  .arg(idx)
                  .arg(ageMs.value(idx))
                  .arg((m_durationMs.value(sfx, 0) > 0 ? m_durationMs.value(sfx, 0)
                                                       : int(kUnknownDurationMs))
                       + int(kStuckMarginMs)));
        e->stop();
    }
    // ③ 自愈：后端把效果标成 Error 时（声卡切换 / 设备睡眠之后会遇到），重设一次源。
    if (e->status() == QSoundEffect::Error) {
        e->setSource(m_source.value(sfx));
        trace(QStringLiteral("  ↳ %1 status=Error → 重设源").arg(sfx));   // i18n-keep
    }
    e->play();
    QVector<qint64> updated = starts;
    if (updated.size() < pool.size()) {
        updated.resize(pool.size());
    }
    updated[idx] = now;
    m_startedAt.insert(sfx, updated);
    m_plays[sfx] = m_plays.value(sfx) + 1;
    // ④ **哑掉自愈**（用户报障：两个音效撞在一起进入竞态后，所有音效全哑，连重开一局都不行，
    //    必须重启客户端）。`play()` 不报错、`status()` 仍是 Ready，但后端其实再也没出声 ——
    //    唯一能客观观测到的信号就是"刚 play 完 isPlaying() 就是假"。
    //    连续几次都这样 → 判定整套音频栈已经死了 → `rebuildStack()` 把效果对象**整个重建**
    //    （顺带重新解析默认输出设备），下一次播放就恢复，**不需要重启客户端**。
    if (e->isPlaying()) {
        m_consecutiveFailures = 0;
    } else {
        ++m_consecutiveFailures;
        if (shouldRebuildStack(m_consecutiveFailures)) {
            rebuildStack();
            return;                       // 这一声已经错过了；下一次播放用的是新栈
        }
    }
    if (traceEnabled()) {
        // 「play() 之后还响不响」是这类报障唯一能客观观测的点：效果被卡死时
        // `play()` 不报错、`status()` 仍是 Ready，但 `isPlaying()` 立刻回落。
        trace(QStringLiteral("  ↳ %1 之后：playing=%2 status=%3")   // i18n-keep
                  .arg(sfx)
                  .arg(e->isPlaying() ? 1 : 0)
                  .arg(int(e->status())));
    }
#elif defined(Q_OS_WIN)
    // 回退后端：把 WAV 写进临时文件（`PlaySound` 的 SND_MEMORY 要求内存在
    // 调用期间一直有效，而 SND_ASYNC 是**异步**读的 —— 用栈上的数据会崩）。
    Q_UNUSED(allowOverlap);
    QTemporaryFile*& tmp = g_winTemp[sfx];
    if (!tmp) {
        tmp = new QTemporaryFile(QDir::tempPath() + QStringLiteral("/mahjong-sfx-XXXXXX.wav"));
        if (!tmp->open()) {
            delete tmp;
            tmp = nullptr;
            return;
        }
        tmp->write(bytes);
        tmp->flush();
    }
    PlaySoundA(reinterpret_cast<LPCSTR>(tmp->fileName().toLocal8Bit().constData()), nullptr,
               SND_FILENAME | SND_ASYNC | SND_NODEFAULT);
    m_plays[sfx] = m_plays.value(sfx) + 1;
#else
    Q_UNUSED(sfx);
    Q_UNUSED(bytes);
    Q_UNUSED(allowOverlap);
#endif
}

void Player::stopAll()
{
#if MAHJONG_HAVE_MULTIMEDIA
    for (const QVector<QSoundEffect*>& pool : m_effects) {
        for (QSoundEffect* e : pool) {
            if (e && e->isPlaying()) {
                e->stop();
            }
        }
    }
#elif defined(Q_OS_WIN)
    PlaySoundA(nullptr, nullptr, 0);
#endif
}

void Player::rebuildStack()
{
#if MAHJONG_HAVE_MULTIMEDIA
    ++m_rebuilds;
    trace(QStringLiteral("  ↳ 音频栈疑似哑掉（连续 %1 次 play 之后立刻 isPlaying=false）→ 整套重建")   // i18n-keep
              .arg(m_consecutiveFailures));
    for (QVector<QSoundEffect*>& pool : m_effects) {
        for (QSoundEffect* e : pool) {
            if (e != nullptr) {
                e->stop();
            }
        }
        qDeleteAll(pool);
    }
    m_effects.clear();
    m_startedAt.clear();
    m_consecutiveFailures = 0;
    // ⚠ **必须立刻重建**（与 `clearCache()` 同一套）：`init()` 只跳过"池子还在"的音效，
    //   池子被清空后它就是"按当前默认输出设备重新建池"的唯一入口。漏掉这一句，
    //   `emitSound()` 开头那句 `pool.isEmpty() → return` 会让此后**每一次**播放都静默返回 ——
    //   等于把"哑掉"换成了"永久静音"，比原 bug 更难查（自检有断言钉住重建后仍能再播）。
    init();
#endif
}

void Player::clearCache()
{
#if MAHJONG_HAVE_MULTIMEDIA
    for (QVector<QSoundEffect*>& pool : m_effects) {
        qDeleteAll(pool);
    }
    m_effects.clear();
    m_source.clear();
    m_durationMs.clear();
    m_startedAt.clear();     // 实例都销毁了，旧的"开始时刻"必须一起清掉
#endif
    m_cache.clear();
    // 换材质包后重新探测（`init()` 会按需重建）
    m_backend.clear();
    m_backend = QStringLiteral("none");
    init();
}

int Player::loadedCountForTest()
{
    int n = 0;
    for (const QString& s : allNames()) {
        if (!data(s).isEmpty()) {
            ++n;
        }
    }
    return n;
}

int Player::dataSizeForTest(const QString& sfx)
{
    return data(sfx).size();
}

bool Player::effectReadyForTest(const QString& sfx)
{
#if MAHJONG_HAVE_MULTIMEDIA
    // 池子里**任意一个**就绪即可（三个实例的源相同）
    for (QSoundEffect* e : m_effects.value(sfx)) {
        if (e != nullptr && e->status() == QSoundEffect::Ready) {
            return true;
        }
    }
    return false;
#else
    Q_UNUSED(sfx);
    return false;   // 该后端没有"就绪状态"这个概念
#endif
}

bool Player::effectPlayingForTest(const QString& sfx)
{
#if MAHJONG_HAVE_MULTIMEDIA
    for (QSoundEffect* e : m_effects.value(sfx)) {
        if (e != nullptr && e->isPlaying()) {
            return true;
        }
    }
    return false;
#else
    Q_UNUSED(sfx);
    return false;
#endif
}

int Player::poolSizeForTest(const QString& sfx) const
{
    return int(m_effects.value(sfx).size());
}

int Player::playCountForTest(const QString& sfx) const
{
    return m_plays.value(sfx, 0);
}

} // namespace sound
