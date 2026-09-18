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

#if MAHJONG_HAVE_MULTIMEDIA
#include <QSoundEffect>
#endif

#ifdef Q_OS_WIN
#include <windows.h>
#include <mmsystem.h>
#endif

namespace sound {
namespace {

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
    qDeleteAll(m_effects);
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
            auto* eff = new QSoundEffect(this);
            // ⚠ `QSoundEffect::setSource()` 只吃 URL，没有 setData()：
            //   把 WAV 落到临时文件再喂给它（临时文件在 Player 生命周期内不删）。
            auto* tmp = new QTemporaryFile(
                QDir::tempPath() + QStringLiteral("/mahjong-sfx-XXXXXX.wav"), this);
            if (tmp->open()) {
                tmp->write(bytes);
                tmp->flush();
                eff->setSource(QUrl::fromLocalFile(tmp->fileName()));
                eff->setVolume(m_volume / 100.0);
                m_effects.insert(n, eff);
            } else {
                delete eff;
                delete tmp;
            }
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
    for (QSoundEffect* e : m_effects) {
        if (e) {
            e->setVolume(m_volume / 100.0);
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
        return;
    }
    const QByteArray& bytes = data(sfx);
    if (bytes.isEmpty()) {
        return;   // 素材找不到：静默（绝不因为"没声音"影响对局）
    }
    Q_UNUSED(allowOverlap);
    emitSound(sfx, bytes);
}

void Player::emitSound(const QString& sfx, const QByteArray& bytes)
{
#if MAHJONG_HAVE_MULTIMEDIA
    Q_UNUSED(bytes);
    QSoundEffect* e = m_effects.value(sfx, nullptr);
    if (!e) {
        return;
    }
    if (e->isPlaying()) {
        e->stop();     // 重新从头放：连续摸牌不会被"正在播"吞掉
    }
    e->play();
#elif defined(Q_OS_WIN)
    // 回退后端：把 WAV 写进临时文件（`PlaySound` 的 SND_MEMORY 要求内存在
    // 调用期间一直有效，而 SND_ASYNC 是**异步**读的 —— 用栈上的数据会崩）。
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
#else
    Q_UNUSED(sfx);
    Q_UNUSED(bytes);
#endif
}

void Player::stopAll()
{
#if MAHJONG_HAVE_MULTIMEDIA
    for (QSoundEffect* e : m_effects) {
        if (e && e->isPlaying()) {
            e->stop();
        }
    }
#elif defined(Q_OS_WIN)
    PlaySoundA(nullptr, nullptr, 0);
#endif
}

void Player::clearCache()
{
#if MAHJONG_HAVE_MULTIMEDIA
    qDeleteAll(m_effects);
    m_effects.clear();
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

} // namespace sound
