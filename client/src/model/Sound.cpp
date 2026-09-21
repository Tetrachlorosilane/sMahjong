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
#include <QDir>
#include <QFile>
#include <QTemporaryFile>
#include <QTimer>
#include <QUrl>

#include <cstdio>

#if MAHJONG_HAVE_MULTIMEDIA
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

} // namespace

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
            for (int i = 0; i < kPoolPerSound; ++i) {
                auto* eff = new QSoundEffect(this);
                eff->setSource(url);
                eff->setVolume(m_volume / 100.0);
                pool.append(eff);
            }
            m_source.insert(n, url);
            m_effects.insert(n, pool);
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
    int ready = 0;
    int errors = 0;
    for (QSoundEffect* e : pool) {
        if (e == nullptr) {
            continue;
        }
        if (e->isPlaying()) {
            ++playing;
        }
        if (e->status() == QSoundEffect::Ready) {
            ++ready;
        } else if (e->status() == QSoundEffect::Error) {
            ++errors;
        }
    }
    return QStringLiteral("pool%1(ready %2/playing %3/err %4)")
            .arg(pool.size())
            .arg(ready)
            .arg(playing)
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
    // ① 挑一个**空闲**实例：绝大多数情况下这一步就够了，**完全不需要 stop()**。
    QSoundEffect* e = nullptr;
    for (QSoundEffect* cand : pool) {
        if (cand != nullptr && !cand->isPlaying()) {
            e = cand;
            break;
        }
    }
    if (!allowOverlap) {
        // 「别叠」的语义是**整个音效**只要还在响就不再放（不是"这个实例"）——
        // 摸牌音效每巡都触发，叠起来很吵；而且旧实现"停掉再从头放"正是把声卡
        // 搞哑的那条嫌疑路径（见 Sound.h 顶部）。
        for (QSoundEffect* cand : pool) {
            if (cand != nullptr && cand->isPlaying()) {
                ++m_overlapSkips;
                trace(QStringLiteral("  ↳ %1 仍在播，allowOverlap=false → 跳过").arg(sfx));   // i18n-keep
                return;
            }
        }
    }
    if (e == nullptr) {
        // 池子都忙（只有 `allowOverlap=true` 会走到这里）：从头放最早那个 ——
        // 这是**唯一**还会 stop() 的路径。
        e = pool.first();
        e->stop();
    }
    // ③ 自愈：后端把效果标成 Error 时（声卡切换 / 设备睡眠之后会遇到），重设一次源。
    if (e->status() == QSoundEffect::Error) {
        e->setSource(m_source.value(sfx));
        trace(QStringLiteral("  ↳ %1 status=Error → 重设源").arg(sfx));   // i18n-keep
    }
    e->play();
    m_plays[sfx] = m_plays.value(sfx) + 1;
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

void Player::clearCache()
{
#if MAHJONG_HAVE_MULTIMEDIA
    for (QVector<QSoundEffect*>& pool : m_effects) {
        qDeleteAll(pool);
    }
    m_effects.clear();
    m_source.clear();
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
