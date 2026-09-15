# 立直麻将 Qt6 客户端（C++17 / Qt6 Widgets + Network / MinGW 构建）
cmake_minimum_required(VERSION 3.19)

project(mahjong-client VERSION 1.0.0 LANGUAGES CXX)

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
    src/model/ReplayModel.cpp
    src/model/ReplayModel.h
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
    src/ui/WallView.cpp
    src/ui/WallView.h
)

# 含 Q_OBJECT 的头文件（预生成模式下逐个跑 moc）
set(MJ_MOC_HEADERS
    src/AutoPlay.h
    src/net/NetClient.h
    src/model/TableModel.h
    src/ui/AutoBar.h
    src/ui/TableView.h
    src/ui/ActionBar.h
    src/ui/LobbyDialog.h
    src/ui/ResultDialog.h
    src/ui/MainWindow.h
    src/ui/ReplayWindow.h
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

if(MSVC)
    target_compile_options(mahjong-client PRIVATE /W4 /utf-8)
else()
    target_compile_options(mahjong-client PRIVATE -Wall -Wextra)
endif()
