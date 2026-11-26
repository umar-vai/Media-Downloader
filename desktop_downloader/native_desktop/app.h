// The main window controller: owns the panes, the category tree and the download list, and
// applies each state the Python controller sends.
#pragma once

#include "dialogs.h"
#include "mainwindow.h"

namespace md {

constexpr UINT_PTR TIMER_SELECTION = 11;

// The newest state line from the controller; older ones are dropped (only the latest matters).
inline std::mutex g_inbox;
inline std::unique_ptr<Value> g_latest;
inline std::atomic<bool> g_pending{false};

inline std::unique_ptr<Value> takeLatestState() {
    std::lock_guard<std::mutex> lock(g_inbox);
    g_pending = false;
    return std::move(g_latest);
}

inline void readerThread() {
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    std::string buffer;
    std::vector<char> chunk(1 << 16);
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(in, chunk.data(), static_cast<DWORD>(chunk.size()), &got, nullptr) || got == 0) break;
        buffer.append(chunk.data(), got);
        size_t start = 0, nl;
        while ((nl = buffer.find('\n', start)) != std::string::npos) {
            std::string line = buffer.substr(start, nl - start);
            start = nl + 1;
            auto parsed = std::make_unique<Value>();
            if (!json::parse(line, *parsed) || !parsed->isObject()) continue;
            bool post;
            {
                std::lock_guard<std::mutex> lock(g_inbox);
                g_latest = std::move(parsed);
                post = !g_pending.exchange(true);
            }
            if (post) PostMessageW(g_main, WM_STATE, 0, 0);
        }
        buffer.erase(0, start);
        if (buffer.size() > (256u << 20)) buffer.clear();
    }
    PostMessageW(g_main, WM_INPUT_CLOSED, 0, 0);
}

enum MenuId : UINT {
    ID_ADD = 100, ID_BATCH, ID_CLIP_VIDEO, ID_CLIP_AUDIO, ID_PALETTE, ID_HIDE, ID_EXIT,
    ID_OPEN, ID_OPEN_FOLDER, ID_EDIT, ID_OPEN_DOWNLOADS, ID_CHOOSE_FOLDER, ID_EDITOR, ID_SCREEN, ID_AUDIO,
    ID_RESUME, ID_STOP, ID_CANCEL, ID_REDOWNLOAD, ID_START_ALL, ID_STOP_ALL, ID_RETRY_ALL, ID_DELETE, ID_DELETE_DONE,
    ID_REMOVE_MISSING, ID_CLEANUP, ID_CLEAN_PARTS, ID_COPY_LINK, ID_DETAILS, ID_PROPERTIES, ID_SELECT_ALL,
    ID_VIEW_CATEGORIES, ID_VIEW_DETAILS, ID_RESET_COLUMNS, ID_THEME_LIGHT, ID_THEME_DARK, ID_REFRESH,
    ID_CHECK_UPDATES, ID_ABOUT, ID_INSTALL_EXT, ID_DIAGNOSTICS, ID_LOG,
    ID_COLUMN_FIRST = 300,
    ID_POPUP_FIRST = 500,
};

class App {
public:
    HWND hwnd = nullptr, tree = nullptr, list = nullptr;
    HMENU menuBar = nullptr, fileMenu = nullptr, downloadsMenu = nullptr, viewMenu = nullptr, columnsMenu = nullptr,
          themeMenu = nullptr;
    Toolbar toolbar;
    StatusBar status;
    DetailsPane details;
    ListHeader header;
    Dialogs dialogs;
    HIMAGELIST treeImages = nullptr;

    std::vector<Row> rows;
    std::vector<std::string> columns{"name", "size", "status", "speed", "eta", "last_try", "save_to"};
    Value categories = Value::array();
    Value widths = Value::object();
    Value totals = Value::object();
    Value actionCounts = Value::object();
    std::map<std::string, HTREEITEM> treeItems;
    std::string category = "all", sortKey = "added", themeName, emptyTitle, emptyText, query;
    bool descending = true, navigation = true, showDetails = true, applying = false, haveState = false;
    int visibilityRevision = -1, selectionRevision = -1, sideWidth = 214, dragging = 0;
    int hotHeader = -1;
    std::vector<std::string> lastSentSelection;

    // ------------------------------------------------------------------------------ helpers
    std::vector<std::string> selectedKeys() const {
        std::vector<std::string> keys;
        int i = -1;
        while ((i = ListView_GetNextItem(list, i, LVNI_SELECTED)) >= 0)
            if (i < static_cast<int>(rows.size())) keys.push_back(rows[static_cast<size_t>(i)].key);
        return keys;
    }

    std::vector<const Row *> selectedRows() const {
        std::vector<const Row *> out;
        int i = -1;
        while ((i = ListView_GetNextItem(list, i, LVNI_SELECTED)) >= 0)
            if (i < static_cast<int>(rows.size())) out.push_back(&rows[static_cast<size_t>(i)]);
        return out;
    }

    // Union of the actions of the selected rows (download_list.available_actions).
    std::set<std::string> enabledActions() const {
        std::set<std::string> out;
        auto sel = selectedRows();
        static const std::set<std::string> single{"open", "edit", "open_page", "properties", "details"};
        for (const Row *r : sel)
            for (const auto &a : r->actions)
                if (sel.size() == 1 || !single.count(a)) out.insert(a);
        if (sel.size() == 1) out.insert(sel[0]->actions.begin(), sel[0]->actions.end());
        return out;
    }

    void sendCommand(const std::string &command, const Value &value = Value()) {
        auto keys = selectedKeys();
        send(command, value, &keys);
    }

    // ------------------------------------------------------------------------------ create
    void create() {
        buildMenus();
        SetMenu(hwnd, menuBar);
        toolbar.create(hwnd);
        toolbar.onCommand = [this](const std::string &c) { toolbarCommand(c); };
        status.create(hwnd);

        tree = CreateWindowExW(0, WC_TREEVIEWW, L"",
                               WS_CHILD | WS_VISIBLE | WS_TABSTOP | TVS_HASBUTTONS | TVS_LINESATROOT | TVS_SHOWSELALWAYS |
                                   TVS_FULLROWSELECT | TVS_NOHSCROLL,
                               0, 0, 10, 10, hwnd, nullptr, g_inst, nullptr);
        TreeView_SetExtendedStyle(tree, TVS_EX_DOUBLEBUFFER, TVS_EX_DOUBLEBUFFER);
        SendMessageW(tree, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.listFont()), FALSE);

        header.create(hwnd);
        header.onSearch = [this](const std::string &q) { send("search", Value(q)); };

        list = CreateWindowExW(0, WC_LISTVIEWW, L"",
                               WS_CHILD | WS_VISIBLE | WS_TABSTOP | LVS_REPORT | LVS_OWNERDATA | LVS_SHOWSELALWAYS,
                               0, 0, 10, 10, hwnd, nullptr, g_inst, nullptr);
        SendMessageW(list, WM_SETFONT, reinterpret_cast<WPARAM>(g_theme.listFont()), FALSE);
        ListView_SetExtendedListViewStyle(list, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_LABELTIP | LVS_EX_HEADERINALLVIEWS);
        // A small image list sets the row height (Tk rowheight).
        HIMAGELIST rowHeight = ImageList_Create(1, g_theme.rowHeight(), ILC_COLOR32, 1, 1);
        ListView_SetImageList(list, rowHeight, LVSIL_SMALL);
        SetWindowSubclass(list, listSubclass, 1, reinterpret_cast<DWORD_PTR>(this));

        details.create(hwnd);
        details.onCommand = [this](const std::string &key) {
            if (key == "toggle") sendCommand(enabledActions().count("resume") ? "resume" : "stop");
            else if (key == "more") sendCommand(enabledActions().count("details") ? "details" : "properties");
            else sendCommand(key);
        };

        restyle();
        rebuildColumns(true);
        buildTree();
        dialogs.create(hwnd);
        toolbar.layout();
        toolbar.refreshTooltips();
    }

    void buildMenus() {
        menuBar = CreateMenu();
        HMENU tasks = CreatePopupMenu();
        AppendMenuW(tasks, MF_STRING, ID_ADD, L"Add URL…\tCtrl+N");
        AppendMenuW(tasks, MF_STRING, ID_BATCH, L"Add batch download…\tCtrl+B");
        AppendMenuW(tasks, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(tasks, MF_STRING, ID_CLIP_VIDEO, L"Download link from clipboard\tCtrl+Alt+D");
        AppendMenuW(tasks, MF_STRING, ID_CLIP_AUDIO, L"Download audio from clipboard\tCtrl+Alt+M");
        AppendMenuW(tasks, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(tasks, MF_STRING, ID_PALETTE, L"Command palette…\tCtrl+K");
        AppendMenuW(tasks, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(tasks, MF_STRING, ID_HIDE, L"Hide window");
        AppendMenuW(tasks, MF_STRING, ID_EXIT, L"Exit");
        AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(tasks), L"&Tasks");

        fileMenu = CreatePopupMenu();
        AppendMenuW(fileMenu, MF_STRING, ID_OPEN, L"Open\tEnter");
        AppendMenuW(fileMenu, MF_STRING, ID_OPEN_FOLDER, L"Open folder");
        AppendMenuW(fileMenu, MF_STRING, ID_EDIT, L"Open in media editor");
        AppendMenuW(fileMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(fileMenu, MF_STRING, ID_OPEN_DOWNLOADS, L"Open download folder");
        AppendMenuW(fileMenu, MF_STRING, ID_CHOOSE_FOLDER, L"Choose download folder…");
        AppendMenuW(fileMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(fileMenu, MF_STRING, ID_EDITOR, L"Media editor…");
        AppendMenuW(fileMenu, MF_STRING, ID_SCREEN, L"Screen recorder…");
        AppendMenuW(fileMenu, MF_STRING, ID_AUDIO, L"Audio recorder (calls, meetings)…");
        AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(fileMenu), L"&File");

        downloadsMenu = CreatePopupMenu();
        AppendMenuW(downloadsMenu, MF_STRING, ID_RESUME, L"Resume");
        AppendMenuW(downloadsMenu, MF_STRING, ID_STOP, L"Stop");
        AppendMenuW(downloadsMenu, MF_STRING, ID_CANCEL, L"Cancel download");
        AppendMenuW(downloadsMenu, MF_STRING, ID_REDOWNLOAD, L"Download again");
        AppendMenuW(downloadsMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(downloadsMenu, MF_STRING, ID_START_ALL, L"Start all");
        AppendMenuW(downloadsMenu, MF_STRING, ID_STOP_ALL, L"Stop all");
        AppendMenuW(downloadsMenu, MF_STRING, ID_RETRY_ALL, L"Retry all failed");
        AppendMenuW(downloadsMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(downloadsMenu, MF_STRING, ID_DELETE, L"Delete…\tDel");
        AppendMenuW(downloadsMenu, MF_STRING, ID_DELETE_DONE, L"Delete completed…");
        AppendMenuW(downloadsMenu, MF_STRING, ID_REMOVE_MISSING, L"Remove missing files from list");
        AppendMenuW(downloadsMenu, MF_STRING, ID_CLEANUP, L"Clean up list\u2026");
        AppendMenuW(downloadsMenu, MF_STRING, ID_CLEAN_PARTS, L"Clean up partial files…");
        AppendMenuW(downloadsMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(downloadsMenu, MF_STRING, ID_COPY_LINK, L"Copy download link\tCtrl+C");
        AppendMenuW(downloadsMenu, MF_STRING, ID_DETAILS, L"Error details…");
        AppendMenuW(downloadsMenu, MF_STRING, ID_PROPERTIES, L"Properties\tAlt+Enter");
        AppendMenuW(downloadsMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(downloadsMenu, MF_STRING, ID_SELECT_ALL, L"Select all\tCtrl+A");
        AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(downloadsMenu), L"&Downloads");

        viewMenu = CreatePopupMenu();
        AppendMenuW(viewMenu, MF_STRING, ID_VIEW_CATEGORIES, L"Categories");
        AppendMenuW(viewMenu, MF_STRING, ID_VIEW_DETAILS, L"Download details");
        columnsMenu = CreatePopupMenu();
        UINT id = ID_COLUMN_FIRST;
        for (const auto &c : kColumnDefs) {
            if (std::string(c.key) != "name") AppendMenuW(columnsMenu, MF_STRING, id, c.title);
            ++id;
        }
        AppendMenuW(columnsMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(columnsMenu, MF_STRING, ID_RESET_COLUMNS, L"Reset columns");
        AppendMenuW(viewMenu, MF_POPUP, reinterpret_cast<UINT_PTR>(columnsMenu), L"Columns");
        AppendMenuW(viewMenu, MF_SEPARATOR, 0, nullptr);
        themeMenu = CreatePopupMenu();
        AppendMenuW(themeMenu, MF_STRING, ID_THEME_LIGHT, L"Light");
        AppendMenuW(themeMenu, MF_STRING, ID_THEME_DARK, L"Dark");
        AppendMenuW(viewMenu, MF_POPUP, reinterpret_cast<UINT_PTR>(themeMenu), L"Theme");
        AppendMenuW(viewMenu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(viewMenu, MF_STRING, ID_REFRESH, L"Refresh\tF5");
        AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(viewMenu), L"&View");

        HMENU help = CreatePopupMenu();
        AppendMenuW(help, MF_STRING, ID_CHECK_UPDATES, L"Check for updates…");
        AppendMenuW(help, MF_STRING, ID_ABOUT, L"Updates && about");
        AppendMenuW(help, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(help, MF_STRING, ID_INSTALL_EXT, L"Install browser extension…");
        AppendMenuW(help, MF_STRING, ID_DIAGNOSTICS, L"Diagnostics…");
        AppendMenuW(help, MF_STRING, ID_LOG, L"Open app log");
        AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(help), L"&Help");
    }

    void refreshMenuStates() {
        auto actions = enabledActions();
        auto set = [](HMENU m, UINT id, bool on) { EnableMenuItem(m, id, MF_BYCOMMAND | (on ? MF_ENABLED : MF_GRAYED)); };
        set(downloadsMenu, ID_RESUME, actions.count("resume") > 0);
        set(downloadsMenu, ID_STOP, actions.count("stop") > 0);
        set(downloadsMenu, ID_CANCEL, actions.count("cancel") > 0);
        set(downloadsMenu, ID_REDOWNLOAD, actions.count("redownload") > 0);
        set(downloadsMenu, ID_DELETE, actions.count("delete") > 0);
        set(downloadsMenu, ID_COPY_LINK, actions.count("copy_link") > 0);
        set(downloadsMenu, ID_DETAILS, actions.count("details") > 0);
        set(downloadsMenu, ID_PROPERTIES, actions.count("properties") > 0);
        set(downloadsMenu, ID_START_ALL, totals["paused"].asInt() > 0);
        set(downloadsMenu, ID_STOP_ALL, totals["downloading"].asInt() + totals["queued"].asInt() > 0);
        set(downloadsMenu, ID_RETRY_ALL, totals["failed"].asInt() > 0);
        set(fileMenu, ID_OPEN, actions.count("open") > 0);
        set(fileMenu, ID_OPEN_FOLDER, actions.count("open_folder") > 0);
        set(fileMenu, ID_EDIT, actions.count("edit") > 0);
        CheckMenuItem(viewMenu, ID_VIEW_CATEGORIES, MF_BYCOMMAND | (navigation ? MF_CHECKED : MF_UNCHECKED));
        CheckMenuItem(viewMenu, ID_VIEW_DETAILS, MF_BYCOMMAND | (showDetails ? MF_CHECKED : MF_UNCHECKED));
        UINT id = ID_COLUMN_FIRST;
        for (const auto &c : kColumnDefs) {
            bool on = std::find(columns.begin(), columns.end(), c.key) != columns.end();
            CheckMenuItem(columnsMenu, id++, MF_BYCOMMAND | (on ? MF_CHECKED : MF_UNCHECKED));
        }
        CheckMenuRadioItem(themeMenu, ID_THEME_LIGHT, ID_THEME_DARK, g_theme.dark ? ID_THEME_DARK : ID_THEME_LIGHT, MF_BYCOMMAND);
    }

    void menuCommand(UINT id) {
        static const std::map<UINT, const char *> simple{
            {ID_ADD, "add"}, {ID_BATCH, "batch"}, {ID_PALETTE, "palette"}, {ID_EXIT, "quit"},
            {ID_OPEN, "open"}, {ID_OPEN_FOLDER, "open_folder"}, {ID_EDIT, "edit"}, {ID_OPEN_DOWNLOADS, "open_downloads"},
            {ID_CHOOSE_FOLDER, "choose_folder"}, {ID_EDITOR, "editor"}, {ID_SCREEN, "screen"}, {ID_AUDIO, "audio"},
            {ID_RESUME, "resume"}, {ID_STOP, "stop"}, {ID_CANCEL, "cancel"}, {ID_REDOWNLOAD, "redownload"},
            {ID_START_ALL, "resume_all"}, {ID_STOP_ALL, "pause_all"}, {ID_RETRY_ALL, "retry_all"}, {ID_DELETE, "delete"},
            {ID_DELETE_DONE, "delete_completed"}, {ID_REMOVE_MISSING, "remove_missing"}, {ID_CLEAN_PARTS, "clean_parts"}, {ID_CLEANUP, "cleanup"},
            {ID_COPY_LINK, "copy_link"}, {ID_DETAILS, "details"}, {ID_PROPERTIES, "properties"},
            {ID_VIEW_CATEGORIES, "navigation"}, {ID_VIEW_DETAILS, "details_pane"}, {ID_RESET_COLUMNS, "reset_columns"},
            {ID_REFRESH, "refresh"}, {ID_CHECK_UPDATES, "check_updates"}, {ID_ABOUT, "updates"},
            {ID_INSTALL_EXT, "install_extension"}, {ID_DIAGNOSTICS, "diagnostics"}, {ID_LOG, "open_log"}, {ID_HIDE, "hide"}};
        auto it = simple.find(id);
        if (it != simple.end()) {
            sendCommand(it->second);
            return;
        }
        switch (id) {
        case ID_CLIP_VIDEO: sendCommand("clipboard", Value("Video")); return;
        case ID_CLIP_AUDIO: sendCommand("clipboard", Value("Audio")); return;
        case ID_SELECT_ALL: selectAll(); return;
        case ID_THEME_LIGHT: send("theme", Value("Light")); return;
        case ID_THEME_DARK: send("theme", Value("Dark")); return;
        }
        if (id >= ID_COLUMN_FIRST && id < ID_COLUMN_FIRST + std::size(kColumnDefs))
            send("column", Value(kColumnDefs[id - ID_COLUMN_FIRST].key));
    }

    void toolbarCommand(const std::string &c) {
        if (c == "browsers") {
            browserMenu();
            return;
        }
        if (c == "update_badge") {
            send("update_badge");
            return;
        }
        sendCommand(c);
    }

    void browserMenu() {
        RECT r = toolbar.itemRect("browsers");
        POINT pt{r.left, r.bottom};
        ClientToScreen(toolbar.hwnd, &pt);
        HMENU m = CreatePopupMenu();
        std::wstring state = W(dialogs.extensionMenuStatus.empty() ? "Browser link is starting\xE2\x80\xA6" : dialogs.extensionMenuStatus);
        AppendMenuW(m, MF_STRING | MF_GRAYED, 1, state.c_str());
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, 2, L"Install browser extension…");
        AppendMenuW(m, MF_STRING, 3, L"Copy pairing token");
        AppendMenuW(m, MF_STRING, 4, L"Browser settings…");
        int chosen = TrackPopupMenu(m, TPM_RETURNCMD | TPM_LEFTBUTTON, pt.x, pt.y, 0, hwnd, nullptr);
        DestroyMenu(m);
        if (chosen == 2) send("install_extension");
        else if (chosen == 3) send("copy_pairing_token");
        else if (chosen == 4) send("browser_settings");
    }

    // ----------------------------------------------------------------------------- styling
    void restyle() {
        setDarkTitleBar(hwnd);
        allowDarkWindow(hwnd, g_theme.dark);
        enableDarkMenus(g_theme.dark);
        const Palette &p = g_theme.p;
        themeControl(tree);
        themeControl(list);
        HWND hdr = ListView_GetHeader(list);
        themeControl(hdr, L"DarkMode_ItemsView", L"Explorer");
        TreeView_SetBkColor(tree, p.SIDEBAR);
        TreeView_SetTextColor(tree, p.TEXT);
        TreeView_SetItemHeight(tree, g_theme.treeRowHeight());
        TreeView_SetIndent(tree, g_theme.dark ? S(16) : S(19));
        ListView_SetBkColor(list, p.SURFACE);
        ListView_SetTextBkColor(list, p.SURFACE);
        ListView_SetTextColor(list, p.TEXT);
        if (treeImages) ImageList_Destroy(treeImages);
        int size = S(16);
        treeImages = ImageList_Create(size, size, ILC_COLOR32, 12, 4);
        for (const char *name : {"all", "video", "audio", "compressed", "documents", "programs", "misc", "unfinished",
                                 "finished", "queues", "queue"}) {
            HBITMAP b = icons::render(size, [&](icons::Painter &d) { icons::paintTree(d, name, icons::rgbOf(p.MUTED)); });
            ImageList_Add(treeImages, b, nullptr);
            DeleteObject(b);
        }
        TreeView_SetImageList(tree, treeImages, TVSIL_NORMAL);
        RedrawWindow(hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME);
    }

    static int treeImage(const std::string &key) {
        static const std::vector<std::string> order{"all", "video", "audio", "compressed", "documents", "programs", "misc",
                                                    "unfinished", "finished", "queues", "queue"};
        for (size_t i = 0; i < order.size(); ++i)
            if (order[i] == key) return static_cast<int>(i);
        return 10;
    }

    // ------------------------------------------------------------------------------ tree
    struct TreeEntry {
        const char *key, *parent, *label, *icon;
    };

    void buildTree() {
        static const TreeEntry layout[] = {
            {"all", "", "All Downloads", "all"},     {"video", "all", "Video", "video"},
            {"audio", "all", "Audio", "audio"},      {"compressed", "all", "Compressed", "compressed"},
            {"documents", "all", "Documents", "documents"}, {"programs", "all", "Programs", "programs"},
            {"misc", "all", "Misc", "misc"},          {"unfinished", "", "Unfinished", "unfinished"},
            {"finished", "", "Finished", "finished"}, {"queues", "", "Queues", "queues"},
            {"queue:active", "queues", "Main queue", "queue"},
        };
        applying = true;
        TreeView_DeleteAllItems(tree);
        treeItems.clear();
        for (const auto &e : layout) {
            std::wstring label = L" " + W(e.label);
            TVINSERTSTRUCTW ins{};
            ins.hParent = *e.parent ? treeItems[e.parent] : TVI_ROOT;
            ins.hInsertAfter = TVI_LAST;
            ins.item.mask = TVIF_TEXT | TVIF_IMAGE | TVIF_SELECTEDIMAGE | TVIF_STATE;
            ins.item.pszText = &label[0];
            ins.item.iImage = ins.item.iSelectedImage = treeImage(e.icon);
            ins.item.state = TVIS_EXPANDED;
            ins.item.stateMask = TVIS_EXPANDED;
            treeItems[e.key] = TreeView_InsertItem(tree, &ins);
        }
        for (const char *k : {"all", "queues"}) TreeView_Expand(tree, treeItems[k], TVE_EXPAND);
        TreeView_SelectItem(tree, treeItems["all"]);
        applying = false;
    }

    std::string treeKey(HTREEITEM item) const {
        for (const auto &kv : treeItems)
            if (kv.second == item) return kv.first;
        return {};
    }

    void setTreeText(HTREEITEM item, const std::wstring &label) {
        wchar_t buf[512]{};
        TVITEMW it{};
        it.mask = TVIF_TEXT;
        it.hItem = item;
        it.pszText = buf;
        it.cchTextMax = 512;
        TreeView_GetItem(tree, &it);
        if (label == buf) return;
        std::wstring copy = label;
        it.pszText = &copy[0];
        TreeView_SetItem(tree, &it);
    }

    void updateTree(const Value &cats) {
        applying = true;
        std::set<std::string> batches;
        int index = 0;
        for (const auto &c : cats.items()) {
            std::string key = c["key"].asString(), count = c["count"].asString();
            std::wstring label = L" " + W(c["name"].asString());
            bool isBatch = key.rfind("queue:", 0) == 0 && key != "queue:active";
            if (isBatch) {
                label += L"  (" + W(count) + L")";
                batches.insert(key);
                if (!treeItems.count(key)) {
                    TVINSERTSTRUCTW ins{};
                    ins.hParent = treeItems["queues"];
                    ins.hInsertAfter = TVI_LAST;
                    ins.item.mask = TVIF_TEXT | TVIF_IMAGE | TVIF_SELECTEDIMAGE;
                    ins.item.pszText = &label[0];
                    ins.item.iImage = ins.item.iSelectedImage = treeImage("queue");
                    treeItems[key] = TreeView_InsertItem(tree, &ins);
                    TreeView_Expand(tree, treeItems["queues"], TVE_EXPAND);
                }
            } else if (!count.empty() && count != "0") {
                label += L"  (" + W(count) + L")";
            }
            if (treeItems.count(key)) setTreeText(treeItems[key], label);
            ++index;
        }
        for (auto it = treeItems.begin(); it != treeItems.end();) {
            bool isBatch = it->first.rfind("queue:", 0) == 0 && it->first != "queue:active";
            if (isBatch && !batches.count(it->first)) {
                TreeView_DeleteItem(tree, it->second);
                it = treeItems.erase(it);
            } else {
                ++it;
            }
        }
        if (treeItems.count(category) && TreeView_GetSelection(tree) != treeItems[category]) TreeView_SelectItem(tree, treeItems[category]);
        applying = false;
    }

    LRESULT treeCustomDraw(NMTVCUSTOMDRAW *d) {
        const Palette &p = g_theme.p;
        switch (d->nmcd.dwDrawStage) {
        case CDDS_PREPAINT: return CDRF_NOTIFYITEMDRAW;
        case CDDS_ITEMPREPAINT: {
            bool selected = (d->nmcd.uItemState & CDIS_SELECTED) != 0;
            if (g_theme.dark) {
                d->clrText = p.TEXT;
                d->clrTextBk = selected ? p.ACCENT_SOFT : p.SIDEBAR;
            } else if (selected) {
                bool focused = GetFocus() == tree;
                d->clrText = focused ? p.SELECT_TEXT : p.TEXT;
                d->clrTextBk = focused ? p.SELECT : hex(0xD9D9D9);
            }
            return CDRF_DODEFAULT;
        }
        }
        return CDRF_DODEFAULT;
    }

    // ------------------------------------------------------------------------------ list
    void rebuildColumns(bool force = false) {
        HWND hdr = ListView_GetHeader(list);
        int have = Header_GetItemCount(hdr);
        bool same = !force && have == static_cast<int>(columns.size());
        if (same) return;
        applying = true;
        while (Header_GetItemCount(hdr) > 0) ListView_DeleteColumn(list, 0);
        int index = 0;
        for (const auto &key : columns) {
            const ColumnDef *c = columnDef(key);
            if (!c) continue;
            std::wstring title = c->title;
            LVCOLUMNW col{};
            col.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_FMT;
            col.fmt = c->right ? LVCFMT_RIGHT : LVCFMT_LEFT;
            if (index == 0) col.fmt = LVCFMT_LEFT;
            col.pszText = &title[0];
            col.cx = S(std::max(48, widths[key].asInt(c->width)));
            ListView_InsertColumn(list, index++, &col);
        }
        applying = false;
        updateSortMarks();
    }

    void updateSortMarks() {
        HWND hdr = ListView_GetHeader(list);
        for (size_t i = 0; i < columns.size(); ++i) {
            const ColumnDef *c = columnDef(columns[i]);
            if (!c) continue;
            std::wstring title = c->title;
            if (columns[i] == sortKey) title += descending ? L" ▼" : L" ▲";
            wchar_t buf[128]{};
            HDITEMW item{};
            item.mask = HDI_TEXT;
            item.pszText = buf;
            item.cchTextMax = 128;
            Header_GetItem(hdr, static_cast<int>(i), &item);
            if (title != buf) {
                item.pszText = &title[0];
                Header_SetItem(hdr, static_cast<int>(i), &item);
            }
        }
    }

    void drawRow(NMLVCUSTOMDRAW *d) {
        const Palette &p = g_theme.p;
        int index = static_cast<int>(d->nmcd.dwItemSpec);
        if (index < 0 || index >= static_cast<int>(rows.size())) return;
        const Row &r = rows[static_cast<size_t>(index)];
        HDC dc = d->nmcd.hdc;
        bool selected = (ListView_GetItemState(list, index, LVIS_SELECTED) & LVIS_SELECTED) != 0;
        bool focused = GetFocus() == list;
        RECT full;
        ListView_GetItemRect(list, index, &full, LVIR_BOUNDS);
        RECT client;
        GetClientRect(list, &client);
        COLORREF back = p.SURFACE, ink = p.TEXT;
        if (r.tag == "failed") ink = p.DANGER;
        else if (r.tag == "missing") ink = p.WARNING;
        else if (r.tag == "dim") ink = p.MUTED;
        if (selected) {
            back = g_theme.dark ? p.SELECT : (focused ? p.SELECT : hex(0xD9D9D9));
            ink = g_theme.dark || focused ? p.SELECT_TEXT : p.TEXT;
        }
        RECT rowRc{full.left, full.top, std::max(full.right, client.right), full.bottom};
        fill(dc, rowRc, back);
        HFONT font = g_theme.listFont();
        int iconSize = S(16);
        for (size_t col = 0; col < columns.size(); ++col) {
            RECT cell;
            if (col == 0) {
                cell = full;
                RECT next;
                if (columns.size() > 1 && ListView_GetSubItemRect(list, index, 1, LVIR_BOUNDS, &next)) cell.right = next.left;
                else cell.right = full.left + ListView_GetColumnWidth(list, 0);
            } else {
                ListView_GetSubItemRect(list, index, static_cast<int>(col), LVIR_BOUNDS, &cell);
            }
            if (cell.right <= client.left || cell.left >= client.right) continue;
            const ColumnDef *c = columnDef(columns[col]);
            const std::wstring &value = cellText(r, columns[col]);
            RECT tr = cell;
            tr.left += S(6);
            tr.right -= S(6);
            if (col == 0) {
                int iy = (cell.top + cell.bottom - iconSize) / 2;
                icons::draw(dc, icons::g_cache.file(r.category, r.status, iconSize), cell.left + S(4), iy, iconSize);
                tr.left = cell.left + S(4) + iconSize + S(4);
            }
            UINT flags = DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX | (c && c->right && col > 0 ? DT_RIGHT : DT_LEFT);
            text(dc, value, tr, ink, font, flags);
        }
        if (selected && focused && (ListView_GetItemState(list, index, LVIS_FOCUSED) & LVIS_FOCUSED) && !g_theme.dark) {
            SetTextColor(dc, RGB(0, 0, 0));
            DrawFocusRect(dc, &rowRc);
        }
    }

    LRESULT listCustomDraw(NMLVCUSTOMDRAW *d) {
        switch (d->nmcd.dwDrawStage) {
        case CDDS_PREPAINT: return CDRF_NOTIFYITEMDRAW | CDRF_NOTIFYPOSTPAINT;
        case CDDS_ITEMPREPAINT:
            drawRow(d);
            return CDRF_SKIPDEFAULT;
        case CDDS_POSTPAINT:
            if (rows.empty() && !emptyTitle.empty()) {
                RECT rc;
                GetClientRect(list, &rc);
                RECT hr;
                GetWindowRect(ListView_GetHeader(list), &hr);
                int top = hr.bottom - hr.top;
                int line = measureWindow(L"Ag", g_theme.ui).cy;
                int cy = top + static_cast<int>((rc.bottom - top) * 0.42);
                RECT t1{0, cy - line - S(3), rc.right, cy};
                text(d->nmcd.hdc, W(emptyTitle), t1, g_theme.p.FAINT, g_theme.ui, DT_SINGLELINE | DT_CENTER | DT_BOTTOM | DT_NOPREFIX);
                RECT t2{0, cy + S(3), rc.right, cy + S(3) + line * 3};
                text(d->nmcd.hdc, W(emptyText), t2, g_theme.p.FAINT, g_theme.ui, DT_CENTER | DT_TOP | DT_NOPREFIX | DT_WORDBREAK);
            }
            return CDRF_DODEFAULT;
        }
        return CDRF_DODEFAULT;
    }

    LRESULT headerCustomDraw(NMCUSTOMDRAW *d) {
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
            bool hot = i == hotHeader;
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
            text(d->hdc, buf, tr, hot ? p.TEXT : p.MUTED, g_theme.headingFont(), DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX | align);
            return CDRF_SKIPDEFAULT;
        }
        case CDDS_POSTPAINT: {
            // The empty strip right of the last column.
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

    static LRESULT CALLBACK listSubclass(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR data) {
        auto *self = reinterpret_cast<App *>(data);
        switch (m) {
        case WM_NOTIFY: {
            auto *n = reinterpret_cast<NMHDR *>(l);
            if (n->hwndFrom == ListView_GetHeader(h) && n->code == NM_CUSTOMDRAW)
                return drawDarkHeader(h, reinterpret_cast<NMCUSTOMDRAW *>(l));
            break;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_KEYDOWN: {
            bool ctrl = GetKeyState(VK_CONTROL) < 0, alt = GetKeyState(VK_MENU) < 0, shift = GetKeyState(VK_SHIFT) < 0;
            if (w == VK_RETURN) {
                self->sendCommand(alt ? "properties" : "default");
                return 0;
            }
            if (w == VK_DELETE) {
                self->sendCommand("delete");
                return 0;
            }
            if (ctrl && w == 'A') {
                self->selectAll();
                return 0;
            }
            if (ctrl && w == 'C') {
                self->sendCommand(shift ? "copy_file" : "copy_link");
                return 0;
            }
            break;
        }
        case WM_SYSKEYDOWN:
            if (w == VK_RETURN) {
                self->sendCommand("properties");
                return 0;
            }
            break;
        case WM_CHAR:
            if (w == 1 || w == 3 || w == '\r') return 0;  // no beep for Ctrl+A / Ctrl+C / Enter
            break;
        case WM_MOUSEMOVE:
        case WM_MOUSELEAVE:
            break;
        }
        return DefSubclassProc(h, m, w, l);
    }

    void selectAll() {
        applying = true;
        ListView_SetItemState(list, -1, LVIS_SELECTED, LVIS_SELECTED);
        applying = false;
        sendCommand("selection");
    }

    void contextMenu(POINT screen, bool fromKeyboard) {
        POINT client = screen;
        ScreenToClient(list, &client);
        LVHITTESTINFO hit{};
        hit.pt = client;
        int item = fromKeyboard ? ListView_GetNextItem(list, -1, LVNI_FOCUSED) : ListView_HitTest(list, &hit);
        if (!fromKeyboard) {
            if (item >= 0 && !(ListView_GetItemState(list, item, LVIS_SELECTED) & LVIS_SELECTED)) {
                applying = true;
                ListView_SetItemState(list, -1, 0, LVIS_SELECTED);
                ListView_SetItemState(list, item, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
                applying = false;
                sendCommand("selection");
            } else if (item < 0) {
                applying = true;
                ListView_SetItemState(list, -1, 0, LVIS_SELECTED);
                applying = false;
                sendCommand("selection");
            }
        }
        HMENU m = CreatePopupMenu();
        std::vector<std::string> commands;
        auto add = [&](const std::wstring &label, const char *command, bool enabled = true) {
            commands.push_back(command);
            AppendMenuW(m, MF_STRING | (enabled ? 0 : MF_GRAYED), ID_POPUP_FIRST + commands.size() - 1, label.c_str());
        };
        auto sel = selectedRows();
        if (item < 0 || sel.empty()) {
            add(L"Add URL…\tCtrl+N", "add");
            add(L"Add batch download…\tCtrl+B", "batch");
            AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            add(L"Select all\tCtrl+A", "select_all");
            add(L"Refresh\tF5", "refresh");
        } else {
            auto actions = enabledActions();
            auto label = [&](const wchar_t *base, const char *key) {
                std::wstring s = base;
                int n = actionCounts[key].asInt();
                if (sel.size() > 1 && n > 1) s += L" (" + std::to_wstring(n) + L")";
                return s;
            };
            auto addAction = [&](const wchar_t *base, const char *key, const wchar_t *accel = L"") {
                std::wstring l = label(base, key);
                if (*accel) l += std::wstring(L"\t") + accel;
                add(l, key, actions.count(key) > 0);
            };
            if (sel.size() > 1) {
                std::wstring s = std::to_wstring(sel.size()) + L" items selected";
                AppendMenuW(m, MF_STRING | MF_GRAYED, 1, s.c_str());
                AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            }
            addAction(L"Open", "open", L"Enter");
            addAction(L"Open folder", "open_folder");
            addAction(L"Open in media editor", "edit");
            AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            addAction(L"Resume", "resume");
            addAction(L"Stop", "stop");
            addAction(L"Cancel download", "cancel");
            addAction(L"Download again", "redownload");
            AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            addAction(L"Copy file", "copy_file", L"Ctrl+Shift+C");
            addAction(L"Copy download link", "copy_link", L"Ctrl+C");
            addAction(L"Copy web page link", "copy_page");
            commands.back() = "copy_page";
            // copy_page / open_web_page are enabled by "open_page".
            EnableMenuItem(m, static_cast<UINT>(ID_POPUP_FIRST + commands.size() - 1), MF_BYCOMMAND | (actions.count("open_page") ? MF_ENABLED : MF_GRAYED));
            add(label(L"Open web page", "open_page"), "open_page", actions.count("open_page") > 0);
            AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            addAction(L"Delete…", "delete", L"Del");
            AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
            if (actions.count("details")) addAction(L"Error details…", "details");
            addAction(L"Properties", "properties", L"Alt+Enter");
        }
        int chosen = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, screen.x, screen.y, 0, hwnd, nullptr);
        DestroyMenu(m);
        if (chosen >= static_cast<int>(ID_POPUP_FIRST) && chosen - ID_POPUP_FIRST < static_cast<int>(commands.size())) {
            std::string c = commands[static_cast<size_t>(chosen - ID_POPUP_FIRST)];
            if (c == "select_all") selectAll();
            else sendCommand(c);
        }
    }

    // ---------------------------------------------------------------------------- layout
    void layout() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        if (rc.right <= 0 || rc.bottom <= 0) return;
        int th = toolbar.height();
        place(toolbar.hwnd, 0, 0, rc.right, th);
        int sh = status.height();
        place(status.hwnd, 0, rc.bottom - sh, rc.right, sh);
        int top = th + 1, bottom = rc.bottom - sh;
        int side = navigation ? std::max(S(160), std::min(sideWidth, static_cast<int>(rc.right) - S(460))) : 0;
        show(tree, navigation);
        if (navigation) place(tree, S(3), top, side - S(3), bottom - top - S(3));
        int x = navigation ? side + S(4) : 0, w = rc.right - x;
        int hh = header.height();
        place(header.hwnd, x, top, w, hh);
        header.layout();
        int dh = showDetails ? details.desiredHeight(w) : 0;
        show(details.hwnd, showDetails);
        if (showDetails) place(details.hwnd, x, bottom - dh, w, dh);
        place(list, x, top + hh, w, bottom - dh - top - hh);
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    RECT sashRect() const {
        RECT rc;
        GetClientRect(hwnd, &rc);
        RECT tr;
        GetWindowRect(tree, &tr);
        MapWindowPoints(nullptr, hwnd, reinterpret_cast<POINT *>(&tr), 2);
        return RECT{tr.right, tr.top, tr.right + S(4), tr.bottom + S(3)};
    }

    void paintBackground(HDC dc) {
        RECT rc;
        GetClientRect(hwnd, &rc);
        const Palette &p = g_theme.p;
        fill(dc, rc, p.BG);
        int th = toolbar.height();
        hline(dc, 0, rc.right, th, g_theme.dark ? p.BORDER : hex(0xA0A0A0));
    }

    // ----------------------------------------------------------------------------- state
    void applyRows(const Value &s) {
        std::vector<std::string> before = selectedKeys();
        const auto &items = s["rows"].items();
        std::vector<Row> next;
        next.reserve(items.size());
        for (const auto &v : items) {
            Row r;
            r.key = v["key"].asString();
            r.name = W(v["name"].asString());
            r.size = W(v["size_text"].asString());
            r.status = v["status"].asString();
            r.statusText = W(v["status_text"].asString());
            r.progress = v["progress"].asNumber();
            r.speed = W(v["speed_text"].asString());
            r.eta = W(v["eta_text"].asString());
            r.lastTry = W(v["last_try_text"].asString());
            r.added = W(v["added_text"].asString());
            r.website = W(v["website"].asString());
            r.quality = W(v["quality"].asString());
            r.saveTo = W(v["save_to"].asString());
            r.category = v["category"].asString();
            r.tag = v["tag"].asString();
            r.dragPath = v["dragPath"].asString();
            for (const auto &a : v["actions"].items()) r.actions.insert(a.asString());
            next.push_back(std::move(r));
        }
        bool sameOrder = next.size() == rows.size();
        for (size_t i = 0; sameOrder && i < next.size(); ++i) sameOrder = next[i].key == rows[i].key;
        int focusedIndex = ListView_GetNextItem(list, -1, LVNI_FOCUSED);
        std::string focusedKey = focusedIndex >= 0 && focusedIndex < static_cast<int>(rows.size()) ? rows[static_cast<size_t>(focusedIndex)].key : "";
        rows = std::move(next);

        int selRev = s["selectionRevision"].asInt();
        bool forced = selRev != selectionRevision;
        std::set<std::string> want;
        if (forced) for (const auto &k : s["selection"].items()) want.insert(k.asString());
        else want.insert(before.begin(), before.end());
        applying = true;
        ListView_SetItemCountEx(list, static_cast<int>(rows.size()), LVSICF_NOINVALIDATEALL | LVSICF_NOSCROLL);
        if (forced || !sameOrder) {
            ListView_SetItemState(list, -1, 0, LVIS_SELECTED | LVIS_FOCUSED);
            int first = -1;
            for (size_t i = 0; i < rows.size(); ++i) {
                if (want.count(rows[i].key)) {
                    ListView_SetItemState(list, static_cast<int>(i), LVIS_SELECTED, LVIS_SELECTED);
                    if (first < 0) first = static_cast<int>(i);
                }
                if (!forced && rows[i].key == focusedKey) ListView_SetItemState(list, static_cast<int>(i), LVIS_FOCUSED, LVIS_FOCUSED);
            }
            if (forced && first >= 0) {
                ListView_SetItemState(list, first, LVIS_FOCUSED, LVIS_FOCUSED);
                ListView_EnsureVisible(list, first, FALSE);
            }
        }
        InvalidateRect(list, nullptr, FALSE);
        applying = false;
        selectionRevision = selRev;
        auto now = selectedKeys();
        std::vector<std::string> asked;
        for (const auto &k : s["selection"].items()) asked.push_back(k.asString());
        if (now != asked) sendCommand("selection");
    }

    void apply(const Value &s) {
        haveState = true;
        applying = true;
        int vis = s["visibilityRevision"].asInt();
        if (vis != visibilityRevision) {
            visibilityRevision = vis;
            if (s["visible"].asBool()) {
                if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
                else if (!IsWindowVisible(hwnd)) ShowWindow(hwnd, SW_SHOW);
                forceForeground(hwnd);
            } else {
                ShowWindow(hwnd, SW_HIDE);
            }
        }
        bool nav = s["navigation"].asBool(true), det = s["details"].asBool(true);
        bool relayout = nav != navigation || det != showDetails;
        navigation = nav;
        showDetails = det;

        if (s.has("columns")) {
            std::vector<std::string> next;
            for (const auto &c : s["columns"].items()) next.push_back(c.asString());
            if (next.empty()) next.push_back("name");
            bool widthsChanged = s["widths"] != widths;
            widths = s["widths"];
            if (next != columns) {
                columns = next;
                rebuildColumns(true);
            } else if (widthsChanged) {
                for (size_t i = 0; i < columns.size(); ++i)
                    if (const ColumnDef *c = columnDef(columns[i]))
                        if (ListView_GetColumnWidth(list, static_cast<int>(i)) != S(std::max(48, widths[columns[i]].asInt(c->width))))
                            ListView_SetColumnWidth(list, static_cast<int>(i), S(std::max(48, widths[columns[i]].asInt(c->width))));
            }
        }
        std::string sk = s["sort"].asString();
        bool desc = s["descending"].asBool();
        if (sk != sortKey || desc != descending) {
            sortKey = sk;
            descending = desc;
            updateSortMarks();
        }
        if (s.has("categories")) {
            category = s["category"]["key"].asString();
            if (s["categories"] != categories) {
                categories = s["categories"];
                updateTree(categories);
            } else if (treeItems.count(category) && TreeView_GetSelection(tree) != treeItems[category]) {
                TreeView_SelectItem(tree, treeItems[category]);
            }
            header.set(s["category"]["name"].asString(), s["countText"].asString());
        }
        emptyTitle = s["emptyTitle"].asString();
        emptyText = s["emptyText"].asString();
        totals = s["totals"];
        actionCounts = s["actionCounts"];
        applying = false;
        if (s.has("rows")) applyRows(s);
        applying = true;

        const Value &tb = s["toolbar"];
        for (const char *k : {"resume", "stop", "delete", "start_all", "stop_all", "delete_done", "cleanup"}) toolbar.setEnabled(k, tb[k].asBool());
        const Value &badge = s["updateBadge"];
        COLORREF noteColor = g_theme.p.FAINT;
        std::string kind = badge["kind"].asString();
        if (kind == "success") noteColor = g_theme.p.SUCCESS;
        else if (kind == "danger") noteColor = g_theme.p.DANGER;
        else if (kind == "muted") noteColor = g_theme.p.MUTED;
        toolbar.setBadge(W(badge["note"].asString()), noteColor, badge["bold"].asBool(), W(badge["button"].asString().empty() ? std::string("Updates") : badge["button"].asString()),
                         badge["enabled"].asBool(true));

        status.set(W(s["status"].asString()), W(s["summaryText"].asString()), W(s["speedText"].asString()),
                   W(s["extension"].asString()), W("v" + s["version"].asString()));
        if (showDetails && details.apply(s["detailsPane"])) relayout = true;
        dialogs.extensionMenuStatus = s["extensionMenu"].asString();
        applying = false;
        if (relayout) layout();
        dialogs.apply(s);
    }

    void onSelectionChanged() {
        auto keys = selectedKeys();
        if (keys != lastSentSelection) {
            lastSentSelection = keys;
            sendCommand("selection");
        }
    }

    // ---------------------------------------------------------------------------- notify
    LRESULT notify(NMHDR *n) {
        if (n->hwndFrom == tree) {
            if (n->code == NM_CUSTOMDRAW) return treeCustomDraw(reinterpret_cast<NMTVCUSTOMDRAW *>(n));
            if (n->code == TVN_SELCHANGEDW && !applying) {
                auto *t = reinterpret_cast<NMTREEVIEWW *>(n);
                std::string key = treeKey(t->itemNew.hItem);
                if (key == "queues") key = "queue:active";
                if (!key.empty() && key != category) {
                    category = key;
                    send("category", Value(key));
                }
            }
            if (n->code == TVN_ITEMEXPANDINGW) return FALSE;
            return 0;
        }
        HWND hdr = ListView_GetHeader(list);
        if (n->hwndFrom == hdr) {
            if (n->code == HDN_ENDTRACKW || n->code == HDN_ENDTRACKA) {
                auto *h = reinterpret_cast<NMHEADERW *>(n);
                if (!applying && h->pitem && (h->pitem->mask & HDI_WIDTH) && h->iItem >= 0 && h->iItem < static_cast<int>(columns.size())) {
                    Value v = Value::object();
                    v.set("column", columns[static_cast<size_t>(h->iItem)]);
                    v.set("width", std::max(70, h->pitem->cxy * 96 / g_theme.dpi));
                    widths.set(columns[static_cast<size_t>(h->iItem)], Value(std::max(70, h->pitem->cxy * 96 / g_theme.dpi)));
                    send("width", v);
                }
            }
            return 0;
        }
        if (n->hwndFrom != list) return 0;
        switch (n->code) {
        case LVN_GETDISPINFOW: {
            auto *d = reinterpret_cast<NMLVDISPINFOW *>(n);
            if ((d->item.mask & LVIF_TEXT) && d->item.iItem >= 0 && d->item.iItem < static_cast<int>(rows.size()) &&
                d->item.iSubItem >= 0 && d->item.iSubItem < static_cast<int>(columns.size()) && d->item.cchTextMax > 0) {
                const std::wstring &v = cellText(rows[static_cast<size_t>(d->item.iItem)], columns[static_cast<size_t>(d->item.iSubItem)]);
                wcsncpy_s(d->item.pszText, static_cast<size_t>(d->item.cchTextMax), v.c_str(), _TRUNCATE);
            }
            return 0;
        }
        case LVN_ODFINDITEMW: {
            auto *f = reinterpret_cast<NMLVFINDITEMW *>(n);
            if (!(f->lvfi.flags & (LVFI_STRING | LVFI_PARTIAL)) || !f->lvfi.psz) return -1;
            std::wstring want = f->lvfi.psz;
            int start = std::max(0, f->iStart);
            int count = static_cast<int>(rows.size());
            for (int k = 0; k < count; ++k) {
                int i = (start + k) % count;
                if (_wcsnicmp(rows[static_cast<size_t>(i)].name.c_str(), want.c_str(), want.size()) == 0) return i;
            }
            return -1;
        }
        case NM_CUSTOMDRAW: return listCustomDraw(reinterpret_cast<NMLVCUSTOMDRAW *>(n));
        case LVN_ITEMCHANGED: {
            auto *c = reinterpret_cast<NMLISTVIEW *>(n);
            if (!applying && (c->uChanged & LVIF_STATE) && ((c->uOldState ^ c->uNewState) & LVIS_SELECTED))
                SetTimer(hwnd, TIMER_SELECTION, 30, nullptr);
            return 0;
        }
        case LVN_ODSTATECHANGED:
            if (!applying) SetTimer(hwnd, TIMER_SELECTION, 30, nullptr);
            return 0;
        case LVN_BEGINDRAG: {
            std::vector<std::wstring> files;
            for (const Row *r : selectedRows())
                if (!r->dragPath.empty()) files.push_back(W(r->dragPath));
            if (!files.empty()) dialogs.dragFiles(files);
            return 0;
        }
        case LVN_COLUMNCLICK: {
            auto *c = reinterpret_cast<NMLISTVIEW *>(n);
            if (c->iSubItem >= 0 && c->iSubItem < static_cast<int>(columns.size())) send("sort", Value(columns[static_cast<size_t>(c->iSubItem)]));
            return 0;
        }
        case NM_DBLCLK: {
            auto *a = reinterpret_cast<NMITEMACTIVATE *>(n);
            if (a->iItem >= 0) {
                KillTimer(hwnd, TIMER_SELECTION);
                onSelectionChanged();
                sendCommand("default");
            }
            return 0;
        }
        }
        return 0;
    }

    // ------------------------------------------------------------------------------ proc
    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l);
};

inline App g_app;

inline LRESULT CALLBACK App::proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    App &a = g_app;
    switch (m) {
    case WM_CREATE: return 0;
    case WM_STATE: {
        std::unique_ptr<Value> state = takeLatestState();
        if (state) a.apply(*state);
        return 0;
    }
    case WM_INPUT_CLOSED:
        DestroyWindow(h);
        return 0;
    case WM_APP + 9: {
        std::unique_ptr<Value> req(reinterpret_cast<Value *>(l));
        if (req) Dialogs::runFileDialog(*req);
        return 0;
    }
    case WM_SIZE:
        if (a.list) a.layout();
        return 0;
    case WM_GETMINMAXINFO: {
        auto *mm = reinterpret_cast<MINMAXINFO *>(l);
        mm->ptMinTrackSize.x = S(760);
        mm->ptMinTrackSize.y = S(480);
        return 0;
    }
    case WM_ERASEBKGND:
        a.paintBackground(reinterpret_cast<HDC>(w));
        return 1;
    case WM_CLOSE:
        send("close");
        return 0;
    case WM_ACTIVATE:
        if (LOWORD(w) != WA_INACTIVE) send("focus");
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    case WM_TIMER:
        if (w == TIMER_SELECTION) {
            KillTimer(h, TIMER_SELECTION);
            a.onSelectionChanged();
        }
        return 0;
    case WM_INITMENUPOPUP:
        a.refreshMenuStates();
        return 0;
    case WM_COMMAND:
        if (HIWORD(w) == 0 && l == 0) {
            a.menuCommand(LOWORD(w));
            return 0;
        }
        break;
    case WM_NOTIFY:
        return a.notify(reinterpret_cast<NMHDR *>(l));
    case WM_CONTEXTMENU: {
        HWND from = reinterpret_cast<HWND>(w);
        if (from == a.list) {
            POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
            bool keyboard = pt.x == -1 && pt.y == -1;
            if (keyboard) {
                int i = ListView_GetNextItem(a.list, -1, LVNI_FOCUSED);
                RECT r{};
                if (i >= 0) ListView_GetItemRect(a.list, i, &r, LVIR_BOUNDS);
                else GetClientRect(a.list, &r);
                pt = {r.left + S(40), r.top + (i >= 0 ? (r.bottom - r.top) : S(40))};
                ClientToScreen(a.list, &pt);
            }
            a.contextMenu(pt, keyboard);
            return 0;
        }
        break;
    }
    case WM_SETCURSOR: {
        POINT pt;
        GetCursorPos(&pt);
        ScreenToClient(h, &pt);
        RECT sash = a.sashRect();
        if (a.navigation && PtInRect(&sash, pt)) {
            SetCursor(LoadCursorW(nullptr, IDC_SIZEWE));
            return TRUE;
        }
        break;
    }
    case WM_LBUTTONDOWN: {
        POINT pt{GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        RECT sash = a.sashRect();
        if (a.navigation && PtInRect(&sash, pt)) {
            a.dragging = pt.x - a.sideWidth;
            SetCapture(h);
        }
        return 0;
    }
    case WM_MOUSEMOVE:
        if (GetCapture() == h) {
            int x = GET_X_LPARAM(l);
            a.sideWidth = std::max(S(160), x - a.dragging);
            a.layout();
            UpdateWindow(h);
        }
        return 0;
    case WM_LBUTTONUP:
        if (GetCapture() == h) {
            ReleaseCapture();
            send("sidebar_width", Value(a.sideWidth * 96 / g_theme.dpi));
        }
        return 0;
    case WM_SETTINGCHANGE:
        break;
    }
    LRESULT out = 0;
    if (handleControlMessage(h, m, w, l, &out, g_theme.p.BG)) return out;
    return DefWindowProcW(h, m, w, l);
}

}  // namespace md
