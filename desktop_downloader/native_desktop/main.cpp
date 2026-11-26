// Media Downloader desktop window: the whole user interface, written in C++ with Windows' own
// controls (no UI toolkit is bundled).
//
// The Python controller owns every download and setting. It streams the visible state to this
// process as one JSON object per line on stdin; this process answers with one JSON command per
// line on stdout (see native_shell.py and native_forms.py).
#include "app.h"

#include <initguid.h>
#include <propkey.h>
#include <propsys.h>

namespace md {

// The same identity as the Python process and the installer's shortcuts (APP_USER_MODEL_ID).
constexpr const wchar_t *kAppId = L"TeamFahad.MediaDownloader";

// This window belongs to the app: pinning it to the taskbar must pin the launcher, not this
// helper, which cannot run on its own.
void setTaskbarIdentity(HWND h, const std::wstring &launcher) {
    IPropertyStore *store = nullptr;
    if (FAILED(SHGetPropertyStoreForWindow(h, IID_PPV_ARGS(&store)))) return;
    auto set = [store](const PROPERTYKEY &key, const std::wstring &value) {
        PROPVARIANT pv;
        PropVariantInit(&pv);
        size_t bytes = (value.size() + 1) * sizeof(wchar_t);
        pv.pwszVal = static_cast<LPWSTR>(CoTaskMemAlloc(bytes));
        if (!pv.pwszVal) return;
        memcpy(pv.pwszVal, value.c_str(), bytes);
        pv.vt = VT_LPWSTR;
        store->SetValue(key, pv);
        PropVariantClear(&pv);
    };
    if (!launcher.empty()) {  // the relaunch properties must be set before the ID
        set(PKEY_AppUserModel_RelaunchCommand, L"\"" + launcher + L"\"");
        set(PKEY_AppUserModel_RelaunchDisplayNameResource, L"Media Downloader");
        set(PKEY_AppUserModel_RelaunchIconResource, launcher + L",0");
    }
    set(PKEY_AppUserModel_ID, kAppId);
    store->Release();
}

// Started on its own, for example from a taskbar pin made by an older version: start the app
// instead (it brings its window back when it is already running). The installed layout is
// <app>\_internal\native_desktop\MediaDownloaderDesktop.exe.
void openLauncher() {
    wchar_t self[32768]{};
    DWORD n = GetModuleFileNameW(nullptr, self, 32768);
    if (!n || n >= 32768) return;
    std::wstring dir(self, n);
    for (int up = 0; up < 3; ++up) {
        size_t cut = dir.find_last_of(L'\\');
        if (cut == std::wstring::npos) return;
        dir.resize(cut);
    }
    std::wstring exe = dir + L"\\MediaDownloader.exe";
    if (GetFileAttributesW(exe.c_str()) == INVALID_FILE_ATTRIBUTES) return;
    AllowSetForegroundWindow(ASFW_ANY);
    ShellExecuteW(nullptr, L"open", exe.c_str(), nullptr, dir.c_str(), SW_SHOWNORMAL);
}

bool registerClasses() {
    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_LISTVIEW_CLASSES | ICC_TREEVIEW_CLASSES | ICC_PROGRESS_CLASS |
                                             ICC_STANDARD_CLASSES | ICC_BAR_CLASSES | ICC_UPDOWN_CLASS | ICC_TAB_CLASSES};
    InitCommonControlsEx(&icc);
    WNDCLASSEXW wc{sizeof wc};
    wc.hInstance = g_inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hIcon = g_icon ? g_icon : LoadIconW(g_inst, MAKEINTRESOURCEW(1));
    wc.lpszClassName = L"MDMain";
    wc.lpfnWndProc = App::proc;
    if (!RegisterClassExW(&wc)) return false;
    wc.hIcon = nullptr;
    wc.lpszClassName = L"MDToolbar";
    wc.lpfnWndProc = Toolbar::proc;
    RegisterClassExW(&wc);
    wc.lpszClassName = L"MDStatus";
    wc.lpfnWndProc = StatusBar::proc;
    RegisterClassExW(&wc);
    wc.lpszClassName = L"MDPane";
    wc.lpfnWndProc = paneProc;
    RegisterClassExW(&wc);
    registerProgressClass();
    registerFormClasses();
    return true;
}

// Keyboard shortcuts of the main window, handled before the focused control sees the key.
bool shortcut(const MSG &msg) {
    if (msg.message != WM_KEYDOWN && msg.message != WM_SYSKEYDOWN) return false;
    if (GetAncestor(msg.hwnd, GA_ROOT) != g_main) return false;
    bool ctrl = GetKeyState(VK_CONTROL) < 0, alt = GetKeyState(VK_MENU) < 0;
    App &a = g_app;
    if (msg.wParam == VK_F5) {
        a.sendCommand("refresh");
        return true;
    }
    if (msg.wParam == VK_ESCAPE && msg.hwnd == a.header.search) {
        SetWindowTextW(a.header.search, L"");
        return true;
    }
    if (!ctrl) return false;
    switch (msg.wParam) {
    case 'N': a.sendCommand("add"); return true;
    case 'B': a.sendCommand("batch"); return true;
    case 'K': a.sendCommand("palette"); return true;
    case 'F':
        SetFocus(a.header.search);
        SendMessageW(a.header.search, EM_SETSEL, 0, -1);
        return true;
    case 'D':
        if (alt) {
            a.sendCommand("clipboard", Value("Video"));
            return true;
        }
        break;
    case 'M':
        if (alt) {
            a.sendCommand("clipboard", Value("Audio"));
            return true;
        }
        break;
    }
    return false;
}

// A live dialog (a recorder) gets a new state many times a second. A new clock text or meter level
// must not lay out and repaint the whole window; a note that appears must.
int liveFormTest(Value &state) {
    auto spec = [](const char *clock, double level, bool note) {
        std::string text = std::string(R"({"id":"live","title":"Live","buttons":[],"status":"","body":{"type":"vbox","children":[)") +
            R"({"type":"label","style":"clock","text":")" + clock + R"("},)" +
            R"({"type":"progress","id":"meter","value":)" + std::to_string(level) + R"(},)" +
            R"({"type":"label","text":"Speak now","hidden":)" + (note ? "false" : "true") + "}]}}";
        Value v;
        json::parse(text, v);
        Value list = Value::array();
        list.push(v);
        return list;
    };
    state.set("forms", spec("00:00:01", 0.1, false));
    g_app.apply(state);
    Form *form = g_formsById.count("live") ? g_formsById["live"].get() : nullptr;
    if (!form) return 3;
    HWND clock = form->root.children[0].hwnd;
    UpdateWindow(form->hwnd);
    ValidateRect(form->hwnd, nullptr);
    state.set("forms", spec("00:00:02", 0.8, false));
    g_app.apply(state);
    if (GetUpdateRect(form->hwnd, nullptr, FALSE)) return 4;  // repainted for nothing
    state.set("forms", spec("00:00:03", 0.2, true));
    g_app.apply(state);
    if (!GetUpdateRect(form->hwnd, nullptr, FALSE)) return 5;  // the note needs a new layout
    if (form->root.children[0].hwnd != clock) return 6;        // and no rebuild
    state.set("forms", Value::array());
    g_app.apply(state);
    return 0;
}

// Builds every window and feeds it a state with rows, every pane and a dialog, then exits.
int selfTest() {
    Value state = Value::object();
    state.set("visible", true);
    state.set("visibilityRevision", 1);
    state.set("version", "0.0.0");
    Value row = Value::object();
    row.set("key", "q:smoke");
    row.set("name", "Smoke \xE0\xA6\x9F\xE0\xA7\x87\xE0\xA6\xB8\xE0\xA7\x8D\xE0\xA6\x9F.mp4");
    row.set("status", "downloading");
    row.set("status_text", "50%");
    row.set("category", "video");
    row.set("progress", 0.5);
    Value actions = Value::array();
    actions.push("stop");
    row.set("actions", actions);
    Value rows = Value::array();
    rows.push(row);
    state.set("rows", rows);
    Value cats = Value::array();
    Value all = Value::object();
    all.set("key", "all");
    all.set("name", "All Downloads");
    all.set("count", "1");
    cats.push(all);
    Value batch = Value::object();
    batch.set("key", "queue:b1");
    batch.set("name", "Batch 1");
    batch.set("count", "0/3");
    cats.push(batch);
    state.set("categories", cats);
    state.set("category", all);
    Value cols = Value::array();
    for (const char *c : {"name", "size", "status", "speed"}) cols.push(c);
    state.set("columns", cols);
    Value details = Value::object();
    details.set("title", "Smoke.mp4");
    details.set("progress", 0.5);
    state.set("detailsPane", details);
    Value forms = Value::array();
    Value form = Value::object();
    form.set("id", "smoke");
    form.set("title", "Smoke");
    Value body = Value::object();
    body.set("type", "vbox");
    Value kids = Value::array();
    for (const char *t : {"label", "entry", "check", "combo", "progress", "text"}) {
        Value k = Value::object();
        k.set("type", t);
        k.set("id", std::string("w_") + t);
        k.set("text", "Smoke");
        kids.push(k);
    }
    Value tabs = Value::object();
    tabs.set("type", "tabs");
    Value pages = Value::array();
    Value page = Value::object();
    page.set("title", "General");
    Value pbody = Value::object();
    pbody.set("type", "group");
    pbody.set("title", "Group");
    Value inner = Value::object();
    inner.set("type", "label");
    inner.set("text", "Inside");
    pbody.set("body", inner);
    page.set("body", pbody);
    pages.push(page);
    tabs.set("pages", pages);
    kids.push(tabs);
    body.set("children", kids);
    form.set("body", body);
    Value buttons = Value::array();
    Value ok = Value::object();
    ok.set("id", "ok");
    ok.set("text", "OK");
    ok.set("default", true);
    buttons.push(ok);
    form.set("buttons", buttons);
    forms.push(form);
    state.set("forms", forms);
    g_app.apply(state);
    MSG msg;
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    if (g_app.rows.size() != 1 || g_formsById.size() != 1) return 1;
    state.set("forms", Value::array());
    g_app.apply(state);
    if (!g_formsById.empty()) return 2;
    return liveFormTest(state);
}

}  // namespace md

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR, int) {
    using namespace md;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    g_inst = inst;
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::wstring icon, launcher, themeArg = L"Light";
    bool fromController = false;  // the app always passes --theme
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--self-test") g_testMode = true;
        else if (a == L"--icon" && i + 1 < argc) icon = argv[++i];
        else if (a == L"--theme" && i + 1 < argc) {
            themeArg = argv[++i];
            fromController = true;
        } else if (a == L"--launcher" && i + 1 < argc) launcher = argv[++i];
    }
    LocalFree(argv);
    if (!fromController && !g_testMode) {
        openLauncher();
        return 0;
    }
    SetCurrentProcessExplicitAppUserModelID(kAppId);
    if (!icon.empty())
        g_icon = static_cast<HICON>(LoadImageW(nullptr, icon.c_str(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));
    if (!g_icon) g_icon = LoadIconW(inst, MAKEINTRESOURCEW(1));

    OleInitialize(nullptr);
    icons::startup();
    HDC screen = GetDC(nullptr);
    g_theme.dpi = GetDeviceCaps(screen, LOGPIXELSX);
    ReleaseDC(nullptr, screen);
    // The theme applies at start-up, like the Tk window ("used the next time the app starts").
    g_theme.setDark(themeArg == L"Dark");
    enableDarkMenus(g_theme.dark);
    g_theme.buildFonts();
    if (!registerClasses()) return 2;

    g_main = CreateWindowExW(0, L"MDMain", L"Media Downloader", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN, CW_USEDEFAULT,
                             CW_USEDEFAULT, S(1180), S(780), nullptr, nullptr, inst, nullptr);
    if (!g_main) return 3;
    {
        UINT dpi = GetDpiForWindow(g_main);
        if (dpi && static_cast<int>(dpi) != g_theme.dpi) {
            g_theme.dpi = static_cast<int>(dpi);
            g_theme.buildFonts();
            SetWindowPos(g_main, nullptr, 0, 0, S(1180), S(780), SWP_NOMOVE | SWP_NOZORDER);
        }
    }
    setTaskbarIdentity(g_main, launcher);
    g_app.hwnd = g_main;
    g_app.create();
    centerOn(g_main, nullptr);
    g_app.layout();

    if (g_testMode) {
        int status = selfTest();
        DestroyWindow(g_main);
        icons::shutdown();
        return status;
    }

    std::thread(readerThread).detach();
    send("ready");
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (shortcut(msg)) continue;
        if (formKeyboard(msg)) continue;
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    icons::shutdown();
    return 0;
}
