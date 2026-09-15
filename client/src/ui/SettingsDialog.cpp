#include "ui/SettingsDialog.h"

#include "i18n/Lang.h"
#include "model/Theme.h"
#include "ui/TileRenderer.h"

#include <QFileDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QVBoxLayout>

namespace {

/** 把 `Theme::Status::problems` 里的机读码翻成人话（认不出的原样显示，方便发现新码）。 */
QString problemText(const QString& code)
{
    static const QHash<QString, const char*> map {
        { QStringLiteral("zip_unsupported"), "ui.settings.pack_zip_unsupported" },
        { QStringLiteral("pack_not_archive"), "ui.settings.pack_not_archive" },
        { QStringLiteral("pack_missing"), "ui.settings.pack_missing" },
        { QStringLiteral("manifest_missing"), "ui.settings.pack_manifest_missing" },
        { QStringLiteral("manifest_broken"), "ui.settings.pack_manifest_broken" },
        { QStringLiteral("manifest_too_large"), "ui.settings.pack_manifest_too_large" },
        { QStringLiteral("pack_too_many_entries"), "ui.settings.pack_too_many_entries" },
        { QStringLiteral("cloth_broken"), "ui.settings.pack_cloth_broken" },
        { QStringLiteral("cloth_empty"), "ui.settings.pack_cloth_empty" },
        { QStringLiteral("stick_broken"), "ui.settings.pack_stick_broken" },
        { QStringLiteral("stick_empty"), "ui.settings.pack_stick_empty" },
        { QStringLiteral("font_unreadable"), "ui.settings.pack_font_unreadable" },
        { QStringLiteral("font_empty"), "ui.settings.pack_font_empty" },
    };
    const auto it = map.constFind(code);
    if (it != map.constEnd()) {
        return lang::t(QString::fromLatin1(it.value()));
    }
    // `xxx_bad_path` / `xxx_dir_missing` / `xxx_not_string` 是同构的码，合并成两条
    if (code.endsWith(QLatin1String("_bad_path"))) {
        return lang::t(QStringLiteral("ui.settings.pack_bad_path")).arg(code.section(QLatin1Char('_'), 0, 0));
    }
    if (code.endsWith(QLatin1String("_dir_missing"))) {
        return lang::t(QStringLiteral("ui.settings.pack_dir_missing")).arg(code.section(QLatin1Char('_'), 0, 0));
    }
    if (code.endsWith(QLatin1String("_not_string"))) {
        return lang::t(QStringLiteral("ui.settings.pack_not_string")).arg(code.section(QLatin1Char('_'), 0, 0));
    }
    return code;   // 认不出的码原样显示（与语言文件那条约定一致）
}

}   // namespace

SettingsDialog::SettingsDialog(const Settings& cur, const QString& path, QWidget* parent)
    : QDialog(parent)
    , m_settings(cur)
    , m_path(path)
{
    setWindowTitle(lang::t(QStringLiteral("ui.settings.title")));
    buildUi();
    // 打开时就把当前材质包的状态显示出来（用户一眼能看到上次那包到底生效没有）
    reloadPack();
}

void SettingsDialog::buildUi()
{
    auto* root = new QVBoxLayout(this);
    auto* form = new QFormLayout();

    m_host = new QLineEdit(m_settings.host, this);
    form->addRow(lang::t(QStringLiteral("ui.lobby.host")), m_host);
    m_port = new QSpinBox(this);
    m_port->setRange(1, 65535);
    m_port->setValue(int(m_settings.port));
    form->addRow(lang::t(QStringLiteral("ui.lobby.port")), m_port);
    m_name = new QLineEdit(m_settings.name, this);
    m_name->setMaxLength(24);
    form->addRow(lang::t(QStringLiteral("ui.lobby.nickname")), m_name);

    // 材质包：一行「路径 + 浏览 + 清除」+ 一行状态
    auto* packRow = new QHBoxLayout();
    m_pack = new QLineEdit(m_settings.pack, this);
    m_pack->setPlaceholderText(lang::t(QStringLiteral("ui.settings.pack_placeholder")));
    packRow->addWidget(m_pack, 1);
    auto* browse = new QPushButton(lang::t(QStringLiteral("ui.settings.browse")), this);
    connect(browse, &QPushButton::clicked, this, &SettingsDialog::browsePack);
    packRow->addWidget(browse);
    auto* clearBtn = new QPushButton(lang::t(QStringLiteral("ui.settings.clear")), this);
    connect(clearBtn, &QPushButton::clicked, this, &SettingsDialog::clearPack);
    packRow->addWidget(clearBtn);
    auto* reload = new QPushButton(lang::t(QStringLiteral("ui.settings.reload")), this);
    connect(reload, &QPushButton::clicked, this, &SettingsDialog::reloadPack);
    packRow->addWidget(reload);
    auto* packWrap = new QWidget(this);
    packWrap->setLayout(packRow);
    form->addRow(lang::t(QStringLiteral("ui.settings.pack")), packWrap);

    root->addLayout(form);

    m_packStatus = new QLabel(this);
    m_packStatus->setWordWrap(true);
    m_packStatus->setStyleSheet(QStringLiteral("color:#B9C2D0;"));
    root->addWidget(m_packStatus);

    m_pathLabel = new QLabel(lang::t(QStringLiteral("ui.settings.file")).arg(m_path), this);
    m_pathLabel->setStyleSheet(QStringLiteral("color:#7A808C;"));
    m_pathLabel->setWordWrap(true);
    root->addWidget(m_pathLabel);

    root->addStretch(1);

    auto* btns = new QHBoxLayout();
    btns->addStretch(1);
    auto* ok = new QPushButton(lang::t(QStringLiteral("ui.settings.ok")), this);
    ok->setDefault(true);
    connect(ok, &QPushButton::clicked, this, &SettingsDialog::accept);
    btns->addWidget(ok);
    auto* cancel = new QPushButton(lang::t(QStringLiteral("ui.settings.cancel")), this);
    connect(cancel, &QPushButton::clicked, this, &QDialog::reject);
    btns->addWidget(cancel);
    root->addLayout(btns);

    resize(560, 320);
}

void SettingsDialog::browsePack()
{
    // 目录包与 zip 都支持：文件对话框里两个过滤器都给上，选目录就切到目录模式
    QFileDialog dlg(this, lang::t(QStringLiteral("ui.settings.pick_pack")));
    dlg.setFileMode(QFileDialog::ExistingFile);
    dlg.setNameFilters({ lang::t(QStringLiteral("ui.settings.pack_filter")),
                         lang::t(QStringLiteral("ui.settings.all_files")) });
    if (dlg.exec() != QDialog::Accepted || dlg.selectedFiles().isEmpty()) {
        // 退一步：让用户可以直接选一个**文件夹**（不打包也能用）
        const QString dir = QFileDialog::getExistingDirectory(
                this, lang::t(QStringLiteral("ui.settings.pick_pack_dir")));
        if (dir.isEmpty()) {
            return;
        }
        m_pack->setText(dir);
    } else {
        m_pack->setText(dlg.selectedFiles().first());
    }
    reloadPack();
}

void SettingsDialog::clearPack()
{
    m_pack->clear();
    reloadPack();
}

void SettingsDialog::reloadPack()
{
    collect();
    applyPack();
}

void SettingsDialog::applyPack()
{
    // ⚠ 换包必须清素材缓存，否则画面上还是上一批图（缓存里存的是解析好的素材）
    const Theme::Status st = Theme::instance().load(m_settings.pack);
    TileRenderer::clearAssetCache();

    QStringList lines;
    if (m_settings.pack.isEmpty()) {
        lines << lang::t(QStringLiteral("ui.settings.pack_none"));
    } else if (!st.loaded) {
        lines << lang::t(QStringLiteral("ui.settings.pack_failed"));
    } else {
        lines << lang::t(QStringLiteral("ui.settings.pack_ok"))
                         .arg(st.name.isEmpty() ? m_settings.pack : st.name,
                              st.applied.isEmpty() ? lang::t(QStringLiteral("ui.settings.pack_nothing"))
                                                   : st.applied.join(QStringLiteral(" / ")));
    }
    if (!st.problems.isEmpty()) {
        QStringList ps;
        for (const QString& p : st.problems) {
            ps << problemText(p);
        }
        lines << lang::t(QStringLiteral("ui.settings.pack_problems")).arg(ps.join(QStringLiteral(" / ")));
    }
    m_packStatus->setText(lines.join(QLatin1Char('\n')));
}

void SettingsDialog::collect()
{
    m_settings.host = m_host->text();
    m_settings.port = quint16(m_port->value());
    m_settings.name = m_name->text();
    m_settings.pack = m_pack->text().trimmed();
    QStringList repaired;
    m_settings.sanitize(&repaired);   // 与读文件时**同一套**校验（越界/控制字符都会被修掉）
}

void SettingsDialog::accept()
{
    collect();
    // 先把值弹回控件（规范化可能改过它们，不让界面与磁盘不一致）
    m_host->setText(m_settings.host);
    m_port->setValue(int(m_settings.port));
    m_name->setText(m_settings.name);
    m_pack->setText(m_settings.pack);

    applyPack();
    QString err;
    m_settings.save(m_path, &err);   // 存不下也不拦着用户用（只是下次启动要重设）
    emit applied();
    QDialog::accept();
}
