#pragma once

#include <QDialog>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QVBoxLayout>

#include <functional>

class UpdateDialog final : public QDialog {
public:
    using Send = std::function<void(const QString &)>;

    UpdateDialog(QWidget *parent, const Send &send) : QDialog(parent) {
        setWindowTitle("Media Downloader updates");
        setWindowModality(Qt::WindowModal);
        resize(570, 235);
        auto *body = new QVBoxLayout(this);
        body->setContentsMargins(18, 18, 18, 14);
        headline = addLabel(body);
        headline->setObjectName("title");
        detail = addLabel(body);
        notes = addLabel(body);
        progress = new QProgressBar;
        progress->setRange(0, 1000);
        progress->setTextVisible(false);
        body->addWidget(progress);
        body->addStretch();

        auto *actions = new QHBoxLayout;
        body->addLayout(actions);
        check = addButton(actions, "Check for updates", "update_check", send);
        install = addButton(actions, "Update now", "update_install", send);
        install->setObjectName("primary");
        later = addButton(actions, "Later", "update_later", send);
        cancel = addButton(actions, "Cancel download", "update_cancel", send);
        release = addButton(actions, "Open release", "update_release", send);
        actions->addStretch();
        auto *close = new QPushButton("Close");
        actions->addWidget(close);
        connect(close, &QPushButton::clicked, this, &QDialog::reject);
        connect(this, &QDialog::rejected, this, [send] { send("update_close"); });
    }

    void apply(const QJsonObject &state) {
        if (!state.value("visible").toBool()) {
            hide();
            return;
        }
        headline->setText(state.value("headline").toString());
        detail->setText(state.value("detail").toString());
        notes->setText(state.value("notes").toString());
        notes->setVisible(!notes->text().isEmpty());
        progress->setValue(state.value("progress").toInt());
        progress->setVisible(state.value("downloading").toBool() || progress->value() > 0);
        check->setEnabled(state.value("canCheck").toBool());
        install->setEnabled(state.value("canInstall").toBool());
        install->setText(state.value("installText").toString());
        later->setVisible(state.value("canSnooze").toBool());
        cancel->setVisible(state.value("canCancel").toBool());
        release->setText(state.value("releaseText").toString());
        if (!isVisible())
            show();
    }

private:
    static QLabel *addLabel(QVBoxLayout *layout) {
        auto *label = new QLabel;
        label->setWordWrap(true);
        label->setTextFormat(Qt::PlainText);
        layout->addWidget(label);
        return label;
    }

    QPushButton *addButton(QHBoxLayout *layout, const QString &text, const QString &command, const Send &send) {
        auto *button = new QPushButton(text);
        layout->addWidget(button);
        connect(button, &QPushButton::clicked, this, [send, command] { send(command); });
        return button;
    }

    QLabel *headline{}, *detail{}, *notes{};
    QPushButton *check{}, *install{}, *later{}, *cancel{}, *release{};
    QProgressBar *progress{};
};
