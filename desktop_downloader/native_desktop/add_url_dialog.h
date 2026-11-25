#pragma once

#include <QCheckBox>
#include <QComboBox>
#include <QDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QPixmap>
#include <QPushButton>
#include <QVBoxLayout>

#include <functional>

class AddUrlDialog final : public QDialog {
public:
    using Send = std::function<void(const QString &, const QJsonValue &)>;

    AddUrlDialog(QWidget *parent, Send send) : QDialog(parent) {
        setWindowTitle("Add URL");
        setWindowModality(Qt::WindowModal);
        resize(610, 390);
        auto *body = new QVBoxLayout(this);
        body->setContentsMargins(18, 18, 18, 14);
        auto *form = new QFormLayout;
        form->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
        body->addLayout(form);

        url = new QLineEdit;
        url->setPlaceholderText("https://");
        auto *address = new QHBoxLayout;
        address->addWidget(url, 1);
        analyze = new QPushButton("Analyze");
        address->addWidget(analyze);
        stop = new QPushButton("Stop");
        address->addWidget(stop);
        form->addRow("Link", address);
        connect(url, &QLineEdit::textEdited, this, [send](const QString &text) { send("add_url", text); });
        connect(url, &QLineEdit::returnPressed, analyze, &QPushButton::click);
        connect(analyze, &QPushButton::clicked, this, [this, send] { send("add_analyze", url->text()); });
        connect(stop, &QPushButton::clicked, this, [send] { send("add_stop", {}); });

        auto *info = new QWidget;
        auto *infoLayout = new QVBoxLayout(info);
        infoLayout->setContentsMargins(0, 2, 0, 8);
        thumbnail = new QLabel;
        thumbnail->setAlignment(Qt::AlignLeft);
        infoLayout->addWidget(thumbnail);
        title = textLabel(infoLayout);
        metadata = textLabel(infoLayout);
        form->addRow(info);

        mode = new QComboBox;
        mode->addItems({"Video", "Audio"});
        form->addRow("Type", mode);
        quality = new QComboBox;
        quality->addItems({"Best available", "1080p", "720p", "480p", "360p"});
        form->addRow("Video quality", quality);
        format = new QComboBox;
        format->addItems({"MP3", "M4A", "FLAC", "OPUS", "WAV"});
        form->addRow("Audio format", format);
        bitrate = new QComboBox;
        bitrate->addItems({"128", "192", "256", "320"});
        form->addRow("Bitrate (kbps)", bitrate);
        name = new QLineEdit;
        form->addRow("File name", name);
        folder = new QLineEdit;
        folder->setReadOnly(true);
        auto *browse = new QPushButton("Browse…");
        auto *reset = new QPushButton("Default");
        auto *location = new QHBoxLayout;
        location->addWidget(folder, 1);
        location->addWidget(browse);
        location->addWidget(reset);
        form->addRow("Save to", location);
        editor = new QCheckBox("Open in the media editor after downloading");
        form->addRow(editor);

        connect(browse, &QPushButton::clicked, this, [send] { send("choose_folder", {}); });
        connect(reset, &QPushButton::clicked, this, [send] { send("reset_folder", {}); });
        connect(mode, &QComboBox::currentTextChanged, this, [this] { updateFormat(); });

        status = textLabel(body);
        auto *buttons = new QHBoxLayout;
        buttons->addStretch();
        auto *close = new QPushButton("Cancel");
        buttons->addWidget(close);
        download = new QPushButton("Download video");
        download->setObjectName("primary");
        download->setEnabled(false);
        buttons->addWidget(download);
        body->addLayout(buttons);
        connect(close, &QPushButton::clicked, this, &QDialog::reject);
        connect(this, &QDialog::rejected, this, [send] { send("add_close", {}); });
        connect(download, &QPushButton::clicked, this, [this, send] {
            send("add_submit", QJsonObject{
                {"url", url->text()}, {"name", name->text()}, {"mode", mode->currentText()},
                {"quality", quality->currentText()}, {"format", format->currentText()},
                {"bitrate", bitrate->currentText()}, {"edit", editor->isChecked()},
            });
            download->setEnabled(false);
        });
    }

    void apply(const QJsonObject &state) {
        if (!state.value("visible").toBool()) {
            hide();
            return;
        }
        if (!isVisible()) {
            url->setText(state.value("url").toString());
            mode->setCurrentText(state.value("mode").toString());
            quality->setCurrentText(state.value("quality").toString());
            format->setCurrentText(state.value("format").toString());
            bitrate->setCurrentText(state.value("bitrate").toString());
            editor->setChecked(state.value("edit").toBool());
            suggestion = state.value("name").toString();
            name->setText(suggestion);
            show();
            url->setFocus();
            updateFormat();
        }
        const auto suggestedName = state.value("name").toString();
        if (suggestedName != suggestion && url->text().trimmed() == state.value("url").toString().trimmed()) {
            suggestion = suggestedName;
            name->setText(suggestion);
        }
        const auto image = state.value("thumbnail").toString();
        if (image != imageData) {
            imageData = image;
            QPixmap picture;
            picture.loadFromData(QByteArray::fromBase64(image.toLatin1()));
            thumbnail->setPixmap(picture);
            thumbnail->setVisible(!picture.isNull());
        }
        folder->setText(state.value("folder").toString());
        title->setText(state.value("title").toString());
        metadata->setText(state.value("metadata").toString());
        status->setText(state.value("status").toString());
        const auto running = state.value("running").toBool();
        analyze->setEnabled(!running);
        stop->setEnabled(running);
        download->setEnabled(!running && state.value("ready").toBool() &&
                             url->text().trimmed() == state.value("url").toString().trimmed());
        direct = state.value("direct").toBool();
        updateFormat();
    }

private:
    static QLabel *textLabel(QVBoxLayout *layout) {
        auto *label = new QLabel;
        label->setTextFormat(Qt::PlainText);
        label->setWordWrap(true);
        layout->addWidget(label);
        return label;
    }

    void updateFormat() {
        const auto video = mode->currentText() == "Video";
        quality->setEnabled(video && !direct);
        format->setEnabled(!video && !direct);
        bitrate->setEnabled(!video && !direct);
        download->setText(direct ? "Download file" : video ? "Download video" : "Download audio");
    }

    QLineEdit *url{}, *name{}, *folder{};
    QComboBox *mode{}, *quality{}, *format{}, *bitrate{};
    QCheckBox *editor{};
    QPushButton *analyze{}, *stop{}, *download{};
    QLabel *thumbnail{}, *title{}, *metadata{}, *status{};
    QString suggestion, imageData;
    bool direct = false;
};
