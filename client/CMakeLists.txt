# 立直麻将 Qt6 客户端（C++17 / Qt6 Widgets + Network / MinGW 构建）
cmake_minimum_required(VERSION 3.19)

# ⚠ 版本号有**两处**，改动要一起改：这里 + `src/main.cpp` 的 `setApplicationVersion()`
#   （前者是工程元数据，后者是运行时 `QApplication::applicationVersion()`，发布说明引用的是后者）。
project(mahjong-client VERSION 1.8.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

find_package(Qt6 REQUIRED COMPONENTS Widgets Network Svg)

qt_standard_project_setup()   # 打开 AUTOMOC / AUTOUIC / AUTORCC

# ---------------------------------------------------------------------------
# moc 处理
#   MJ_USE_AUTOMOC=ON（默认，标准做法）：构建期由 AUTOMOC 调用 moc。
#   MJ_USE_AUTOMOC=OFF（受限环境回退）：某些沙箱禁止构建期派生子进程，
#     AUTOMOC 会以 "libuv process spawn failed: operation not permitted" 失败；
#     此时改为「配置期预生成 moc 源文件」（同样调用 Qt 的 moc，
#     产物放 build/mj_moc/ 后照常参与编译）。build.ps1 会自动检测并回退。
# ---------------------------------------------------------------------------
option(MJ_USE_AUTOMOC "构建期使用 AUTOMOC 调用 moc（受限沙箱可设为 OFF）" ON)

if(MJ_USE_AUTOMOC)
    message(STATUS "moc 模式：AUTOMOC（构建期）")
else()
    message(STATUS "moc 模式：配置期预生成")
endif()

# 找到 moc 可执行文件（回退模式需要）
set(_mj_moc_exe "")
if(TARGET Qt6::moc)
    get_target_property(_mj_moc_exe Qt6::moc IMPORTED_LOCATION)
    if(NOT _mj_moc_exe)
        get_target_property(_mj_moc_exe Qt6::moc IMPORTED_LOCATION_RELEASE)
    endif()
endif()
if(NOT _mj_moc_exe)
    get_filename_component(_mj_qt_prefix "${Qt6Core_DIR}/../../.." ABSOLUTE)
    find_program(_mj_moc_exe NAMES moc moc.exe HINTS "${_mj_qt_prefix}/bin")
else()
    get_filename_component(_mj_qt_prefix "${_mj_moc_exe}/../.." ABSOLUTE)
endif()

set(MJ_SOURCES
    src/main.cpp
    src/AutoPlay.cpp
    src/AutoPlay.h
    src/FontProbe.cpp
    src/FontProbe.h
    src/GenTiles.cpp
    src/GenTiles.h
    src/SelfTest.cpp
    src/SelfTest.h
    src/net/NetClient.cpp
    src/net/NetClient.h
    src/net/Protocol.cpp
    src/net/Protocol.h
    src/model/Tile.cpp
    src/model/Tile.h
    src/model/TableModel.cpp
    src/model/TableModel.h
    src/model/AutoPolicy.cpp
    src/model/AutoPolicy.h
    src/model/Sound.cpp
    src/model/Sound.h
    src/model/ReplayModel.cpp
    src/model/ReplayModel.h
    src/model/TenhouLog.cpp
    src/model/TenhouLog.h
    src/model/Settings.cpp
    src/model/Settings.h
    src/model/Theme.cpp
    src/model/Theme.h
    src/ui/AutoBar.cpp
    src/ui/AutoBar.h
    src/ui/TileFont.cpp
    src/ui/TileFont.h
    src/i18n/Lang.cpp
    src/i18n/Lang.h
    src/ui/TileRenderer.cpp
    src/ui/TileRenderer.h
    src/ui/TableLayout.cpp
    src/ui/TableLayout.h
    src/ui/TableView.cpp
    src/ui/TableView.h
    src/ui/ActionBar.cpp
    src/ui/ActionBar.h
    src/ui/LobbyDialog.cpp
    src/ui/LobbyDialog.h
    src/ui/ResultDialog.cpp
    src/ui/ResultDialog.h
    src/ui/MainWindow.cpp
    src/ui/MainWindow.h
    src/ui/ReplayWindow.cpp
    src/ui/ReplayWindow.h
    src/ui/SettingsDialog.cpp
    src/ui/SettingsDialog.h
    src/ui/WallView.cpp
    src/ui/WallView.h
)

# 含 Q_OBJECT 的头文件（预生成模式下逐个跑 moc）
set(MJ_MOC_HEADERS
    src/AutoPlay.h
    src/net/NetClient.h
    src/model/TableModel.h
    src/model/Sound.h
    src/ui/AutoBar.h
    src/ui/TableView.h
    src/ui/ActionBar.h
    src/ui/LobbyDialog.h
    src/ui/ResultDialog.h
    src/ui/MainWindow.h
    src/ui/ReplayWindow.h
    src/ui/SettingsDialog.h
    src/ui/WallView.h
)

if(NOT MJ_USE_AUTOMOC)
    if(NOT _mj_moc_exe)
        message(FATAL_ERROR "MJ_USE_AUTOMOC=OFF 但未找到 moc 可执行文件")
    endif()
    set(MJ_MOC_SOURCES "")
    set(_mj_moc_dir "${CMAKE_CURRENT_BINARY_DIR}/mj_moc")
    file(MAKE_DIRECTORY "${_mj_moc_dir}")
    foreach(_hdr IN LISTS MJ_MOC_HEADERS)
        get_filename_component(_name "${_hdr}" NAME_WE)
        set(_out "${_mj_moc_dir}/moc_${_name}.cpp")
        execute_process(
            COMMAND "${_mj_moc_exe}"
                    -I "${CMAKE_CURRENT_SOURCE_DIR}/src"
                    -I "${_mj_qt_prefix}/include"
                    -I "${_mj_qt_prefix}/include/QtCore"
                    -I "${_mj_qt_prefix}/include/QtGui"
                    -I "${_mj_qt_prefix}/include/QtWidgets"
                    -I "${_mj_qt_prefix}/include/QtNetwork"
                    "${CMAKE_CURRENT_SOURCE_DIR}/${_hdr}"
                    -o "${_out}"
            RESULT_VARIABLE _mj_rc
            ERROR_VARIABLE _mj_err
            TIMEOUT 120)
        if(NOT _mj_rc STREQUAL "0")
            message(FATAL_ERROR "moc 预生成失败：${_hdr}\n${_mj_err}")
        endif()
        list(APPEND MJ_MOC_SOURCES "${_out}")
    endforeach()
    list(APPEND MJ_SOURCES ${MJ_MOC_SOURCES})
    message(STATUS "已预生成 ${MJ_MOC_SOURCES} 个 moc 源文件到 ${_mj_moc_dir}")
endif()

# 牌面矢量素材（SVG）：以**目录**形式随 exe 发布（build.ps1 会拷 assets/tiles → <exe>/tiles）。
# 这样做是为了让美术**改完 SVG 重启客户端即生效，无需重新编译**。
# 下面同时把 assets/tiles.qrc 编进去作为**兜底**：目录缺失时仍能显示牌面。
list(APPEND MJ_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/assets/tiles.qrc)

# 语言文件（文案表）：同样**目录优先**（build.ps1 拷 assets/i18n → <exe>/i18n），
# 改文案不用重新编译；assets/i18n.qrc 只是目录缺失时的兜底（和 tiles 一个套路）。
list(APPEND MJ_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/assets/i18n.qrc)

# 音效（离线合成的 WAV，见 tools/gen-sfx.mjs）：同样**目录优先**
# （build.ps1 拷 assets/sfx → <exe>/sfx），qrc 只是兜底。
#   ⚠ AUTORCC 在本项目的**预生成 moc 回退模式**下不一定被打开（MJ_USE_AUTOMOC=OFF 时
#   只关掉了 AUTOMOC），所以这里显式打开 —— 否则 `.qrc` 会被当成普通源文件忽略，
#   `:/sfx/*.wav` 全不存在，自检的「qrc 兜底」那条就会红。
set(CMAKE_AUTORCC ON)
list(APPEND MJ_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/assets/sfx.qrc)

qt_add_executable(mahjong-client WIN32 ${MJ_SOURCES})

if(MJ_USE_AUTOMOC)
    # 关闭 moc_predefs.h 生成（该步骤要求构建期再启动一次编译器，且对本项目无必要）
    set_target_properties(mahjong-client PROPERTIES
        AUTOMOC ON
        AUTOMOC_COMPILER_PREDEFINES OFF)
else()
    # 预生成模式下关闭 AUTOMOC，避免重复生成
    set_target_properties(mahjong-client PROPERTIES AUTOMOC OFF)
endif()

target_include_directories(mahjong-client PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

target_link_libraries(mahjong-client PRIVATE Qt6::Widgets Qt6::Network Qt6::Svg)

# ---------------------------------------------------------------------------
# 音效后端：**优先 Qt Multimedia**（`QSoundEffect`），找不到才退回系统原生 API
#
# 选型理由（2026-09 用户拍板）：客户端未来要支持多平台，**跨平台的 Qt API 比平台原生 API
# 更值**；体积不再是约束（Qt Multimedia 会连带 FFmpeg 约 20MB + `plugins/multimedia/`，
# 用户明确表示不用为此担心）。Qt 是 **LGPLv3 动态链接**，所以随包分发 QtMultimedia 与
# FFmpeg 后端时**必须保留许可文本并让用户能替换这些库** —— 见 licenses/ 与
# docs/THIRD-PARTY.md（这两处也是本次一并更新的）。
#
# 分层仍然是"三条路都编得过"，只是**优先级反过来**：
#   ① 有 Qt6::Multimedia → `QSoundEffect`（低延迟、可叠放，跨平台一致）；
#   ② Windows 且没有 Multimedia → `winmm` 的 `PlaySound`（兜底，系统自带）；
#   ③ 其余 → 静默（界面上的音效开关置灰并写明原因）。
# ---------------------------------------------------------------------------
find_package(Qt6 QUIET COMPONENTS Multimedia)
if(TARGET Qt6::Multimedia)
    target_link_libraries(mahjong-client PRIVATE Qt6::Multimedia)
    target_compile_definitions(mahjong-client PRIVATE MAHJONG_HAVE_MULTIMEDIA=1)
    message(STATUS "音效：Qt6::Multimedia（QSoundEffect，跨平台）")
elseif(WIN32)
    target_link_libraries(mahjong-client PRIVATE winmm)
    target_compile_definitions(mahjong-client PRIVATE MAHJONG_HAVE_MULTIMEDIA=0)
    message(STATUS "音效：没有 Qt6::Multimedia，退回 Windows winmm（PlaySound）")
else()
    target_compile_definitions(mahjong-client PRIVATE MAHJONG_HAVE_MULTIMEDIA=0)
    message(STATUS "音效：本构建无可用后端（静默，开关置灰）")
endif()

# ---------------------------------------------------------------------------
# 材质包支持 **zip**（可选能力）
#
# Qt 没有公开的 zip 读取 API；`QZipReader` 在 `QtCore/private/qzipreader_p.h`。
# 我们把这条依赖做成**可选的**：
#   · 有 Qt6::CorePrivate（装了私有头/配置）→ 定义 MAHJONG_HAVE_ZIP=1，支持 .zip 材质包；
#   · 没有（精简安装 / 别人自己编的 Qt）→ 只支持**目录**材质包，其余照常工作。
# 这样"换个 Qt 就编不过"这件事不会发生（见 docs/THEME.md 的"zip 支持"一节）。
# ---------------------------------------------------------------------------
find_package(Qt6 QUIET COMPONENTS CorePrivate)
if(TARGET Qt6::CorePrivate)
    target_link_libraries(mahjong-client PRIVATE Qt6::CorePrivate)
    target_compile_definitions(mahjong-client PRIVATE MAHJONG_HAVE_ZIP=1)
    message(STATUS "材质包：支持 .zip（Qt6::CorePrivate 可用）")
else()
    target_compile_definitions(mahjong-client PRIVATE MAHJONG_HAVE_ZIP=0)
    message(STATUS "材质包：只支持目录形式（没有 Qt6::CorePrivate）")
endif()

if(MSVC)
    target_compile_options(mahjong-client PRIVATE /W4 /utf-8)
else()
    target_compile_options(mahjong-client PRIVATE -Wall -Wextra)
endif()
