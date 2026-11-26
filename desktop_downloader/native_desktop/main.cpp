// Media Downloader desktop window: a small native Win32 front end.
//
// The Python controller owns every download and setting. It streams the whole visible state to this
// process as one JSON object per line on stdin; this process answers with one JSON command per line
// on stdout (see native_shell.py). No UI toolkit is bundled: only Windows' own controls are used.
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <uxtheme.h>

#include <algorithm>
#include <cstring>
#include <cwchar>
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "json.h"

#ifdef _MSC_VER
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "uxtheme.lib")
#pragma comment(lib, "dwmapi.lib")
#endif

namespace {

using json::Value;

constexpr UINT WM_STATE = WM_APP + 1;
constexpr UINT WM_INPUT_CLOSED = WM_APP + 2;
constexpr UINT_PTR TIMER_SELECTION = 1;
constexpr UINT_PTR TIMER_BATCH = 2;

// ------------------------------------------------------------------------------------------ text
std::wstring W(const std::string &s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), &w[0], n);
    return w;
}

std::string U8(const std::wstring &w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), &s[0], n, nullptr, nullptr);
    return s;
}

std::string getText(HWND h) {
    int n = GetWindowTextLengthW(h);
    std::wstring w(static_cast<size_t>(n) + 1, L'\0');
    n = GetWindowTextW(h, &w[0], n + 1);
    w.resize(static_cast<size_t>(n));
    return U8(w);
}

// Edit controls want \r\n; the controller works with \n.
std::string toWindowsNewlines(const std::string &s) {
    std::string out;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r')) out += '\r';
        out += s[i];
    }
    return out;
}

void setText(HWND h, const std::string &s) {
    std::wstring w = W(s);
    if (getText(h) != s) SetWindowTextW(h, w.c_str());
}

// ----------------------------------------------------------------------------------------- theme
struct Palette {
    COLORREF bg, surface, panel, ink, border, selection, muted, accent, primary, good, bad;
};

constexpr COLORREF rgb(int r, int g, int b) { return RGB(r, g, b); }

const Palette kDark{rgb(0x0e, 0x16, 0x21), rgb(0x17, 0x21, 0x2b), rgb(0x20, 0x2b, 0x36), rgb(0xed, 0xf4, 0xfa),
                    rgb(0x2c, 0x3c, 0x4b), rgb(0x2b, 0x52, 0x78), rgb(0xa0, 0xb1, 0xc0), rgb(0x29, 0x8c, 0xc5),
                    rgb(0x08, 0x6a, 0x9c), rgb(0x5c, 0xe5, 0xb4), rgb(0xff, 0xa9, 0xaa)};
const Palette kLight{rgb(0xe9, 0xef, 0xf4), rgb(0xff, 0xff, 0xff), rgb(0xf2, 0xf6, 0xfa), rgb(0x18, 0x25, 0x33),
                     rgb(0xd7, 0xe3, 0xeb), rgb(0xdb, 0xee, 0xfb), rgb(0x4e, 0x64, 0x77), rgb(0x29, 0x8c, 0xc5),
                     rgb(0x08, 0x6a, 0x9c), rgb(0x08, 0x73, 0x4d), rgb(0xa7, 0x23, 0x3a)};

struct Theme {
    bool dark = false;
    Palette p = kLight;
    HBRUSH bg = nullptr, surface = nullptr, panel = nullptr;
    HFONT font = nullptr, bold = nullptr, title = nullptr;
    int dpi = 96;

    int S(int v) const { return MulDiv(v, dpi, 96); }

    void build(bool wantDark) {
        dark = wantDark;
        p = dark ? kDark : kLight;
        for (HBRUSH *b : {&bg, &surface, &panel})
            if (*b) DeleteObject(*b);
        bg = CreateSolidBrush(p.bg);
        surface = CreateSolidBrush(p.surface);
        panel = CreateSolidBrush(p.panel);
    }

    void buildFonts() {
        for (HFONT *f : {&font, &bold, &title})
            if (*f) DeleteObject(*f);
        font = CreateFontW(-MulDiv(9, dpi, 72), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        bold = CreateFontW(-MulDiv(9, dpi, 72), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        title = CreateFontW(-MulDiv(12, dpi, 72), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    }
};

Theme g_theme;
HWND g_main = nullptr;
HICON g_icon = nullptr;

// Applies the title-bar and scroll-bar flavour that matches the chosen theme.
void themeWindow(HWND h, const wchar_t *dark, const wchar_t *light) {
    SetWindowTheme(h, g_theme.dark ? dark : light, nullptr);
}

void setDarkTitleBar(HWND h) {
    BOOL on = g_theme.dark ? TRUE : FALSE;
    DwmSetWindowAttribute(h, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &on, sizeof on);
}

// ------------------------------------------------------------------------------------- messaging
std::mutex g_out;

void send(const std::string &command, const Value &value = Value(), const std::vector<std::string> *keys = nullptr) {
    Value msg = Value::object();
    msg.set("command", command);
    msg.set("value", value);
    Value list = Value::array();
    if (keys)
        for (const auto &k : *keys) list.push(k);
    msg.set("keys", std::move(list));
    std::string line = msg.dump() + "\n";
    std::lock_guard<std::mutex> lock(g_out);
    HANDLE out = GetStdHandle(STD_OUTPUT_HANDLE);
    if (out == INVALID_HANDLE_VALUE || out == nullptr) return;
    DWORD written = 0;
    WriteFile(out, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
}

std::mutex g_inbox;
std::unique_ptr<Value> g_latest;
std::atomic<bool> g_pending{false};

void readerThread() {
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    std::string buffer;
    char chunk[65536];
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(in, chunk, sizeof chunk, &got, nullptr) || got == 0) break;
        buffer.append(chunk, got);
        size_t start = 0, nl;
        while ((nl = buffer.find('\n', start)) != std::string::npos) {
            std::string line = buffer.substr(start, nl - start);
            start = nl + 1;
            auto parsed = std::make_unique<Value>();
            if (!json::parse(line, *parsed) || !parsed->isObject()) continue;
            {
                std::lock_guard<std::mutex> lock(g_inbox);
                g_latest = std::move(parsed);
            }
            if (!g_pending.exchange(true)) PostMessageW(g_main, WM_STATE, 0, 0);
        }
        buffer.erase(0, start);
        if (buffer.size() > (64u << 20)) buffer.clear();
    }
    PostMessageW(g_main, WM_INPUT_CLOSED, 0, 0);
}

// -------------------------------------------------------------------------------------- widgets
int g_nextId = 1000;
std::map<int, std::function<void()>> g_clicks;

struct ButtonInfo {
    bool primary = false;
    bool check = false;
    bool on = false;
};
std::map<HWND, ButtonInfo> g_buttons;

HWND make(HWND parent, const wchar_t *cls, const std::wstring &text, DWORD style, DWORD ex = 0) {
    HWND h = CreateWindowExW(ex, cls, text.c_str(), WS_CHILD | WS_VISIBLE | style, 0, 0, 10, 10, parent,
                             reinterpret_cast<HMENU>(static_cast<INT_PTR>(g_nextId++)), GetModuleHandleW(nullptr), nullptr);
    SendMessageW(h, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.font), TRUE);
    return h;
}

HWND label(HWND parent, const std::string &text = "", DWORD style = 0) {
    return make(parent, L"STATIC", W(text), SS_NOPREFIX | style);
}

HWND button(HWND parent, const std::string &text, std::function<void()> action, bool primary = false) {
    HWND h = make(parent, L"BUTTON", W(text), BS_OWNERDRAW | WS_TABSTOP);
    g_buttons[h] = ButtonInfo{primary, false, false};
    g_clicks[GetDlgCtrlID(h)] = std::move(action);
    return h;
}

HWND checkbox(HWND parent, const std::string &text) {
    HWND h = make(parent, L"BUTTON", W(text), BS_OWNERDRAW | WS_TABSTOP);
    g_buttons[h] = ButtonInfo{false, true, false};
    g_clicks[GetDlgCtrlID(h)] = [h] {
        g_buttons[h].on = !g_buttons[h].on;
        InvalidateRect(h, nullptr, FALSE);
    };
    return h;
}

void setChecked(HWND h, bool on) {
    g_buttons[h].on = on;
    InvalidateRect(h, nullptr, FALSE);
}
bool isChecked(HWND h) { return g_buttons[h].on; }

HWND edit(HWND parent, bool multiline = false) {
    DWORD style = WS_TABSTOP | ES_AUTOHSCROLL;
    if (multiline) style = WS_TABSTOP | ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_VSCROLL;
    HWND h = make(parent, L"EDIT", L"", style | WS_BORDER);
    if (multiline) themeWindow(h, L"DarkMode_Explorer", L"Explorer");
    return h;
}

HWND combo(HWND parent, const std::vector<std::string> &items) {
    HWND h = make(parent, L"COMBOBOX", L"", CBS_DROPDOWNLIST | WS_TABSTOP | WS_VSCROLL);
    for (const auto &item : items) SendMessageW(h, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(W(item).c_str()));
    themeWindow(h, L"DarkMode_CFD", L"Explorer");
    SendMessageW(h, CB_SETCURSEL, 0, 0);
    return h;
}

std::string comboText(HWND h) {
    int i = static_cast<int>(SendMessageW(h, CB_GETCURSEL, 0, 0));
    if (i < 0) return {};
    int n = static_cast<int>(SendMessageW(h, CB_GETLBTEXTLEN, i, 0));
    std::wstring w(static_cast<size_t>(n) + 1, L'\0');
    SendMessageW(h, CB_GETLBTEXT, i, reinterpret_cast<LPARAM>(&w[0]));
    w.resize(static_cast<size_t>(n));
    return U8(w);
}

void setComboText(HWND h, const std::string &text) {
    int i = static_cast<int>(SendMessageW(h, CB_FINDSTRINGEXACT, -1, reinterpret_cast<LPARAM>(W(text).c_str())));
    if (i >= 0) SendMessageW(h, CB_SETCURSEL, i, 0);
}

void place(HWND h, int x, int y, int w, int hgt) { SetWindowPos(h, nullptr, x, y, w, hgt, SWP_NOZORDER | SWP_NOACTIVATE); }

void drawButton(const DRAWITEMSTRUCT *d) {
    const Palette &p = g_theme.p;
    auto info = g_buttons[d->hwndItem];
    bool disabled = (d->itemState & ODS_DISABLED) != 0;
    bool pressed = (d->itemState & ODS_SELECTED) != 0;
    bool hot = (d->itemState & (ODS_HOTLIGHT | ODS_FOCUS)) != 0;
    HDC dc = d->hDC;
    RECT rc = d->rcItem;
    wchar_t text[256];
    GetWindowTextW(d->hwndItem, text, 256);
    SetBkMode(dc, TRANSPARENT);
    SelectObject(dc, g_theme.font);
    if (info.check) {
        FillRect(dc, &rc, g_theme.bg);
        int box = g_theme.S(16);
        RECT b{rc.left, (rc.top + rc.bottom - box) / 2, rc.left + box, (rc.top + rc.bottom - box) / 2 + box};
        HBRUSH fill = CreateSolidBrush(info.on ? p.accent : p.surface);
        HBRUSH edge = CreateSolidBrush(info.on ? p.accent : p.border);
        FillRect(dc, &b, fill);
        FrameRect(dc, &b, edge);
        DeleteObject(fill);
        DeleteObject(edge);
        if (info.on) {
            HPEN pen = CreatePen(PS_SOLID, std::max(2, g_theme.S(2)), RGB(255, 255, 255));
            HGDIOBJ old = SelectObject(dc, pen);
            MoveToEx(dc, b.left + box / 5, b.top + box / 2, nullptr);
            LineTo(dc, b.left + box * 2 / 5, b.bottom - box / 4);
            LineTo(dc, b.right - box / 5, b.top + box / 4);
            SelectObject(dc, old);
            DeleteObject(pen);
        }
        RECT t{b.right + g_theme.S(8), rc.top, rc.right, rc.bottom};
        SetTextColor(dc, disabled ? p.muted : p.ink);
        DrawTextW(dc, text, -1, &t, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        if (d->itemState & ODS_FOCUS) DrawFocusRect(dc, &rc);
        return;
    }
    COLORREF fill = info.primary ? (pressed ? RGB(0x07, 0x54, 0x7c) : p.primary) : (pressed ? p.selection : p.panel);
    if (!info.primary && hot && !pressed) fill = g_theme.dark ? RGB(0x28, 0x36, 0x44) : RGB(0xe3, 0xec, 0xf3);
    HBRUSH bg = CreateSolidBrush(fill);
    HBRUSH edge = CreateSolidBrush(hot ? p.accent : (info.primary ? p.primary : p.border));
    HRGN region = CreateRoundRectRgn(rc.left, rc.top, rc.right + 1, rc.bottom + 1, g_theme.S(8), g_theme.S(8));
    FillRect(dc, &rc, g_theme.bg);
    FillRgn(dc, region, bg);
    FrameRgn(dc, region, edge, 1, 1);
    DeleteObject(region);
    DeleteObject(bg);
    DeleteObject(edge);
    SetTextColor(dc, disabled ? p.muted : (info.primary ? RGB(255, 255, 255) : p.ink));
    DrawTextW(dc, text, -1, &rc, DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
}

// ---------------------------------------------------------------------------------------- columns
struct Column {
    const char *key, *label, *field;
    int width;
};
const Column kColumns[] = {
    {"name", "File name", "name", 300},      {"size", "Size", "size_text", 90},
    {"status", "Status", "status_text", 125}, {"speed", "Transfer rate", "speed_text", 115},
    {"eta", "Time left", "eta_text", 106},   {"last_try", "Last tried", "last_try_text", 150},
    {"added", "Date added", "added_text", 150}, {"website", "Website", "website", 125},
    {"quality", "Quality", "quality", 90},   {"save_to", "Save to", "save_to", 250},
};

// ------------------------------------------------------------------------------------------ forms
// A form is a small modeless window. Closing it only hides it and tells the controller.
class Form {
public:
    Form(const wchar_t *title, int width, int height, std::function<void()> onClose)
        : title_(title), w_(width), h_(height), onClose_(std::move(onClose)) {}
    virtual ~Form() = default;

    HWND hwnd = nullptr;

    void create(HWND owner);
    virtual void build() = 0;
    virtual void layout(int width, int height) = 0;
    virtual void apply(const Value &state) = 0;
    // Edit and combo notifications from the form's controls.
    virtual void notifyControl(HWND, int) {}

    void setVisible(bool visible) {
        if (visible == IsWindowVisible(hwnd)) return;
        if (visible) {
            RECT o;
            GetWindowRect(GetParent(hwnd), &o);
            RECT me;
            GetWindowRect(hwnd, &me);
            int x = o.left + ((o.right - o.left) - (me.right - me.left)) / 2;
            int y = o.top + ((o.bottom - o.top) - (me.bottom - me.top)) / 3;
            if (!IsWindowVisible(GetParent(hwnd))) {
                x = GetSystemMetrics(SM_CXSCREEN) / 2 - (me.right - me.left) / 2;
                y = GetSystemMetrics(SM_CYSCREEN) / 3 - (me.bottom - me.top) / 2;
            }
            SetWindowPos(hwnd, HWND_TOP, x, y, 0, 0, SWP_NOSIZE | SWP_SHOWWINDOW);
            SetForegroundWindow(hwnd);
        } else {
            ShowWindow(hwnd, SW_HIDE);
        }
    }

    void restyle() {
        setDarkTitleBar(hwnd);
        InvalidateRect(hwnd, nullptr, TRUE);
    }

    void close() {
        ShowWindow(hwnd, SW_HIDE);
        if (onClose_) onClose_();
    }

    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l);

protected:
    const wchar_t *title_;
    int w_, h_;
    std::function<void()> onClose_;
};

std::map<HWND, Form *> g_forms;

LRESULT CALLBACK Form::proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    Form *self = nullptr;
    auto it = g_forms.find(h);
    if (it != g_forms.end()) self = it->second;
    switch (m) {
    case WM_SIZE:
        if (self) self->layout(LOWORD(l), HIWORD(l));
        return 0;
    case WM_CLOSE:
        if (self) self->close();
        return 0;
    case WM_COMMAND: {
        int id = LOWORD(w);
        auto c = g_clicks.find(id);
        if (c != g_clicks.end() && HIWORD(w) == BN_CLICKED) c->second();
        else if (self && l) self->notifyControl(reinterpret_cast<HWND>(l), HIWORD(w));
        return 0;
    }
    case WM_DRAWITEM:
        drawButton(reinterpret_cast<DRAWITEMSTRUCT *>(l));
        return TRUE;
    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(h, &rc);
        FillRect(reinterpret_cast<HDC>(w), &rc, g_theme.bg);
        return 1;
    }
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORLISTBOX: {
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, g_theme.p.ink);
        SetBkColor(dc, g_theme.p.surface);
        return reinterpret_cast<LRESULT>(g_theme.surface);
    }
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLORBTN: {
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, g_theme.p.ink);
        SetBkColor(dc, g_theme.p.bg);
        return reinterpret_cast<LRESULT>(g_theme.bg);
    }
    case WM_KEYDOWN:
        if (w == VK_ESCAPE && self) {
            self->close();
            return 0;
        }
        break;
    }
    return DefWindowProcW(h, m, w, l);
}

void Form::create(HWND owner) {
    RECT rc{0, 0, g_theme.S(w_), g_theme.S(h_)};
    DWORD style = WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_CLIPCHILDREN;
    AdjustWindowRectEx(&rc, style, FALSE, 0);
    hwnd = CreateWindowExW(0, L"MDForm", title_, style, CW_USEDEFAULT, CW_USEDEFAULT, rc.right - rc.left, rc.bottom - rc.top,
                           owner, nullptr, GetModuleHandleW(nullptr), nullptr);
    g_forms[hwnd] = this;
    if (g_icon) {
        SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(g_icon));
        SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(g_icon));
    }
    setDarkTitleBar(hwnd);
    build();
    RECT client;
    GetClientRect(hwnd, &client);
    layout(client.right, client.bottom);
}

const std::vector<std::string> kQualities{"Best available", "1080p", "720p", "480p", "360p"};
const std::vector<std::string> kFormats{"MP3", "M4A", "FLAC", "OPUS", "WAV"};
const std::vector<std::string> kBitrates{"128", "192", "256", "320"};

// ---------------------------------------------------------------------------------------- Add URL
class AddForm : public Form {
public:
    AddForm() : Form(L"Add download", 560, 430, [] { send("add_close"); }) {}

    void build() override {
        lUrl = label(hwnd, "Link");
        url = edit(hwnd);
        analyze = button(hwnd, "Analyze", [this] { send("add_analyze", Value(getText(url))); });
        stop = button(hwnd, "Stop", [] { send("add_stop"); });
        title = label(hwnd);
        SendMessageW(title, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.bold), TRUE);
        meta = label(hwnd);
        status = label(hwnd);
        lMode = label(hwnd, "Type");
        mode = combo(hwnd, {"Video", "Audio"});
        lQuality = label(hwnd, "Quality");
        quality = combo(hwnd, kQualities);
        lFormat = label(hwnd, "Format");
        format = combo(hwnd, kFormats);
        lBitrate = label(hwnd, "Bitrate (kbps)");
        bitrate = combo(hwnd, kBitrates);
        lName = label(hwnd, "File name");
        name = edit(hwnd);
        lFolder = label(hwnd, "Save to");
        folder = label(hwnd);
        browse = button(hwnd, "Browse...", [] { send("choose_folder"); });
        reset = button(hwnd, "Default", [] { send("reset_folder"); });
        editor = checkbox(hwnd, "Open in the media editor after downloading");
        cancel = button(hwnd, "Cancel", [this] { close(); });
        download = button(hwnd, "Download", [this] { submit(); }, true);
        EnableWindow(download, FALSE);
        EnableWindow(stop, FALSE);
    }

    void layout(int w, int) override {
        const auto &t = g_theme;
        int m = t.S(16), row = t.S(30), gap = t.S(10), lw = t.S(100);
        int y = m;
        place(lUrl, m, y + t.S(6), lw, row - t.S(6));
        place(url, m + lw, y, w - 2 * m - lw - t.S(168), row);
        place(analyze, w - m - t.S(160), y, t.S(76), row);
        place(stop, w - m - t.S(76), y, t.S(76), row);
        y += row + gap;
        place(title, m, y, w - 2 * m, t.S(22));
        y += t.S(24);
        place(meta, m, y, w - 2 * m, t.S(22));
        y += t.S(26);
        place(lMode, m, y + t.S(6), lw, row - t.S(6));
        place(mode, m + lw, y, t.S(140), t.S(300));
        y += row + gap;
        bool audio = comboText(mode) == "Audio";
        ShowWindow(lQuality, audio ? SW_HIDE : SW_SHOW);
        ShowWindow(quality, audio ? SW_HIDE : SW_SHOW);
        ShowWindow(lFormat, audio ? SW_SHOW : SW_HIDE);
        ShowWindow(format, audio ? SW_SHOW : SW_HIDE);
        ShowWindow(lBitrate, audio ? SW_SHOW : SW_HIDE);
        ShowWindow(bitrate, audio ? SW_SHOW : SW_HIDE);
        place(lQuality, m, y + t.S(6), lw, row - t.S(6));
        place(quality, m + lw, y, t.S(160), t.S(300));
        place(lFormat, m, y + t.S(6), lw, row - t.S(6));
        place(format, m + lw, y, t.S(120), t.S(300));
        place(lBitrate, m + lw + t.S(136), y + t.S(6), t.S(100), row - t.S(6));
        place(bitrate, m + lw + t.S(240), y, t.S(100), t.S(300));
        y += row + gap;
        place(lName, m, y + t.S(6), lw, row - t.S(6));
        place(name, m + lw, y, w - 2 * m - lw, row);
        y += row + gap;
        place(lFolder, m, y + t.S(6), lw, row - t.S(6));
        place(folder, m + lw, y + t.S(6), w - 2 * m - lw - t.S(168), row - t.S(6));
        place(browse, w - m - t.S(160), y, t.S(76), row);
        place(reset, w - m - t.S(76), y, t.S(76), row);
        y += row + gap;
        place(editor, m, y, w - 2 * m, t.S(24));
        y += t.S(34);
        place(status, m, y, w - 2 * m, t.S(40));
        place(cancel, w - m - t.S(220), t.S(h_) - m - row, t.S(100), row);
        place(download, w - m - t.S(112), t.S(h_) - m - row, t.S(112), row);
    }

    void apply(const Value &s) override {
        bool visible = s["visible"].asBool();
        if (!visible) {
            setVisible(false);
            shown = false;
            return;
        }
        if (!shown) {
            shown = true;
            setText(url, s["url"].asString());
            setComboText(mode, s["mode"].asString());
            setComboText(quality, s["quality"].asString());
            setComboText(format, s["format"].asString());
            setComboText(bitrate, s["bitrate"].asString());
            setChecked(editor, s["edit"].asBool());
            suggestion = s["name"].asString();
            setText(name, suggestion);
            setVisible(true);
            SetFocus(url);
        }
        std::string suggested = s["name"].asString();
        if (suggested != suggestion && trim(getText(url)) == trim(s["url"].asString())) {
            suggestion = suggested;
            setText(name, suggested);
        }
        RECT rc;
        GetClientRect(hwnd, &rc);
        layout(rc.right, rc.bottom);
        setText(folder, s["folder"].asString());
        setText(title, s["title"].asString());
        setText(meta, s["metadata"].asString());
        setText(status, s["status"].asString());
        bool running = s["running"].asBool();
        EnableWindow(analyze, !running);
        EnableWindow(stop, running);
        EnableWindow(download, !running && s["ready"].asBool() && trim(getText(url)) == trim(s["url"].asString()));
        SetWindowTextW(download, comboText(mode) == "Audio" ? L"Download audio" : L"Download video");
    }

    void notifyControl(HWND ctl, int code) override {
        if (ctl == url && code == EN_CHANGE) typed();
        else if (ctl == mode && code == CBN_SELCHANGE) modeChanged();
    }

    // Typing a link tells the controller so it can validate it and remember it.
    void typed() {
        std::string now = getText(url);
        if (now != lastSentUrl) {
            lastSentUrl = now;
            send("add_url", Value(now));
        }
    }

    void modeChanged() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        layout(rc.right, rc.bottom);
        SetWindowTextW(download, comboText(mode) == "Audio" ? L"Download audio" : L"Download video");
    }

private:
    static std::string trim(const std::string &s) {
        size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

    void submit() {
        Value v = Value::object();
        v.set("url", getText(url));
        v.set("name", getText(name));
        v.set("mode", comboText(mode));
        v.set("quality", comboText(quality));
        v.set("format", comboText(format));
        v.set("bitrate", comboText(bitrate));
        v.set("edit", isChecked(editor));
        send("add_submit", v);
    }

    HWND url{}, mode{}, lUrl{}, analyze{}, stop{}, title{}, meta{}, status{}, lMode{}, lQuality{}, quality{}, lFormat{}, format{}, lBitrate{},
        bitrate{}, lName{}, name{}, lFolder{}, folder{}, browse{}, reset{}, editor{}, cancel{}, download{};
    bool shown = false;
    std::string suggestion, lastSentUrl;
};

// ----------------------------------------------------------------------------------------- Batch
class BatchForm : public Form {
public:
    BatchForm() : Form(L"Batch download", 600, 470, [this] { send("batch_close", Value(getText(links))); }) {}

    void build() override {
        head = label(hwnd, "Links to download (one per line)");
        links = edit(hwnd, true);
        count = label(hwnd);
        status = label(hwnd);
        paste = button(hwnd, "Paste", [this] { SendMessageW(links, WM_PASTE, 0, 0); });
        clear = button(hwnd, "Clear", [this] { SetWindowTextW(links, L""); });
        lMode = label(hwnd, "Type");
        mode = combo(hwnd, {"Video", "Audio"});
        lQuality = label(hwnd, "Quality");
        quality = combo(hwnd, kQualities);
        lFormat = label(hwnd, "Format");
        format = combo(hwnd, kFormats);
        lBitrate = label(hwnd, "Bitrate");
        bitrate = combo(hwnd, kBitrates);
        close_ = button(hwnd, "Close", [this] { close(); });
        start = button(hwnd, "Add to queue", [this] { submit(); }, true);
    }

    void layout(int w, int) override {
        const auto &t = g_theme;
        int m = t.S(16), row = t.S(30);
        int y = m;
        place(head, m, y, w - 2 * m, t.S(22));
        y += t.S(26);
        place(links, m, y, w - 2 * m, t.S(210));
        y += t.S(218);
        place(count, m, y, t.S(150), t.S(22));
        place(status, m + t.S(160), y, w - 2 * m - t.S(400), t.S(22));
        place(paste, w - m - t.S(160), y - t.S(4), t.S(76), t.S(28));
        place(clear, w - m - t.S(76), y - t.S(4), t.S(76), t.S(28));
        y += t.S(40);
        place(lMode, m, y + t.S(6), t.S(40), row);
        place(mode, m + t.S(44), y, t.S(100), t.S(300));
        place(lQuality, m + t.S(160), y + t.S(6), t.S(50), row);
        place(quality, m + t.S(214), y, t.S(130), t.S(300));
        place(lFormat, m + t.S(160), y + t.S(6), t.S(50), row);
        place(format, m + t.S(214), y, t.S(90), t.S(300));
        place(lBitrate, m + t.S(316), y + t.S(6), t.S(50), row);
        place(bitrate, m + t.S(370), y, t.S(90), t.S(300));
        place(close_, w - m - t.S(232), t.S(h_) - m - row, t.S(100), row);
        place(start, w - m - t.S(120), t.S(h_) - m - row, t.S(120), row);
        updateModeFields();
    }

    void updateModeFields() {
        bool audio = comboText(mode) == "Audio";
        for (HWND h : {lQuality, quality}) ShowWindow(h, audio ? SW_HIDE : SW_SHOW);
        for (HWND h : {lFormat, format, lBitrate, bitrate}) ShowWindow(h, audio ? SW_SHOW : SW_HIDE);
    }

    void apply(const Value &s) override {
        if (!s["visible"].asBool()) {
            setVisible(false);
            shown = false;
            KillTimer(hwnd, TIMER_BATCH);
            return;
        }
        if (!shown) {
            shown = true;
            SetWindowTextW(links, W(toWindowsNewlines(s["links"].asString())).c_str());
            setComboText(mode, s["mode"].asString());
            setComboText(quality, s["quality"].asString());
            setComboText(format, s["format"].asString());
            setComboText(bitrate, s["bitrate"].asString());
            updateModeFields();
            setVisible(true);
            SetFocus(links);
            SetTimer(hwnd, TIMER_BATCH, 400, nullptr);
        }
        setText(count, s["count"].asString());
        setText(status, s["status"].asString());
    }

    // Keeps the controller's link counter live while the user types.
    void tick() {
        std::string now = getText(links);
        if (now != lastSent) {
            lastSent = now;
            send("batch_links", Value(now));
        }
    }

    void notifyControl(HWND ctl, int code) override {
        if (ctl == mode && code == CBN_SELCHANGE) updateModeFields();
    }

private:
    void submit() {
        Value v = Value::object();
        v.set("links", getText(links));
        v.set("mode", comboText(mode));
        v.set("quality", comboText(quality));
        v.set("format", comboText(format));
        v.set("bitrate", comboText(bitrate));
        send("batch_submit", v);
    }

    HWND mode{}, head{}, links{}, count{}, status{}, paste{}, clear{}, lMode{}, lQuality{}, quality{}, lFormat{}, format{}, lBitrate{},
        bitrate{}, close_{}, start{};
    bool shown = false;
    std::string lastSent;
};

// ---------------------------------------------------------------------------------------- Update
class UpdateForm : public Form {
public:
    UpdateForm() : Form(L"Updates", 520, 330, [] { send("update_close"); }) {}

    void build() override {
        headline = label(hwnd);
        SendMessageW(headline, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.title), TRUE);
        detail = label(hwnd);
        notes = label(hwnd);
        progress = make(hwnd, PROGRESS_CLASSW, L"", PBS_SMOOTH);
        SetWindowTheme(progress, L"", L"");
        SendMessageW(progress, PBM_SETRANGE32, 0, 100);
        check = button(hwnd, "Check for updates", [] { send("update_check"); });
        install = button(hwnd, "Update now", [] { send("update_install"); }, true);
        later = button(hwnd, "Later", [] { send("update_later"); });
        cancel = button(hwnd, "Cancel download", [] { send("update_cancel"); });
        release = button(hwnd, "Open release", [] { send("update_release"); });
        close_ = button(hwnd, "Close", [this] { close(); });
    }

    void layout(int w, int) override {
        const auto &t = g_theme;
        int m = t.S(16), row = t.S(30);
        place(headline, m, m, w - 2 * m, t.S(28));
        place(detail, m, m + t.S(34), w - 2 * m, t.S(44));
        place(notes, m, m + t.S(82), w - 2 * m, t.S(80));
        place(progress, m, m + t.S(170), w - 2 * m, t.S(10));
        int y = m + t.S(200);
        place(check, m, y, t.S(150), row);
        place(install, m + t.S(160), y, t.S(130), row);
        place(later, m + t.S(300), y, t.S(90), row);
        place(cancel, m, y + row + t.S(8), t.S(150), row);
        place(release, m + t.S(160), y + row + t.S(8), t.S(130), row);
        place(close_, w - m - t.S(90), y + row + t.S(8), t.S(90), row);
    }

    void apply(const Value &s) override {
        if (!s["visible"].asBool()) {
            setVisible(false);
            return;
        }
        setVisible(true);
        SetWindowTextW(headline, W(s["headline"].asString()).c_str());
        setText(detail, s["detail"].asString());
        setText(notes, s["notes"].asString());
        SendMessageW(progress, PBM_SETPOS, s["progress"].asInt(), 0);
        bool showProgress = s["downloading"].asBool() || s["progress"].asInt() > 0;
        ShowWindow(progress, showProgress ? SW_SHOW : SW_HIDE);
        EnableWindow(check, s["canCheck"].asBool());
        EnableWindow(install, s["canInstall"].asBool());
        EnableWindow(later, s["canSnooze"].asBool());
        EnableWindow(cancel, s["canCancel"].asBool());
        if (!s["installText"].asString().empty()) setText(install, s["installText"].asString());
        if (!s["releaseText"].asString().empty()) setText(release, s["releaseText"].asString());
    }

private:
    HWND headline{}, detail{}, notes{}, progress{}, check{}, install{}, later{}, cancel{}, release{}, close_{};
};

// -------------------------------------------------------------------------------------- Settings
struct Field {
    enum Kind { Check, Combo, Text, Number } kind;
    const char *key, *label;
    int tab;
    std::vector<std::string> items;
};

const char *const kTabs[] = {"General", "Downloads", "Files", "Browser", "Updates"};
constexpr int kTabCount = 5;

class SettingsForm : public Form {
public:
    SettingsForm() : Form(L"Settings", 600, 520, [] { send("settings_close"); }) {}

    void build() override {
        fields = {
            {Field::Check, "start_with_windows", "Start with Windows", 0, {}},
            {Field::Check, "close_to_tray", "Keep running in the system tray after closing the window", 0, {}},
            {Field::Check, "confirm_before_exit", "Confirm before exiting with active downloads", 0, {}},
            {Field::Check, "auto_analyze_links", "Analyze pasted links automatically", 0, {}},
            {Field::Check, "open_editor_after_download", "Open finished downloads in the editor", 0, {}},
            {Field::Check, "clipboard_hotkey", "Enable clipboard download shortcuts", 0, {}},
            {Field::Check, "capture_dialog", "Ask where to save browser downloads", 0, {}},
            {Field::Combo, "theme", "Theme", 0, {"Light", "Dark"}},
            {Field::Text, "download_dir", "Download folder", 1, {}},
            {Field::Combo, "default_mode", "Default type", 1, {"Video", "Audio"}},
            {Field::Combo, "video_quality", "Video quality", 1, kQualities},
            {Field::Combo, "audio_format", "Audio format", 1, kFormats},
            {Field::Combo, "audio_quality", "Bitrate (kbps)", 1, kBitrates},
            {Field::Number, "max_concurrent_downloads", "Simultaneous downloads", 1, {}},
            {Field::Check, "organize_by_site", "Use a folder named after each website", 2, {}},
            {Field::Check, "skip_existing", "Skip existing files", 2, {}},
            {Field::Check, "fast_mode", "Download stream segments in parallel", 2, {}},
            {Field::Check, "embed_metadata", "Embed media metadata", 2, {}},
            {Field::Check, "embed_thumbnail", "Use the thumbnail as cover art", 2, {}},
            {Field::Combo, "subtitles", "Subtitles", 2, {"Off", "Sidecar file", "Embed in video"}},
            {Field::Text, "subtitle_langs", "Subtitle languages (for example en,bn)", 2, {}},
            {Field::Check, "extension_enabled", "Capture browser downloads", 3, {}},
            {Field::Text, "cookies_file", "Cookies file", 3, {}},
            {Field::Combo, "cookies_browser", "Read cookies from", 3, {"None", "Firefox", "Chrome", "Edge", "Brave"}},
            {Field::Check, "auto_check_updates", "Check for updates on startup", 4, {}},
            {Field::Check, "auto_download_updates", "Download verified updates automatically", 4, {}},
        };
        for (int i = 0; i < kTabCount; ++i)
            tabs[i] = button(hwnd, kTabs[i], [this, i] { selectTab(i); });
        for (auto &f : fields) {
            if (f.kind == Field::Check) {
                f.control = checkbox(hwnd, f.label);
            } else {
                f.caption = label(hwnd, f.label);
                f.control = f.kind == Field::Combo ? combo(hwnd, f.items) : edit(hwnd);
            }
        }
        extras = {
            {3, "Install browser extension", "install_extension"}, {3, "Copy pairing token", "copy_pairing_token"},
            {4, "Check for updates", "settings_check_updates"},    {4, "Release notes", "update_release"},
            {4, "Diagnostics", "diagnostics"},                      {4, "Open app log", "open_log"},
        };
        for (auto &e : extras) {
            std::string command = e.command;
            e.control = button(hwnd, e.text, [command] { send(command); });
        }
        extension = label(hwnd);
        status = label(hwnd);
        cancel = button(hwnd, "Cancel", [this] { close(); });
        save = button(hwnd, "Save", [this] { submit(false); });
        ok = button(hwnd, "OK", [this] { submit(true); }, true);
        selectTab(0);
    }

    void layout(int w, int) override {
        const auto &t = g_theme;
        int m = t.S(16), row = t.S(30);
        int tabW = (w - 2 * m) / kTabCount;
        for (int i = 0; i < kTabCount; ++i) place(tabs[i], m + i * tabW, m, tabW - t.S(4), row);
        int y0 = m + row + t.S(16);
        int y[kTabCount];
        for (int &v : y) v = y0;
        for (auto &f : fields) {
            int &cy = y[f.tab];
            if (f.kind == Field::Check) {
                place(f.control, m, cy, w - 2 * m, t.S(24));
                cy += t.S(32);
            } else {
                place(f.caption, m, cy + t.S(5), t.S(200), t.S(22));
                int cw = f.kind == Field::Number ? t.S(80) : (f.kind == Field::Text ? w - 2 * m - t.S(210) : t.S(200));
                place(f.control, m + t.S(210), cy, cw, f.kind == Field::Combo ? t.S(300) : t.S(28));
                cy += t.S(38);
            }
        }
        for (auto &e : extras) {
            int &cy = y[e.tab];
            place(e.control, m, cy, t.S(240), t.S(30));
            cy += t.S(38);
        }
        place(extension, m, t.S(h_) - m - t.S(60), w - 2 * m, t.S(22));
        place(status, m, t.S(h_) - m - t.S(36), w - 2 * m - t.S(330), t.S(22));
        place(cancel, w - m - t.S(320), t.S(h_) - m - row, t.S(100), row);
        place(save, w - m - t.S(212), t.S(h_) - m - row, t.S(100), row);
        place(ok, w - m - t.S(104), t.S(h_) - m - row, t.S(104), row);
    }

    void apply(const Value &s) override {
        if (!s["visible"].asBool()) {
            setVisible(false);
            shown = false;
            return;
        }
        int revision = s["revision"].asInt();
        if (!shown || revision != revision_) {
            revision_ = revision;
            const Value &values = s["values"];
            for (auto &f : fields) {
                const Value &v = values[f.key];
                switch (f.kind) {
                case Field::Check: setChecked(f.control, v.asBool()); break;
                case Field::Combo: setComboText(f.control, v.asString()); break;
                case Field::Text: setText(f.control, v.asString()); break;
                case Field::Number: setText(f.control, std::to_string(v.asInt(6))); break;
                }
            }
            std::string tab = s["tab"].asString();
            int index = 0;
            for (int i = 0; i < kTabCount; ++i)
                if (tab == kTabs[i]) index = i;
            if (tab == "Save To") index = 1;
            selectTab(index);
        }
        if (!shown) {
            shown = true;
            setVisible(true);
        }
        setText(status, s["status"].asString());
        setText(extension, s["extension"].asString());
    }

private:
    struct Extra {
        int tab;
        const char *text, *command;
        HWND control = nullptr;
    };

    void selectTab(int index) {
        current = index;
        for (int i = 0; i < kTabCount; ++i) g_buttons[tabs[i]].primary = (i == index), InvalidateRect(tabs[i], nullptr, FALSE);
        for (auto &f : fields) {
            int cmd = f.tab == index ? SW_SHOW : SW_HIDE;
            ShowWindow(f.control, cmd);
            if (f.caption) ShowWindow(f.caption, cmd);
        }
        for (auto &e : extras) ShowWindow(e.control, e.tab == index ? SW_SHOW : SW_HIDE);
    }

    void submit(bool closeAfter) {
        Value values = Value::object();
        for (auto &f : fields) {
            switch (f.kind) {
            case Field::Check: values.set(f.key, isChecked(f.control)); break;
            case Field::Combo: values.set(f.key, comboText(f.control)); break;
            case Field::Text: values.set(f.key, getText(f.control)); break;
            case Field::Number: {
                int n = std::atoi(getText(f.control).c_str());
                values.set(f.key, std::max(1, std::min(n, 32)));
                break;
            }
            }
        }
        Value msg = Value::object();
        msg.set("values", values);
        msg.set("close", closeAfter);
        msg.set("tab", std::string(kTabs[current]));
        send("settings_save", msg);
    }

    struct F : Field {
        HWND control = nullptr, caption = nullptr;
        F(Field::Kind k, const char *key, const char *label, int tab, std::vector<std::string> items)
            : Field{k, key, label, tab, std::move(items)} {}
    };

    std::vector<F> fields;
    std::vector<Extra> extras;
    HWND tabs[kTabCount]{}, extension{}, status{}, cancel{}, save{}, ok{};
    int current = 0, revision_ = -1;
    bool shown = false;
};

// ------------------------------------------------------------------------------------ main window
struct MainWindow {
    HWND hwnd = nullptr, categories = nullptr, search = nullptr, title = nullptr, list = nullptr, empty = nullptr,
         detailTitle = nullptr, detailText = nullptr, progress = nullptr, statusLeft = nullptr, statusRight = nullptr,
         updates = nullptr;
    std::vector<HWND> toolbar;
    std::vector<std::pair<std::string, HWND>> rowButtons;

    std::vector<Value> rows;
    std::vector<std::string> columnKeys;   // shown columns, in list order
    Value categoryRows = Value::array();
    Value widths = Value::object();
    std::string themeName, selectedCategory;
    int visibilityRevision = -1, selectionRevision = -1;
    bool applying = false, navigation = true, details = true, havePlacedOnce = false;
    int sortColumn = -1;
    bool descending = false;
    std::string sortKey;

    AddForm add;
    BatchForm batch;
    UpdateForm update;
    SettingsForm settings;

    std::vector<std::string> selectedKeys() const {
        std::vector<std::string> keys;
        int i = -1;
        while ((i = ListView_GetNextItem(list, i, LVNI_SELECTED)) >= 0)
            if (i < static_cast<int>(rows.size())) keys.push_back(rows[static_cast<size_t>(i)]["key"].asString());
        return keys;
    }

    void sendCommand(const std::string &command, const Value &value = Value()) {
        auto keys = selectedKeys();
        send(command, value, &keys);
    }

    std::set<std::string> enabledActions() const {
        std::set<std::string> out;
        static const std::set<std::string> single{"open", "edit", "details", "properties"};
        int count = ListView_GetSelectedCount(list);
        int i = -1;
        while ((i = ListView_GetNextItem(list, i, LVNI_SELECTED)) >= 0) {
            if (i >= static_cast<int>(rows.size())) break;
            for (const auto &a : rows[static_cast<size_t>(i)]["actions"].items())
                if (count == 1 || !single.count(a.asString())) out.insert(a.asString());
        }
        return out;
    }

    void create();
    void layout();
    void restyle();
    void apply(const Value &s);
    void rebuildColumns(const Value &shown);
    void rebuildCategories(const Value &s);
    void contextMenu(int x, int y);
    LRESULT notify(NMHDR *h);
    void drawCell(NMLVCUSTOMDRAW *d);
};

MainWindow g_ui;

const char *const kToolbar[][2] = {{"Add URL", "add"},  {"Batch", "batch"},  {"Pause all", "pause_all"},
                                   {"Resume all", "resume_all"}, {"Editor", "editor"}, {"Screen", "screen"},
                                   {"Audio", "audio"},  {"Settings", "settings"}};

const char *const kRowActions[][2] = {{"Open", "open"},         {"Show in folder", "open_folder"}, {"Open in editor", "edit"},
                                      {"Resume", "resume"},     {"Pause", "stop"},                 {"Cancel", "cancel"},
                                      {"Download again", "redownload"}, {"Copy download link", "copy_link"},
                                      {"Copy file", "copy_file"}, {"Error details", "details"},    {"Properties", "properties"},
                                      {"Delete...", "delete"}};

void MainWindow::create() {
    categories = make(hwnd, L"LISTBOX", L"", LBS_NOTIFY | LBS_OWNERDRAWFIXED | LBS_NOINTEGRALHEIGHT | WS_VSCROLL | WS_TABSTOP);
    themeWindow(categories, L"DarkMode_Explorer", L"Explorer");
    search = edit(hwnd);
    SendMessageW(search, EM_SETCUEBANNER, TRUE, reinterpret_cast<LPARAM>(L"Search downloads"));
    title = label(hwnd);
    SendMessageW(title, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.title), TRUE);
    for (auto &item : kToolbar) {
        std::string command = item[1];
        toolbar.push_back(button(hwnd, item[0], [this, command] { sendCommand(command); }, command == "add"));
    }
    updates = button(hwnd, "Updates", [this] { sendCommand("updates"); });
    for (auto &item : kRowActions) {
        std::string command = item[1];
        if (command == "open" || command == "resume" || command == "stop" || command == "cancel" || command == "delete")
            rowButtons.emplace_back(command, button(hwnd, item[0], [this, command] { sendCommand(command); }));
    }

    list = CreateWindowExW(0, WC_LISTVIEWW, L"", WS_CHILD | WS_VISIBLE | WS_TABSTOP | LVS_REPORT | LVS_OWNERDATA | LVS_SHOWSELALWAYS,
                           0, 0, 10, 10, hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(g_nextId++)), GetModuleHandleW(nullptr), nullptr);
    SendMessageW(list, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.font), TRUE);
    ListView_SetExtendedListViewStyle(list, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_HEADERDRAGDROP | LVS_EX_LABELTIP);
    themeWindow(list, L"DarkMode_Explorer", L"Explorer");

    empty = label(hwnd, "No downloads in this view.", SS_CENTER);
    detailTitle = label(hwnd);
    SendMessageW(detailTitle, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.bold), TRUE);
    detailText = label(hwnd);
    progress = make(hwnd, PROGRESS_CLASSW, L"", PBS_SMOOTH);
    SetWindowTheme(progress, L"", L"");
    SendMessageW(progress, PBM_SETRANGE32, 0, 100);
    statusLeft = label(hwnd);
    statusRight = label(hwnd, "", SS_RIGHT);

    add.create(hwnd);
    batch.create(hwnd);
    update.create(hwnd);
    settings.create(hwnd);
}

void MainWindow::layout() {
    RECT rc;
    GetClientRect(hwnd, &rc);
    const auto &t = g_theme;
    int W_ = rc.right, H_ = rc.bottom;
    int side = navigation ? t.S(210) : 0;
    int top = t.S(56), status = t.S(28), m = t.S(12);
    int detailH = details ? t.S(92) : 0;

    ShowWindow(categories, navigation ? SW_SHOW : SW_HIDE);
    place(categories, 0, 0, side, H_ - status);
    place(search, side + m, m, std::max(t.S(120), std::min(t.S(280), W_ - side - 2 * m)), t.S(30));
    // Toolbar buttons flow after the search box and wrap off-screen only on very narrow windows.
    int x = side + m + std::max(t.S(120), std::min(t.S(280), W_ - side - 2 * m)) + m;
    int widths_[] = {96, 78, 92, 92, 70, 72, 66, 84};
    for (size_t i = 0; i < toolbar.size(); ++i) {
        int bw = t.S(widths_[i]);
        bool fits = x + bw + t.S(110) < W_;
        ShowWindow(toolbar[i], fits ? SW_SHOW : SW_HIDE);
        place(toolbar[i], x, m, bw, t.S(30));
        x += bw + t.S(6);
    }
    place(updates, W_ - m - t.S(110), top + t.S(2), t.S(110), t.S(28));
    place(title, side + m, top + t.S(4), W_ - side - t.S(140), t.S(26));

    int rowY = top + t.S(36);
    int bx = side + m;
    for (auto &kv : rowButtons) {
        place(kv.second, bx, rowY, t.S(86), t.S(28));
        bx += t.S(92);
    }
    int listTop = rowY + t.S(36);
    int listH = H_ - status - detailH - listTop;
    place(list, side, listTop, W_ - side, std::max(t.S(40), listH));
    place(empty, side, listTop + t.S(40), W_ - side, t.S(30));
    int dy = H_ - status - detailH;
    ShowWindow(detailTitle, details ? SW_SHOW : SW_HIDE);
    ShowWindow(detailText, details ? SW_SHOW : SW_HIDE);
    place(detailTitle, side + m, dy + t.S(8), W_ - side - 2 * m, t.S(22));
    place(detailText, side + m, dy + t.S(32), W_ - side - 2 * m, t.S(36));
    place(progress, side + m, dy + t.S(74), W_ - side - 2 * m, t.S(8));
    place(statusLeft, m, H_ - status + t.S(4), W_ / 2, t.S(20));
    place(statusRight, W_ / 2, H_ - status + t.S(4), W_ / 2 - m, t.S(20));
}

void MainWindow::restyle() {
    setDarkTitleBar(hwnd);
    for (HWND h : {categories, list}) themeWindow(h, L"DarkMode_Explorer", L"Explorer");
    add.restyle();
    batch.restyle();
    update.restyle();
    settings.restyle();
    HWND header = ListView_GetHeader(list);
    InvalidateRect(header, nullptr, TRUE);
    ListView_SetBkColor(list, g_theme.p.surface);
    ListView_SetTextBkColor(list, g_theme.p.surface);
    SendMessageW(progress, PBM_SETBARCOLOR, 0, g_theme.p.accent);
    SendMessageW(progress, PBM_SETBKCOLOR, 0, g_theme.p.panel);
    RedrawWindow(hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN);
    for (auto &f : std::vector<Form *>{&add, &batch, &update, &settings}) {
        RedrawWindow(f->hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN);
        EnumChildWindows(f->hwnd, [](HWND child, LPARAM) -> BOOL {
            wchar_t cls[32];
            GetClassNameW(child, cls, 32);
            if (wcscmp(cls, L"msctls_progress32") == 0) {
                SendMessageW(child, PBM_SETBARCOLOR, 0, g_theme.p.accent);
                SendMessageW(child, PBM_SETBKCOLOR, 0, g_theme.p.panel);
            } else if (wcscmp(cls, L"ComboBox") == 0) {
                SetWindowTheme(child, g_theme.dark ? L"DarkMode_CFD" : L"Explorer", nullptr);
            }
            return TRUE;
        }, 0);
    }
}

void MainWindow::rebuildColumns(const Value &shown) {
    std::vector<std::string> next;
    for (const auto &k : shown.items()) next.push_back(k.asString());
    if (next.empty()) next.push_back("name");
    bool changed = next != columnKeys;
    if (changed) {
        applying = true;
        while (Header_GetItemCount(ListView_GetHeader(list)) > 0) ListView_DeleteColumn(list, 0);
        columnKeys = next;
        int index = 0;
        for (const auto &key : columnKeys) {
            for (const auto &c : kColumns) {
                if (key != c.key) continue;
                std::wstring label_ = W(c.label);
                LVCOLUMNW col{};
                col.mask = LVCF_TEXT | LVCF_WIDTH;
                col.pszText = &label_[0];
                col.cx = g_theme.S(widths[key].asInt(c.width));
                ListView_InsertColumn(list, index++, &col);
            }
        }
        applying = false;
    }
}

void MainWindow::rebuildCategories(const Value &s) {
    const Value &next = s["categories"];
    if (next != categoryRows) {
        categoryRows = next;
        SendMessageW(categories, WM_SETREDRAW, FALSE, 0);
        SendMessageW(categories, LB_RESETCONTENT, 0, 0);
        for (const auto &c : next.items()) {
            std::string count = c["count"].asString();
            std::string text = c["name"].asString() + (count == "0" || count.empty() ? "" : "\t" + count);
            SendMessageW(categories, LB_ADDSTRING, 0, reinterpret_cast<LPARAM>(W(text).c_str()));
        }
        SendMessageW(categories, WM_SETREDRAW, TRUE, 0);
    }
    selectedCategory = s["category"]["key"].asString();
    for (size_t i = 0; i < next.items().size(); ++i)
        if (next.items()[i]["key"].asString() == selectedCategory) {
            if (SendMessageW(categories, LB_GETCURSEL, 0, 0) != static_cast<LRESULT>(i))
                SendMessageW(categories, LB_SETCURSEL, i, 0);
            break;
        }
    InvalidateRect(categories, nullptr, FALSE);
}

void MainWindow::apply(const Value &s) {
    applying = true;
    std::string themeNow = s["theme"].asString();
    if (themeNow != themeName) {
        themeName = themeNow;
        g_theme.build(themeNow == "Dark");
        restyle();
    }
    int vis = s["visibilityRevision"].asInt();
    if (vis != visibilityRevision) {
        visibilityRevision = vis;
        if (s["visible"].asBool()) {
            if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
            else ShowWindow(hwnd, SW_SHOW);
            SetForegroundWindow(hwnd);
        } else {
            ShowWindow(hwnd, SW_HIDE);
        }
    }

    bool nav = s["navigation"].asBool(true), det = s["details"].asBool(true);
    if (nav != navigation || det != details) {
        navigation = nav;
        details = det;
        layout();
    }
    setText(title, s["category"]["name"].asString());
    rebuildCategories(s);
    rebuildColumns(s["columns"]);

    // Column widths and sort arrow follow the controller's saved settings.
    if (s["widths"] != widths) {
        widths = s["widths"];
        for (size_t i = 0; i < columnKeys.size(); ++i)
            for (const auto &c : kColumns)
                if (columnKeys[i] == c.key) ListView_SetColumnWidth(list, static_cast<int>(i), g_theme.S(widths[c.key].asInt(c.width)));
    }
    sortKey = s["sort"].asString();
    descending = s["descending"].asBool();
    HWND header = ListView_GetHeader(list);
    for (size_t i = 0; i < columnKeys.size(); ++i) {
        HDITEMW item{};
        item.mask = HDI_FORMAT;
        Header_GetItem(header, static_cast<int>(i), &item);
        item.fmt &= ~(HDF_SORTUP | HDF_SORTDOWN);
        if (columnKeys[i] == sortKey) item.fmt |= descending ? HDF_SORTDOWN : HDF_SORTUP;
        Header_SetItem(header, static_cast<int>(i), &item);
    }

    // Rows: replace the data, then restore the selection the controller asked for.
    std::vector<std::string> before = selectedKeys();
    std::vector<Value> next = s["rows"].items();
    bool sameOrder = next.size() == rows.size();
    for (size_t i = 0; sameOrder && i < next.size(); ++i)
        sameOrder = next[i]["key"].asString() == rows[i]["key"].asString();
    rows = std::move(next);
    int selRev = s["selectionRevision"].asInt();
    bool setSelection = selRev != selectionRevision || !sameOrder;
    std::set<std::string> want;
    if (selRev != selectionRevision)
        for (const auto &k : s["selection"].items()) want.insert(k.asString());
    else
        want.insert(before.begin(), before.end());
    ListView_SetItemCountEx(list, static_cast<int>(rows.size()), LVSICF_NOINVALIDATEALL | LVSICF_NOSCROLL);
    InvalidateRect(list, nullptr, FALSE);
    if (setSelection) {
        ListView_SetItemState(list, -1, 0, LVIS_SELECTED);
        for (size_t i = 0; i < rows.size(); ++i)
            if (want.count(rows[i]["key"].asString())) ListView_SetItemState(list, static_cast<int>(i), LVIS_SELECTED, LVIS_SELECTED);
    }
    selectionRevision = selRev;
    applying = false;
    if (setSelection) {
        std::vector<std::string> now = selectedKeys();
        std::vector<std::string> asked;
        for (const auto &k : s["selection"].items()) asked.push_back(k.asString());
        if (now != asked) sendCommand("selection");
    }

    bool none = rows.empty();
    ShowWindow(list, none ? SW_HIDE : SW_SHOW);
    ShowWindow(empty, none ? SW_SHOW : SW_HIDE);
    SetWindowTextW(empty, getText(search).empty() ? L"No downloads in this view." : L"No matching downloads.");

    auto enabled = enabledActions();
    for (auto &kv : rowButtons) EnableWindow(kv.second, enabled.count(kv.first) ? TRUE : FALSE);

    std::string summary = s["summary"].asString();
    setText(detailTitle, summary.empty() ? "Download details" : summary);
    setText(detailText, summary.empty() ? "Select a download to see its status." : s["selectedDetails"].asString());
    int pct = 0;
    auto sel = selectedKeys();
    if (sel.size() == 1)
        for (const auto &r : rows)
            if (r["key"].asString() == sel[0]) pct = std::max(0, std::min(100, static_cast<int>(r["progress"].asNumber() * 100 + 0.5)));
    SendMessageW(progress, PBM_SETPOS, pct, 0);
    ShowWindow(progress, details && pct > 0 && pct < 100 ? SW_SHOW : SW_HIDE);

    setText(updates, s["updateAvailable"].asBool() ? "Install update" : "Updates");
    std::string status = s["status"].asString();
    if (status.empty()) {
        const Value &t = s["totals"];
        status = std::to_string(t["downloading"].asInt()) + " downloading  |  " + std::to_string(t["queued"].asInt()) +
                 " queued  |  " + std::to_string(t["paused"].asInt()) + " paused";
        if (!t["speed_text"].asString().empty() && t["downloading"].asInt() > 0) status += "  |  " + t["speed_text"].asString();
    }
    setText(statusLeft, status);
    std::string right = s["extension"].asString();
    if (!right.empty()) right += "    ";
    setText(statusRight, right + "v" + s["version"].asString());

    add.apply(s["addForm"]);
    batch.apply(s["batchDialog"]);
    update.apply(s["updateDialog"]);
    settings.apply(s["settingsDialog"]);
}

void MainWindow::contextMenu(int x, int y) {
    HMENU menu = CreatePopupMenu();
    POINT pt{x, y};
    POINT client = pt;
    ScreenToClient(list, &client);
    LVHITTESTINFO hit{};
    hit.pt = client;
    int item = ListView_HitTest(list, &hit);
    if (item >= 0 && !(ListView_GetItemState(list, item, LVIS_SELECTED) & LVIS_SELECTED)) {
        ListView_SetItemState(list, -1, 0, LVIS_SELECTED);
        ListView_SetItemState(list, item, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
        sendCommand("selection");
    }
    std::vector<std::string> commands;
    auto add = [&](const char *text, const char *command) {
        commands.push_back(command);
        AppendMenuW(menu, MF_STRING, static_cast<UINT_PTR>(commands.size()), W(text).c_str());
    };
    if (item < 0) {
        add("Add URL...", "add");
        add("Refresh", "refresh");
    } else {
        auto enabled = enabledActions();
        for (auto &a : kRowActions)
            if (enabled.count(a[1])) add(a[0], a[1]);
    }
    int chosen = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, hwnd, nullptr);
    DestroyMenu(menu);
    if (chosen > 0) sendCommand(commands[static_cast<size_t>(chosen) - 1]);
}

void MainWindow::drawCell(NMLVCUSTOMDRAW *d) {
    const Palette &p = g_theme.p;
    int row = static_cast<int>(d->nmcd.dwItemSpec);
    int sub = d->iSubItem;
    if (row < 0 || row >= static_cast<int>(rows.size()) || sub < 0 || sub >= static_cast<int>(columnKeys.size())) return;
    const Value &r = rows[static_cast<size_t>(row)];
    RECT rc;
    ListView_GetSubItemRect(list, row, sub, sub == 0 ? LVIR_LABEL : LVIR_BOUNDS, &rc);
    bool selected = (ListView_GetItemState(list, row, LVIS_SELECTED) & LVIS_SELECTED) != 0;
    HDC dc = d->nmcd.hdc;
    COLORREF back = selected ? p.selection : (row % 2 ? p.bg : p.surface);
    HBRUSH brush = CreateSolidBrush(back);
    RECT fillRc = rc;
    if (sub == 0) fillRc.left = 0;
    FillRect(dc, &fillRc, brush);
    DeleteObject(brush);
    const char *field = "name";
    for (const auto &c : kColumns)
        if (columnKeys[static_cast<size_t>(sub)] == c.key) field = c.field;
    std::string text = r[field].asString();
    COLORREF ink = p.ink;
    if (columnKeys[static_cast<size_t>(sub)] == "status") {
        std::string st = r["status"].asString();
        if (st == "complete") ink = p.good;
        else if (st == "failed") ink = p.bad;
    }
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, ink);
    SelectObject(dc, g_theme.font);
    RECT tr = rc;
    tr.left += g_theme.S(8);
    tr.right -= g_theme.S(6);
    DrawTextW(dc, W(text).c_str(), -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
    if (columnKeys[static_cast<size_t>(sub)] == "status") {
        double progressValue = r["progress"].asNumber();
        std::string st = r["status"].asString();
        if (progressValue > 0 && progressValue < 1 && st != "complete" && st != "failed") {
            RECT track{rc.left + g_theme.S(8), rc.bottom - g_theme.S(6), rc.right - g_theme.S(8), rc.bottom - g_theme.S(3)};
            HBRUSH t = CreateSolidBrush(p.border);
            FillRect(dc, &track, t);
            DeleteObject(t);
            track.right = track.left + static_cast<LONG>((track.right - track.left) * progressValue);
            HBRUSH f = CreateSolidBrush(p.accent);
            FillRect(dc, &track, f);
            DeleteObject(f);
        }
    }
}

LRESULT MainWindow::notify(NMHDR *h) {
    HWND header = ListView_GetHeader(list);
    if (h->hwndFrom == header) {
        switch (h->code) {
        case NM_CUSTOMDRAW: {
            auto *d = reinterpret_cast<NMCUSTOMDRAW *>(h);
            if (d->dwDrawStage == CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
            if (d->dwDrawStage == CDDS_ITEMPREPAINT) {
                HDC dc = d->hdc;
                HBRUSH b = CreateSolidBrush(g_theme.p.panel);
                FillRect(dc, &d->rc, b);
                DeleteObject(b);
                RECT line{d->rc.right - 1, d->rc.top + g_theme.S(6), d->rc.right, d->rc.bottom - g_theme.S(6)};
                HBRUSH l = CreateSolidBrush(g_theme.p.border);
                FillRect(dc, &line, l);
                DeleteObject(l);
                wchar_t text[128]{};
                HDITEMW item{};
                item.mask = HDI_TEXT | HDI_FORMAT;
                item.pszText = text;
                item.cchTextMax = 128;
                Header_GetItem(header, static_cast<int>(d->dwItemSpec), &item);
                std::wstring label_ = text;
                if (item.fmt & HDF_SORTUP) label_ += L"  \x25B2";
                if (item.fmt & HDF_SORTDOWN) label_ += L"  \x25BC";
                SetBkMode(dc, TRANSPARENT);
                SetTextColor(dc, g_theme.p.ink);
                SelectObject(dc, g_theme.bold);
                RECT tr = d->rc;
                tr.left += g_theme.S(8);
                DrawTextW(dc, label_.c_str(), -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
                return CDRF_SKIPDEFAULT;
            }
            return CDRF_DODEFAULT;
        }
        case HDN_ENDTRACKW: {
            auto *n = reinterpret_cast<NMHEADERW *>(h);
            if (!applying && n->pitem && (n->pitem->mask & HDI_WIDTH) && n->iItem >= 0 && n->iItem < static_cast<int>(columnKeys.size())) {
                Value v = Value::object();
                v.set("column", columnKeys[static_cast<size_t>(n->iItem)]);
                v.set("width", n->pitem->cxy * 96 / g_theme.dpi);
                send("width", v);
            }
            return 0;
        }
        }
        return 0;
    }
    if (h->hwndFrom != list) return 0;
    switch (h->code) {
    case LVN_GETDISPINFOW: {
        auto *d = reinterpret_cast<NMLVDISPINFOW *>(h);
        if ((d->item.mask & LVIF_TEXT) && d->item.iItem >= 0 && d->item.iItem < static_cast<int>(rows.size()) &&
            d->item.iSubItem >= 0 && d->item.iSubItem < static_cast<int>(columnKeys.size())) {
            const char *field = "name";
            for (const auto &c : kColumns)
                if (columnKeys[static_cast<size_t>(d->item.iSubItem)] == c.key) field = c.field;
            std::wstring w = W(rows[static_cast<size_t>(d->item.iItem)][field].asString());
            wcsncpy(d->item.pszText, w.c_str(), static_cast<size_t>(d->item.cchTextMax) - 1);
            d->item.pszText[d->item.cchTextMax - 1] = 0;
        }
        return 0;
    }
    case NM_CUSTOMDRAW: {
        auto *d = reinterpret_cast<NMLVCUSTOMDRAW *>(h);
        switch (d->nmcd.dwDrawStage) {
        case CDDS_PREPAINT: {
            RECT rc;
            GetClientRect(list, &rc);
            HBRUSH b = CreateSolidBrush(g_theme.p.surface);
            FillRect(d->nmcd.hdc, &rc, b);
            DeleteObject(b);
            return CDRF_NOTIFYITEMDRAW;
        }
        case CDDS_ITEMPREPAINT: return CDRF_NOTIFYSUBITEMDRAW;
        case CDDS_ITEMPREPAINT | CDDS_SUBITEM:
            drawCell(d);
            return CDRF_SKIPDEFAULT;
        }
        return CDRF_DODEFAULT;
    }
    case LVN_ITEMCHANGED: {
        auto *n = reinterpret_cast<NMLISTVIEW *>(h);
        if (!applying && (n->uChanged & LVIF_STATE) && ((n->uOldState ^ n->uNewState) & LVIS_SELECTED))
            SetTimer(hwnd, TIMER_SELECTION, 30, nullptr);
        return 0;
    }
    case LVN_COLUMNCLICK: {
        auto *n = reinterpret_cast<NMLISTVIEW *>(h);
        if (n->iSubItem >= 0 && n->iSubItem < static_cast<int>(columnKeys.size()))
            sendCommand("sort", Value(columnKeys[static_cast<size_t>(n->iSubItem)]));
        return 0;
    }
    case NM_DBLCLK: {
        auto *n = reinterpret_cast<NMITEMACTIVATE *>(h);
        if (n->iItem >= 0) sendCommand("default");
        return 0;
    }
    case LVN_KEYDOWN: {
        auto *n = reinterpret_cast<NMLVKEYDOWN *>(h);
        bool ctrl = GetKeyState(VK_CONTROL) < 0, alt = GetKeyState(VK_MENU) < 0, shift = GetKeyState(VK_SHIFT) < 0;
        if (n->wVKey == VK_RETURN) sendCommand(alt ? "properties" : "default");
        else if (n->wVKey == VK_DELETE) sendCommand("delete");
        else if (ctrl && n->wVKey == 'C') sendCommand(shift ? "copy_file" : "copy_link");
        else if (ctrl && n->wVKey == 'A') {
            ListView_SetItemState(list, -1, LVIS_SELECTED, LVIS_SELECTED);
        }
        return 0;
    }
    }
    return 0;
}

LRESULT CALLBACK mainProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_STATE: {
        std::unique_ptr<Value> state;
        {
            std::lock_guard<std::mutex> lock(g_inbox);
            state = std::move(g_latest);
        }
        g_pending = false;
        if (state) g_ui.apply(*state);
        return 0;
    }
    case WM_INPUT_CLOSED:
        DestroyWindow(h);
        return 0;
    case WM_SIZE:
        if (g_ui.list) g_ui.layout();
        return 0;
    case WM_GETMINMAXINFO: {
        auto *mm = reinterpret_cast<MINMAXINFO *>(l);
        mm->ptMinTrackSize.x = g_theme.S(720);
        mm->ptMinTrackSize.y = g_theme.S(460);
        return 0;
    }
    case WM_CLOSE:
        send("close");
        return 0;
    case WM_ACTIVATE:
        if (LOWORD(w) != WA_INACTIVE) send("focus");
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(h, &rc);
        FillRect(reinterpret_cast<HDC>(w), &rc, g_theme.bg);
        return 1;
    }
    case WM_TIMER:
        if (w == TIMER_SELECTION) {
            KillTimer(h, TIMER_SELECTION);
            g_ui.sendCommand("selection");
        }
        return 0;
    case WM_COMMAND: {
        int id = LOWORD(w);
        if (reinterpret_cast<HWND>(l) == g_ui.categories && HIWORD(w) == LBN_SELCHANGE) {
            int i = static_cast<int>(SendMessageW(g_ui.categories, LB_GETCURSEL, 0, 0));
            const auto &rows = g_ui.categoryRows.items();
            if (i >= 0 && i < static_cast<int>(rows.size()) && rows[static_cast<size_t>(i)]["key"].asString() != g_ui.selectedCategory)
                g_ui.sendCommand("category", Value(rows[static_cast<size_t>(i)]["key"].asString()));
            return 0;
        }
        if (reinterpret_cast<HWND>(l) == g_ui.search && HIWORD(w) == EN_CHANGE) {
            if (!g_ui.applying) g_ui.sendCommand("search", Value(getText(g_ui.search)));
            return 0;
        }
        auto c = g_clicks.find(id);
        if (c != g_clicks.end() && HIWORD(w) == BN_CLICKED) c->second();
        return 0;
    }
    case WM_NOTIFY:
        return g_ui.notify(reinterpret_cast<NMHDR *>(l));
    case WM_CONTEXTMENU:
        if (reinterpret_cast<HWND>(w) == g_ui.list || reinterpret_cast<HWND>(w) == g_ui.empty) {
            POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
            if (pt.x == -1 && pt.y == -1) {
                RECT rc;
                GetWindowRect(g_ui.list, &rc);
                pt = {rc.left + 40, rc.top + 40};
            }
            g_ui.contextMenu(pt.x, pt.y);
            return 0;
        }
        break;
    case WM_MEASUREITEM: {
        auto *mi = reinterpret_cast<MEASUREITEMSTRUCT *>(l);
        if (mi->CtlType == ODT_LISTBOX) mi->itemHeight = static_cast<UINT>(g_theme.S(34));
        return TRUE;
    }
    case WM_DRAWITEM: {
        auto *d = reinterpret_cast<DRAWITEMSTRUCT *>(l);
        if (d->CtlType == ODT_BUTTON) {
            drawButton(d);
            return TRUE;
        }
        if (d->CtlType == ODT_LISTBOX && d->itemID != static_cast<UINT>(-1)) {
            const Palette &p = g_theme.p;
            bool selected = (d->itemState & ODS_SELECTED) != 0;
            HBRUSH bg = CreateSolidBrush(selected ? p.selection : p.surface);
            FillRect(d->hDC, &d->rcItem, bg);
            DeleteObject(bg);
            wchar_t text[256]{};
            SendMessageW(d->hwndItem, LB_GETTEXT, d->itemID, reinterpret_cast<LPARAM>(text));
            wchar_t *tab = wcschr(text, L'\t');
            std::wstring count;
            if (tab) {
                count = tab + 1;
                *tab = 0;
            }
            SetBkMode(d->hDC, TRANSPARENT);
            SelectObject(d->hDC, g_theme.font);
            RECT tr = d->rcItem;
            tr.left += g_theme.S(16);
            tr.right -= g_theme.S(14);
            SetTextColor(d->hDC, p.ink);
            DrawTextW(d->hDC, text, -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
            if (!count.empty()) {
                SetTextColor(d->hDC, p.muted);
                DrawTextW(d->hDC, count.c_str(), -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_RIGHT | DT_NOPREFIX);
            }
            return TRUE;
        }
        return TRUE;
    }
    case WM_CTLCOLOREDIT: {
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, g_theme.p.ink);
        SetBkColor(dc, g_theme.p.surface);
        return reinterpret_cast<LRESULT>(g_theme.surface);
    }
    case WM_CTLCOLORLISTBOX: {
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, g_theme.p.ink);
        SetBkColor(dc, g_theme.p.surface);
        return reinterpret_cast<LRESULT>(g_theme.surface);
    }
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLORBTN: {
        HDC dc = reinterpret_cast<HDC>(w);
        HWND ctl = reinterpret_cast<HWND>(l);
        bool muted = ctl == g_ui.statusLeft || ctl == g_ui.statusRight || ctl == g_ui.empty || ctl == g_ui.detailText;
        SetTextColor(dc, muted ? g_theme.p.muted : g_theme.p.ink);
        SetBkColor(dc, g_theme.p.bg);
        return reinterpret_cast<LRESULT>(g_theme.bg);
    }
    }
    return DefWindowProcW(h, m, w, l);
}

// Keyboard shortcuts the Qt version had; handled before the focused control sees the key.
bool shortcut(const MSG &msg) {
    if (msg.message != WM_KEYDOWN && msg.message != WM_SYSKEYDOWN) return false;
    if (GetAncestor(msg.hwnd, GA_ROOT) != g_main) return false;
    bool ctrl = GetKeyState(VK_CONTROL) < 0, alt = GetKeyState(VK_MENU) < 0;
    if (msg.wParam == VK_F5) { g_ui.sendCommand("refresh"); return true; }
    if (!ctrl) return false;
    switch (msg.wParam) {
    case 'N': g_ui.sendCommand("add"); return true;
    case 'B': g_ui.sendCommand("batch"); return true;
    case 'K': g_ui.sendCommand("palette"); return true;
    case 'F': SetFocus(g_ui.search); return true;
    case 'D': if (alt) { g_ui.sendCommand("clipboard", Value("Video")); return true; } break;
    case 'M': if (alt) { g_ui.sendCommand("clipboard", Value("Audio")); return true; } break;
    }
    return false;
}

bool initClasses(HINSTANCE inst) {
    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_LISTVIEW_CLASSES | ICC_PROGRESS_CLASS | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof wc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr;
    wc.hIcon = g_icon ? g_icon : LoadIconW(inst, MAKEINTRESOURCEW(1));
    wc.lpszClassName = L"MDMain";
    wc.lpfnWndProc = mainProc;
    if (!RegisterClassExW(&wc)) return false;
    wc.lpszClassName = L"MDForm";
    wc.lpfnWndProc = Form::proc;
    return RegisterClassExW(&wc) != 0;
}

// Text edits in the forms notify the controller as the user types.
void pumpFormTimers(const MSG &msg) {
    if (msg.message == WM_TIMER && msg.hwnd == g_ui.batch.hwnd && msg.wParam == TIMER_BATCH) g_ui.batch.tick();
}

int selfTest() {
    // Builds every window and feeds it a state with each dialog open, as the controller would.
    Value state = Value::object();
    state.set("visible", true);
    state.set("theme", "Dark");
    Value row = Value::object();
    row.set("key", "q:smoke");
    row.set("name", "Smoke.mp4");
    row.set("status", "downloading");
    row.set("progress", 0.5);
    Value rows = Value::array();
    rows.push(row);
    state.set("rows", rows);
    Value cols = Value::array();
    cols.push("name");
    cols.push("status");
    state.set("columns", cols);
    g_ui.apply(state);
    for (const char *dialog : {"addForm", "updateDialog", "batchDialog", "settingsDialog"}) {
        Value d = Value::object();
        d.set("visible", true);
        state.set(dialog, d);
        g_ui.apply(state);
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (g_ui.rows.size() != 1) return 1;
        Value hidden = Value::object();
        state.set(dialog, hidden);
        g_ui.apply(state);
    }
    state.set("theme", "Light");
    g_ui.apply(state);
    return 0;
}

} // namespace

int WINAPI WinMain(HINSTANCE inst, HINSTANCE, LPSTR, int) {
    SetProcessDPIAware();
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::wstring icon;
    bool test = false;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--self-test") test = true;
        else if (a == L"--icon" && i + 1 < argc) icon = argv[++i];
    }
    LocalFree(argv);
    if (!icon.empty())
        g_icon = static_cast<HICON>(LoadImageW(nullptr, icon.c_str(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));

    HDC screen = GetDC(nullptr);
    g_theme.dpi = GetDeviceCaps(screen, LOGPIXELSX);
    ReleaseDC(nullptr, screen);
    g_theme.build(false);
    g_theme.buildFonts();
    if (!initClasses(inst)) return 2;

    g_main = CreateWindowExW(0, L"MDMain", L"Media Downloader", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN, CW_USEDEFAULT, CW_USEDEFAULT,
                             g_theme.S(1180), g_theme.S(720), nullptr, nullptr, inst, nullptr);
    if (!g_main) return 3;
    g_ui.hwnd = g_main;
    if (g_icon) {
        SendMessageW(g_main, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(g_icon));
        SendMessageW(g_main, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(g_icon));
    }
    g_ui.create();
    g_ui.layout();
    setDarkTitleBar(g_main);

    if (test) {
        int status = selfTest();
        DestroyWindow(g_main);
        return status;
    }

    std::thread(readerThread).detach();
    send("ready");
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (shortcut(msg)) continue;
        pumpFormTimers(msg);
        bool handled = false;
        for (auto &kv : g_forms) {
            if (IsWindowVisible(kv.first) && (msg.hwnd == kv.first || IsChild(kv.first, msg.hwnd))) {
                if (msg.message == WM_KEYDOWN && msg.wParam == VK_ESCAPE) {
                    kv.second->close();
                    handled = true;
                } else if (IsDialogMessageW(kv.first, &msg)) {
                    handled = true;
                }
                break;
            }
        }
        if (handled) continue;
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
