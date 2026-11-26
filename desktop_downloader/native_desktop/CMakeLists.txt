cmake_minimum_required(VERSION 3.21)
project(MediaDownloaderDesktop LANGUAGES CXX)

# A single self-contained Win32 executable: the whole desktop UI, with no UI toolkit or runtime
# DLLs to ship. Headers hold the window parts (toolbar, list, dialogs...).
add_executable(MediaDownloaderDesktop WIN32 main.cpp desktop.rc)
target_compile_features(MediaDownloaderDesktop PRIVATE cxx_std_17)
target_compile_definitions(MediaDownloaderDesktop PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_link_libraries(MediaDownloaderDesktop PRIVATE comctl32 uxtheme dwmapi shell32 gdi32 gdiplus msimg32 user32 ole32 oleaut32
                      windowscodecs uuid winmm)
if(MSVC)
    # The manifest (common controls 6, per-monitor DPI awareness) comes from desktop.rc.
    target_compile_options(MediaDownloaderDesktop PRIVATE /utf-8 /W3 /permissive- /MT$<$<CONFIG:Debug>:d>)
    target_link_options(MediaDownloaderDesktop PRIVATE /MANIFEST:NO)
    set_property(TARGET MediaDownloaderDesktop PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(json_test json_test.cpp)
    target_compile_features(json_test PRIVATE cxx_std_17)
    add_test(NAME json COMMAND json_test)
    add_test(NAME native_desktop_smoke COMMAND MediaDownloaderDesktop --self-test)
endif()
