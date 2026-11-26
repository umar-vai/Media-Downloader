// Controls for the dialogs. The light theme uses the real Windows controls (like Tk's "vista"
// theme did); the dark theme draws the same controls with the dark palette (like Tk's "clam").
#pragma once

#include "common.h"

#pragma comment(lib, "msimg32.lib")

namespace md {

constexpr int PAD = 10;  // dialog margin (px at 96 dpi), as in native.py
constexpr int GAP = 6;   // space between controls

enum class Ink { Text, Muted, Faint, Danger, Success, Accent, Warning };

inline COLORREF inkColor(Ink k) {
    const Palette &p = g_theme.p;
    switch (k) {
    case Ink::Muted: return p.MUTED;
    case Ink::Faint: return p.FAINT;
    case Ink::Danger: return p.DANGER;
    case Ink::Success: return p.SUCCESS;
    case Ink::Accent: return p.ACCENT;
    case Ink::Warning: return p.WARNING;
    default: return p.TEXT;
    }
}

// Per-control look, read by the owner's WM_CTLCOLOR* / WM_DRAWITEM handlers.
struct ControlInfo {
    enum Kind { Label, Button, Check, Radio, Edit, Combo, Other } kind = Other;
    Ink ink = Ink::Text;
    bool bold = false;
    bool primary = false;   // default push button
    bool checked = false;   // owner-drawn check / radio state
    bool hot = false;
    int group = 0;          // radio group
    COLORREF background = CLR_INVALID;  // label on a different surface
    std::function<void()> onClick;
    std::function<void()> onChange;
};

inline std::unordered_map<HWND, ControlInfo> g_controls;
inline int g_nextId = 2000;

inline ControlInfo *info(HWND h) {
    auto it = g_controls.find(h);
    return it == g_controls.end() ? nullptr : &it->second;
}

inline HWND create(HWND parent, const wchar_t *cls, const std::wstring &textValue, DWORD style, DWORD ex = 0) {
    HWND h = CreateWindowExW(ex, cls, textValue.c_str(), WS_CHILD | WS_VISIBLE | style, 0, 0, 10, 10, parent,
                             reinterpret_cast<HMENU>(static_cast<INT_PTR>(g_nextId++)), g_inst, nullptr);
    SendMessageW(h, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.ui), FALSE);
    return h;
}

inline HWND makeLabel(HWND parent, const std::string &s = "", Ink ink = Ink::Text, bool bold = false, DWORD style = 0) {
    HWND h = create(parent, L"STATIC", W(s), SS_NOPREFIX | SS_LEFT | style);
    ControlInfo ci;
    ci.kind = ControlInfo::Label;
    ci.ink = ink;
    ci.bold = bold;
    g_controls[h] = ci;
    if (bold) SendMessageW(h, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.uiBold), FALSE);
    return h;
}

inline void setInk(HWND h, Ink ink) {
    if (auto *ci = info(h)) {
        if (ci->ink != ink) {
            ci->ink = ink;
            InvalidateRect(h, nullptr, TRUE);
        }
    }
}

// Push button: native in the light theme, flat dark button otherwise.
inline HWND makeButton(HWND parent, const std::string &s, std::function<void()> onClick, bool primary = false) {
    DWORD style = WS_TABSTOP | (g_theme.dark ? BS_OWNERDRAW : (primary ? BS_DEFPUSHBUTTON : BS_PUSHBUTTON));
    HWND h = create(parent, L"BUTTON", W(s), style);
    ControlInfo ci;
    ci.kind = ControlInfo::Button;
    ci.primary = primary;
    ci.onClick = std::move(onClick);
    g_controls[h] = ci;
    if (!g_theme.dark) themeControl(h);
    return h;
}

inline HWND makeCheck(HWND parent, const std::string &s, std::function<void()> onChange = {}) {
    DWORD style = WS_TABSTOP | (g_theme.dark ? BS_OWNERDRAW : BS_AUTOCHECKBOX);
    HWND h = create(parent, L"BUTTON", W(s), style);
    ControlInfo ci;
    ci.kind = ControlInfo::Check;
    ci.onChange = std::move(onChange);
    g_controls[h] = ci;
    return h;
}

inline HWND makeRadio(HWND parent, const std::string &s, int group, std::function<void()> onChange = {}) {
    DWORD style = WS_TABSTOP | (g_theme.dark ? BS_OWNERDRAW : BS_RADIOBUTTON);
    HWND h = create(parent, L"BUTTON", W(s), style);
    ControlInfo ci;
    ci.kind = ControlInfo::Radio;
    ci.group = group;
    ci.onChange = std::move(onChange);
    g_controls[h] = ci;
    return h;
}

inline bool isChecked(HWND h) {
    auto *ci = info(h);
    if (!ci) return false;
    if (g_theme.dark) return ci->checked;
    return SendMessageW(h, BM_GETCHECK, 0, 0) == BST_CHECKED;
}

inline void setChecked(HWND h, bool on) {
    auto *ci = info(h);
    if (!ci) return;
    if (ci->checked == on && (g_theme.dark || (SendMessageW(h, BM_GETCHECK, 0, 0) == BST_CHECKED) == on)) return;
    ci->checked = on;
    if (g_theme.dark) InvalidateRect(h, nullptr, FALSE);
    else SendMessageW(h, BM_SETCHECK, on ? BST_CHECKED : BST_UNCHECKED, 0);
}

// Selecting one radio clears the others of its group under the same parent.
inline void selectRadio(HWND h) {
    auto *ci = info(h);
    if (!ci) return;
    HWND parent = GetParent(h);
    for (auto &kv : g_controls)
        if (kv.second.kind == ControlInfo::Radio && kv.second.group == ci->group && GetParent(kv.first) == parent)
            setChecked(kv.first, kv.first == h);
}

// Edit boxes draw their own 1 px border in the palette colour.
inline LRESULT CALLBACK editSubclass(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_NCPAINT && g_theme.dark) {
        DefSubclassProc(h, m, w, l);
        HDC dc = GetWindowDC(h);
        RECT rc;
        GetWindowRect(h, &rc);
        OffsetRect(&rc, -rc.left, -rc.top);
        bool focus = GetFocus() == h;
        frame(dc, rc, focus ? g_theme.p.ACCENT : g_theme.p.BORDER);
        ReleaseDC(h, dc);
        return 0;
    }
    if ((m == WM_SETFOCUS || m == WM_KILLFOCUS) && g_theme.dark) {
        LRESULT r = DefSubclassProc(h, m, w, l);
        SetWindowPos(h, nullptr, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE);
        return r;
    }
    if (m == WM_KEYDOWN && w == 'A' && GetKeyState(VK_CONTROL) < 0) {
        SendMessageW(h, EM_SETSEL, 0, -1);
        return 0;
    }
    if (m == WM_CHAR && w == 1) return 0;  // Ctrl+A beep
    return DefSubclassProc(h, m, w, l);
}

inline HWND makeEdit(HWND parent, bool multiline = false, bool readOnly = false, std::function<void()> onChange = {}) {
    DWORD style = WS_TABSTOP | WS_BORDER | (multiline ? (ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_VSCROLL) : ES_AUTOHSCROLL);
    if (readOnly) style |= ES_READONLY;
    HWND h = create(parent, L"EDIT", L"", style, g_theme.dark ? 0 : WS_EX_CLIENTEDGE);
    if (!g_theme.dark) SetWindowLongPtrW(h, GWL_STYLE, GetWindowLongPtrW(h, GWL_STYLE) & ~WS_BORDER);
    SendMessageW(h, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(S(3), S(3)));
    SetWindowSubclass(h, editSubclass, 1, 0);
    ControlInfo ci;
    ci.kind = ControlInfo::Edit;
    ci.onChange = std::move(onChange);
    g_controls[h] = ci;
    themeControl(h);
    SetWindowPos(h, nullptr, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
    return h;
}

inline HWND makeCombo(HWND parent, const std::vector<std::string> &items, std::function<void()> onChange = {}, bool editable = false) {
    HWND h = create(parent, L"COMBOBOX", L"", (editable ? CBS_DROPDOWN : CBS_DROPDOWNLIST) | WS_TABSTOP | WS_VSCROLL | CBS_HASSTRINGS);
    for (const auto &item : items) SendMessageW(h, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(W(item).c_str()));
    SendMessageW(h, CB_SETMINVISIBLE, 12, 0);
    themeControl(h, L"DarkMode_CFD", L"Explorer");
    ControlInfo ci;
    ci.kind = ControlInfo::Combo;
    ci.onChange = std::move(onChange);
    g_controls[h] = ci;
    if (!items.empty()) SendMessageW(h, CB_SETCURSEL, 0, 0);
    return h;
}

inline void setComboItems(HWND h, const std::vector<std::string> &items) {
    SendMessageW(h, CB_RESETCONTENT, 0, 0);
    for (const auto &item : items) SendMessageW(h, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(W(item).c_str()));
}

inline std::string comboText(HWND h) {
    int i = static_cast<int>(SendMessageW(h, CB_GETCURSEL, 0, 0));
    if (i < 0) return getText(h);
    int n = static_cast<int>(SendMessageW(h, CB_GETLBTEXTLEN, i, 0));
    std::wstring w(static_cast<size_t>(n) + 1, L'\0');
    SendMessageW(h, CB_GETLBTEXT, i, reinterpret_cast<LPARAM>(&w[0]));
    w.resize(static_cast<size_t>(n));
    return U8(w);
}

inline void setComboText(HWND h, const std::string &s) {
    if (comboText(h) == s) return;
    int i = static_cast<int>(SendMessageW(h, CB_FINDSTRINGEXACT, static_cast<WPARAM>(-1), reinterpret_cast<LPARAM>(W(s).c_str())));
    if (i >= 0) SendMessageW(h, CB_SETCURSEL, i, 0);
    else if (GetWindowLongPtrW(h, GWL_STYLE) & CBS_DROPDOWN) SetWindowTextW(h, W(s).c_str());
}

// ------------------------------------------------------------------------------------- painting
inline void drawOwnerButton(const DRAWITEMSTRUCT *d) {
    const Palette &p = g_theme.p;
    ControlInfo *ci = info(d->hwndItem);
    if (!ci) return;
    HDC dc = d->hDC;
    RECT rc = d->rcItem;
    bool disabled = (d->itemState & ODS_DISABLED) != 0;
    bool pressed = (d->itemState & ODS_SELECTED) != 0;
    bool focus = (d->itemState & ODS_FOCUS) != 0;
    std::wstring label = windowText(d->hwndItem);
    HWND parent = GetParent(d->hwndItem);
    COLORREF parentBg = p.BG;
    if (auto *pi = info(parent)) if (pi->background != CLR_INVALID) parentBg = pi->background;
    if (ci->background != CLR_INVALID) parentBg = ci->background;

    if (ci->kind == ControlInfo::Check || ci->kind == ControlInfo::Radio) {
        fill(dc, rc, parentBg);
        int box = S(13);
        RECT b{rc.left + S(1), (rc.top + rc.bottom - box) / 2, rc.left + S(1) + box, (rc.top + rc.bottom - box) / 2 + box};
        COLORREF edge = ci->hot && !disabled ? p.ACCENT : p.BORDER;
        if (ci->kind == ControlInfo::Check) {
            fill(dc, b, p.SURFACE_2);
            frame(dc, b, edge);
            if (ci->checked) {
                HGDIOBJ old = SelectObject(dc, g_theme.pen(disabled ? p.FAINT : p.TEXT, std::max(2, S(2))));
                MoveToEx(dc, b.left + box / 5, b.top + box / 2, nullptr);
                LineTo(dc, b.left + box * 2 / 5, b.bottom - box / 4);
                LineTo(dc, b.right - box / 5, b.top + box / 4);
                SelectObject(dc, old);
            }
        } else {
            HGDIOBJ oldPen = SelectObject(dc, g_theme.pen(edge));
            HGDIOBJ oldBrush = SelectObject(dc, g_theme.brush(p.SURFACE_2));
            Ellipse(dc, b.left, b.top, b.right, b.bottom);
            if (ci->checked) {
                SelectObject(dc, g_theme.brush(disabled ? p.FAINT : p.TEXT));
                SelectObject(dc, g_theme.pen(disabled ? p.FAINT : p.TEXT));
                int inset = box / 4 + 1;
                Ellipse(dc, b.left + inset, b.top + inset, b.right - inset, b.bottom - inset);
            }
            SelectObject(dc, oldPen);
            SelectObject(dc, oldBrush);
        }
        RECT t{b.right + S(5), rc.top, rc.right, rc.bottom};
        text(dc, label, t, disabled ? p.FAINT : p.TEXT, g_theme.ui);
        if (focus && !(d->itemState & ODS_NOFOCUSRECT)) {
            SIZE sz = measure(dc, label, g_theme.ui);
            RECT f{t.left - S(1), (rc.top + rc.bottom - sz.cy) / 2 - 1, t.left + sz.cx + S(1), (rc.top + rc.bottom + sz.cy) / 2 + 1};
            SetTextColor(dc, p.MUTED);
            DrawFocusRect(dc, &f);
        }
        return;
    }
    // Coloured flat button (the recorders' Start / Pause / Stop buttons).
    if (ci->kind == ControlInfo::Button && ci->background != CLR_INVALID) {
        COLORREF base = ci->background;
        COLORREF bgc = disabled ? mix(base, p.BG, 0.6) : pressed ? mix(base, RGB(0, 0, 0), 0.2)
                     : ci->hot ? mix(base, RGB(255, 255, 255), 0.12) : base;
        fill(dc, rc, bgc);
        RECT t = rc;
        text(dc, label, t, disabled ? mix(RGB(255, 255, 255), p.BG, 0.5) : RGB(255, 255, 255), g_theme.uiBold,
             DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        return;
    }
    // Push button (Tk "clam" button in the dark palette).
    COLORREF bg = pressed ? p.BORDER : (ci->hot && !disabled ? p.BORDER : p.SURFACE_3);
    COLORREF edge = ci->primary || focus ? p.ACCENT : p.BORDER;
    fill(dc, rc, bg);
    frame(dc, rc, disabled ? p.BORDER_SOFT : edge);
    RECT t = rc;
    if (pressed) OffsetRect(&t, 0, 1);
    text(dc, label, t, disabled ? p.FAINT : p.TEXT, g_theme.ui, DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
}

// Hover tracking for owner-drawn buttons.
inline LRESULT CALLBACK hoverSubclass(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    switch (m) {
    case WM_MOUSEMOVE:
        if (auto *ci = info(h); ci && !ci->hot) {
            ci->hot = true;
            TRACKMOUSEEVENT t{sizeof t, TME_LEAVE, h, 0};
            TrackMouseEvent(&t);
            InvalidateRect(h, nullptr, FALSE);
        }
        break;
    case WM_MOUSELEAVE:
        if (auto *ci = info(h)) {
            ci->hot = false;
            InvalidateRect(h, nullptr, FALSE);
        }
        break;
    case WM_NCDESTROY:
        g_controls.erase(h);
        break;
    }
    return DefSubclassProc(h, m, w, l);
}

inline void trackHover(HWND h) { SetWindowSubclass(h, hoverSubclass, 2, 0); }

// The common WM_CTLCOLOR* / WM_DRAWITEM / WM_COMMAND handling every themed window shares.
// Returns true when the message was handled (result in *out).
inline bool handleControlMessage(HWND owner, UINT m, WPARAM w, LPARAM l, LRESULT *out, COLORREF surface) {
    const Palette &p = g_theme.p;
    switch (m) {
    case WM_CTLCOLORSTATIC: {
        HDC dc = reinterpret_cast<HDC>(w);
        HWND ctl = reinterpret_cast<HWND>(l);
        ControlInfo *ci = info(ctl);
        COLORREF bg = surface;
        if (ci && ci->background != CLR_INVALID) bg = ci->background;
        wchar_t cls[16]{};
        GetClassNameW(ctl, cls, 16);
        if (_wcsicmp(cls, L"Edit") == 0) {  // read-only edit
            bg = g_theme.dark ? p.SURFACE_2 : GetSysColor(COLOR_BTNFACE);
            SetTextColor(dc, p.TEXT);
            SetBkColor(dc, bg);
            *out = reinterpret_cast<LRESULT>(g_theme.dark ? g_theme.brush(bg) : GetSysColorBrush(COLOR_BTNFACE));
            return true;
        }
        SetTextColor(dc, ci ? inkColor(ci->ink) : p.TEXT);
        SetBkColor(dc, bg);
        SetBkMode(dc, TRANSPARENT);
        *out = reinterpret_cast<LRESULT>(g_theme.brush(bg));
        return true;
    }
    case WM_CTLCOLORBTN: {
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, p.TEXT);
        SetBkColor(dc, surface);
        *out = reinterpret_cast<LRESULT>(g_theme.brush(surface));
        return true;
    }
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORLISTBOX: {
        if (!g_theme.dark) return false;
        HDC dc = reinterpret_cast<HDC>(w);
        SetTextColor(dc, p.TEXT);
        SetBkColor(dc, p.SURFACE_2);
        *out = reinterpret_cast<LRESULT>(g_theme.brush(p.SURFACE_2));
        return true;
    }
    case WM_DRAWITEM: {
        auto *d = reinterpret_cast<DRAWITEMSTRUCT *>(l);
        if (d->CtlType == ODT_BUTTON && info(d->hwndItem)) {
            drawOwnerButton(d);
            *out = TRUE;
            return true;
        }
        return false;
    }
    case WM_COMMAND: {
        HWND ctl = reinterpret_cast<HWND>(l);
        ControlInfo *ci = ctl ? info(ctl) : nullptr;
        if (!ci) return false;
        int code = HIWORD(w);
        if (ci->kind == ControlInfo::Button && code == BN_CLICKED) {
            auto action = ci->onClick;  // the handler may destroy the control
            if (action) action();
        } else if (ci->kind == ControlInfo::Check && code == BN_CLICKED) {
            if (g_theme.dark) {
                ci->checked = !ci->checked;
                InvalidateRect(ctl, nullptr, FALSE);
            } else {
                ci->checked = SendMessageW(ctl, BM_GETCHECK, 0, 0) == BST_CHECKED;
            }
            auto action = ci->onChange;
            if (action) action();
        } else if (ci->kind == ControlInfo::Radio && code == BN_CLICKED) {
            selectRadio(ctl);
            auto action = ci->onChange;
            if (action) action();
        } else if (ci->kind == ControlInfo::Edit && code == EN_CHANGE) {
            auto action = ci->onChange;
            if (action) action();
        } else if (ci->kind == ControlInfo::Combo && (code == CBN_SELCHANGE || code == CBN_EDITCHANGE)) {
            auto action = ci->onChange;
            if (action) action();
        } else {
            return false;
        }
        *out = 0;
        return true;
    }
    }
    (void)owner;
    return false;
}

// Makes every owner-drawn button of a window hover-aware.
inline void trackAllHover(HWND parent) {
    EnumChildWindows(parent, [](HWND child, LPARAM) -> BOOL {
        if (auto *ci = info(child))
            if (ci->kind == ControlInfo::Button || ci->kind == ControlInfo::Check || ci->kind == ControlInfo::Radio) trackHover(child);
        return TRUE;
    }, 0);
}

// ------------------------------------------------------------------------------- progress bar
// A determinate / marquee progress bar drawn in the palette (Tk's MD.Horizontal.TProgressbar).
struct ProgressState {
    double value = 0;
    bool busy = false;
    int phase = 0;
    COLORREF bar = CLR_INVALID;
};
inline std::unordered_map<HWND, ProgressState> g_progress;

inline LRESULT CALLBACK progressProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        BufferedPaint bp(h);
        const Palette &p = g_theme.p;
        auto &st = g_progress[h];
        RECT rc = bp.rect();
        HWND parent = GetParent(h);
        COLORREF outer = p.BG;
        if (auto *pi = info(parent)) if (pi->background != CLR_INVALID) outer = pi->background;
        fill(bp.dc(), rc, outer);
        fill(bp.dc(), rc, g_theme.dark ? p.SURFACE_3 : hex(0xE6E6E6));
        frame(bp.dc(), rc, g_theme.dark ? p.BORDER : hex(0xBCBCBC));
        RECT in = rc;
        InflateRect(&in, -1, -1);
        COLORREF bar = st.bar != CLR_INVALID ? st.bar : (g_theme.dark ? p.SUCCESS : hex(0x06B025));
        if (st.busy) {
            int width = in.right - in.left, seg = std::max(S(40), width / 4);
            int x = in.left + (st.phase % (width + seg)) - seg;
            RECT r{std::max(static_cast<int>(in.left), x), in.top, std::min(static_cast<int>(in.right), x + seg), in.bottom};
            if (r.right > r.left) fill(bp.dc(), r, bar);
        } else if (st.value > 0) {
            RECT r = in;
            r.right = in.left + static_cast<LONG>((in.right - in.left) * std::min(1.0, st.value) + 0.5);
            fill(bp.dc(), r, bar);
        }
        return 0;
    }
    case WM_TIMER:
        g_progress[h].phase += S(6);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_ERASEBKGND: return 1;
    case WM_NCDESTROY: g_progress.erase(h); break;
    }
    return DefWindowProcW(h, m, w, l);
}

inline void registerProgressClass() {
    WNDCLASSEXW wc{sizeof wc};
    wc.lpfnWndProc = progressProc;
    wc.hInstance = g_inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MDProgress";
    RegisterClassExW(&wc);
}

inline HWND makeProgress(HWND parent) {
    HWND h = CreateWindowExW(0, L"MDProgress", L"", WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, parent, nullptr, g_inst, nullptr);
    g_progress[h] = ProgressState{};
    return h;
}

inline void setProgress(HWND h, double value, bool busy) {
    auto &st = g_progress[h];
    if (busy != st.busy) {
        st.busy = busy;
        if (busy) SetTimer(h, 1, 25, nullptr);
        else KillTimer(h, 1);
        InvalidateRect(h, nullptr, FALSE);
    }
    if (std::fabs(st.value - value) > 0.0005) {
        st.value = value;
        InvalidateRect(h, nullptr, FALSE);
    }
}

// ---------------------------------------------------------------------- dark list headers
// Column headers in the palette (Tk's MD.Treeview.Heading): HEADING background, MUTED text.
inline std::unordered_map<HWND, int> g_hotHeader;

inline LRESULT drawDarkHeader(HWND list, NMCUSTOMDRAW *d) {
    if (!g_theme.dark) return CDRF_DODEFAULT;
    const Palette &p = g_theme.p;
    HWND hdr = ListView_GetHeader(list);
    switch (d->dwDrawStage) {
    case CDDS_PREPAINT: {
        RECT rc;
        GetClientRect(hdr, &rc);
        fill(d->hdc, rc, p.HEADING);
        return CDRF_NOTIFYITEMDRAW | CDRF_NOTIFYPOSTPAINT;
    }
    case CDDS_ITEMPREPAINT: {
        int i = static_cast<int>(d->dwItemSpec);
        bool hot = (d->uItemState & CDIS_HOT) != 0;
        RECT rc = d->rc;
        fill(d->hdc, rc, hot ? p.SURFACE_3 : p.HEADING);
        vline(d->hdc, rc.right - 1, rc.top, rc.bottom, p.BORDER_SOFT);
        hline(d->hdc, rc.left, rc.right, rc.bottom - 1, p.BORDER_SOFT);
        wchar_t buf[128]{};
        HDITEMW item{};
        item.mask = HDI_TEXT | HDI_FORMAT;
        item.pszText = buf;
        item.cchTextMax = 128;
        Header_GetItem(hdr, i, &item);
        RECT tr = rc;
        tr.left += S(6);
        tr.right -= S(6);
        UINT align = (item.fmt & HDF_RIGHT) ? DT_RIGHT : DT_LEFT;
        text(d->hdc, buf, tr, hot ? p.TEXT : p.MUTED, g_theme.headingFont(),
             DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX | align);
        return CDRF_SKIPDEFAULT;
    }
    case CDDS_POSTPAINT: {
        RECT rc;
        GetClientRect(hdr, &rc);
        int n = Header_GetItemCount(hdr);
        RECT last{};
        if (n > 0) Header_GetItemRect(hdr, n - 1, &last);
        RECT rest{last.right, rc.top, rc.right, rc.bottom};
        if (rest.right > rest.left) {
            fill(d->hdc, rest, p.HEADING);
            hline(d->hdc, rest.left, rest.right, rc.bottom - 1, p.BORDER_SOFT);
        }
        return CDRF_DODEFAULT;
    }
    }
    return CDRF_DODEFAULT;
}

inline LRESULT CALLBACK darkHeaderSubclass(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_NOTIFY) {
        auto *n = reinterpret_cast<NMHDR *>(l);
        if (n->hwndFrom == ListView_GetHeader(h) && n->code == NM_CUSTOMDRAW)
            return drawDarkHeader(h, reinterpret_cast<NMCUSTOMDRAW *>(l));
    }
    return DefSubclassProc(h, m, w, l);
}

// --------------------------------------------------------------------------------- tool tips
inline HWND g_tooltip = nullptr;

inline void addTooltip(HWND tool, const std::wstring &tip, HWND owner = nullptr, const RECT *area = nullptr, UINT id = 0) {
    if (!g_tooltip) {
        g_tooltip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_NOPREFIX | TTS_ALWAYSTIP,
                                    CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, g_main, nullptr, g_inst, nullptr);
        SendMessageW(g_tooltip, TTM_SETMAXTIPWIDTH, 0, S(420));
        SendMessageW(g_tooltip, TTM_SETDELAYTIME, TTDT_INITIAL, 650);
        themeControl(g_tooltip, L"DarkMode_Explorer", L"Explorer");
    }
    TOOLINFOW ti{};
    ti.cbSize = sizeof ti;
    ti.hwnd = owner ? owner : GetParent(tool);
    ti.lpszText = const_cast<wchar_t *>(tip.c_str());
    if (area) {
        ti.uFlags = TTF_SUBCLASS;
        ti.uId = id;
        ti.rect = *area;
    } else {
        ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS;
        ti.uId = reinterpret_cast<UINT_PTR>(tool);
    }
    SendMessageW(g_tooltip, TTM_ADDTOOLW, 0, reinterpret_cast<LPARAM>(&ti));
}

}  // namespace md
