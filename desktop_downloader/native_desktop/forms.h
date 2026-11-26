// Generic native dialogs. The Python controller describes a dialog (a tree of boxes, grids,
// group boxes, tabs and controls, plus a row of push buttons) and this file builds it from real
// Windows controls, lays it out like the Tk dialogs did, and reports clicks and edits back.
//
// Spec (JSON):
//   {"id", "title", "width", "minHeight", "resizable", "modal", "topmost", "owner": "main"|"none",
//    "body": node, "buttons": [{"id","text","width","default","enabled"}], "status", "statusKind",
//    "focus": widget id, "rev": number}
// Nodes: {"type": "vbox"|"hbox"|"grid"|"group"|"tabs"|"label"|"entry"|"text"|"check"|"radio"|"combo"|
//         "button"|"progress"|"image"|"list"|"spin"|"separator"|"space"|"drop", ...}
#pragma once

#include "widgets.h"

namespace md {

// ------------------------------------------------------------------------- pictures (PNG)
inline std::string base64Decode(const std::string &in) {
    static int table[256];
    static bool ready = false;
    if (!ready) {
        for (int &t : table) t = -1;
        const char *a = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for (int i = 0; i < 64; ++i) table[static_cast<unsigned char>(a[i])] = i;
        ready = true;
    }
    std::string out;
    out.reserve(in.size() * 3 / 4);
    int acc = 0, bits = 0;
    for (unsigned char c : in) {
        if (table[c] < 0) continue;
        acc = (acc << 6) | table[c];
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out += static_cast<char>((acc >> bits) & 0xFF);
        }
    }
    return out;
}

template <class T> void releaseCom(T *&p) {
    if (p) p->Release();
    p = nullptr;
}

// Decodes PNG/JPEG bytes into a 32-bit bitmap with Windows' own imaging component.
inline HBITMAP decodeImage(const std::string &bytes, int *outW = nullptr, int *outH = nullptr) {
    if (bytes.empty()) return nullptr;
    IWICImagingFactory *factory = nullptr;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)))) return nullptr;
    IWICStream *stream = nullptr;
    IWICBitmapDecoder *decoder = nullptr;
    IWICBitmapFrameDecode *frameDecode = nullptr;
    IWICFormatConverter *converter = nullptr;
    HBITMAP bitmap = nullptr;
    do {
        if (FAILED(factory->CreateStream(&stream))) break;
        if (FAILED(stream->InitializeFromMemory(reinterpret_cast<BYTE *>(const_cast<char *>(bytes.data())), static_cast<DWORD>(bytes.size())))) break;
        if (FAILED(factory->CreateDecoderFromStream(stream, nullptr, WICDecodeMetadataCacheOnLoad, &decoder))) break;
        if (FAILED(decoder->GetFrame(0, &frameDecode))) break;
        if (FAILED(factory->CreateFormatConverter(&converter))) break;
        if (FAILED(converter->Initialize(frameDecode, GUID_WICPixelFormat32bppPBGRA, WICBitmapDitherTypeNone, nullptr, 0, WICBitmapPaletteTypeCustom))) break;
        UINT w = 0, h = 0;
        converter->GetSize(&w, &h);
        if (w == 0 || h == 0 || w > 8192 || h > 8192) break;
        BITMAPINFO bi{};
        bi.bmiHeader.biSize = sizeof bi.bmiHeader;
        bi.bmiHeader.biWidth = static_cast<LONG>(w);
        bi.bmiHeader.biHeight = -static_cast<LONG>(h);
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        void *pixels = nullptr;
        bitmap = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &pixels, nullptr, 0);
        if (!bitmap) break;
        if (FAILED(converter->CopyPixels(nullptr, w * 4, w * h * 4, static_cast<BYTE *>(pixels)))) {
            DeleteObject(bitmap);
            bitmap = nullptr;
            break;
        }
        if (outW) *outW = static_cast<int>(w);
        if (outH) *outH = static_cast<int>(h);
    } while (false);
    releaseCom(converter);
    releaseCom(frameDecode);
    releaseCom(decoder);
    releaseCom(stream);
    releaseCom(factory);
    return bitmap;
}

struct PictureState {
    HBITMAP bitmap = nullptr;
    int w = 0, h = 0;
    std::string source;
};
inline std::unordered_map<HWND, PictureState> g_pictures;

inline LRESULT CALLBACK pictureProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        BufferedPaint bp(h);
        const Palette &p = g_theme.p;
        RECT rc = bp.rect();
        fill(bp.dc(), rc, g_theme.dark ? p.SURFACE_3 : hex(0xE5E5E5));
        auto &st = g_pictures[h];
        if (st.bitmap) {
            double scale = std::min((rc.right - 2.0) / st.w, (rc.bottom - 2.0) / st.h);
            int dw = std::max(1, static_cast<int>(st.w * scale)), dh = std::max(1, static_cast<int>(st.h * scale));
            HDC mem = CreateCompatibleDC(bp.dc());
            HGDIOBJ old = SelectObject(mem, st.bitmap);
            SetStretchBltMode(bp.dc(), HALFTONE);
            BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
            AlphaBlend(bp.dc(), (rc.right - dw) / 2, (rc.bottom - dh) / 2, dw, dh, mem, 0, 0, st.w, st.h, bf);
            SelectObject(mem, old);
            DeleteDC(mem);
        } else {
            text(bp.dc(), windowText(h), rc, p.FAINT, g_theme.ui, DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_NOPREFIX);
        }
        frame(bp.dc(), rc, g_theme.dark ? p.BORDER : hex(0xABADB3));
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_NCDESTROY: {
        auto it = g_pictures.find(h);
        if (it != g_pictures.end()) {
            if (it->second.bitmap) DeleteObject(it->second.bitmap);
            g_pictures.erase(it);
        }
        break;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

inline void setPicture(HWND h, const std::string &b64) {
    auto &st = g_pictures[h];
    if (st.source == b64) return;
    st.source = b64;
    if (st.bitmap) DeleteObject(st.bitmap);
    st.bitmap = b64.empty() ? nullptr : decodeImage(base64Decode(b64), &st.w, &st.h);
    InvalidateRect(h, nullptr, FALSE);
}

struct FormNode {
    std::string type, id;
    Value spec;
    std::vector<FormNode> children;
    HWND hwnd = nullptr, extra = nullptr;  // extra: spin buddy / list header
    RECT bounds{};
    int selectedTab = 0;
    std::vector<RECT> tabRects;
    Value lastValue;                        // last value the controller sent (do not clobber edits)
    bool visible = true;
};

class Form;
inline std::map<std::string, std::unique_ptr<Form>> g_formsById;
inline std::map<HWND, Form *> g_formsByHwnd;

inline std::wstring wtext(const Value &v, const char *key) { return W(v[key].asString()); }

inline COLORREF parseColor(const std::string &s, COLORREF fallback = CLR_INVALID) {
    if (s.size() != 7 || s[0] != '#') return fallback;
    return hex(static_cast<unsigned>(std::strtoul(s.c_str() + 1, nullptr, 16)));
}

// Row colours of form lists ("error" rows in red, like Tk's "fail" tag).
inline std::unordered_map<HWND, std::vector<std::string>> g_listKinds;

class Form {
public:
    std::string id;
    HWND hwnd = nullptr, status = nullptr;
    Value spec;
    FormNode root;
    std::vector<std::pair<Value, HWND>> buttons;
    std::string structure;   // signature of the node tree; a change rebuilds the window
    bool modal = false, closing = false, placed = false;
    int width = 0;
    struct GroupFrame {
        RECT rc;
        std::wstring title;
        FormNode *node;
    };
    std::vector<GroupFrame> groupFrames;

    // ------------------------------------------------------------------------ structure
    static std::string signature(const Value &node) {
        std::string s = node["type"].asString() + ":" + node["id"].asString() + "[";
        if (node["type"].asString() == "tabs") {
            for (const auto &page : node["pages"].items()) s += "<" + page["title"].asString() + ">" + signature(page["body"]);
        } else if (node["type"].asString() == "group") {
            s += signature(node["body"]);
        } else if (node["type"].asString() == "list") {
            for (const auto &c : node["columns"].items()) s += c["title"].asString() + ",";
        } else if (node["type"].asString() == "combo") {
            for (const auto &c : node["items"].items()) s += c.asString() + ",";
        } else {
            for (const auto &c : node["children"].items()) s += signature(c) + ";";
        }
        return s + "]";
    }

    static std::string fullSignature(const Value &spec) {
        std::string s = signature(spec["body"]) + "|";
        for (const auto &b : spec["buttons"].items()) s += b["id"].asString() + "," + b["text"].asString() + ";";
        return s;
    }

    // --------------------------------------------------------------------------- create
    void build(const Value &s) {
        spec = s;
        id = s["id"].asString();
        modal = s["modal"].asBool();
        structure = fullSignature(s);
        bool resizable = s["resizable"].asBool();
        DWORD style = WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_CLIPCHILDREN | (resizable ? (WS_THICKFRAME | WS_MAXIMIZEBOX) : 0);
        if (s["minimize"].asBool()) style |= WS_MINIMIZEBOX;
        HWND owner = s["owner"].asString() == "none" ? nullptr : g_main;
        DWORD ex = WS_EX_CONTROLPARENT | WS_EX_DLGMODALFRAME | (owner ? 0 : WS_EX_APPWINDOW);
        hwnd = CreateWindowExW(ex, L"MDForm", W(s["title"].asString()).c_str(), style, CW_USEDEFAULT, CW_USEDEFAULT, 400, 300,
                               owner, nullptr, g_inst, nullptr);
        g_formsByHwnd[hwnd] = this;
        if (g_icon) {
            SendMessageW(hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(g_icon));
            SendMessageW(hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(g_icon));
        }
        setDarkTitleBar(hwnd);
        allowDarkWindow(hwnd, g_theme.dark);
        root = makeNode(s["body"]);
        status = makeLabel(hwnd, s["status"].asString(), Ink::Muted, false, SS_ENDELLIPSIS);
        for (const auto &b : s["buttons"].items()) {
            std::string bid = b["id"].asString();
            HWND h = makeButton(hwnd, b["text"].asString(), [this, bid] { event("click", bid); }, b["default"].asBool());
            enable(h, b["enabled"].asBool(true));
            buttons.emplace_back(b, h);
        }
        trackAllHover(hwnd);
        applyValues(root, s["body"], true);
        sizeToContent();
    }

    FormNode makeNode(const Value &v) {
        FormNode n;
        n.spec = v;
        n.type = v["type"].asString();
        n.id = v["id"].asString();
        const std::string &t = n.type;
        const std::string wid = n.id;
        if (t == "vbox" || t == "hbox" || t == "grid") {
            for (const auto &c : v["children"].items()) n.children.push_back(makeNode(c));
        } else if (t == "group") {
            n.children.push_back(makeNode(v["body"]));
        } else if (t == "tabs") {
            for (const auto &page : v["pages"].items()) n.children.push_back(makeNode(page["body"]));
            n.selectedTab = v["selected"].asInt(0);
        } else if (t == "label") {
            std::string style = v["style"].asString();
            Ink ink = style == "hint" ? Ink::Muted : style == "error" ? Ink::Danger : style == "ok" ? Ink::Success
                    : style == "link" ? Ink::Accent : style == "warning" ? Ink::Warning : style == "faint" ? Ink::Faint : Ink::Text;
            bool wrap = v["wrap"].asBool();
            n.hwnd = makeLabel(hwnd, v["text"].asString(), ink, style == "title", wrap ? SS_EDITCONTROL : SS_ENDELLIPSIS);
            if (style == "big") SendMessageW(n.hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.big), FALSE);
            if (style == "mono") SendMessageW(n.hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.mono), FALSE);
            if (style == "clock") {
                static HFONT clock = Theme::makeFont(MulDiv(22, g_theme.dpi, 72), FW_BOLD, L"Segoe UI");
                SendMessageW(n.hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(clock), FALSE);
                SetWindowLongPtrW(n.hwnd, GWL_STYLE, (GetWindowLongPtrW(n.hwnd, GWL_STYLE) & ~SS_LEFT) | SS_CENTER);
            }
            if (v["click"].asBool()) {
                SetWindowLongPtrW(n.hwnd, GWL_STYLE, GetWindowLongPtrW(n.hwnd, GWL_STYLE) | SS_NOTIFY);
                info(n.hwnd)->onClick = [this, wid] { event("click", wid); };
            }
        } else if (t == "entry") {
            n.hwnd = makeEdit(hwnd, false, v["readonly"].asBool(), [this, wid] { if (!applyingValues) event("change", wid, false); });
            if (v["password"].asBool()) SendMessageW(n.hwnd, EM_SETPASSWORDCHAR, L'\x2022', 0);
            if (v["enter"].asBool()) SetWindowSubclass(n.hwnd, enterSubclass, 7, reinterpret_cast<DWORD_PTR>(this));
            if (v["cue"].isString()) SendMessageW(n.hwnd, EM_SETCUEBANNER, TRUE, reinterpret_cast<LPARAM>(W(v["cue"].asString()).c_str()));
        } else if (t == "text") {
            n.hwnd = makeEdit(hwnd, true, v["readonly"].asBool(), [this, wid] { if (!applyingValues) event("change", wid, false); });
            if (!v["wrap"].asBool(true)) SetWindowLongPtrW(n.hwnd, GWL_STYLE, GetWindowLongPtrW(n.hwnd, GWL_STYLE) | WS_HSCROLL | ES_AUTOHSCROLL);
            if (v["mono"].asBool()) SendMessageW(n.hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.mono), FALSE);
            SendMessageW(n.hwnd, EM_SETLIMITTEXT, 4 * 1024 * 1024, 0);
            if (v["kind"].asString() == "error") info(n.hwnd)->ink = Ink::Danger;
        } else if (t == "check") {
            n.hwnd = makeCheck(hwnd, v["text"].asString(), [this, wid] { event("change", wid); });
        } else if (t == "radio") {
            int group = static_cast<int>(std::hash<std::string>{}(v["group"].asString()) & 0x7fffffff);
            n.hwnd = makeRadio(hwnd, v["text"].asString(), group, [this, wid] { event("change", wid); });
        } else if (t == "combo") {
            std::vector<std::string> items;
            for (const auto &i : v["items"].items()) items.push_back(i.asString());
            n.hwnd = makeCombo(hwnd, items, [this, wid] { if (!applyingValues) event("change", wid); }, v["editable"].asBool());
        } else if (t == "button") {
            n.hwnd = makeButton(hwnd, v["text"].asString(), [this, wid] { event("click", wid); }, v["default"].asBool());
            if (v["color"].isString()) {
                info(n.hwnd)->background = parseColor(v["color"].asString());
                if (!g_theme.dark) SetWindowLongPtrW(n.hwnd, GWL_STYLE, (GetWindowLongPtrW(n.hwnd, GWL_STYLE) & ~0xF) | BS_OWNERDRAW);
            }
        } else if (t == "slider") {
            n.hwnd = CreateWindowExW(0, TRACKBAR_CLASSW, L"", WS_CHILD | WS_VISIBLE | WS_TABSTOP | TBS_HORZ | TBS_NOTICKS | TBS_BOTH,
                                     0, 0, 10, 10, hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(g_nextId++)), g_inst, nullptr);
            SendMessageW(n.hwnd, TBM_SETRANGEMIN, FALSE, v["min"].asInt(0));
            SendMessageW(n.hwnd, TBM_SETRANGEMAX, TRUE, v["max"].asInt(100));
            themeControl(n.hwnd);
            sliderIds[n.hwnd] = wid;
        } else if (t == "progress") {
            n.hwnd = makeProgress(hwnd);
        } else if (t == "image") {
            n.hwnd = CreateWindowExW(0, L"STATIC", L"", WS_CHILD | WS_VISIBLE | SS_ICON | SS_REALSIZEIMAGE, 0, 0, 10, 10, hwnd, nullptr, g_inst, nullptr);
            std::string name = v["name"].asString();
            HICON icon = nullptr;
            int size = S(v["size"].asInt(32));
            if (name == "app" && g_icon) icon = static_cast<HICON>(CopyImage(g_icon, IMAGE_ICON, size, size, 0));
            else {
                SHSTOCKICONID sid = name == "error" ? SIID_ERROR : name == "info" ? SIID_INFO : name == "question" ? SIID_HELP : SIID_WARNING;
                SHSTOCKICONINFO sii{sizeof sii};
                if (SUCCEEDED(SHGetStockIconInfo(sid, SHGSI_ICON | (size > S(24) ? SHGSI_LARGEICON : SHGSI_SMALLICON), &sii))) icon = sii.hIcon;
            }
            if (icon) SendMessageW(n.hwnd, STM_SETICON, reinterpret_cast<WPARAM>(icon), 0);
        } else if (t == "list") {
            n.hwnd = CreateWindowExW(g_theme.dark ? 0 : WS_EX_CLIENTEDGE, WC_LISTVIEWW, L"",
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | LVS_REPORT | LVS_SHOWSELALWAYS | (v["multi"].asBool() ? 0 : LVS_SINGLESEL) |
                                         (v["headers"].asBool(true) ? 0 : LVS_NOCOLUMNHEADER) | (g_theme.dark ? WS_BORDER : 0),
                                     0, 0, 10, 10, hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(g_nextId++)), g_inst, nullptr);
            SendMessageW(n.hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.ui), FALSE);
            ListView_SetExtendedListViewStyle(n.hwnd, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_LABELTIP);
            themeControl(n.hwnd);
            themeControl(ListView_GetHeader(n.hwnd), L"DarkMode_ItemsView", L"Explorer");
            SetWindowSubclass(n.hwnd, darkHeaderSubclass, 3, 0);
            ListView_SetBkColor(n.hwnd, g_theme.p.SURFACE);
            ListView_SetTextBkColor(n.hwnd, g_theme.p.SURFACE);
            ListView_SetTextColor(n.hwnd, g_theme.p.TEXT);
            int index = 0;
            for (const auto &c : v["columns"].items()) {
                std::wstring title = W(c["title"].asString());
                LVCOLUMNW col{};
                col.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_FMT;
                col.fmt = c["right"].asBool() ? LVCFMT_RIGHT : LVCFMT_LEFT;
                col.pszText = &title[0];
                col.cx = S(c["width"].asInt(120));
                ListView_InsertColumn(n.hwnd, index++, &col);
            }
            ControlInfo ci;
            ci.kind = ControlInfo::Other;
            g_controls[n.hwnd] = ci;
            listIds[n.hwnd] = wid;
        } else if (t == "spin") {
            n.hwnd = makeEdit(hwnd, false, false, [this, wid] { if (!applyingValues) event("change", wid, false); });
            SetWindowLongPtrW(n.hwnd, GWL_STYLE, GetWindowLongPtrW(n.hwnd, GWL_STYLE) | ES_NUMBER);
            n.extra = CreateWindowExW(0, UPDOWN_CLASSW, L"", WS_CHILD | WS_VISIBLE | UDS_ALIGNRIGHT | UDS_SETBUDDYINT | UDS_ARROWKEYS | UDS_NOTHOUSANDS,
                                      0, 0, 0, 0, hwnd, nullptr, g_inst, nullptr);
            SendMessageW(n.extra, UDM_SETBUDDY, reinterpret_cast<WPARAM>(n.hwnd), 0);
            SendMessageW(n.extra, UDM_SETRANGE32, v["min"].asInt(0), v["max"].asInt(100));
            themeControl(n.extra);
        } else if (t == "picture") {
            n.hwnd = CreateWindowExW(0, L"MDPicture", W(v["text"].asString()).c_str(), WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, hwnd, nullptr, g_inst, nullptr);
        } else if (t == "drop") {
            n.hwnd = CreateWindowExW(0, L"MDDrop", W(v["text"].asString()).c_str(), WS_CHILD | WS_VISIBLE, 0, 0, 10, 10, hwnd, nullptr, g_inst, nullptr);
            dropPaths[n.hwnd] = W(v["path"].asString());
        }
        if (n.hwnd && v["tip"].isString()) addTooltip(n.hwnd, W(v["tip"].asString()), hwnd);
        return n;
    }

    // ------------------------------------------------------------------------- values
    bool applyingValues = false;
    std::map<HWND, std::string> listIds, sliderIds;
    std::map<HWND, std::wstring> dropPaths;

    void applyValues(FormNode &n, const Value &v, bool first) {
        applyingValues = true;
        n.spec = v;
        const std::string &t = n.type;
        if (n.hwnd) {
            bool en = v["enabled"].asBool(true);
            enable(n.hwnd, en);
            if (n.extra) enable(n.extra, en);
        }
        if (t == "label") {
            setText(n.hwnd, v["text"].asString());
            std::string style = v["style"].asString();
            Ink ink = style == "hint" ? Ink::Muted : style == "error" ? Ink::Danger : style == "ok" ? Ink::Success
                    : style == "link" ? Ink::Accent : style == "warning" ? Ink::Warning : style == "faint" ? Ink::Faint : Ink::Text;
            setInk(n.hwnd, ink);
        } else if (t == "entry" || t == "text" || t == "spin") {
            const Value &value = v["value"];
            if (first || value != n.lastValue || v["force"].asBool()) {
                std::string s = value.isString() ? value.asString() : std::to_string(value.asInt());
                if (t == "text") s = toWindowsNewlines(s);
                if (getText(n.hwnd) != s) {
                    SetWindowTextW(n.hwnd, W(s).c_str());
                    if (t == "text" && v["scrollEnd"].asBool()) SendMessageW(n.hwnd, EM_LINESCROLL, 0, 0x7fffff);
                }
            }
            n.lastValue = value;
            if (t == "text" && v["kind"].isString()) setInk(n.hwnd, v["kind"].asString() == "error" ? Ink::Danger : Ink::Text);
        } else if (t == "check" || t == "radio") {
            setText(n.hwnd, v["text"].asString());
            if (first || v["value"] != n.lastValue) setChecked(n.hwnd, v["value"].asBool());
            n.lastValue = v["value"];
        } else if (t == "combo") {
            if (first || v["value"] != n.lastValue) setComboText(n.hwnd, v["value"].asString());
            n.lastValue = v["value"];
        } else if (t == "button") {
            setText(n.hwnd, v["text"].asString());
        } else if (t == "progress") {
            COLORREF bar = parseColor(v["color"].asString());
            if (g_progress[n.hwnd].bar != bar) {
                g_progress[n.hwnd].bar = bar;
                InvalidateRect(n.hwnd, nullptr, FALSE);
            }
            setProgress(n.hwnd, v["value"].asNumber(), v["busy"].asBool());
        } else if (t == "slider") {
            if (first || (v["value"] != n.lastValue && GetCapture() != n.hwnd))
                SendMessageW(n.hwnd, TBM_SETPOS, TRUE, v["value"].asInt(0));
            n.lastValue = v["value"];
        } else if (t == "list") {
            applyList(n, v);
        } else if (t == "picture") {
            setPicture(n.hwnd, v["png"].asString());
        } else if (t == "drop") {
            dropPaths[n.hwnd] = W(v["path"].asString());
            setText(n.hwnd, v["text"].asString());
        } else if (t == "group") {
            if (!n.children.empty()) applyValues(n.children[0], v["body"], first);
        } else if (t == "tabs") {
            const auto &pages = v["pages"].items();
            for (size_t i = 0; i < n.children.size() && i < pages.size(); ++i) applyValues(n.children[i], pages[i]["body"], first);
            if (first || v["selected"] != n.lastValue) {
                n.selectedTab = std::max(0, std::min(v["selected"].asInt(0), static_cast<int>(n.children.size()) - 1));
                n.lastValue = v["selected"];
            }
        } else {
            const auto &kids = v["children"].items();
            for (size_t i = 0; i < n.children.size() && i < kids.size(); ++i) applyValues(n.children[i], kids[i], first);
        }
        applyingValues = false;
    }

    void applyList(FormNode &n, const Value &v) {
        const Value &rows = v["rows"];
        {
            std::vector<std::string> kinds;
            for (const auto &k : v["rowKinds"].items()) kinds.push_back(k.asString());
            g_listKinds[n.hwnd] = kinds;
        }
        if (rows != n.lastValue) {
            n.lastValue = rows;
            SendMessageW(n.hwnd, WM_SETREDRAW, FALSE, 0);
            int count = static_cast<int>(rows.items().size());
            int have = ListView_GetItemCount(n.hwnd);
            int columns = Header_GetItemCount(ListView_GetHeader(n.hwnd));
            for (int i = 0; i < count; ++i) {
                const auto &cells = rows.items()[static_cast<size_t>(i)].items();
                for (int c = 0; c < columns; ++c) {
                    std::wstring cell = c < static_cast<int>(cells.size()) ? W(cells[static_cast<size_t>(c)].asString()) : L"";
                    if (c == 0 && i >= have) {
                        LVITEMW it{};
                        it.mask = LVIF_TEXT;
                        it.iItem = i;
                        it.pszText = &cell[0];
                        ListView_InsertItem(n.hwnd, &it);
                    } else {
                        wchar_t buf[1024]{};
                        ListView_GetItemText(n.hwnd, i, c, buf, 1024);
                        if (cell != buf) ListView_SetItemText(n.hwnd, i, c, &cell[0]);
                    }
                }
            }
            for (int i = have - 1; i >= count; --i) ListView_DeleteItem(n.hwnd, i);
            SendMessageW(n.hwnd, WM_SETREDRAW, TRUE, 0);
            InvalidateRect(n.hwnd, nullptr, TRUE);
        }
        if (v.has("selected")) {
            int want = v["selected"].asInt(-1);
            int now = ListView_GetNextItem(n.hwnd, -1, LVNI_SELECTED);
            if (want != now && v["selected"] != n.spec["_lastSelected"]) {
                applyingValues = true;
                ListView_SetItemState(n.hwnd, -1, 0, LVIS_SELECTED | LVIS_FOCUSED);
                if (want >= 0) {
                    ListView_SetItemState(n.hwnd, want, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
                    ListView_EnsureVisible(n.hwnd, want, FALSE);
                }
            }
        }
    }

    void collect(const FormNode &n, Value &out) const {
        const std::string &t = n.type;
        if (!n.id.empty()) {
            if (t == "entry" || t == "spin") out.set(n.id, Value(getText(n.hwnd)));
            else if (t == "text") out.set(n.id, Value(fromWindowsNewlines(getText(n.hwnd))));
            else if (t == "check" || t == "radio") out.set(n.id, Value(isChecked(n.hwnd)));
            else if (t == "combo") out.set(n.id, Value(comboText(n.hwnd)));
            else if (t == "list") out.set(n.id, Value(static_cast<int>(ListView_GetNextItem(n.hwnd, -1, LVNI_SELECTED))));
            else if (t == "tabs") out.set(n.id, Value(n.selectedTab));
            else if (t == "slider") out.set(n.id, Value(static_cast<int>(SendMessageW(n.hwnd, TBM_GETPOS, 0, 0))));
        }
        for (const auto &c : n.children) collect(c, out);
    }

    void event(const std::string &kind, const std::string &widget, bool immediate = true) {
        if (closing && kind != "close") return;
        Value v = Value::object();
        v.set("form", id);
        v.set("event", kind);
        v.set("widget", widget);
        Value values = Value::object();
        collect(root, values);
        v.set("values", values);
        (void)immediate;
        if (kind == "click" || kind == "enter") AllowSetForegroundWindow(ASFW_ANY);
        send("form", v);
    }

    // ------------------------------------------------------------------------- layout
    int lineHeight() const { return measureWindow(L"Ag", g_theme.ui).cy; }
    int avgChar() const { return measureWindow(L"0", g_theme.ui).cx; }
    int controlHeight() const { return lineHeight() + S(8); }
    int buttonHeight() const { return lineHeight() + S(10); }

    int buttonWidth(const Value &v, HWND h) const {
        int chars = v["width"].asInt(11);
        int byText = measureWindow(windowText(h), g_theme.ui).cx + S(20);
        return std::max(avgChar() * chars + S(10), byText);
    }

    HFONT fontOf(HWND h) const {
        HFONT f = reinterpret_cast<HFONT>(SendMessageW(h, WM_GETFONT, 0, 0));
        return f ? f : g_theme.ui;
    }

    int prefWidth(const FormNode &n) const {
        const Value &v = n.spec;
        const std::string &t = n.type;
        int fixed = v["minWidth"].asInt(0) ? S(v["minWidth"].asInt(0)) : 0;
        int w = 0;
        if (t == "vbox") {
            for (const auto &c : n.children) w = std::max(w, prefWidth(c) + S(c.spec["padx"].asInt(0)) * 2);
        } else if (t == "hbox") {
            int gap = S(v["gap"].asInt(GAP));
            for (size_t i = 0; i < n.children.size(); ++i) w += prefWidth(n.children[i]) + (i ? gap : 0);
        } else if (t == "grid") {
            auto widths = gridColumnPrefs(n);
            int gap = S(v["colgap"].asInt(PAD));
            for (size_t i = 0; i < widths.size(); ++i) w += widths[i] + (i ? gap : 0);
        } else if (t == "group") {
            w = (n.children.empty() ? 0 : prefWidth(n.children[0])) + S(PAD) * 2 + 2;
            w = std::max(w, static_cast<int>(measureWindow(W(v["title"].asString()), g_theme.ui).cx) + S(24));
        } else if (t == "tabs") {
            for (const auto &c : n.children) w = std::max(w, prefWidth(c) + S(PAD) * 2 + 2);
        } else if (t == "label") {
            if (v["wrap"].asBool()) w = S(v["wrapWidth"].asInt(360));
            else w = measureWindow(windowText(n.hwnd), fontOf(n.hwnd)).cx + S(2);
        } else if (t == "entry" || t == "combo" || t == "spin") {
            w = avgChar() * v["width"].asInt(t == "spin" ? 5 : 20) + S(t == "combo" ? 26 : t == "spin" ? 30 : 10);
        } else if (t == "text") {
            w = avgChar() * v["width"].asInt(60) + S(24);
        } else if (t == "check" || t == "radio") {
            w = measureWindow(windowText(n.hwnd), g_theme.ui).cx + S(22);
        } else if (t == "button") {
            w = buttonWidth(v, n.hwnd);
        } else if (t == "progress") {
            w = S(v["length"].asInt(200));
        } else if (t == "image") {
            w = S(v["size"].asInt(32));
        } else if (t == "list") {
            int cols = 0;
            for (const auto &c : v["columns"].items()) cols += S(c["width"].asInt(120));
            w = cols + S(24);
        } else if (t == "picture") {
            w = S(v["w"].asInt(184));
        } else if (t == "slider") {
            w = S(v["length"].asInt(160));
        } else if (t == "drop") {
            w = measureWindow(W(v["text"].asString()), g_theme.ui).cx + S(PAD) * 4;
        } else if (t == "space") {
            w = S(v["width"].asInt(0));
        }
        return std::max(w, fixed);
    }

    std::vector<int> gridColumnPrefs(const FormNode &n) const {
        int columns = std::max(1, n.spec["columns"].asInt(2));
        std::vector<int> widths(static_cast<size_t>(columns), 0);
        int col = 0;
        for (const auto &c : n.children) {
            int span = std::max(1, c.spec["colspan"].asInt(1));
            if (col + span > columns) col = 0;
            if (span == 1 && c.type != "skip") widths[static_cast<size_t>(col)] = std::max(widths[static_cast<size_t>(col)], prefWidth(c));
            col += span;
            if (col >= columns) col = 0;
        }
        return widths;
    }

    int nodeHeight(const FormNode &n, int width) const {
        const Value &v = n.spec;
        const std::string &t = n.type;
        if (t == "vbox") {
            int h = 0;
            for (size_t i = 0; i < n.children.size(); ++i) {
                const auto &c = n.children[i];
                if (c.spec["hidden"].asBool()) continue;
                h += S(c.spec["padTop"].asInt(i ? v["gap"].asInt(0) : 0)) + nodeHeight(c, width - S(c.spec["padx"].asInt(0)) * 2) + S(c.spec["padBottom"].asInt(0));
            }
            return h;
        }
        if (t == "hbox") {
            auto widths = hboxWidths(n, width);
            int h = 0;
            for (size_t i = 0; i < n.children.size(); ++i) h = std::max(h, nodeHeight(n.children[i], widths[i]));
            return h;
        }
        if (t == "grid") {
            auto cols = gridWidths(n, width);
            int h = 0;
            forEachGridRow(n, cols, [&](const std::vector<std::pair<const FormNode *, int>> &cells, int) {
                int rh = 0;
                for (auto &cell : cells) rh = std::max(rh, nodeHeight(*cell.first, cell.second));
                h += rh + S(n.spec["rowgap"].asInt(4));
            });
            return std::max(0, h - S(n.spec["rowgap"].asInt(4)));
        }
        if (t == "group") {
            int top = lineHeight() / 2 + lineHeight() / 2 + S(GAP);
            return top + (n.children.empty() ? 0 : nodeHeight(n.children[0], width - S(PAD) * 2 - 2)) + S(PAD) + 1;
        }
        if (t == "tabs") {
            int h = 0;
            for (const auto &c : n.children) h = std::max(h, nodeHeight(c, width - S(PAD) * 2 - 2));
            return tabHeight() + S(PAD) * 2 + h + 1;
        }
        if (t == "label") {
            if (!v["wrap"].asBool()) return measureWindow(L"Ag", fontOf(n.hwnd)).cy;
            HDC dc = GetDC(nullptr);
            HGDIOBJ old = SelectObject(dc, fontOf(n.hwnd));
            RECT r{0, 0, std::max(10, width), 0};
            std::wstring s = windowText(n.hwnd);
            if (s.empty()) s = L" ";
            DrawTextW(dc, s.c_str(), -1, &r, DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX | DT_EDITCONTROL);
            SelectObject(dc, old);
            ReleaseDC(nullptr, dc);
            return r.bottom;
        }
        if (t == "entry" || t == "combo" || t == "spin") return controlHeight();
        if (t == "text") {
            HFONT f = fontOf(n.hwnd);
            return measureWindow(L"Ag", f).cy * v["height"].asInt(5) + S(8);
        }
        if (t == "check" || t == "radio") return std::max(lineHeight(), S(17)) + S(2);
        if (t == "button") return buttonHeight();
        if (t == "progress") return S(v["height"].asInt(15));
        if (t == "image") return S(v["size"].asInt(32));
        if (t == "list") return S(v["height"].asInt(8) * 19 + 26);
        if (t == "separator") return S(9);
        if (t == "space") return S(v["height"].asInt(GAP));
        if (t == "drop") return lineHeight() + S(PAD) * 2 + 4;
        if (t == "picture") return S(v["h"].asInt(104));
        if (t == "slider") return S(24);
        if (t == "label" && v["style"].asString() == "clock") return S(40);
        return 0;
    }

    int tabHeight() const { return lineHeight() + S(8); }

    std::vector<int> hboxWidths(const FormNode &n, int width) const {
        int gap = S(n.spec["gap"].asInt(GAP));
        std::vector<int> widths;
        int used = 0, stretch = 0;
        for (size_t i = 0; i < n.children.size(); ++i) {
            const auto &c = n.children[i];
            int w = prefWidth(c);
            widths.push_back(w);
            used += w + (i ? gap : 0);
            if (c.spec["stretch"].asBool()) ++stretch;
        }
        int extra = width - used;
        if (stretch && extra != 0)
            for (size_t i = 0; i < n.children.size(); ++i)
                if (n.children[i].spec["stretch"].asBool()) widths[i] = std::max(S(20), widths[i] + extra / stretch);
        return widths;
    }

    std::vector<int> gridWidths(const FormNode &n, int width) const {
        auto prefs = gridColumnPrefs(n);
        int gap = S(n.spec["colgap"].asInt(PAD));
        int used = 0;
        for (size_t i = 0; i < prefs.size(); ++i) used += prefs[i] + (i ? gap : 0);
        const auto &weights = n.spec["weights"].items();
        int total = 0;
        for (const auto &wt : weights) total += wt.asInt(0);
        if (total == 0) {  // default: the last column stretches
            prefs.back() += width - used;
        } else {
            int extra = width - used;
            for (size_t i = 0; i < prefs.size() && i < weights.size(); ++i) prefs[i] += extra * weights[i].asInt(0) / total;
        }
        for (auto &p : prefs) p = std::max(0, p);
        return prefs;
    }

    void forEachGridRow(const FormNode &n, const std::vector<int> &cols,
                        const std::function<void(const std::vector<std::pair<const FormNode *, int>> &, int)> &fn) const {
        int columns = static_cast<int>(cols.size());
        int gap = S(n.spec["colgap"].asInt(PAD));
        std::vector<std::pair<const FormNode *, int>> cells;
        int col = 0, row = 0;
        for (const auto &c : n.children) {
            int span = std::max(1, c.spec["colspan"].asInt(1));
            if (col + span > columns) {
                fn(cells, row++);
                cells.clear();
                col = 0;
            }
            int w = 0;
            for (int k = col; k < col + span; ++k) w += cols[static_cast<size_t>(k)] + (k > col ? gap : 0);
            cells.emplace_back(&c, w);
            col += span;
            if (col >= columns) {
                fn(cells, row++);
                cells.clear();
                col = 0;
            }
        }
        if (!cells.empty()) fn(cells, row);
    }

    void setVisibleTree(FormNode &n, bool on) {
        n.visible = on;
        if (n.hwnd) show(n.hwnd, on && !n.spec["hidden"].asBool());
        if (n.extra) show(n.extra, on && !n.spec["hidden"].asBool());
        for (size_t i = 0; i < n.children.size(); ++i) {
            bool childOn = on && !n.spec["hidden"].asBool();
            if (n.type == "tabs") childOn = childOn && static_cast<int>(i) == n.selectedTab;
            setVisibleTree(n.children[i], childOn);
        }
    }

    void placeNode(FormNode &n, int x, int y, int width) {
        const Value &v = n.spec;
        const std::string &t = n.type;
        int h = nodeHeight(n, width);
        n.bounds = {x, y, x + width, y + h};
        if (t == "vbox") {
            int cy = y;
            for (size_t i = 0; i < n.children.size(); ++i) {
                auto &c = n.children[i];
                if (c.spec["hidden"].asBool()) continue;
                int padx = S(c.spec["padx"].asInt(0));
                cy += S(c.spec["padTop"].asInt(i ? v["gap"].asInt(0) : 0));
                int cw = width - padx * 2;
                std::string align = c.spec["align"].asString();
                if (!align.empty() && align != "fill") cw = std::min(cw, prefWidth(c));
                int cx = x + padx;
                if (align == "right") cx = x + width - padx - cw;
                else if (align == "center") cx = x + (width - cw) / 2;
                placeNode(c, cx, cy, cw);
                cy += nodeHeight(c, cw) + S(c.spec["padBottom"].asInt(0));
            }
        } else if (t == "hbox") {
            auto widths = hboxWidths(n, width);
            int gap = S(v["gap"].asInt(GAP));
            int cx = x;
            if (v["align"].asString() == "right") {
                int total = 0;
                for (size_t i = 0; i < widths.size(); ++i) total += widths[i] + (i ? gap : 0);
                cx = x + width - total;
            }
            for (size_t i = 0; i < n.children.size(); ++i) {
                auto &c = n.children[i];
                int ch = nodeHeight(c, widths[i]);
                int cy = y + (v["valign"].asString() == "top" ? 0 : (h - ch) / 2);
                placeNode(c, cx, cy, widths[i]);
                cx += widths[i] + gap;
            }
        } else if (t == "grid") {
            auto cols = gridWidths(n, width);
            int gap = S(v["colgap"].asInt(PAD));
            int cy = y;
            std::vector<int> starts;
            int acc = x;
            for (size_t i = 0; i < cols.size(); ++i) {
                starts.push_back(acc);
                acc += cols[i] + gap;
            }
            int col = 0;
            forEachGridRow(n, cols, [&](const std::vector<std::pair<const FormNode *, int>> &cells, int) {
                int rh = 0;
                for (auto &cell : cells) rh = std::max(rh, nodeHeight(*cell.first, cell.second));
                col = 0;
                for (auto &cell : cells) {
                    auto *node = const_cast<FormNode *>(cell.first);
                    int ch = nodeHeight(*node, cell.second);
                    int cw = cell.second;
                    std::string align = node->spec["align"].asString();
                    bool fillCell = align == "fill" || (align.empty() && (node->type == "entry" || node->type == "text" ||
                                    node->type == "progress" || node->type == "list" || node->type == "vbox" || node->type == "hbox" ||
                                    node->type == "group" || node->type == "grid" || (node->type == "label" && node->spec["wrap"].asBool())));
                    if (!fillCell) cw = std::min(cw, prefWidth(*node));
                    int cx = starts[static_cast<size_t>(col)];
                    if (align == "right") cx = starts[static_cast<size_t>(col)] + cell.second - cw;
                    int yy = cy + (node->spec["valign"].asString() == "top" ? 0 : (rh - ch) / 2);
                    placeNode(*node, cx, yy, cw);
                    col += std::max(1, node->spec["colspan"].asInt(1));
                }
                cy += rh + S(v["rowgap"].asInt(4));
            });
        } else if (t == "group") {
            int top = lineHeight() + S(GAP);
            groupFrames.push_back({RECT{x, y + lineHeight() / 2, x + width, y + h}, W(v["title"].asString()), &n});
            if (!n.children.empty()) placeNode(n.children[0], x + S(PAD) + 1, y + top, width - S(PAD) * 2 - 2);
        } else if (t == "tabs") {
            HDC dc = GetDC(hwnd);
            n.tabRects.clear();
            int tx = x + S(2);
            for (const auto &page : v["pages"].items()) {
                SIZE sz = measure(dc, W(page["title"].asString()), g_theme.ui);
                RECT r{tx, y, tx + sz.cx + S(16), y + tabHeight()};
                n.tabRects.push_back(r);
                tx = r.right;
            }
            ReleaseDC(hwnd, dc);
            for (size_t i = 0; i < n.children.size(); ++i)
                placeNode(n.children[i], x + S(PAD) + 1, y + tabHeight() + S(PAD), width - S(PAD) * 2 - 2);
            tabsNodes.push_back(&n);
        } else if (n.hwnd) {
            int hh = h;
            if (t == "combo") hh = h + S(240);
            if (t == "spin") {
                int bw = S(18);
                place(n.hwnd, x, y, width - bw, h);
                place(n.extra, x + width - bw, y, bw, h);
                SendMessageW(n.extra, UDM_SETBUDDY, reinterpret_cast<WPARAM>(n.hwnd), 0);
                return;
            }
            if (t == "check" || t == "radio") {
                // Owner-drawn and native check boxes: the clickable area is the text only.
                int cw = std::min(width, prefWidth(n));
                place(n.hwnd, x, y, cw, h);
                return;
            }
            place(n.hwnd, x, y, width, hh);
        }
    }

    std::vector<FormNode *> tabsNodes;
    std::string laidOut;  // layoutKey() of what is on the screen

    // Everything about the controls that decides where they go: their sizes and visibility.
    void addLayoutKey(const FormNode &n, bool fillsParent, std::string &out) const {
        bool hidden = n.spec["hidden"].asBool();
        out += hidden ? 'h' : 'v';
        if (n.children.empty()) {
            int width = n.bounds.right - n.bounds.left;
            int pref = prefWidth(n);
            // A control stretched across its box keeps its place while its text still fits.
            out += std::to_string(fillsParent && pref <= width ? 0 : pref) + 'x' +
                   std::to_string(nodeHeight(n, width)) + ';';
            return;
        }
        for (const auto &c : n.children) {
            std::string align = c.spec["align"].asString();
            addLayoutKey(c, n.type == "vbox" && (align.empty() || align == "fill"), out);
        }
    }

    std::string layoutKey() const {
        std::string out;
        addLayoutKey(root, false, out);
        return out;
    }

    void layout() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        groupFrames.clear();
        tabsNodes.clear();
        int pad = S(PAD);
        int bh = buttonHeight();
        int barH = buttons.empty() && windowText(status).empty() ? 0 : pad + S(2) + bh + pad;
        placeNode(root, pad, pad, rc.right - pad * 2);
        setVisibleTree(root, true);
        // Button row: status at the left, buttons at the right in the order given.
        int bx = rc.right - pad;
        for (auto it = buttons.rbegin(); it != buttons.rend(); ++it) {
            int w = buttonWidth(it->first, it->second);
            bx -= w;
            place(it->second, bx, rc.bottom - pad - bh, w, bh);
            bx -= S(GAP);
        }
        place(status, pad, rc.bottom - pad - bh + (bh - lineHeight()) / 2, std::max(0, bx - pad - S(GAP)), lineHeight());
        (void)barH;
        laidOut = layoutKey();
        InvalidateRect(hwnd, nullptr, TRUE);
    }

    int contentWidth() const {
        int w = std::max(S(spec["width"].asInt(0)), prefWidth(root));
        int bw = 0;
        for (const auto &b : buttons) bw += buttonWidth(b.first, b.second) + S(GAP);
        if (!windowText(status).empty()) bw += S(120);
        return std::max(w, bw);
    }

    void sizeToContent() {
        int w = contentWidth();
        int pad = S(PAD);
        int bh = buttonHeight();
        int barH = buttons.empty() && windowText(status).empty() && !spec.has("status") ? pad : pad + S(2) + bh + pad;
        int h = pad + nodeHeight(root, w) + barH;
        h = std::max(h, S(spec["minHeight"].asInt(0)));
        RECT rc{0, 0, w + pad * 2, h};
        DWORD style = static_cast<DWORD>(GetWindowLongPtrW(hwnd, GWL_STYLE));
        DWORD ex = static_cast<DWORD>(GetWindowLongPtrW(hwnd, GWL_EXSTYLE));
        AdjustWindowRectEx(&rc, style, FALSE, ex);
        width = w;
        minTrack = {rc.right - rc.left, rc.bottom - rc.top};
        SetWindowPos(hwnd, nullptr, 0, 0, rc.right - rc.left, rc.bottom - rc.top, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
        layout();
    }

    SIZE minTrack{0, 0};

    // -------------------------------------------------------------------------- update
    void update(const Value &s) {
        if (fullSignature(s) != structure) {
            rebuild(s);
            return;
        }
        SetWindowTextW(hwnd, W(s["title"].asString()).c_str());
        setText(status, s["status"].asString());
        std::string sk = s["statusKind"].asString();
        setInk(status, sk == "error" ? Ink::Danger : sk == "ok" ? Ink::Success : Ink::Muted);
        const auto &bs = s["buttons"].items();
        for (size_t i = 0; i < buttons.size() && i < bs.size(); ++i) {
            enable(buttons[i].second, bs[i]["enabled"].asBool(true));
            buttons[i].first = bs[i];
        }
        Value old = spec;
        spec = s;
        applyValues(root, s["body"], false);
        // The recorders send a new state many times a second (clock, sound meters). Moving and
        // repainting every control each time makes the window flicker, so that only happens when
        // something changed size or visibility.
        bool grow = s["relayout"].asBool() || layoutKey() != laidOut ||
                    s["status"].asString().empty() != old["status"].asString().empty();
        if (grow) {
            int w = contentWidth();
            int pad = S(PAD);
            int bh = buttonHeight();
            int h = pad + nodeHeight(root, w) + pad + S(2) + bh + pad;
            RECT cr;
            GetClientRect(hwnd, &cr);
            if (!s["resizable"].asBool() && (cr.bottom != h || cr.right < w + pad * 2)) sizeToContent();
            else layout();
        }
        if (s["focus"] != old["focus"]) focusWidget(s["focus"].asString());
        if (s["minimizeRev"] != old["minimizeRev"]) ShowWindow(hwnd, SW_MINIMIZE);
        if (s["hidden"].asBool() != old["hidden"].asBool()) {
            if (s["hidden"].asBool()) ShowWindow(hwnd, SW_HIDE);
            else {
                ShowWindow(hwnd, SW_SHOW);
                bringToFront(false);
            }
        }
        // The controller toggles "flash" each time the window is asked for again.
        if (s["flash"].asBool() != old["flash"].asBool()) bringToFront(true);
    }

    void rebuild(const Value &s) {
        // Same window, new controls: keeps the position.
        RECT wr;
        GetWindowRect(hwnd, &wr);
        std::vector<HWND> children;
        EnumChildWindows(hwnd, [](HWND c, LPARAM p) -> BOOL {
            reinterpret_cast<std::vector<HWND> *>(p)->push_back(c);
            return TRUE;
        }, reinterpret_cast<LPARAM>(&children));
        for (HWND c : children)
            if (GetParent(c) == hwnd) DestroyWindow(c);
        buttons.clear();
        listIds.clear();
        sliderIds.clear();
        dropPaths.clear();
        bool wasHidden = spec["hidden"].asBool();
        spec = s;
        structure = fullSignature(s);
        SetWindowTextW(hwnd, W(s["title"].asString()).c_str());
        root = makeNode(s["body"]);
        status = makeLabel(hwnd, s["status"].asString(), Ink::Muted, false, SS_ENDELLIPSIS);
        for (const auto &b : s["buttons"].items()) {
            std::string bid = b["id"].asString();
            HWND h = makeButton(hwnd, b["text"].asString(), [this, bid] { event("click", bid); }, b["default"].asBool());
            enable(h, b["enabled"].asBool(true));
            buttons.emplace_back(b, h);
        }
        trackAllHover(hwnd);
        applyValues(root, s["body"], true);
        sizeToContent();
        SetWindowPos(hwnd, nullptr, wr.left, wr.top, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        if (s["hidden"].asBool() != wasHidden) {
            ShowWindow(hwnd, s["hidden"].asBool() ? SW_HIDE : SW_SHOW);
            if (!s["hidden"].asBool()) bringToFront(false);
        }
        focusWidget(s["focus"].asString());
    }

    FormNode *find(FormNode &n, const std::string &wid) {
        if (n.id == wid) return &n;
        for (auto &c : n.children)
            if (FormNode *f = find(c, wid)) return f;
        return nullptr;
    }

    void focusWidget(const std::string &wid) {
        if (wid.empty()) {
            for (auto &b : buttons)
                if (b.first["default"].asBool() && IsWindowEnabled(b.second)) {
                    SetFocus(b.second);
                    return;
                }
            return;
        }
        if (FormNode *n = find(root, wid)) {
            if (n->hwnd) {
                SetFocus(n->hwnd);
                if (n->type == "entry") SendMessageW(n->hwnd, EM_SETSEL, n->spec["selectAll"].asBool(true) ? 0 : -1, -1);
            }
        }
    }

    void showFirst() {
        if (placed) return;
        placed = true;
        HWND owner = spec["owner"].asString() == "none" ? nullptr : g_main;
        centerOn(hwnd, owner);
        int offset = spec["offset"].asInt(0);
        if (offset) {
            RECT r;
            GetWindowRect(hwnd, &r);
            SetWindowPos(hwnd, nullptr, r.left + S(28) * (offset % 8), r.top + S(28) * (offset % 8), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        }
        if (!spec["hidden"].asBool()) ShowWindow(hwnd, SW_SHOW);
        for (auto &kv : listIds) {
            int sel = ListView_GetNextItem(kv.first, -1, LVNI_SELECTED);
            if (ListView_GetItemCount(kv.first) > 0) {
                ListView_EnsureVisible(kv.first, 0, FALSE);
                if (sel > 0) ListView_EnsureVisible(kv.first, sel, FALSE);
            }
        }
        bringToFront(spec["topmost"].asBool());
        focusWidget(spec["focus"].asString());
        if (modal && g_main && IsWindowVisible(g_main)) EnableWindow(g_main, FALSE);
    }

    void bringToFront(bool topmost) {
        if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
        if (topmost) {
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
            SetTimer(hwnd, 77, 500, nullptr);
        }
        forceForeground(hwnd);
    }

    void destroy() {
        closing = true;
        if (modal && g_main) EnableWindow(g_main, TRUE);
        g_formsByHwnd.erase(hwnd);
        DestroyWindow(hwnd);
        hwnd = nullptr;
    }

    // --------------------------------------------------------------------------- paint
    void paint() {
        BufferedPaint bp(hwnd);
        HDC dc = bp.dc();
        const Palette &p = g_theme.p;
        fill(dc, bp.rect(), p.BG);
        COLORREF edge = g_theme.dark ? p.BORDER : hex(0xDCDCDC);
        for (FormNode *n : tabsNodes) {
            if (!n->visible) continue;
            RECT body = n->bounds;
            body.top += tabHeight() - 1;
            fill(dc, body, p.BG);
            frame(dc, body, edge);
            for (size_t i = 0; i < n->tabRects.size(); ++i) {
                RECT r = n->tabRects[i];
                bool sel = static_cast<int>(i) == n->selectedTab;
                if (sel) {
                    r.top -= S(2);
                    r.bottom += 1;
                }
                fill(dc, r, sel ? p.BG : (g_theme.dark ? p.SURFACE : hex(0xE3E3E3)));
                RECT outline = r;
                if (sel) outline.bottom -= 1;
                HGDIOBJ old = SelectObject(dc, g_theme.pen(edge));
                MoveToEx(dc, outline.left, outline.bottom, nullptr);
                LineTo(dc, outline.left, outline.top);
                LineTo(dc, outline.right - 1, outline.top);
                LineTo(dc, outline.right - 1, outline.bottom);
                SelectObject(dc, old);
                text(dc, W(n->spec["pages"].items()[i]["title"].asString()), r, p.TEXT, g_theme.ui,
                     DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_NOPREFIX);
            }
        }
        for (const auto &g : groupFrames) {
            if (!g.node->visible || g.node->spec["hidden"].asBool()) continue;
            RECT r = g.rc;
            frame(dc, r, edge);
            SIZE sz = measure(dc, g.title, g_theme.ui);
            RECT t{r.left + S(8), r.top - sz.cy / 2, r.left + S(8) + sz.cx + S(4), r.top + sz.cy / 2 + 1};
            fill(dc, t, p.BG);
            t.left += S(2);
            text(dc, g.title, t, g_theme.dark ? p.TEXT : hex(0x000000), g_theme.ui);
        }
    }

    bool tabClick(POINT pt) {
        for (FormNode *n : tabsNodes) {
            if (!n->visible) continue;
            for (size_t i = 0; i < n->tabRects.size(); ++i)
                if (PtInRect(&n->tabRects[i], pt)) {
                    if (n->selectedTab != static_cast<int>(i)) {
                        n->selectedTab = static_cast<int>(i);
                        setVisibleTree(root, true);
                        InvalidateRect(hwnd, nullptr, TRUE);
                        event("tab", n->id);
                    }
                    return true;
                }
        }
        return false;
    }

    // Enter in an entry with "enter": true clicks it.
    static LRESULT CALLBACK enterSubclass(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR data) {
        if (m == WM_KEYDOWN && w == VK_RETURN) {
            auto *self = reinterpret_cast<Form *>(data);
            for (auto &kv : self->g_entryIds())
                if (kv.first == h) {
                    self->event("enter", kv.second);
                    return 0;
                }
        }
        if (m == WM_CHAR && w == '\r') return 0;
        return DefSubclassProc(h, m, w, l);
    }

    std::vector<std::pair<HWND, std::string>> g_entryIds() {
        std::vector<std::pair<HWND, std::string>> out;
        std::function<void(FormNode &)> walk = [&](FormNode &n) {
            if (n.type == "entry" && n.hwnd) out.emplace_back(n.hwnd, n.id);
            for (auto &c : n.children) walk(c);
        };
        walk(root);
        return out;
    }

    void defaultButton() {
        for (auto &b : buttons)
            if (b.first["default"].asBool() && IsWindowEnabled(b.second)) {
                event("click", b.first["id"].asString());
                return;
            }
    }
};

// ---------------------------------------------------------------------------- window procs
inline LRESULT CALLBACK formProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    auto it = g_formsByHwnd.find(h);
    Form *f = it == g_formsByHwnd.end() ? nullptr : it->second;
    LRESULT out = 0;
    if (f && handleControlMessage(h, m, w, l, &out, g_theme.p.BG)) return out;
    switch (m) {
    case WM_PAINT:
        if (f) {
            f->paint();
            return 0;
        }
        break;
    case WM_ERASEBKGND: return 1;
    case WM_SIZE:
        if (f) f->layout();
        return 0;
    case WM_GETMINMAXINFO:
        if (f && f->minTrack.cx) {
            auto *mm = reinterpret_cast<MINMAXINFO *>(l);
            mm->ptMinTrackSize.x = f->minTrack.cx;
            mm->ptMinTrackSize.y = f->minTrack.cy;
        }
        return 0;
    case WM_LBUTTONDOWN:
        if (f) f->tabClick(POINT{GET_X_LPARAM(l), GET_Y_LPARAM(l)});
        return 0;
    case WM_CLOSE:
        if (f) f->event("close", "");
        return 0;
    case WM_TIMER:
        if (w == 77) {
            KillTimer(h, 77);
            SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
        return 0;
    case WM_NOTIFY: {
        auto *n = reinterpret_cast<NMHDR *>(l);
        if (f && f->listIds.count(n->hwndFrom)) {
            const std::string &wid = f->listIds[n->hwndFrom];
            if (n->code == LVN_ITEMCHANGED) {
                auto *c = reinterpret_cast<NMLISTVIEW *>(l);
                if (!f->applyingValues && (c->uChanged & LVIF_STATE) && (c->uNewState & LVIS_SELECTED)) f->event("select", wid);
            } else if (n->code == NM_CUSTOMDRAW) {
                auto *cd = reinterpret_cast<NMLVCUSTOMDRAW *>(l);
                if (cd->nmcd.dwDrawStage == CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
                if (cd->nmcd.dwDrawStage == CDDS_ITEMPREPAINT) {
                    auto &kinds = g_listKinds[n->hwndFrom];
                    size_t row = static_cast<size_t>(cd->nmcd.dwItemSpec);
                    if (row < kinds.size() && kinds[row] == "error") cd->clrText = g_theme.p.DANGER;
                    return CDRF_DODEFAULT;
                }
                return CDRF_DODEFAULT;
            } else if (n->code == NM_DBLCLK) {
                f->event("dblclick", wid);
            } else if (n->code == LVN_KEYDOWN) {
                auto *k = reinterpret_cast<NMLVKEYDOWN *>(l);
                if (k->wVKey == VK_DELETE) f->event("delete", wid);
                else if (k->wVKey == VK_RETURN) f->event("dblclick", wid);
            }
            return 0;
        }
        break;
    }
    case WM_ACTIVATE:
        if (f && LOWORD(w) != WA_INACTIVE) f->event("activate", "");
        break;
    case WM_HSCROLL:
        if (f && l && f->sliderIds.count(reinterpret_cast<HWND>(l)) && !f->applyingValues) {
            int code = LOWORD(w);
            if (code != TB_ENDTRACK) f->event("change", f->sliderIds[reinterpret_cast<HWND>(l)]);
        }
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

// A box the user drags a finished file out of (into Telegram, a browser, a folder...).
inline std::function<void(const std::vector<std::wstring> &)> g_dragFiles;

inline LRESULT CALLBACK dropProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        BufferedPaint bp(h);
        const Palette &p = g_theme.p;
        RECT rc = bp.rect();
        fill(bp.dc(), rc, p.BG);
        RECT in = rc;
        frame(bp.dc(), in, g_theme.dark ? p.BORDER : hex(0xA0A0A0));
        InflateRect(&in, -1, -1);
        frame(bp.dc(), in, g_theme.dark ? p.BG : hex(0xFFFFFF));
        text(bp.dc(), windowText(h), rc, p.TEXT, g_theme.ui, DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_SETCURSOR:
        SetCursor(LoadCursorW(nullptr, IDC_HAND));
        return TRUE;
    case WM_LBUTTONDOWN:
        SetCapture(h);
        return 0;
    case WM_MOUSEMOVE:
        if (GetCapture() == h && (w & MK_LBUTTON)) {
            ReleaseCapture();
            Form *f = g_formsByHwnd.count(GetParent(h)) ? g_formsByHwnd[GetParent(h)] : nullptr;
            if (f && f->dropPaths.count(h) && !f->dropPaths[h].empty() && g_dragFiles) g_dragFiles({f->dropPaths[h]});
        }
        return 0;
    case WM_LBUTTONUP:
        ReleaseCapture();
        return 0;
    case WM_SETTEXT: {
        LRESULT r = DefWindowProcW(h, m, w, l);
        InvalidateRect(h, nullptr, FALSE);
        return r;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

inline void registerFormClasses() {
    WNDCLASSEXW wc{sizeof wc};
    wc.hInstance = g_inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.lpfnWndProc = formProc;
    wc.lpszClassName = L"MDForm";
    wc.hIcon = g_icon;
    RegisterClassExW(&wc);
    wc.lpfnWndProc = pictureProc;
    wc.lpszClassName = L"MDPicture";
    wc.hIcon = nullptr;
    RegisterClassExW(&wc);
    wc.lpfnWndProc = dropProc;
    wc.lpszClassName = L"MDDrop";
    wc.hIcon = nullptr;
    RegisterClassExW(&wc);
}

// Applies the "forms" list of a state: creates, updates and closes the dialogs it names.
inline void applyForms(const Value &forms) {
    std::set<std::string> wanted;
    for (const auto &s : forms.items()) {
        std::string fid = s["id"].asString();
        if (fid.empty()) continue;
        wanted.insert(fid);
        auto it = g_formsById.find(fid);
        if (it == g_formsById.end()) {
            auto form = std::make_unique<Form>();
            form->build(s);
            Form *raw = form.get();
            g_formsById[fid] = std::move(form);
            raw->showFirst();
        } else {
            it->second->update(s);
        }
    }
    for (auto it = g_formsById.begin(); it != g_formsById.end();) {
        if (!wanted.count(it->first)) {
            it->second->destroy();
            it = g_formsById.erase(it);
        } else {
            ++it;
        }
    }
}

// Keyboard: Esc closes, Enter clicks the default button, Tab moves focus.
inline bool formKeyboard(MSG &msg) {
    for (auto &kv : g_formsById) {
        Form *f = kv.second.get();
        if (!f->hwnd || !(msg.hwnd == f->hwnd || IsChild(f->hwnd, msg.hwnd))) continue;
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_ESCAPE) {
            f->event("close", "");
            return true;
        }
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_RETURN) {
            wchar_t cls[16]{};
            GetClassNameW(msg.hwnd, cls, 16);
            bool multiline = _wcsicmp(cls, L"Edit") == 0 && (GetWindowLongPtrW(msg.hwnd, GWL_STYLE) & ES_MULTILINE);
            bool button = _wcsicmp(cls, L"Button") == 0;
            bool enterEntry = false;
            for (auto &e : f->g_entryIds())
                if (e.first == msg.hwnd)
                    if (FormNode *n = f->find(f->root, e.second)) enterEntry = n->spec["enter"].asBool();
            if (!multiline && !button && !enterEntry) {
                f->defaultButton();
                return true;
            }
        }
        return IsDialogMessageW(f->hwnd, &msg) != FALSE;
    }
    return false;
}

}  // namespace md
