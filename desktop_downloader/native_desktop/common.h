// Shared pieces of the native desktop window: text conversion, the colour palette of the
// classic (5.x) desktop window, fonts, the JSON line protocol and small control helpers.
#pragma once

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <objbase.h>
#include <ole2.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <uxtheme.h>
#include <vssym32.h>
#include <wincodec.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "json.h"

#ifdef _MSC_VER
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "gdiplus.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "uxtheme.lib")
#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "uuid.lib")
#pragma comment(lib, "winmm.lib")
#endif

namespace md {

using json::Value;

constexpr UINT WM_STATE = WM_APP + 1;
constexpr UINT WM_INPUT_CLOSED = WM_APP + 2;

// ------------------------------------------------------------------------------------------- text
inline std::wstring W(const std::string &s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), &w[0], n);
    return w;
}

inline std::string U8(const std::wstring &w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), &s[0], n, nullptr, nullptr);
    return s;
}

inline std::wstring windowText(HWND h) {
    int n = GetWindowTextLengthW(h);
    std::wstring w(static_cast<size_t>(n) + 1, L'\0');
    n = GetWindowTextW(h, &w[0], n + 1);
    w.resize(static_cast<size_t>(std::max(0, n)));
    return w;
}

inline std::string getText(HWND h) { return U8(windowText(h)); }

// Edit controls want \r\n; the controller works with \n.
inline std::string toWindowsNewlines(const std::string &s) {
    std::string out;
    out.reserve(s.size() + 16);
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r')) out += '\r';
        out += s[i];
    }
    return out;
}

inline std::string fromWindowsNewlines(const std::string &s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s)
        if (c != '\r') out += c;
    return out;
}

// Only touches the control when the text really changed (no flicker, keeps the caret).
inline void setText(HWND h, const std::string &s) {
    if (!h) return;
    std::wstring w = W(s);
    if (windowText(h) != w) SetWindowTextW(h, w.c_str());
}

// ----------------------------------------------------------------------------------------- colours
constexpr COLORREF hex(unsigned v) { return RGB((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF); }

// The same names and values as theme.py, so both windows look the same.
struct Palette {
    COLORREF BG, SIDEBAR, SURFACE, SURFACE_2, SURFACE_3, BORDER, BORDER_SOFT, TEXT, MUTED, FAINT, ACCENT,
        ACCENT_HOVER, ACCENT_SOFT, ON_ACCENT, SELECT, SELECT_TEXT, SUCCESS, WARNING, DANGER, DANGER_SOFT, STRIPE,
        HEADING, TOOL_HOVER, TOOL_PRESS, TOOL_EDGE, INFO_SOFT;
};

const Palette kLight{hex(0xF0F0F0), hex(0xFFFFFF), hex(0xFFFFFF), hex(0xFFFFFF), hex(0xE5E5E5), hex(0xABADB3),
                     hex(0xD5D5D5), hex(0x000000), hex(0x444444), hex(0x6D6D6D), hex(0x0066CC), hex(0x004C99),
                     hex(0xCCE8FF), hex(0xFFFFFF), hex(0x0078D7), hex(0xFFFFFF), hex(0x008000), hex(0x9D5D00),
                     hex(0xC00000), hex(0xFDE7E9), hex(0xFFFFFF), hex(0xF0F0F0), hex(0xE5F3FF), hex(0xCCE8FF),
                     hex(0x99D1FF), hex(0xE5F3FF)};

const Palette kDark{hex(0x0B0F17), hex(0x0E131D), hex(0x121826), hex(0x171E2E), hex(0x1F2840), hex(0x29344D),
                    hex(0x1C2538), hex(0xEEF2FF), hex(0x8F9AB4), hex(0x5D6782), hex(0x7C6CFF), hex(0x6A59F0),
                    hex(0x28224F), hex(0xFFFFFF), hex(0x2E2766), hex(0xFFFFFF), hex(0x34D399), hex(0xFBBF24),
                    hex(0xFB7185), hex(0x361721), hex(0x141B2A), hex(0x171E2E), hex(0x1D2538), hex(0x252F48),
                    hex(0x29344D), hex(0x13233F)};

inline COLORREF mix(COLORREF a, COLORREF b, double t) {
    auto ch = [t](int x, int y) { return static_cast<int>(x + (y - x) * t + 0.5); };
    return RGB(ch(GetRValue(a), GetRValue(b)), ch(GetGValue(a), GetGValue(b)), ch(GetBValue(a), GetBValue(b)));
}

struct Theme {
    bool dark = false;
    Palette p = kLight;
    int dpi = 96;
    // ui: the Windows message font (Segoe UI 9 pt); body/heading: the list fonts of the dark theme.
    HFONT ui = nullptr, uiBold = nullptr, body = nullptr, heading = nullptr, big = nullptr, mono = nullptr;
    std::unordered_map<COLORREF, HBRUSH> brushes;
    std::unordered_map<unsigned long long, HPEN> pens;

    int S(int v) const { return MulDiv(v, dpi, 96); }
    double Sf(double v) const { return v * dpi / 96.0; }

    HBRUSH brush(COLORREF c) {
        auto it = brushes.find(c);
        if (it != brushes.end()) return it->second;
        HBRUSH b = CreateSolidBrush(c);
        brushes[c] = b;
        return b;
    }
    HPEN pen(COLORREF c, int width = 1) {
        unsigned long long key = (static_cast<unsigned long long>(width) << 32) | c;
        auto it = pens.find(key);
        if (it != pens.end()) return it->second;
        HPEN h = CreatePen(PS_SOLID, width, c);
        pens[key] = h;
        return h;
    }

    void setDark(bool on) {
        dark = on;
        p = on ? kDark : kLight;
    }

    static HFONT makeFont(int px, int weight, const wchar_t *face) {
        return CreateFontW(-px, 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                           CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, face);
    }

    void buildFonts() {
        for (HFONT *f : {&ui, &uiBold, &body, &heading, &big, &mono})
            if (*f) DeleteObject(*f);
        ui = makeFont(MulDiv(9, dpi, 72), FW_NORMAL, L"Segoe UI");
        uiBold = makeFont(MulDiv(9, dpi, 72), FW_BOLD, L"Segoe UI");
        body = makeFont(S(13), FW_NORMAL, L"Segoe UI");
        heading = makeFont(S(12), FW_SEMIBOLD, L"Segoe UI");
        big = makeFont(MulDiv(14, dpi, 72), FW_BOLD, L"Segoe UI");
        mono = makeFont(MulDiv(9, dpi, 72), FW_NORMAL, L"Consolas");
    }

    // Lists and the category tree use the system font in the light theme and the 13 px body font
    // in the dark theme, like the Tk window did.
    HFONT listFont() const { return dark ? body : ui; }
    HFONT headingFont() const { return dark ? heading : ui; }
    int rowHeight() const { return dark ? S(25) : S(19); }
    int treeRowHeight() const { return dark ? S(27) : S(19); }
};

inline Theme g_theme;
inline HWND g_main = nullptr;
inline HICON g_icon = nullptr;
inline HINSTANCE g_inst = nullptr;

inline int S(int v) { return g_theme.S(v); }

inline void setDarkTitleBar(HWND h) {
    BOOL on = g_theme.dark ? TRUE : FALSE;
    DwmSetWindowAttribute(h, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &on, sizeof on);
}

// Dark scroll bars, combo boxes and tree glyphs where Windows offers them.
inline void themeControl(HWND h, const wchar_t *darkTheme = L"DarkMode_Explorer", const wchar_t *lightTheme = L"Explorer") {
    SetWindowTheme(h, g_theme.dark ? darkTheme : lightTheme, nullptr);
}

// Windows 10 1809+ dark context menus (uxtheme ordinal 135 / 136).
inline void enableDarkMenus(bool dark) {
    HMODULE ux = GetModuleHandleW(L"uxtheme.dll");
    if (!ux) ux = LoadLibraryExW(L"uxtheme.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!ux) return;
    using SetMode = int(WINAPI *)(int);
    using Flush = void(WINAPI *)();
    auto setMode = reinterpret_cast<SetMode>(GetProcAddress(ux, MAKEINTRESOURCEA(135)));
    auto flush = reinterpret_cast<Flush>(GetProcAddress(ux, MAKEINTRESOURCEA(136)));
    if (setMode) setMode(dark ? 2 /* ForceDark */ : 3 /* ForceLight */);
    if (flush) flush();
}

inline void allowDarkWindow(HWND h, bool dark) {
    HMODULE ux = GetModuleHandleW(L"uxtheme.dll");
    if (!ux) return;
    using Allow = bool(WINAPI *)(HWND, bool);
    auto allow = reinterpret_cast<Allow>(GetProcAddress(ux, MAKEINTRESOURCEA(133)));
    if (allow) allow(h, dark);
}

// --------------------------------------------------------------------------------------- protocol
inline std::mutex g_out;
inline bool g_testMode = false;

inline void sendRaw(const Value &msg) {
    if (g_testMode) return;
    std::string line = msg.dump() + "\n";
    std::lock_guard<std::mutex> lock(g_out);
    HANDLE out = GetStdHandle(STD_OUTPUT_HANDLE);
    if (out == INVALID_HANDLE_VALUE || out == nullptr) return;
    DWORD written = 0;
    WriteFile(out, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
}

inline void send(const std::string &command, const Value &value = Value(), const std::vector<std::string> *keys = nullptr) {
    Value msg = Value::object();
    msg.set("command", command);
    msg.set("value", value);
    Value list = Value::array();
    if (keys)
        for (const auto &k : *keys) list.push(k);
    msg.set("keys", std::move(list));
    sendRaw(msg);
}

// ----------------------------------------------------------------------------------------- drawing
inline void fill(HDC dc, const RECT &rc, COLORREF c) { FillRect(dc, &rc, g_theme.brush(c)); }

inline void frame(HDC dc, const RECT &rc, COLORREF c) { FrameRect(dc, &rc, g_theme.brush(c)); }

inline void hline(HDC dc, int x0, int x1, int y, COLORREF c) {
    RECT r{x0, y, x1, y + 1};
    fill(dc, r, c);
}

inline void vline(HDC dc, int x, int y0, int y1, COLORREF c) {
    RECT r{x, y0, x + 1, y1};
    fill(dc, r, c);
}

inline void text(HDC dc, const std::wstring &s, RECT rc, COLORREF color, HFONT font,
                 UINT flags = DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_END_ELLIPSIS | DT_NOPREFIX) {
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, color);
    HGDIOBJ old = SelectObject(dc, font);
    DrawTextW(dc, s.c_str(), static_cast<int>(s.size()), &rc, flags);
    SelectObject(dc, old);
}

inline SIZE measure(HDC dc, const std::wstring &s, HFONT font) {
    HGDIOBJ old = SelectObject(dc, font);
    SIZE sz{0, 0};
    GetTextExtentPoint32W(dc, s.c_str(), static_cast<int>(s.size()), &sz);
    SelectObject(dc, old);
    return sz;
}

inline SIZE measureWindow(const std::wstring &s, HFONT font) {
    HDC dc = GetDC(nullptr);
    SIZE sz = measure(dc, s, font);
    ReleaseDC(nullptr, dc);
    return sz;
}

// Flicker-free painting: everything is drawn into a memory bitmap, then copied once.
class BufferedPaint {
public:
    explicit BufferedPaint(HWND h) : hwnd_(h) {
        target_ = BeginPaint(h, &ps_);
        GetClientRect(h, &rc_);
        dc_ = CreateCompatibleDC(target_);
        bmp_ = CreateCompatibleBitmap(target_, std::max<LONG>(1, rc_.right), std::max<LONG>(1, rc_.bottom));
        old_ = SelectObject(dc_, bmp_);
    }
    ~BufferedPaint() {
        BitBlt(target_, 0, 0, rc_.right, rc_.bottom, dc_, 0, 0, SRCCOPY);
        SelectObject(dc_, old_);
        DeleteObject(bmp_);
        DeleteDC(dc_);
        EndPaint(hwnd_, &ps_);
    }
    HDC dc() const { return dc_; }
    const RECT &rect() const { return rc_; }

private:
    HWND hwnd_;
    PAINTSTRUCT ps_{};
    HDC target_ = nullptr, dc_ = nullptr;
    HBITMAP bmp_ = nullptr;
    HGDIOBJ old_ = nullptr;
    RECT rc_{};
};

inline void place(HWND h, int x, int y, int w, int hgt) {
    if (h) SetWindowPos(h, nullptr, x, y, std::max(0, w), std::max(0, hgt), SWP_NOZORDER | SWP_NOACTIVATE);
}

inline void show(HWND h, bool on) {
    if (!h) return;
    bool now = (GetWindowLongPtrW(h, GWL_STYLE) & WS_VISIBLE) != 0;
    if (now != on) ShowWindow(h, on ? SW_SHOWNA : SW_HIDE);
}

inline void enable(HWND h, bool on) {
    if (h && (IsWindowEnabled(h) != FALSE) != on) EnableWindow(h, on ? TRUE : FALSE);
}

inline std::wstring percentText(double v) {
    wchar_t b[32];
    swprintf(b, 32, L"%.0f%%", v * 100.0);
    return b;
}

inline void copyToClipboard(HWND owner, const std::wstring &s) {
    if (!OpenClipboard(owner)) return;
    EmptyClipboard();
    HGLOBAL mem = GlobalAlloc(GMEM_MOVEABLE, (s.size() + 1) * sizeof(wchar_t));
    if (mem) {
        memcpy(GlobalLock(mem), s.c_str(), (s.size() + 1) * sizeof(wchar_t));
        GlobalUnlock(mem);
        SetClipboardData(CF_UNICODETEXT, mem);
    }
    CloseClipboard();
}

inline std::wstring clipboardText(HWND owner) {
    std::wstring out;
    if (!OpenClipboard(owner)) return out;
    HANDLE h = GetClipboardData(CF_UNICODETEXT);
    if (h) {
        if (auto *p = static_cast<const wchar_t *>(GlobalLock(h))) {
            out = p;
            GlobalUnlock(h);
        }
    }
    CloseClipboard();
    return out;
}

inline void centerOn(HWND h, HWND owner) {
    RECT me;
    GetWindowRect(h, &me);
    int w = me.right - me.left, hh = me.bottom - me.top;
    RECT area{};
    if (owner && IsWindowVisible(owner) && !IsIconic(owner)) {
        GetWindowRect(owner, &area);
    } else {
        HMONITOR mon = MonitorFromPoint(POINT{0, 0}, MONITOR_DEFAULTTOPRIMARY);
        MONITORINFO mi{sizeof mi};
        GetMonitorInfoW(mon, &mi);
        area = mi.rcWork;
    }
    int x = area.left + ((area.right - area.left) - w) / 2;
    int y = area.top + ((area.bottom - area.top) - hh) / 3;
    HMONITOR mon = MonitorFromRect(&area, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi{sizeof mi};
    GetMonitorInfoW(mon, &mi);
    x = std::max(static_cast<int>(mi.rcWork.left), std::min(x, static_cast<int>(mi.rcWork.right) - w));
    y = std::max(static_cast<int>(mi.rcWork.top), std::min(y, static_cast<int>(mi.rcWork.bottom) - hh));
    SetWindowPos(h, nullptr, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
}

}  // namespace md
