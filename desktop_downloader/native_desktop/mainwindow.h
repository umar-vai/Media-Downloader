// The main window, laid out exactly like the classic desktop window (5.x): menu bar, toolbar,
// category tree, list header with search, the download list, the details pane and the status bar.
#pragma once

#include "common.h"
#include "icons.h"
#include "widgets.h"

namespace md {

// ------------------------------------------------------------------------------------------ data
struct Row {
    std::string key;
    std::wstring name, size, statusText, speed, eta, lastTry, added, website, quality, saveTo;
    std::string status, category, tag, dragPath;
    double progress = 0;
    std::set<std::string> actions;
};

struct ColumnDef {
    const char *key;
    const wchar_t *title;
    int width;      // design px
    bool right;     // anchor "e"
};

// Same columns, titles, widths and anchors as download_list.COLUMNS.
const ColumnDef kColumnDefs[] = {
    {"name", L"File Name", 290, false}, {"size", L"Size", 82, true},          {"status", L"Status", 100, false},
    {"speed", L"Transfer rate", 96, true}, {"eta", L"Time left", 96, true},     {"last_try", L"Last Try Date", 134, false},
    {"added", L"Date Added", 134, false},  {"website", L"Website", 120, false}, {"quality", L"Quality", 80, false},
    {"save_to", L"Save To", 250, false},
};

inline const ColumnDef *columnDef(const std::string &key) {
    for (const auto &c : kColumnDefs)
        if (key == c.key) return &c;
    return nullptr;
}

inline const std::wstring &cellText(const Row &r, const std::string &key) {
    static const std::wstring none;
    if (key == "name") return r.name;
    if (key == "size") return r.size;
    if (key == "status") return r.statusText;
    if (key == "speed") return r.speed;
    if (key == "eta") return r.eta;
    if (key == "last_try") return r.lastTry;
    if (key == "added") return r.added;
    if (key == "website") return r.website;
    if (key == "quality") return r.quality;
    if (key == "save_to") return r.saveTo;
    return none;
}

// ------------------------------------------------------------------------------------- toolbar
struct ToolItem {
    std::string key, label, icon, command;
    std::wstring tip;
    bool separator = false;
    bool enabled = true;
    RECT rc{};
};

class Toolbar {
public:
    HWND hwnd = nullptr;
    std::vector<ToolItem> items;
    // Update badge at the right end: a one-line note and the Updates / Install update button.
    std::wstring note = L"Click to check", updateLabel = L"Updates";
    COLORREF noteColor = CLR_INVALID;
    bool noteBold = false, updateEnabled = true;
    RECT noteRc{}, updateRc{};
    int hot = -1, pressed = -1;  // index into items, or -2 for the update button
    std::function<void(const std::string &)> onCommand;

    void create(HWND parent) {
        hwnd = CreateWindowExW(0, L"MDToolbar", L"", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, parent, nullptr, g_inst, this);
        auto add = [&](const char *key, const char *label, const char *icon, const char *command, const wchar_t *tip) {
            ToolItem t;
            t.key = key;
            t.label = label;
            t.icon = icon;
            t.command = command;
            t.tip = tip;
            items.push_back(t);
        };
        auto sep = [&] {
            ToolItem t;
            t.separator = true;
            items.push_back(t);
        };
        add("add", "Add URL", "add", "add", L"Add a download link  (Ctrl+N)");
        add("batch", "Batch", "batch", "batch", L"Add many links at once  (Ctrl+B)");
        sep();
        add("resume", "Resume", "resume", "resume", L"Resume the selected downloads");
        add("stop", "Stop", "stop", "stop", L"Stop the selected downloads (they can be resumed)");
        add("start_all", "Start All", "queue_start", "resume_all", L"Resume every stopped download");
        add("stop_all", "Stop All", "stop_all", "pause_all", L"Stop every running and queued download");
        sep();
        add("delete", "Delete", "delete", "delete", L"Remove the selected downloads  (Del)");
        add("delete_done", "Delete Completed", "delete_done", "delete_completed", L"Remove finished downloads from the list");
        sep();
        add("options", "Options", "settings", "settings", L"Settings");
        add("browsers", "Browsers", "browser", "browsers", L"Browser extension");
        add("editor", "Editor", "editor", "editor", L"Cut, convert and export media files");
        add("recorder", "Record", "record", "screen", L"Record the screen (full screen or an area) with sound");
        add("audio_recorder", "Audio", "mic", "audio", L"Record a call or meeting: the other side's voice and yours, as MP3 / WAV");
    }

    int height() const {
        SIZE t = measureWindow(L"Ag", g_theme.ui);
        return S(2) * 2 + 2 + S(1) * 2 + S(20) + t.cy + S(1);
    }

    void layout() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        HDC dc = GetDC(hwnd);
        int x = S(2), top = S(2), h = rc.bottom - S(4);
        for (auto &t : items) {
            if (t.separator) {
                t.rc = {x + S(3), top + S(3), x + S(3) + 2, top + h - S(3)};
                x += S(6) + 2;
                continue;
            }
            SIZE sz = measure(dc, W(t.label), g_theme.ui);
            int w = std::max(S(20), static_cast<int>(sz.cx)) + S(5) * 2 + 2;
            t.rc = {x + S(1), top, x + S(1) + w, top + h};
            x += w + S(2);
        }
        int right = rc.right - S(4);
        SIZE bsz = measure(dc, updateLabel, g_theme.ui);
        int bw = std::max(S(20), static_cast<int>(bsz.cx)) + S(5) * 2 + 2;
        updateRc = {right - bw, top, right, top + h};
        HFONT nf = noteBold ? g_theme.uiBold : g_theme.ui;
        SIZE nsz = measure(dc, note, nf);
        int noteLeft = updateRc.left - S(4) - nsz.cx;
        // In a narrow window the buttons win: the note is hidden unless an update is waiting.
        bool fits = noteLeft > x + S(16);
        noteRc = fits || noteBold ? RECT{std::max(x, static_cast<int>(noteLeft)), top, updateRc.left - S(4), top + h} : RECT{0, 0, 0, 0};
        ReleaseDC(hwnd, dc);
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    void setEnabled(const std::string &key, bool on) {
        for (auto &t : items)
            if (t.key == key && t.enabled != on) {
                t.enabled = on;
                InvalidateRect(hwnd, &t.rc, FALSE);
            }
    }

    void setBadge(const std::wstring &n, COLORREF color, bool bold, const std::wstring &label, bool enabledButton) {
        if (n == note && color == noteColor && bold == noteBold && label == updateLabel && enabledButton == updateEnabled) return;
        note = n;
        noteColor = color;
        noteBold = bold;
        updateLabel = label;
        updateEnabled = enabledButton;
        layout();
    }

    RECT itemRect(const std::string &key) const {
        for (const auto &t : items)
            if (t.key == key) return t.rc;
        return {};
    }

    void paint() {
        BufferedPaint bp(hwnd);
        HDC dc = bp.dc();
        const Palette &p = g_theme.p;
        fill(dc, bp.rect(), p.BG);
        int iconSize = S(20);
        auto drawButton = [&](const RECT &r, const std::wstring &label, HBITMAP icon, HBITMAP iconOff, bool enabled, int index) {
            bool isHot = hot == index && enabled, isPressed = pressed == index && enabled && hot == index;
            if (isHot || isPressed) {
                fill(dc, r, isPressed ? p.TOOL_PRESS : p.TOOL_HOVER);
                frame(dc, r, p.TOOL_EDGE);
            }
            int ix = (r.left + r.right - iconSize) / 2;
            icons::draw(dc, enabled ? icon : iconOff, ix, r.top + S(1) + 1, iconSize);
            RECT tr{r.left, r.top + S(1) + 1 + iconSize, r.right, r.bottom - S(1)};
            text(dc, label, tr, enabled ? p.TEXT : p.FAINT, g_theme.ui, DT_SINGLELINE | DT_CENTER | DT_TOP | DT_NOPREFIX);
        };
        for (size_t i = 0; i < items.size(); ++i) {
            auto &t = items[i];
            if (t.separator) {
                vline(dc, t.rc.left, t.rc.top, t.rc.bottom, g_theme.dark ? p.BORDER : hex(0xA0A0A0));
                vline(dc, t.rc.left + 1, t.rc.top, t.rc.bottom, g_theme.dark ? p.BG : hex(0xFFFFFF));
                continue;
            }
            drawButton(t.rc, W(t.label), icons::g_cache.tool(t.icon, iconSize), icons::g_cache.tool(t.icon, iconSize, true),
                       t.enabled, static_cast<int>(i));
        }
        if (noteRc.right > noteRc.left)
            text(dc, note, noteRc, noteColor == CLR_INVALID ? p.FAINT : noteColor, noteBold ? g_theme.uiBold : g_theme.ui,
                 DT_SINGLELINE | DT_VCENTER | DT_RIGHT | DT_END_ELLIPSIS | DT_NOPREFIX);
        drawButton(updateRc, updateLabel, icons::g_cache.tool("update", iconSize), icons::g_cache.tool("update", iconSize, true),
                   updateEnabled, -2);
    }

    int hitTest(POINT pt) const {
        for (size_t i = 0; i < items.size(); ++i)
            if (!items[i].separator && PtInRect(&items[i].rc, pt)) return static_cast<int>(i);
        if (PtInRect(&updateRc, pt) || (noteRc.right > noteRc.left && PtInRect(&noteRc, pt))) return -2;
        return -1;
    }

    bool enabledAt(int index) const {
        if (index == -2) return updateEnabled;
        return index >= 0 && items[static_cast<size_t>(index)].enabled;
    }

    void refreshTooltips() {
        for (size_t i = 0; i < items.size(); ++i)
            if (!items[i].separator) addTooltip(hwnd, items[i].tip, hwnd, &items[i].rc, static_cast<UINT>(i + 1));
        addTooltip(hwnd, L"Check for a new version (one click)", hwnd, &updateRc, 999);
    }

    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l) {
        auto *self = reinterpret_cast<Toolbar *>(GetWindowLongPtrW(h, GWLP_USERDATA));
        if (m == WM_NCCREATE) {
            self = static_cast<Toolbar *>(reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams);
            SetWindowLongPtrW(h, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (!self) return DefWindowProcW(h, m, w, l);
        switch (m) {
        case WM_PAINT: self->paint(); return 0;
        case WM_ERASEBKGND: return 1;
        case WM_SIZE: self->layout(); return 0;
        case WM_MOUSEMOVE: {
            POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
            int now = self->hitTest(pt);
            if (now != self->hot) {
                self->hot = now;
                InvalidateRect(h, nullptr, FALSE);
                TRACKMOUSEEVENT t{sizeof t, TME_LEAVE, h, 0};
                TrackMouseEvent(&t);
            }
            return 0;
        }
        case WM_MOUSELEAVE:
            self->hot = -1;
            InvalidateRect(h, nullptr, FALSE);
            return 0;
        case WM_LBUTTONDOWN: {
            POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
            int at = self->hitTest(pt);
            if (at != -1 && self->enabledAt(at)) {
                self->pressed = at;
                SetCapture(h);
                InvalidateRect(h, nullptr, FALSE);
            }
            return 0;
        }
        case WM_LBUTTONUP: {
            int was = self->pressed;
            self->pressed = -1;
            ReleaseCapture();
            POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
            int at = self->hitTest(pt);
            InvalidateRect(h, nullptr, FALSE);
            if (was != -1 && was == at && self->enabledAt(at) && self->onCommand) {
                if (at == -2) self->onCommand("update_badge");
                else self->onCommand(self->items[static_cast<size_t>(at)].command);
            }
            return 0;
        }
        }
        return DefWindowProcW(h, m, w, l);
    }
};

// -------------------------------------------------------------------------------- status bar
class StatusBar {
public:
    HWND hwnd = nullptr;
    std::wstring message = L"Ready", summary, speed, extension, version;
    COLORREF messageColor = CLR_INVALID;

    void create(HWND parent) {
        hwnd = CreateWindowExW(0, L"MDStatus", L"", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, parent, nullptr, g_inst, this);
    }
    int height() const { return measureWindow(L"Ag", g_theme.ui).cy + S(2) * 2 + 1; }

    void set(const std::wstring &msg, const std::wstring &sum, const std::wstring &spd, const std::wstring &ext, const std::wstring &ver) {
        if (msg == message && sum == summary && spd == speed && ext == extension && ver == version) return;
        message = msg.empty() ? L"Ready" : msg;
        summary = sum;
        speed = spd;
        extension = ext;
        version = ver;
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    void paint() {
        BufferedPaint bp(hwnd);
        HDC dc = bp.dc();
        const Palette &p = g_theme.p;
        RECT rc = bp.rect();
        fill(dc, rc, p.BG);
        COLORREF etched = g_theme.dark ? p.BORDER : hex(0xA0A0A0);
        hline(dc, 0, rc.right, 0, etched);
        if (!g_theme.dark) hline(dc, 0, rc.right, 1, hex(0xFFFFFF));
        int x = rc.right;
        // Panes from the right: version | extension | speed | summary; then the message.
        for (const std::wstring *pane : {&version, &extension, &speed, &summary}) {
            if (pane->empty()) {
                x -= S(8) * 2;
                vline(dc, x, rc.top + S(2) + 1, rc.bottom - S(2), etched);
                continue;
            }
            SIZE sz = measure(dc, *pane, g_theme.ui);
            RECT tr{x - S(8) - sz.cx, 1, x - S(8), rc.bottom};
            text(dc, *pane, tr, p.TEXT, g_theme.ui, DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX);
            x = tr.left - S(8);
            vline(dc, x, rc.top + S(2) + 1, rc.bottom - S(2), etched);
        }
        RECT mr{S(6), 1, x - S(4), rc.bottom};
        text(dc, message, mr, p.TEXT, g_theme.ui);
    }

    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l) {
        auto *self = reinterpret_cast<StatusBar *>(GetWindowLongPtrW(h, GWLP_USERDATA));
        if (m == WM_NCCREATE) {
            self = static_cast<StatusBar *>(reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams);
            SetWindowLongPtrW(h, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        if (self && m == WM_PAINT) {
            self->paint();
            return 0;
        }
        if (m == WM_ERASEBKGND) return 1;
        if (m == WM_SIZE) InvalidateRect(h, nullptr, FALSE);
        return DefWindowProcW(h, m, w, l);
    }
};

// ------------------------------------------------------------------------------ details pane
class DetailsPane {
public:
    HWND hwnd = nullptr, title = nullptr, state = nullptr, bar = nullptr, where = nullptr;
    std::vector<std::pair<HWND, HWND>> facts;  // label, value
    std::vector<std::pair<std::string, HWND>> buttons;
    std::function<void(const std::string &)> onCommand;
    Value last;
    int perRow = 0;
    bool stacked = false;

    static constexpr const char *kFields[][2] = {{"size", "Size"},       {"done", "Downloaded"}, {"speed", "Transfer rate"},
                                                 {"eta", "Time left"},  {"website", "Website"}, {"quality", "Quality"}};

    void create(HWND parent) {
        hwnd = CreateWindowExW(WS_EX_CONTROLPARENT, L"MDPane", L"", WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN, 0, 0, 10, 10,
                               parent, nullptr, g_inst, this);
        title = makeLabel(hwnd, "No download selected", Ink::Text, true, SS_ENDELLIPSIS);
        state = makeLabel(hwnd, "", Ink::Text, false, SS_RIGHT);
        bar = makeProgress(hwnd);
        for (auto &f : kFields) {
            HWND name = makeLabel(hwnd, std::string(f[1]) + ":", Ink::Muted);
            HWND value = makeLabel(hwnd, "\xE2\x80\x94", Ink::Text);
            facts.emplace_back(name, value);
        }
        where = makeLabel(hwnd, "", Ink::Muted, false, SS_ENDELLIPSIS);
        const char *defs[][2] = {{"toggle", "Pause"}, {"cancel", "Cancel"}, {"open", "Open"}, {"open_folder", "Open folder"},
                                 {"more", "Properties\xE2\x80\xA6"}};
        for (auto &d : defs) {
            std::string key = d[0];
            HWND b = makeButton(hwnd, d[1], [this, key] { if (onCommand) onCommand(key); });
            buttons.emplace_back(key, b);
            EnableWindow(b, FALSE);
        }
        trackAllHover(hwnd);
    }

    int buttonWidth(const std::string &key) const {
        // ttk width 11 / 13 characters of the average glyph, plus the button padding.
        SIZE avg = measureWindow(L"0", g_theme.ui);
        return avg.cx * (key == "more" ? 13 : 11) + S(10) * 2;
    }

    int desiredHeight(int width) {
        reflow(width);
        SIZE t = measureWindow(L"Ag", g_theme.ui);
        int line = t.cy;
        int rows = perRow == static_cast<int>(facts.size()) ? 1 : 2;
        int h = 1 + S(4) + line + S(3) + S(15) + S(3) + rows * line + S(4) + std::max(line, S(24)) + S(5);
        if (stacked) h += S(4) + S(24);
        return h;
    }

    void reflow(int width) {
        HDC dc = GetDC(hwnd);
        int total = 0;
        for (auto &f : facts) total += measure(dc, windowText(f.first), g_theme.ui).cx + measure(dc, windowText(f.second), g_theme.ui).cx + S(17);
        ReleaseDC(hwnd, dc);
        int inner = width - S(6) * 2;
        perRow = total <= inner ? static_cast<int>(facts.size()) : static_cast<int>((facts.size() + 1) / 2);
        int buttonsW = 0;
        for (auto &b : buttons) buttonsW += buttonWidth(b.first) + S(4);
        stacked = buttonsW + S(260) > inner;
    }

    void layout() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        int width = rc.right;
        reflow(width);
        SIZE t = measureWindow(L"Ag", g_theme.ui);
        int line = t.cy;
        int x0 = S(6), x1 = width - S(6), y = 1 + S(4);
        SIZE st = measureWindow(windowText(state), g_theme.ui);
        int stateW = std::min(static_cast<int>(st.cx) + S(2), (x1 - x0) / 2);
        place(title, x0, y, x1 - x0 - stateW - S(12), line);
        place(state, x1 - stateW, y, stateW, line);
        y += line + S(3);
        place(bar, x0, y, x1 - x0, S(15));
        y += S(15) + S(3);
        HDC dc = GetDC(hwnd);
        int col = 0, x = x0;
        for (size_t i = 0; i < facts.size(); ++i) {
            if (col == perRow) {
                col = 0;
                x = x0;
                y += line;
            }
            int nw = measure(dc, windowText(facts[i].first), g_theme.ui).cx;
            int vw = measure(dc, windowText(facts[i].second), g_theme.ui).cx;
            if (col > 0) x += S(14);
            place(facts[i].first, x, y, nw + S(1), line);
            x += nw + S(3);
            place(facts[i].second, x, y, vw + S(2), line);
            x += vw;
            ++col;
        }
        ReleaseDC(hwnd, dc);
        y += line + S(4);
        int bh = S(24);
        int buttonsW = 0;
        for (auto &b : buttons) buttonsW += buttonWidth(b.first) + S(4);
        if (!stacked) {
            place(where, x0, y + (bh - line) / 2, x1 - x0 - buttonsW - S(8), line);
            int bx = x1 - buttonsW;
            for (auto &b : buttons) {
                bx += S(4);
                place(b.second, bx, y, buttonWidth(b.first), bh);
                bx += buttonWidth(b.first);
            }
        } else {
            place(where, x0, y, x1 - x0, line);
            y += line + S(4);
            int bx = x1 - buttonsW;
            for (auto &b : buttons) {
                bx += S(4);
                place(b.second, bx, y, buttonWidth(b.first), bh);
                bx += buttonWidth(b.first);
            }
        }
        InvalidateRect(hwnd, nullptr, TRUE);
    }

    // Returns true when the height may have changed.
    bool apply(const Value &v) {
        if (v == last) return false;
        last = v;
        setText(title, v["title"].asString());
        setText(state, v["state"].asString());
        setInk(state, v["state_kind"].asString() == "danger" ? Ink::Danger : Ink::Text);
        setProgress(bar, v["progress"].asNumber(), v["busy"].asBool());
        for (size_t i = 0; i < facts.size(); ++i) setText(facts[i].second, v["values"][kFields[i][0]].asString());
        setText(where, v["where"].asString());
        setInk(where, v["where_kind"].asString() == "danger" ? Ink::Danger : Ink::Muted);
        for (auto &b : buttons) enable(b.second, v["enabled"][b.first].asBool());
        setText(buttons[0].second, v["toggle"].asString());
        setText(buttons[4].second, v["more"].asString());
        layout();
        return true;
    }

    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l) {
        auto *self = reinterpret_cast<DetailsPane *>(GetWindowLongPtrW(h, GWLP_USERDATA));
        if (m == WM_NCCREATE) {
            self = static_cast<DetailsPane *>(reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams);
            SetWindowLongPtrW(h, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        }
        LRESULT out = 0;
        if (handleControlMessage(h, m, w, l, &out, g_theme.p.BG)) return out;
        switch (m) {
        case WM_ERASEBKGND: {
            RECT rc;
            GetClientRect(h, &rc);
            HDC dc = reinterpret_cast<HDC>(w);
            fill(dc, rc, g_theme.p.BG);
            hline(dc, 0, rc.right, 0, g_theme.dark ? g_theme.p.BORDER : hex(0xA0A0A0));
            return 1;
        }
        case WM_SIZE:
            if (self) self->layout();
            return 0;
        }
        return DefWindowProcW(h, m, w, l);
    }
};

// ----------------------------------------------------------------------------- list header
class ListHeader {
public:
    HWND hwnd = nullptr, title = nullptr, count = nullptr, searchLabel = nullptr, search = nullptr;
    std::function<void(const std::string &)> onSearch;
    bool applying = false;

    void create(HWND parent) {
        hwnd = CreateWindowExW(WS_EX_CONTROLPARENT, L"MDPane", L"", WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN, 0, 0, 10, 10,
                               parent, nullptr, g_inst, nullptr);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        title = makeLabel(hwnd, "All Downloads", Ink::Text, true);
        count = makeLabel(hwnd, "", Ink::Faint);
        searchLabel = makeLabel(hwnd, "Search:");
        search = makeEdit(hwnd, false, false, [this] {
            if (!applying && onSearch) onSearch(getText(search));
        });
    }

    int height() const { return measureWindow(L"Ag", g_theme.ui).cy + S(6) + S(2) * 2; }

    void layout() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        int line = measureWindow(L"Ag", g_theme.ui).cy;
        int eh = line + S(6);
        int cy = (rc.bottom - line) / 2;
        SIZE tw = measureWindow(windowText(title), g_theme.uiBold);
        place(title, S(2), cy, tw.cx + S(2), line);
        SIZE cw = measureWindow(windowText(count), g_theme.ui);
        place(count, S(2) + tw.cx + S(6), cy, cw.cx + S(2), line);
        int ew = measureWindow(L"0", g_theme.ui).cx * 28 + S(8);
        int ex = rc.right - S(3) - ew;
        place(search, ex, (rc.bottom - eh) / 2, ew, eh);
        SIZE sw = measureWindow(L"Search:", g_theme.ui);
        place(searchLabel, ex - S(5) - sw.cx, cy, sw.cx + S(1), line);
    }

    void set(const std::string &t, const std::string &c) {
        std::wstring before = windowText(title) + L"|" + windowText(count);
        setText(title, t);
        setText(count, c);
        if (before != windowText(title) + L"|" + windowText(count)) layout();
    }
};

inline LRESULT CALLBACK paneProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (GetWindowLongPtrW(h, GWLP_USERDATA) == 0 || m == WM_NCCREATE) {
        LRESULT out = 0;
        if (m != WM_NCCREATE && handleControlMessage(h, m, w, l, &out, g_theme.p.BG)) return out;
        if (m == WM_ERASEBKGND) {
            RECT rc;
            GetClientRect(h, &rc);
            fill(reinterpret_cast<HDC>(w), rc, g_theme.p.BG);
            return 1;
        }
        if (m == WM_COMMAND || m == WM_NOTIFY) return SendMessageW(GetParent(h), m, w, l);
        if (m == WM_NCCREATE && reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams)
            return DetailsPane::proc(h, m, w, l);
        return DefWindowProcW(h, m, w, l);
    }
    return DetailsPane::proc(h, m, w, l);
}

}  // namespace md
