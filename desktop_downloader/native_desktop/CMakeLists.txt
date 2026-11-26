cmake_minimum_required(VERSION 3.21)
project(MediaDownloaderDesktop LANGUAGES CXX)

# A single self-contained Win32 executable: no UI toolkit or runtime DLLs to ship.
add_executable(MediaDownloaderDesktop WIN32 main.cpp desktop.rc)
target_compile_features(MediaDownloaderDesktop PRIVATE cxx_std_17)
target_link_libraries(MediaDownloaderDesktop PRIVATE comctl32 uxtheme dwmapi shell32 gdi32 user32 ole32 windowscodecs uuid)
if(MSVC)
    # The manifest (common controls 6, DPI awareness) comes from desktop.rc.
    target_compile_options(MediaDownloaderDesktop PRIVATE /utf-8 /W3 /MT$<$<CONFIG:Debug>:d>)
    target_link_options(MediaDownloaderDesktop PRIVATE /MANIFEST:NO)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(json_test json_test.cpp)
    target_compile_features(json_test PRIVATE cxx_std_17)
    add_test(NAME json COMMAND json_test)
    add_test(NAME native_desktop_smoke COMMAND MediaDownloaderDesktop --self-test)
endif()
