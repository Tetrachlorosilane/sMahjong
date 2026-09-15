#pragma once

// 个人设置对话框：服务器地址/端口、用户名、材质包。
//
// 点「确定」时才落盘（`Settings::save`），并**立刻**把材质包应用到界面上：
// `Theme::load()` → 清素材缓存 → 重画。材质包的问题（缺失/损坏/清单不对）
// 都只在这里显示成一句人话，**不会弹错误框、也不会阻止保存** —— 设置里那条路径原样留着，
// 用户修好文件后点「重新载入」即可，不必重新输一遍。

#include <QDialog>
#include <QString>

#include "model/Settings.h"

class QLabel;
class QLineEdit;
class QPushButton;
class QSpinBox;

class SettingsDialog : public QDialog
{
    Q_OBJECT
public:
    /** @param path 设置文件路径（由调用方给，便于自检用临时文件） */
    SettingsDialog(const Settings& cur, const QString& path, QWidget* parent = nullptr);

    /** 对话框里当前的设置（点确定后与磁盘一致）。 */
    const Settings& settings() const { return m_settings; }

signals:
    /** 设置已保存并生效（大厅据此刷新自己的输入框）。 */
    void applied();

private:
    void buildUi();
    void browsePack();
    void clearPack();
    void reloadPack();
    void accept() override;
    /** 把当前编辑框里的值收集进 `m_settings`（先做一遍规范化）。 */
    void collect();
    /** 应用材质包（不落盘），把状态写进提示行。 */
    void applyPack();

    Settings m_settings;
    QString m_path;

    QLineEdit* m_host = nullptr;
    QSpinBox* m_port = nullptr;
    QLineEdit* m_name = nullptr;
    QLineEdit* m_pack = nullptr;
    QLabel* m_packStatus = nullptr;
    QLabel* m_pathLabel = nullptr;
};
