#pragma once

#include <QCheckBox>
#include <QComboBox>
#include <QDialog>
#include <QFileDialog>
#include <QFormLayout>
#include <QHash>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QTableWidget>
#include <QTabWidget>
#include <QVBoxLayout>

#include <functional>

class SettingsDialog final : public QDialog {
public:
    using Send = std::function<void(const QString &, const QJsonValue &)>;

    SettingsDialog(QWidget *parent, Send send) : QDialog(parent), send(std::move(send)) {
        setWindowTitle("Settings");
        setWindowModality(Qt::WindowModal);
        resize(670, 470);
        auto *layout = new QVBoxLayout(this);
        layout->setContentsMargins(16, 16, 16, 12);
        tabs = new QTabWidget;
        layout->addWidget(tabs, 1);

        auto *general = page("General");
        checkbox(general, "start_with_windows", "Start with Windows");
        checkbox(general, "close_to_tray", "Keep running in the system tray after closing the window");
        checkbox(general, "confirm_before_exit", "Confirm before exiting with active downloads");
        checkbox(general, "auto_analyze_links", "Analyze pasted links automatically");
        checkbox(general, "open_editor_after_download", "Open finished downloads in the editor");
        checkbox(general, "clipboard_hotkey", "Enable clipboard download shortcuts");
        checkbox(general, "capture_dialog", "Ask where to save browser downloads");
        combo(general, "theme", "Theme", {"Light", "Dark"});

        auto *downloads = page("Downloads");
        path(downloads, "download_dir", "Download folder", true);
        combo(downloads, "default_mode", "Default type", {"Video", "Audio"});
        combo(downloads, "video_quality", "Video quality", qualities);
        combo(downloads, "audio_format", "Audio format", {"MP3", "M4A", "FLAC", "OPUS", "WAV"});
        combo(downloads, "audio_quality", "Bitrate (kbps)", {"128", "192", "256", "320"});
        concurrency = new QSpinBox;
        concurrency->setRange(1, 10);
        downloads->addRow("Concurrent downloads", concurrency);

        auto *folders = page("Save To");
        rules = new QTableWidget(0, 3);
        rules->setHorizontalHeaderLabels({"Website", "Folder", "Quality"});
        rules->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
        rules->verticalHeader()->hide();
        rules->setSelectionBehavior(QAbstractItemView::SelectRows);
        rules->setSelectionMode(QAbstractItemView::SingleSelection);
        folders->addRow(rules);
        auto *ruleActions = new QHBoxLayout;
        folders->addRow(ruleActions);
        connect(makeButton(ruleActions, "Add rule"), &QPushButton::clicked, this, [this] {
            const int row = appendRule({});
            rules->setCurrentCell(row, 0);
            rules->editItem(rules->item(row, 0));
        });
        connect(makeButton(ruleActions, "Choose folder"), &QPushButton::clicked, this, [this] {
            const int row = rules->currentRow();
            if (row == -1)
                return;
            auto *cell = rules->item(row, 1);
            const auto folder = QFileDialog::getExistingDirectory(this, "Download folder", cell->text());
            if (!folder.isEmpty())
                cell->setText(folder);
        });
        connect(makeButton(ruleActions, "Remove rule"), &QPushButton::clicked, this, [this] {
            if (rules->currentRow() != -1)
                rules->removeRow(rules->currentRow());
        });
        ruleActions->addStretch();
        checkbox(folders, "organize_by_site", "Use a folder named after each website");

        auto *files = page("Files");
        checkbox(files, "skip_existing", "Skip existing files");
        checkbox(files, "fast_mode", "Download stream segments in parallel");
        checkbox(files, "embed_metadata", "Embed media metadata");
        checkbox(files, "embed_thumbnail", "Use the thumbnail as cover art");
        combo(files, "subtitles", "Subtitles", {"Off", "Sidecar file", "Embed in video"});
        field(files, "subtitle_langs", "Subtitle languages")->setPlaceholderText("en,bn");

        auto *browser = page("Browser");
        checkbox(browser, "extension_enabled", "Capture browser downloads");
        extension = new QLabel;
        browser->addRow(extension);
        browser->addRow(action("Install browser extension", "install_extension"));
        browser->addRow(action("Copy pairing token", "copy_pairing_token"));
        path(browser, "cookies_file", "Cookies file", false);
        combo(browser, "cookies_browser", "Read cookies from", {"None", "Firefox", "Chrome", "Edge", "Brave"});

        auto *updates = page("Updates");
        checkbox(updates, "auto_check_updates", "Check for updates on startup");
        checkbox(updates, "auto_download_updates", "Download verified updates automatically");
        updates->addRow(action("Check for updates", "settings_check_updates"));
        updates->addRow(action("Release notes", "update_release"));
        updates->addRow(action("Diagnostics", "diagnostics"));
        updates->addRow(action("Open app log", "open_log"));

        status = new QLabel;
        status->setWordWrap(true);
        status->setTextFormat(Qt::PlainText);
        layout->addWidget(status);
        auto *buttons = new QHBoxLayout;
        buttons->addStretch();
        auto *cancel = makeButton(buttons, "Cancel");
        auto *apply = makeButton(buttons, "Apply");
        auto *ok = makeButton(buttons, "OK");
        ok->setObjectName("primary");
        layout->addLayout(buttons);
        connect(cancel, &QPushButton::clicked, this, &QDialog::reject);
        connect(this, &QDialog::rejected, this, [this] { this->send("settings_close", {}); });
        connect(apply, &QPushButton::clicked, this, [this] { save(false); });
        connect(ok, &QPushButton::clicked, this, [this] { save(true); });
    }

    void apply(const QJsonObject &state) {
        if (!state.value("visible").toBool()) {
            hide();
            return;
        }
        const auto nextRevision = state.value("revision").toInt();
        if (revision != nextRevision || !isVisible()) {
            revision = nextRevision;
            const auto values = state.value("values").toObject();
            for (auto it = checks.cbegin(); it != checks.cend(); ++it)
                it.value()->setChecked(values.value(it.key()).toBool());
            for (auto it = fields.cbegin(); it != fields.cend(); ++it)
                it.value()->setText(values.value(it.key()).toString());
            for (auto it = combos.cbegin(); it != combos.cend(); ++it)
                it.value()->setCurrentText(values.value(it.key()).toString());
            concurrency->setValue(values.value("max_concurrent_downloads").toInt(6));
            rules->setRowCount(0);
            for (const auto &rule : state.value("rules").toArray())
                appendRule(rule.toArray());
            const auto tab = state.value("tab").toString();
            for (int index = 0; index < tabs->count(); ++index)
                if (tabs->tabText(index) == tab)
                    tabs->setCurrentIndex(index);
            show();
        }
        status->setText(state.value("status").toString());
        extension->setText(state.value("extension").toString());
    }

private:
    static QPushButton *makeButton(QHBoxLayout *layout, const QString &label) {
        auto *button = new QPushButton(label);
        layout->addWidget(button);
        return button;
    }

    QPushButton *action(const QString &label, const QString &command) {
        auto *button = new QPushButton(label);
        connect(button, &QPushButton::clicked, this, [this, command] {
            if (command == "settings_check_updates")
                save(true);
            send(command, {});
        });
        return button;
    }

    QFormLayout *page(const QString &label) {
        auto *widget = new QWidget;
        auto *form = new QFormLayout(widget);
        form->setContentsMargins(14, 14, 14, 14);
        form->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
        tabs->addTab(widget, label);
        return form;
    }

    void checkbox(QFormLayout *form, const QString &key, const QString &label) {
        auto *check = new QCheckBox(label);
        checks.insert(key, check);
        form->addRow(check);
    }

    QLineEdit *field(QFormLayout *form, const QString &key, const QString &label) {
        auto *input = new QLineEdit;
        fields.insert(key, input);
        form->addRow(label, input);
        return input;
    }

    void path(QFormLayout *form, const QString &key, const QString &label, bool directory) {
        auto *input = new QLineEdit;
        fields.insert(key, input);
        auto *row = new QHBoxLayout;
        row->addWidget(input, 1);
        auto *browse = makeButton(row, "Browse…");
        connect(browse, &QPushButton::clicked, this, [this, directory, input] {
            const auto value = directory ? QFileDialog::getExistingDirectory(this, "Choose a folder", input->text())
                         : QFileDialog::getOpenFileName(this, "Choose cookies file", input->text(), "Text files (*.txt)");
            if (!value.isEmpty())
                input->setText(value);
        });
        form->addRow(label, row);
    }

    void combo(QFormLayout *form, const QString &key, const QString &label, const QStringList &items) {
        auto *box = new QComboBox;
        box->addItems(items);
        combos.insert(key, box);
        form->addRow(label, box);
    }

    int appendRule(const QJsonArray &rule) {
        const int row = rules->rowCount();
        rules->insertRow(row);
        rules->setItem(row, 0, new QTableWidgetItem(rule.size() > 0 ? rule[0].toString() : QString()));
        rules->setItem(row, 1, new QTableWidgetItem(rule.size() > 1 ? rule[1].toString() : QString()));
        auto *choice = new QComboBox;
        choice->addItem("Default");
        choice->addItems(qualities);
        if (rule.size() > 2 && !rule[2].toString().isEmpty())
            choice->setCurrentText(rule[2].toString());
        rules->setCellWidget(row, 2, choice);
        return row;
    }

    void save(bool close) {
        QJsonObject values{{"max_concurrent_downloads", concurrency->value()}};
        for (auto it = checks.cbegin(); it != checks.cend(); ++it)
            values.insert(it.key(), it.value()->isChecked());
        for (auto it = fields.cbegin(); it != fields.cend(); ++it)
            values.insert(it.key(), it.value()->text());
        for (auto it = combos.cbegin(); it != combos.cend(); ++it)
            values.insert(it.key(), it.value()->currentText());
        QJsonArray rows;
        for (int index = 0; index < rules->rowCount(); ++index) {
            const auto *choice = qobject_cast<QComboBox *>(rules->cellWidget(index, 2));
            rows.append(QJsonArray{rules->item(index, 0)->text(), rules->item(index, 1)->text(), choice->currentText()});
        }
        send("settings_save", QJsonObject{{"values", values}, {"rules", rows}, {"close", close},
                                          {"tab", tabs->tabText(tabs->currentIndex())}});
    }

    Send send;
    QTabWidget *tabs{};
    QTableWidget *rules{};
    QSpinBox *concurrency{};
    QLabel *extension{}, *status{};
    QHash<QString, QCheckBox *> checks;
    QHash<QString, QLineEdit *> fields;
    QHash<QString, QComboBox *> combos;
    int revision = -1;
    const QStringList qualities{"Best available", "1080p", "720p", "480p", "360p"};
};
