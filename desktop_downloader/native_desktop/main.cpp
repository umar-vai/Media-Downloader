#include <QAbstractTableModel>
#include <QAction>
#include <QApplication>
#include <QCloseEvent>
#include <QColor>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QIcon>
#include <QItemSelection>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMainWindow>
#include <QMenu>
#include <QMimeData>
#include <QProgressBar>
#include <QPushButton>
#include <QResizeEvent>
#include <QShortcut>
#include <QSet>
#include <QSignalBlocker>
#include <QSplitter>
#include <QStatusBar>
#include <QTableView>
#include <QThread>
#include <QUrl>
#include <QVBoxLayout>

#include <array>
#include <iostream>
#include <string>

#include "add_url_dialog.h"
#include "batch_dialog.h"
#include "update_dialog.h"

namespace {

struct Column {
    const char *key;
    const char *label;
    const char *field;
    int width;
};

constexpr std::array columns{
    Column{"name", "File name", "name", 300},
    Column{"size", "Size", "size_text", 90},
    Column{"status", "Status", "status_text", 125},
    Column{"speed", "Transfer rate", "speed_text", 115},
    Column{"eta", "Time left", "eta_text", 106},
    Column{"last_try", "Last tried", "last_try_text", 150},
    Column{"added", "Date added", "added_text", 150},
    Column{"website", "Website", "website", 125},
    Column{"quality", "Quality", "quality", 90},
    Column{"save_to", "Save to", "save_to", 250},
};

class DownloadModel final : public QAbstractTableModel {
public:
    using QAbstractTableModel::QAbstractTableModel;

    int rowCount(const QModelIndex &parent) const override { return parent.isValid() ? 0 : rows.size(); }
    int columnCount(const QModelIndex &parent) const override { return parent.isValid() ? 0 : columns.size(); }

    QVariant headerData(int index, Qt::Orientation orientation, int role) const override {
        if (orientation == Qt::Horizontal && role == Qt::DisplayRole && index >= 0 && index < int(columns.size()))
            return QString::fromLatin1(columns[index].label);
        return {};
    }

    QVariant data(const QModelIndex &index, int role) const override {
        if (!index.isValid() || index.row() < 0 || index.row() >= rows.size())
            return {};
        const auto row = rows[index.row()].toObject();
        if (role == Qt::DisplayRole || role == Qt::ToolTipRole)
            return row.value(QLatin1StringView(columns[index.column()].field)).toString();
        if (role == Qt::ForegroundRole && index.column() == 2) {
            const auto status = row.value("status").toString();
            if (status == "complete")
                return QColor(dark ? "#5ce5b4" : "#08734d");
            if (status == "failed")
                return QColor(dark ? "#ffa9aa" : "#a7233a");
        }
        return {};
    }

    QJsonObject row(int index) const { return rows.at(index).toObject(); }
    QString key(int index) const { return row(index).value("key").toString(); }

    bool sameOrder(const QJsonArray &next) const {
        if (next.size() != rows.size())
            return false;
        for (qsizetype i = 0; i < rows.size(); ++i)
            if (next[i].toObject().value("key") != row(int(i)).value("key"))
                return false;
        return true;
    }

    void replace(const QJsonArray &next, bool sameOrder) {
        if (!sameOrder) {
            beginResetModel();
            rows = next;
            endResetModel();
            return;
        }
        if (next == rows)
            return;
        for (int i = 0; i < rows.size(); ++i) {
            if (rows.at(i) != next.at(i)) {
                rows[i] = next.at(i);
                emit dataChanged(index(i, 0), index(i, columns.size() - 1));
            }
        }
    }

    Qt::ItemFlags flags(const QModelIndex &index) const override {
        auto result = QAbstractTableModel::flags(index);
        if (index.isValid() && !row(index.row()).value("dragPath").toString().isEmpty())
            result |= Qt::ItemIsDragEnabled;
        return result;
    }

    QStringList mimeTypes() const override { return {"text/uri-list"}; }
    QMimeData *mimeData(const QModelIndexList &indexes) const override {
        QList<QUrl> urls;
        for (const auto &index : indexes) {
            const auto path = row(index.row()).value("dragPath").toString();
            const auto url = QUrl::fromLocalFile(path);
            if (!path.isEmpty() && !urls.contains(url))
                urls.append(url);
        }
        auto *mime = new QMimeData;
        mime->setUrls(urls);
        return mime;
    }

    bool dark = false;

private:
    QJsonArray rows;
};

class PipeReader final : public QThread {
    Q_OBJECT

Q_SIGNALS:
    void stateReceived(QJsonObject state);

private:
    void run() override {
        std::string line;
        while (std::getline(std::cin, line)) {
            const auto message = QJsonDocument::fromJson(QByteArray::fromStdString(line));
            if (message.isObject())
                emit stateReceived(message.object());
        }
    }
};

class Window final : public QMainWindow {
public:
    Window() : model(this) {
        setWindowTitle("Media Downloader");
        setMinimumSize(740, 470);
        resize(1110, 690);

        auto *central = new QWidget;
        auto *layout = new QVBoxLayout(central);
        layout->setContentsMargins(0, 0, 0, 0);
        layout->setSpacing(0);
        auto *splitter = new QSplitter(Qt::Horizontal);
        splitter->setChildrenCollapsible(false);
        splitter->setHandleWidth(1);
        layout->addWidget(splitter);
        setCentralWidget(central);
        addUrl = new AddUrlDialog(this, [this](const QString &command, const QJsonValue &value) {
            send(command, value);
        });

        batchDialog = new BatchDialog(this, [this](const QString &command, const QJsonValue &value) {
            send(command, value);
        });
        updateDialog = new UpdateDialog(this, [this](const QString &command) { send(command); });

        auto *sidebar = new QWidget;
        sidebar->setObjectName("sidebar");
        auto *nav = new QVBoxLayout(sidebar);
        nav->setContentsMargins(13, 18, 13, 14);
        auto *brand = new QLabel("Media Downloader");
        brand->setObjectName("brand");
        nav->addWidget(brand);
        auto *add = button("Add URL", "add", sidebar);
        add->setObjectName("primary");
        nav->addWidget(add);
        nav->addWidget(button("Batch download", "batch", sidebar));
        categories = new QListWidget;
        categories->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        categories->setAccessibleName("Download categories");
        nav->addWidget(categories, 1);
        nav->addWidget(button("Settings", "settings", sidebar));
        sidebar->setMinimumWidth(175);
        sidebar->setMaximumWidth(320);
        navigation = sidebar;
        splitter->addWidget(sidebar);

        auto *content = new QWidget;
        auto *body = new QVBoxLayout(content);
        body->setContentsMargins(20, 14, 20, 10);
        body->setSpacing(10);
        auto *toolbar = new QHBoxLayout;
        title = new QLabel("All Downloads");
        title->setObjectName("title");
        title->setTextFormat(Qt::PlainText);
        toolbar->addWidget(title);
        toolbar->addStretch(1);
        search = new QLineEdit;
        search->setPlaceholderText("Search downloads");
        search->setAccessibleName("Search downloads");
        search->setClearButtonEnabled(true);
        search->setMaximumWidth(310);
        toolbar->addWidget(search, 2);
        updates = button("Updates", "updates", content);
        toolbar->addWidget(updates);
        body->addLayout(toolbar);

        auto *actions = new QHBoxLayout;
        for (const auto &item : {qMakePair("Resume", "resume"), qMakePair("Pause", "stop"),
                                 qMakePair("Open", "open"), qMakePair("Show in folder", "open_folder")}) {
            auto *control = button(item.first, item.second, content);
            rowButtons.insert(item.second, control);
            control->setEnabled(false);
            actions->addWidget(control);
        }
        actions->addStretch(1);
        auto *tools = new QPushButton("Tools", content);
        auto *toolMenu = new QMenu(tools);
        addAction(toolMenu, "Media editor", "editor");
        addAction(toolMenu, "Screen recorder", "screen");
        addAction(toolMenu, "Audio recorder", "audio");
        toolMenu->addSeparator();
        addAction(toolMenu, "Add URL…", "add");
        addAction(toolMenu, "Batch download…", "batch");
        addAction(toolMenu, "Open downloads folder", "open_downloads");
        addAction(toolMenu, "Choose downloads folder…", "choose_folder");
        toolMenu->addSeparator();
        addAction(toolMenu, "Resume all", "resume_all");
        addAction(toolMenu, "Pause all", "pause_all");
        addAction(toolMenu, "Retry failed", "retry_all");
        addAction(toolMenu, "Remove completed…", "delete_completed");
        addAction(toolMenu, "Remove missing files", "remove_missing");
        addAction(toolMenu, "Clean partial files…", "clean_parts");
        toolMenu->addSeparator();
        addAction(toolMenu, "Install browser extension…", "install_extension");
        addAction(toolMenu, "Browser settings…", "browser_settings");
        addAction(toolMenu, "Diagnostics…", "diagnostics");
        addAction(toolMenu, "Open app log", "open_log");
        addAction(toolMenu, "Settings", "settings");
        auto *viewMenu = toolMenu->addMenu("View");
        auto *columnMenu = viewMenu->addMenu("Columns");
        for (const auto &column : columns) {
            if (QLatin1StringView(column.key) == "name")
                continue;
            auto *action = columnMenu->addAction(QString::fromLatin1(column.label));
            action->setCheckable(true);
            columnActions.insert(QString::fromLatin1(column.key), action);
            connect(action, &QAction::triggered, this, [this, column] { send("column", column.key); });
        }
        addAction(columnMenu, "Reset columns", "reset_columns");
        showNavigation = viewMenu->addAction("Categories");
        showNavigation->setCheckable(true);
        connect(showNavigation, &QAction::triggered, this, [this] { send("navigation"); });
        showDetails = viewMenu->addAction("Download details");
        showDetails->setCheckable(true);
        connect(showDetails, &QAction::triggered, this, [this] { send("details_pane"); });
        auto *themes = viewMenu->addMenu("Theme");
        connect(themes->addAction("Light"), &QAction::triggered, this, [this] { send("theme", "Light"); });
        connect(themes->addAction("Dark"), &QAction::triggered, this, [this] { send("theme", "Dark"); });
        addAction(toolMenu, "Exit", "quit");
        tools->setMenu(toolMenu);
        actions->addWidget(tools);
        body->addLayout(actions);

        downloadsSplit = new QSplitter(Qt::Horizontal);
        downloadsSplit->setChildrenCollapsible(false);
        downloadsSplit->setHandleWidth(1);
        body->addWidget(downloadsSplit, 1);
        auto *listPane = new QWidget;
        auto *listLayout = new QVBoxLayout(listPane);
        listLayout->setContentsMargins(0, 0, 0, 0);
        listLayout->setSpacing(0);
        downloadsSplit->addWidget(listPane);

        table = new QTableView;
        table->setModel(&model);
        table->setAccessibleName("Downloads and history");
        table->setSelectionMode(QAbstractItemView::ExtendedSelection);
        table->setSelectionBehavior(QAbstractItemView::SelectRows);
        table->setDragEnabled(true);
        table->setDragDropMode(QAbstractItemView::DragOnly);
        table->setShowGrid(false);
        table->setAlternatingRowColors(true);
        table->verticalHeader()->hide();
        table->verticalHeader()->setDefaultSectionSize(40);
        table->horizontalHeader()->setSectionsClickable(true);
        table->horizontalHeader()->setMinimumSectionSize(70);
        table->horizontalHeader()->setSectionResizeMode(QHeaderView::Interactive);
        for (int i = 0; i < int(columns.size()); ++i)
            table->setColumnWidth(i, columns[i].width);
        table->setContextMenuPolicy(Qt::CustomContextMenu);
        listLayout->addWidget(table, 1);

        empty = new QLabel("No downloads yet. Add a URL to get started.");
        empty->setObjectName("empty");
        empty->setAlignment(Qt::AlignCenter);
        empty->setWordWrap(true);
        listLayout->addWidget(empty, 1);

        details = new QWidget;
        details->setObjectName("details");
        auto *detailLayout = new QVBoxLayout(details);
        detailLayout->setContentsMargins(16, 14, 16, 14);
        detailTitle = new QLabel;
        detailTitle->setTextFormat(Qt::PlainText);
        detailTitle->setObjectName("detailTitle");
        detailTitle->setWordWrap(true);
        detailText = new QLabel;
        detailText->setTextFormat(Qt::PlainText);
        detailText->setWordWrap(true);
        progress = new QProgressBar;
        progress->setRange(0, 100);
        progress->setFixedHeight(10);
        progress->setTextVisible(false);
        detailLayout->addWidget(detailTitle);
        detailLayout->addWidget(detailText);
        detailLayout->addWidget(progress);
        detailLayout->addStretch();
        downloadsSplit->addWidget(details);
        downloadsSplit->setStretchFactor(0, 3);
        downloadsSplit->setStretchFactor(1, 1);
        downloadsSplit->setSizes({600, 220});
        splitter->addWidget(content);
        splitter->setSizes({193, 917});
        splitter->setStretchFactor(1, 1);

        version = new QLabel;
        extension = new QLabel;
        statusBar()->addPermanentWidget(extension, 0);
        statusBar()->addPermanentWidget(version, 0);

        connect(table->selectionModel(), &QItemSelectionModel::selectionChanged, this, [this] {
            updateActions();
            send("selection");
        });
        connect(table, &QTableView::doubleClicked, this, [this] { send("default"); });
        connect(table, &QTableView::customContextMenuRequested, this, &Window::contextMenu);
        connect(table->horizontalHeader(), &QHeaderView::sectionClicked, this, [this](int index) {
            send("sort", columns[index].key);
        });
        connect(table->horizontalHeader(), &QHeaderView::sectionResized, this, [this](int index, int, int width) {
            if (!applyingState)
                send("width", QJsonObject{{"column", columns[index].key}, {"width", width}});
        });
        connect(categories, &QListWidget::currentItemChanged, this, [this](QListWidgetItem *item) {
            if (item)
                send("category", item->data(Qt::UserRole).toString());
        });
        connect(search, &QLineEdit::textEdited, this, [this](const QString &value) { send("search", value); });
        auto *find = new QShortcut(QKeySequence::Find, this);
        connect(find, &QShortcut::activated, search, qOverload<>(&QLineEdit::setFocus));
        shortcut("Ctrl+N", "add");
        shortcut("Ctrl+B", "batch");
        shortcut("Ctrl+K", "palette");
        shortcut("Ctrl+Alt+D", "clipboard", "Video");
        shortcut("Ctrl+Alt+M", "clipboard", "Audio");
        shortcut("F5", "refresh");
        shortcut("Delete", "delete", {}, table);
        shortcut("Return", "default", {}, table);
        shortcut("Alt+Return", "properties", {}, table);
        shortcut("Ctrl+C", "copy_link", {}, table);
        shortcut("Ctrl+Shift+C", "copy_file", {}, table);
    }

    void apply(const QJsonObject &state) {
        const auto appearance = state.value("theme").toString();
        if (appearance != theme) {
            theme = appearance;
            model.dark = theme == "Dark";
            applyTheme(model.dark);
        }
        const auto visible = state.value("visibilityRevision").toInt();
        if (visible != visibilityRevision) {
            visibilityRevision = visible;
            if (state.value("visible").toBool()) {
                if (isMinimized())
                    showNormal();
                else
                    show();
                raise();
                activateWindow();
            } else {
                hide();
            }
        }
        const auto category = state.value("category").toObject();
        title->setText(category.value("name").toString());
        const auto nextCategories = state.value("categories").toArray();
        {
            const QSignalBlocker guard(categories);
            if (nextCategories != categoryRows) {
                categories->clear();
                for (const auto &entry : nextCategories) {
                    const auto item = entry.toObject();
                    const auto count = item.value("count").toString();
                    auto *row = new QListWidgetItem(item.value("name").toString()
                                                   + (count == "0" ? QString() : QString("  ·  ") + count),
                                                   categories);
                    row->setData(Qt::UserRole, item.value("key").toString());
                }
                categoryRows = nextCategories;
            }
            for (int i = 0; i < categories->count(); ++i) {
                if (categories->item(i)->data(Qt::UserRole).toString() == category.value("key").toString()) {
                    categories->setCurrentRow(i);
                    break;
                }
            }
        }

        const auto nextRows = state.value("rows").toArray();
        const bool sameOrder = model.sameOrder(nextRows);
        const auto revision = state.value("selectionRevision").toInt();
        const bool setSelection = revision != selectionRevision || !sameOrder;
        const auto nextSelection = revision != selectionRevision ? state.value("selection").toArray() : selectedKeys();
        {
            const QSignalBlocker guard(table->selectionModel());
            model.replace(nextRows, sameOrder);
            if (setSelection) {
                QItemSelection selection;
                QSet<QString> keys;
                for (const auto &key : nextSelection)
                    keys.insert(key.toString());
                for (int i = 0; i < model.rowCount({}); ++i)
                    if (keys.contains(model.key(i)))
                        selection.select(model.index(i, 0), model.index(i, columns.size() - 1));
                table->selectionModel()->select(selection, QItemSelectionModel::ClearAndSelect | QItemSelectionModel::Rows);
            }
        }
        selectionRevision = revision;
        if (setSelection && selectedKeys() != state.value("selection").toArray())
            send("selection");
        table->setVisible(!nextRows.isEmpty());
        empty->setVisible(nextRows.isEmpty());
        empty->setText(search->text().isEmpty() ? "No downloads in this view." : "No matching downloads.");

        applyingState = true;
        const auto shown = state.value("columns").toArray();
        const auto widths = state.value("widths").toObject();
        for (int i = 0; i < int(columns.size()); ++i) {
            const auto key = QString::fromLatin1(columns[i].key);
            const auto enabled = shown.contains(key);
            table->setColumnHidden(i, !enabled);
            if (enabled && widths != columnWidths)
                table->setColumnWidth(i, widths.value(key).toInt(columns[i].width));
            if (columnActions.contains(key))
                columnActions.value(key)->setChecked(enabled);
            if (state.value("sort").toString() == key)
                table->horizontalHeader()->setSortIndicator(i, state.value("descending").toBool()
                                                              ? Qt::DescendingOrder : Qt::AscendingOrder);
        }
        columnWidths = widths;
        table->horizontalHeader()->setSortIndicatorShown(true);
        applyingState = false;
        const bool navVisible = state.value("navigation").toBool(true);
        navigation->setVisible(navVisible);
        showNavigation->setChecked(navVisible);
        const bool detailsEnabled = state.value("details").toBool(true);
        showDetails->setChecked(detailsEnabled);

        updateActions();

        const auto summary = state.value("summary").toString();
        details->setVisible(detailsEnabled);
        detailTitle->setText(summary.isEmpty() ? "Download details" : summary);
        auto detail = state.value("selectedDetails").toString();
        if (summary.isEmpty())
            detail = "Select a download to see its status.";
        detailText->setText(detail);
        detailText->setVisible(!detail.isEmpty());
        progress->hide();
        const auto selected = table->selectionModel()->selectedRows();
        if (selected.size() == 1) {
            const auto row = model.row(selected.front().row());
            const int value = qBound(0, qRound(row.value("progress").toDouble() * 100), 100);
            progress->setValue(value);
            progress->setVisible(detailsEnabled && (value > 0 && value < 100));
        }

        const bool updateAvailable = state.value("updateAvailable").toBool();
        updates->setText(updateAvailable ? "Install update" : "Updates");
        updates->setToolTip(state.value("updates").toString());
        version->setText("v" + state.value("version").toString());
        extension->setText(state.value("extension").toString());
        const auto totals = state.value("totals").toObject();
        const auto status = state.value("status").toString();
        addUrl->apply(state.value("addForm").toObject());
        updateDialog->apply(state.value("updateDialog").toObject());
        batchDialog->apply(state.value("batchDialog").toObject());
        statusBar()->showMessage(status.isEmpty() ? QString("%1 downloading  ·  %2 queued  ·  %3 paused")
                                  .arg(totals.value("downloading").toInt()).arg(totals.value("queued").toInt())
                                  .arg(totals.value("paused").toInt()) : status);
    }

    int itemCount() const { return model.rowCount({}); }

    void send(const QString &command, const QJsonValue &value = {}) const {
        const QJsonObject message{{"command", command}, {"value", value}, {"keys", selectedKeys()}};
        std::cout << QJsonDocument(message).toJson(QJsonDocument::Compact).constData() << std::endl;
    }

protected:
    void resizeEvent(QResizeEvent *event) override {
        QMainWindow::resizeEvent(event);
        if (downloadsSplit)
            downloadsSplit->setOrientation(event->size().width() < 1000 ? Qt::Vertical : Qt::Horizontal);
    }

    void closeEvent(QCloseEvent *event) override {
        event->ignore();
        send("close");
    }
    void changeEvent(QEvent *event) override {
        QMainWindow::changeEvent(event);
        if (event->type() == QEvent::ActivationChange && isActiveWindow())
            send("focus");
    }

private:
    QJsonArray selectedKeys() const {
        QJsonArray keys;
        if (table)
            for (const auto &index : table->selectionModel()->selectedRows())
                keys.append(model.key(index.row()));
        return keys;
    }

    QPushButton *button(const QString &text, const QString &command, QWidget *parent) {
        auto *control = new QPushButton(text, parent);
        connect(control, &QPushButton::clicked, this, [this, command] { send(command); });
        return control;
    }

    QAction *addAction(QMenu *menu, const QString &label, const QString &command) {
        auto *action = menu->addAction(label);
        connect(action, &QAction::triggered, this, [this, command] { send(command); });
        return action;
    }

    void shortcut(const QString &key, const QString &command, const QJsonValue &value = {}, QWidget *parent = nullptr) {
        auto *binding = new QShortcut(QKeySequence(key), parent ? parent : this);
        binding->setContext(parent ? Qt::WidgetWithChildrenShortcut : Qt::WindowShortcut);
        connect(binding, &QShortcut::activated, this, [this, command, value] { send(command, value); });
    }

    void contextMenu(const QPoint &position) {
        const auto index = table->indexAt(position);
        if (index.isValid() && !table->selectionModel()->isRowSelected(index.row(), {}))
            table->selectionModel()->select(index, QItemSelectionModel::ClearAndSelect | QItemSelectionModel::Rows);
        QMenu menu(this);
        if (!index.isValid()) {
            addAction(&menu, "Add URL…", "add");
            addAction(&menu, "Refresh", "refresh");
        } else {
            const auto enabled = selectedActions();
            for (const auto &item : {qMakePair("Open", "open"), qMakePair("Show in folder", "open_folder"),
                                     qMakePair("Open in editor", "edit"), qMakePair("Resume", "resume"),
                                     qMakePair("Pause", "stop"), qMakePair("Cancel", "cancel"),
                                     qMakePair("Download again", "redownload"), qMakePair("Copy download link", "copy_link"),
                                     qMakePair("Copy file", "copy_file"), qMakePair("Error details", "details"),
                                     qMakePair("Properties", "properties"), qMakePair("Delete…", "delete")})
                if (enabled.contains(item.second))
                    addAction(&menu, item.first, item.second);
        }
        menu.exec(table->viewport()->mapToGlobal(position));
    }

    QSet<QString> selectedActions() const {
        QSet<QString> actions;
        const auto selected = table->selectionModel()->selectedRows();
        const QSet<QString> singleOnly{"open", "edit", "details", "properties"};
        for (const auto &index : selected) {
            for (const auto &item : model.row(index.row()).value("actions").toArray()) {
                const auto action = item.toString();
                if (selected.size() == 1 || !singleOnly.contains(action))
                    actions.insert(action);
            }
        }
        return actions;
    }

    void updateActions() {
        const auto enabled = selectedActions();
        for (auto it = rowButtons.cbegin(); it != rowButtons.cend(); ++it)
            it.value()->setEnabled(enabled.contains(it.key()));
    }

    void applyTheme(bool dark) {
        const QString background = dark ? "#0e1621" : "#e9eff4";
        const QString surface = dark ? "#17212b" : "#ffffff";
        const QString panel = dark ? "#202b36" : "#f2f6fa";
        const QString ink = dark ? "#edf4fa" : "#182533";
        const QString border = dark ? "#2c3c4b" : "#d7e3eb";
        const QString selection = dark ? "#2b5278" : "#dbeefb";
        const QString muted = dark ? "#a0b1c0" : "#4e6477";
        setStyleSheet(QString(R"(
            QWidget { background: %1; color: %4; font-size: 13px; }
            QWidget#sidebar { background: %2; }
            QLabel#title, QLabel#brand { font-size: 16px; font-weight: 650; }
            QLabel#detailTitle { font-weight: 650; }
            QLabel#empty { color: %7; }
            QPushButton, QLineEdit, QComboBox, QPlainTextEdit { background: %2; border: 1px solid %5; border-radius: 9px; padding: 7px 11px; }
            QPushButton:hover, QPushButton:focus, QLineEdit:focus { border-color: #298cc5; }
            QPushButton:disabled { color: %7; }
            QPushButton#primary { background: #086a9c; border-color: #086a9c; color: white; font-weight: 650; }
            QPushButton#primary:hover, QPushButton#primary:focus { background: #095783; }
            QListWidget, QTableView { background: %2; alternate-background-color: %1; border: 0;
                selection-background-color: %6; selection-color: %4; }
            QListWidget::item { padding: 8px 7px; border-radius: 6px; }
            QListWidget::item:hover:!selected { background: %3; }
            QHeaderView::section { background: %3; color: %4; border: 0; padding: 7px 4px; }
            QWidget#details, QMenu { background: %2; border: 1px solid %5; border-radius: 8px; }
            QMenu::item { padding: 6px 24px 6px 10px; }
            QMenu::item:selected { background: %6; }
            QProgressBar { background: %3; border: 0; border-radius: 5px; }
            QProgressBar::chunk { background: #298cc5; border-radius: 5px; }
            QSplitter::handle { background: %5; }
        )").arg(background, surface, panel, ink, border, selection, muted));
    }

    DownloadModel model;
    AddUrlDialog *addUrl{};
    UpdateDialog *updateDialog{};
    BatchDialog *batchDialog{};
    QWidget *navigation{};
    QSplitter *downloadsSplit{};
    QListWidget *categories{};
    QLabel *title{};
    QLineEdit *search{};
    QPushButton *updates{};
    QTableView *table{};
    QLabel *empty{};
    QWidget *details{};
    QLabel *detailTitle{};
    QLabel *detailText{};
    QProgressBar *progress{};
    QLabel *version{};
    QLabel *extension{};
    QAction *showNavigation{};
    QAction *showDetails{};
    QHash<QString, QAction *> columnActions;
    QHash<QString, QPushButton *> rowButtons;
    QJsonObject columnWidths;
    QJsonArray categoryRows;
    QString theme;
    int visibilityRevision = -1;
    int selectionRevision = -1;
    bool applyingState = false;
};

} // namespace

int main(int argc, char *argv[]) {
    QApplication app(argc, argv);
    app.setApplicationName("Media Downloader");
    app.setQuitOnLastWindowClosed(false);
    const auto args = app.arguments();
    const auto icon = args.indexOf("--icon");
    if (icon >= 0 && icon + 1 < args.size())
        app.setWindowIcon(QIcon(args.at(icon + 1)));
    Window window;
    if (args.contains("--self-test")) {
        window.apply(QJsonObject{{"visible", false}, {"theme", "Dark"},
                                {"rows", QJsonArray{QJsonObject{{"key", "q:smoke"}, {"name", "Smoke.mp4"}}}},
                                {"columns", QJsonArray{"name", "status"}}});
        return window.itemCount() == 1 ? 0 : 1;
    }

    PipeReader reader;
    QObject::connect(&reader, &PipeReader::stateReceived, &window, &Window::apply, Qt::QueuedConnection);
    QObject::connect(&reader, &QThread::finished, &app, &QApplication::quit);
    reader.start();
    window.send("ready");
    const int status = app.exec();
    reader.wait();
    return status;
}

#include "main.moc"
