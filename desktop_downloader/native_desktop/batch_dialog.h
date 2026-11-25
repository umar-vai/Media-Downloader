#pragma once

#include <QApplication>
#include <QClipboard>
#include <QComboBox>
#include <QDialog>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QTimer>
#include <QVBoxLayout>

#include <functional>

class BatchDialog final : public QDialog {
public:
    using Send = std::function<void(const QString &, const QJsonValue &)>;

    BatchDialog(QWidget *parent, const Send &send) : QDialog(parent) {
        setWindowTitle("Batch downloads");
        setWindowModality(Qt::WindowModal);
        resize(595, 418);
        auto *layout = new QVBoxLayout(this);
        layout->setContentsMargins(18, 18, 18, 14);
        auto *heading = new QHBoxLayout;
        heading->addWidget(new QLabel("Links to download"));
        heading->addStretch();
        count = new QLabel;
        heading->addWidget(count);
        layout->addLayout(heading);

        links = new QPlainTextEdit;
        links->setPlaceholderText("Paste one link per line");
        layout->addWidget(links, 1);
        auto *refresh = new QTimer(this);
        refresh->setSingleShot(true);
        refresh->setInterval(120);
        connect(links, &QPlainTextEdit::textChanged, refresh, qOverload<>(&QTimer::start));
        connect(refresh, &QTimer::timeout, this, [this, send] { send("batch_links", links->toPlainText().left(2'000'000)); });

        auto *tools = new QHBoxLayout;
        auto *paste = new QPushButton("Paste");
        auto *load = new QPushButton("Load from file…");
        auto *clear = new QPushButton("Clear");
        tools->addWidget(paste);
        tools->addWidget(load);
        tools->addWidget(clear);
        tools->addStretch();
        layout->addLayout(tools);
        connect(paste, &QPushButton::clicked, this, [this] { append(QApplication::clipboard()->text()); });
        connect(clear, &QPushButton::clicked, links, &QPlainTextEdit::clear);
        connect(load, &QPushButton::clicked, this, [this] {
            const auto path = QFileDialog::getOpenFileName(this, "Load links from a text file", {},
                                                          "Text files (*.txt *.csv *.list *.md);;All files (*.*)");
            if (path.isEmpty())
                return;
            QFile file(path);
            if (file.open(QIODevice::ReadOnly))
                append(QString::fromUtf8(file.read(2'000'000)));
            else
                status->setText(file.errorString());
        });

        auto *options = new QFormLayout;
        options->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
        mode = new QComboBox;
        mode->addItems({"Video", "Audio"});
        quality = new QComboBox;
        quality->addItems({"Best available", "1080p", "720p", "480p", "360p"});
        format = new QComboBox;
        format->addItems({"MP3", "M4A", "FLAC", "OPUS", "WAV"});
        bitrate = new QComboBox;
        bitrate->addItems({"128", "192", "256", "320"});
        auto *audio = new QHBoxLayout;
        audio->addWidget(format);
        audio->addWidget(new QLabel("Bitrate (kbps)"));
        audio->addWidget(bitrate);
        options->addRow("Type", mode);
        options->addRow("Video quality", quality);
        options->addRow("Audio format", audio);
        layout->addLayout(options);
        connect(mode, &QComboBox::currentTextChanged, this, [this] { updateFormat(); });

        status = new QLabel;
        status->setWordWrap(true);
        status->setTextFormat(Qt::PlainText);
        layout->addWidget(status);
        auto *actions = new QHBoxLayout;
        actions->addStretch();
        auto *close = new QPushButton("Close");
        start = new QPushButton("Add to queue");
        start->setObjectName("primary");
        actions->addWidget(close);
        actions->addWidget(start);
        layout->addLayout(actions);
        connect(close, &QPushButton::clicked, this, &QDialog::reject);
        connect(this, &QDialog::rejected, this, [this, send] {
            send("batch_close", links->toPlainText().left(2'000'000));
        });
        connect(start, &QPushButton::clicked, this, [this, send] {
            start->setEnabled(false);
            send("batch_submit", QJsonObject{{"links", links->toPlainText().left(2'000'000)}, {"mode", mode->currentText()},
                    {"quality", quality->currentText()}, {"format", format->currentText()},
                    {"bitrate", bitrate->currentText()}});
        });
    }

    void apply(const QJsonObject &state) {
        if (!state.value("visible").toBool()) {
            hide();
            return;
        }
        if (!isVisible()) {
            links->setPlainText(state.value("links").toString());
            mode->setCurrentText(state.value("mode").toString());
            quality->setCurrentText(state.value("quality").toString());
            format->setCurrentText(state.value("format").toString());
            bitrate->setCurrentText(state.value("bitrate").toString());
            updateFormat();
            show();
            links->setFocus();
        }
        count->setText(state.value("count").toString());
        status->setText(state.value("status").toString());
        start->setEnabled(!links->toPlainText().trimmed().isEmpty());
    }

private:
    void updateFormat() {
        const bool video = mode->currentText() == "Video";
        quality->setEnabled(video);
        format->setEnabled(!video);
        bitrate->setEnabled(!video);
    }

    void append(const QString &text) {
        if (!text.trimmed().isEmpty())
            links->appendPlainText(text.trimmed());
        links->setFocus();
    }

    QPlainTextEdit *links{};
    QComboBox *mode{}, *quality{}, *format{}, *bitrate{};
    QLabel *count{}, *status{};
    QPushButton *start{};
};
