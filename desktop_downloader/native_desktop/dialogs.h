// Everything the main window opens: the generic dialogs (forms.h), the Windows file pickers and
// dragging finished files out to other programs.
#pragma once

#include "forms.h"

namespace md {

// Dragging finished downloads out of the app: a minimal CF_HDROP data object.
class FileDrop final : public IDataObject {
public:
    explicit FileDrop(std::vector<std::wstring> files) : files_(std::move(files)) {}
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = --refs_;
        if (n == 0) delete this;
        return n;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (iid == IID_IUnknown || iid == IID_IDataObject) {
            *out = static_cast<IDataObject *>(this);
            AddRef();
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    HRESULT STDMETHODCALLTYPE GetData(FORMATETC *f, STGMEDIUM *m) override {
        if (QueryGetData(f) != S_OK) return DV_E_FORMATETC;
        size_t chars = 1;
        for (const auto &p : files_) chars += p.size() + 1;
        HGLOBAL mem = GlobalAlloc(GHND, sizeof(DROPFILES) + chars * sizeof(wchar_t));
        if (!mem) return E_OUTOFMEMORY;
        auto *drop = static_cast<DROPFILES *>(GlobalLock(mem));
        drop->pFiles = sizeof(DROPFILES);
        drop->fWide = TRUE;
        auto *at = reinterpret_cast<wchar_t *>(reinterpret_cast<BYTE *>(drop) + sizeof(DROPFILES));
        for (const auto &p : files_) {
            wmemcpy(at, p.c_str(), p.size() + 1);
            at += p.size() + 1;
        }
        GlobalUnlock(mem);
        m->tymed = TYMED_HGLOBAL;
        m->hGlobal = mem;
        m->pUnkForRelease = nullptr;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDataHere(FORMATETC *, STGMEDIUM *) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE QueryGetData(FORMATETC *f) override {
        return f->cfFormat == CF_HDROP && (f->tymed & TYMED_HGLOBAL) && f->dwAspect == DVASPECT_CONTENT ? S_OK : DV_E_FORMATETC;
    }
    HRESULT STDMETHODCALLTYPE GetCanonicalFormatEtc(FORMATETC *, FORMATETC *out) override {
        out->ptd = nullptr;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE SetData(FORMATETC *, STGMEDIUM *, BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumFormatEtc(DWORD direction, IEnumFORMATETC **out) override {
        if (direction != DATADIR_GET) return E_NOTIMPL;
        FORMATETC f{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        return SHCreateStdEnumFmtEtc(1, &f, out);
    }
    HRESULT STDMETHODCALLTYPE DAdvise(FORMATETC *, DWORD, IAdviseSink *, DWORD *) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE DUnadvise(DWORD) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumDAdvise(IEnumSTATDATA **) override { return OLE_E_ADVISENOTSUPPORTED; }

private:
    std::vector<std::wstring> files_;
    ULONG refs_ = 1;
};

class DropSource final : public IDropSource {
public:
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = --refs_;
        if (n == 0) delete this;
        return n;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (iid == IID_IUnknown || iid == IID_IDropSource) {
            *out = static_cast<IDropSource *>(this);
            AddRef();
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    HRESULT STDMETHODCALLTYPE QueryContinueDrag(BOOL escape, DWORD keys) override {
        if (escape) return DRAGDROP_S_CANCEL;
        return (keys & MK_LBUTTON) ? S_OK : DRAGDROP_S_DROP;
    }
    HRESULT STDMETHODCALLTYPE GiveFeedback(DWORD) override { return DRAGDROP_S_USEDEFAULTCURSORS; }

private:
    ULONG refs_ = 1;
};

class Dialogs {
public:
    std::string extensionMenuStatus;
    Value lastFileRequest;

    void create(HWND) {
        g_dragFiles = [this](const std::vector<std::wstring> &files) { dragFiles(files); };
    }

    void dragFiles(const std::vector<std::wstring> &files) {
        if (files.empty()) return;
        auto *data = new FileDrop(files);
        auto *source = new DropSource();
        DWORD effect = 0;
        DoDragDrop(data, source, DROPEFFECT_COPY, &effect);
        data->Release();
        source->Release();
    }

    void apply(const Value &s) {
        if (s.has("forms")) applyForms(s["forms"]);
        const Value &file = s["fileDialog"];
        if (file.isObject() && file["id"] != lastFileRequest["id"]) {
            lastFileRequest = file;
            // Shown after this state is applied, so the window keeps painting meanwhile.
            Value copy = file;
            PostMessageW(g_main, WM_APP + 9, 0, reinterpret_cast<LPARAM>(new Value(copy)));
        }
    }

    // Windows' own Open / Save / Folder pickers, owned by the window that asked.
    static void runFileDialog(const Value &req) {
        std::string kind = req["kind"].asString();
        HWND owner = g_main;
        std::string ownerForm = req["owner"].asString();
        if (!ownerForm.empty() && g_formsById.count(ownerForm)) owner = g_formsById[ownerForm]->hwnd;
        if (owner && !IsWindowVisible(owner)) owner = nullptr;
        Value result = Value::object();
        result.set("id", req["id"]);
        Value paths = Value::array();
        IFileDialog *dialog = nullptr;
        bool save = kind == "save";
        HRESULT hr = CoCreateInstance(save ? CLSID_FileSaveDialog : CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                      save ? IID_IFileSaveDialog : IID_IFileOpenDialog, reinterpret_cast<void **>(&dialog));
        if (SUCCEEDED(hr)) {
            DWORD options = 0;
            dialog->GetOptions(&options);
            options |= FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR;
            if (kind == "folder") options |= FOS_PICKFOLDERS | FOS_PATHMUSTEXIST;
            if (kind == "open" || kind == "open_many") options |= FOS_FILEMUSTEXIST;
            if (kind == "open_many") options |= FOS_ALLOWMULTISELECT;
            if (save) options |= FOS_OVERWRITEPROMPT;
            dialog->SetOptions(options);
            std::wstring title = W(req["title"].asString());
            if (!title.empty()) dialog->SetTitle(title.c_str());
            std::vector<std::wstring> names, specs;
            for (const auto &f : req["filters"].items()) {
                names.push_back(W(f.items().size() > 0 ? f.items()[0].asString() : std::string()));
                std::string pattern = f.items().size() > 1 ? f.items()[1].asString() : std::string("*.*");
                for (auto &c : pattern)
                    if (c == ' ') c = ';';
                specs.push_back(W(pattern));
            }
            std::vector<COMDLG_FILTERSPEC> filters;
            for (size_t i = 0; i < names.size(); ++i) filters.push_back({names[i].c_str(), specs[i].c_str()});
            if (!filters.empty()) dialog->SetFileTypes(static_cast<UINT>(filters.size()), filters.data());
            std::wstring ext = W(req["defaultExt"].asString());
            if (!ext.empty()) dialog->SetDefaultExtension(ext[0] == L'.' ? ext.c_str() + 1 : ext.c_str());
            std::wstring name = W(req["fileName"].asString());
            if (!name.empty()) dialog->SetFileName(name.c_str());
            std::wstring initial = W(req["initialDir"].asString());
            if (!initial.empty()) {
                IShellItem *folder = nullptr;
                if (SUCCEEDED(SHCreateItemFromParsingName(initial.c_str(), nullptr, IID_PPV_ARGS(&folder)))) {
                    dialog->SetFolder(folder);
                    folder->Release();
                }
            }
            if (SUCCEEDED(dialog->Show(owner))) {
                if (kind == "open_many") {
                    IShellItemArray *items = nullptr;
                    if (SUCCEEDED(static_cast<IFileOpenDialog *>(dialog)->GetResults(&items))) {
                        DWORD count = 0;
                        items->GetCount(&count);
                        for (DWORD i = 0; i < count; ++i) {
                            IShellItem *item = nullptr;
                            if (SUCCEEDED(items->GetItemAt(i, &item))) {
                                PWSTR path = nullptr;
                                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                                    paths.push(U8(path));
                                    CoTaskMemFree(path);
                                }
                                item->Release();
                            }
                        }
                        items->Release();
                    }
                } else {
                    IShellItem *item = nullptr;
                    if (SUCCEEDED(dialog->GetResult(&item))) {
                        PWSTR path = nullptr;
                        if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                            paths.push(U8(path));
                            CoTaskMemFree(path);
                        }
                        item->Release();
                    }
                }
            }
            dialog->Release();
        }
        result.set("paths", paths);
        send("file_dialog", result);
    }
};

}  // namespace md
