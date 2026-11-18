using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace LiteRecorder
{
    class MainForm : Form
    {
        readonly Settings cfg = Settings.Load();
        readonly RecordButton btn = new RecordButton();
        readonly Label timeLbl = new Label();
        readonly Label status = new Label();
        readonly CheckBox micChk = new CheckBox();
        readonly CheckBox sysChk = new CheckBox();
        readonly ComboBox micBox = new ComboBox();
        readonly ComboBox sysBox = new ComboBox();
        readonly LevelBar micBar = new LevelBar();
        readonly LevelBar sysBar = new LevelBar();
        readonly ComboBox qual = new ComboBox();
        readonly Label folderLbl = new Label();
        readonly Button folderBtn = new Button();
        readonly Button pinBtn = new Button();
        readonly Timer ui = new Timer();
        Source micSrc, sysSrc;
        string micErr, sysErr;
        Recorder rec;
        string lastSaved, idleNote;
        int tick;
        bool filling;

        public MainForm()
        {
            Text = "Lite Recorder";
            Font = new Font("Segoe UI", 9f);
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            ClientSize = new Size(320, 480);
            MinimumSize = new Size(336, 519);
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            TopMost = cfg.Pinned;

            pinBtn.FlatStyle = FlatStyle.Flat; pinBtn.FlatAppearance.BorderSize = 0;
            pinBtn.Cursor = Cursors.Hand; pinBtn.Size = new Size(96, 26);
            pinBtn.Click += delegate { cfg.Pinned = !cfg.Pinned; TopMost = cfg.Pinned; cfg.Save(); UpdatePin(); };
            UpdatePin();

            btn.Click += delegate { Toggle(); };

            timeLbl.Text = "00:00:00"; timeLbl.Font = new Font("Segoe UI Semibold", 22f);
            timeLbl.TextAlign = ContentAlignment.MiddleCenter; timeLbl.AutoSize = false;
            status.ForeColor = Theme.Dim; status.TextAlign = ContentAlignment.MiddleCenter; status.AutoSize = false;
            status.Click += delegate { if (lastSaved != null && File.Exists(lastSaved)) Process.Start("explorer.exe", "/select,\"" + lastSaved + "\""); };

            foreach (CheckBox c in new CheckBox[] { micChk, sysChk })
            {
                c.AutoSize = false; c.Size = new Size(76, 24); c.FlatStyle = FlatStyle.Flat; c.Cursor = Cursors.Hand;
            }
            micChk.Text = "Mic"; micChk.Checked = cfg.Mic;
            sysChk.Text = "PC sound"; sysChk.Checked = cfg.Sys;
            micChk.CheckedChanged += delegate { cfg.Mic = micChk.Checked; cfg.Save(); RestartMonitors(); };
            sysChk.CheckedChanged += delegate { cfg.Sys = sysChk.Checked; cfg.Save(); RestartMonitors(); };

            foreach (ComboBox c in new ComboBox[] { micBox, sysBox, qual })
            {
                c.DropDownStyle = ComboBoxStyle.DropDownList; c.FlatStyle = FlatStyle.Flat;
                c.BackColor = Theme.Panel; c.ForeColor = Theme.Text; c.DropDownWidth = 340;
            }
            FillDevices(micBox, true, cfg.MicId);
            FillDevices(sysBox, false, cfg.SysId);
            micBox.DropDown += delegate { FillDevices(micBox, true, SelectedId(micBox)); };
            sysBox.DropDown += delegate { FillDevices(sysBox, false, SelectedId(sysBox)); };
            micBox.SelectedIndexChanged += delegate { if (filling) return; cfg.MicId = SelectedId(micBox) ?? ""; cfg.Save(); RestartMonitors(); };
            sysBox.SelectedIndexChanged += delegate { if (filling) return; cfg.SysId = SelectedId(sysBox) ?? ""; cfg.Save(); RestartMonitors(); };

            qual.Items.AddRange(new object[] { "MP3 96 kbps", "MP3 128 kbps", "MP3 192 kbps" });
            qual.SelectedIndex = cfg.Kbps == 96 ? 0 : cfg.Kbps == 192 ? 2 : 1;
            qual.SelectedIndexChanged += delegate { cfg.Kbps = qual.SelectedIndex == 0 ? 96 : qual.SelectedIndex == 2 ? 192 : 128; cfg.Save(); };

            folderLbl.AutoEllipsis = true; folderLbl.ForeColor = Theme.Dim; folderLbl.TextAlign = ContentAlignment.MiddleLeft;
            folderBtn.Text = "Folder..."; folderBtn.FlatStyle = FlatStyle.Flat; folderBtn.FlatAppearance.BorderSize = 0;
            folderBtn.BackColor = Theme.Panel; folderBtn.ForeColor = Theme.Text; folderBtn.Cursor = Cursors.Hand;
            folderBtn.Click += delegate
            {
                using (FolderBrowserDialog d = new FolderBrowserDialog())
                {
                    d.Description = "Where should recordings be saved?";
                    d.SelectedPath = cfg.Folder;
                    if (d.ShowDialog(this) == DialogResult.OK) { cfg.Folder = d.SelectedPath; cfg.Save(); folderLbl.Text = cfg.Folder; }
                }
            };
            folderLbl.Text = cfg.Folder;

            Controls.AddRange(new Control[] { pinBtn, btn, timeLbl, status, micChk, micBox, micBar, sysChk, sysBox, sysBar, qual, folderLbl, folderBtn });
            Resize += delegate { DoLayout(); };
            DoLayout();

            ui.Interval = 60;
            ui.Tick += delegate { OnTick(); };
            ui.Start();
            FormClosing += OnClosing;
            Shown += delegate { RestartMonitors(); };
        }

        // ---- devices ----
        static string SelectedId(ComboBox box)
        {
            AudioDevice d = box.SelectedItem as AudioDevice;
            return d == null ? null : d.Id;
        }

        void FillDevices(ComboBox box, bool capture, string selectedId)
        {
            filling = true;
            box.Items.Clear();
            AudioDevice def = new AudioDevice();
            def.Name = capture ? "Default microphone" : "Default speakers";
            box.Items.Add(def);
            int sel = 0;
            foreach (AudioDevice d in AudioDevice.List(capture))
            {
                int i = box.Items.Add(d);
                if (!string.IsNullOrEmpty(selectedId) && d.Id == selectedId) sel = i;
            }
            box.SelectedIndex = sel;
            filling = false;
        }

        // (Re)open the inputs so the level bars work even when not recording.
        void RestartMonitors()
        {
            if (rec != null) return;
            if (micSrc != null) { micSrc.Stop(); micSrc = null; }
            if (sysSrc != null) { sysSrc.Stop(); sysSrc = null; }
            micErr = sysErr = null;
            if (micChk.Checked)
            {
                Source s = new Source(false, SelectedId(micBox));
                s.Start();
                if (s.Ok) micSrc = s; else micErr = "Microphone not available - choose another one";
            }
            if (sysChk.Checked)
            {
                Source s = new Source(true, SelectedId(sysBox));
                s.Start();
                if (s.Ok) sysSrc = s; else sysErr = "PC sound not available - choose another output";
            }
            micBar.Reset(); sysBar.Reset();
            UpdateIdleStatus();
        }

        void UpdateIdleStatus()
        {
            if (rec != null) return;
            if (micErr != null || sysErr != null) { status.ForeColor = Theme.Red; status.Text = micErr ?? sysErr; }
            else if (idleNote != null) { status.ForeColor = Theme.Green; status.Text = idleNote; }
            else { status.ForeColor = Theme.Dim; status.Text = micSrc != null ? "Ready - speak to test the microphone" : "Ready"; }
        }

        // ---- ui ----
        void UpdatePin()
        {
            pinBtn.Text = cfg.Pinned ? "Pinned on top" : "Pin on top";
            pinBtn.BackColor = cfg.Pinned ? Theme.Red : Theme.Panel;
            pinBtn.ForeColor = Color.White;
        }

        void DoLayout()
        {
            int w = ClientSize.Width, cx = w / 2;
            int y = 12;
            pinBtn.Location = new Point(w - pinBtn.Width - 12, y);
            y += 38;
            btn.Location = new Point(cx - btn.Width / 2, y); y += btn.Height + 4;
            timeLbl.SetBounds(0, y, w, 44); y += 44;
            status.SetBounds(12, y, w - 24, 32); y += 40;
            micChk.Location = new Point(20, y);
            micBox.SetBounds(100, y, w - 120, 26); y += 30;
            micBar.SetBounds(24, y, w - 48, 8); y += 22;
            sysChk.Location = new Point(20, y);
            sysBox.SetBounds(100, y, w - 120, 26); y += 30;
            sysBar.SetBounds(24, y, w - 48, 8); y += 24;
            qual.SetBounds(24, y, w - 48, 26); y += 36;
            folderBtn.SetBounds(w - 24 - 80, y, 80, 28);
            folderLbl.SetBounds(24, y, w - 24 - 80 - 32, 28);
        }

        static float Meter(int peak)
        {
            if (peak <= 0) return 0;
            double db = 20 * Math.Log10(peak / 32768.0);   // -60 dB .. 0 dB across the bar
            return (float)Math.Max(0, Math.Min(1, (db + 60) / 60));
        }

        void OnTick()
        {
            if (micSrc != null) micBar.SetLevel(Meter(micSrc.ReadLevel())); else micBar.Reset();
            if (sysSrc != null) sysBar.SetLevel(Meter(sysSrc.ReadLevel())); else sysBar.Reset();

            if (rec != null)
            {
                TimeSpan t = rec.Elapsed;
                timeLbl.Text = string.Format("{0:00}:{1:00}:{2:00}", (int)t.TotalHours, t.Minutes, t.Seconds);
                btn.Invalidate();
                return;
            }
            // idle: if a device vanished (unplugged headset...), reopen
            if (++tick % 20 == 0 && ((micSrc != null && micSrc.Dead) || (sysSrc != null && sysSrc.Dead))) RestartMonitors();
        }

        void Toggle()
        {
            if (rec == null) StartRecording(); else StopRecording();
        }

        void SetInputsEnabled(bool on)
        {
            micChk.Enabled = sysChk.Enabled = micBox.Enabled = sysBox.Enabled = qual.Enabled = folderBtn.Enabled = on;
        }

        void StartRecording()
        {
            try
            {
                RestartMonitors();   // pick up whatever Windows' default devices are right now
                if (micSrc == null && sysSrc == null)
                    throw new InvalidOperationException(micErr ?? sysErr ?? "Turn on Mic or PC sound first.");
                Directory.CreateDirectory(cfg.Folder);
                Recorder r = new Recorder();
                string basePath = Path.Combine(cfg.Folder, ".recording_" + Guid.NewGuid().ToString("N").Substring(0, 8));
                r.Start(basePath, micSrc, sysSrc, cfg.Kbps);
                rec = r;
                idleNote = null;
                btn.Recording = true;
                SetInputsEnabled(false);
                status.ForeColor = Theme.Red;
                string warn = micErr ?? sysErr ?? r.Warning;
                status.Text = warn != null ? "Recording... " + warn : "Recording...";
                btn.Invalidate();
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "Could not start recording:\n" + ex.Message, "Lite Recorder", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        void StopRecording()
        {
            if (rec == null) return;
            Recorder r = rec; rec = null;
            TimeSpan dur = r.Elapsed;
            string err = null;
            try { r.Stop(); err = r.WriteError; } catch (Exception ex) { err = ex.Message; }
            btn.Recording = false; btn.Invalidate();
            SetInputsEnabled(true);
            timeLbl.Text = "00:00:00";

            string silent = null;
            if (micSrc != null && micSrc.RecordedMax < 100) silent = "Microphone was silent! Pick another mic.";
            else if (micSrc == null && sysSrc != null && sysSrc.RecordedMax < 100) silent = "No PC sound was playing.";

            string info = string.Format("Length {0:00}:{1:00}:{2:00}", (int)dur.TotalHours, dur.Minutes, dur.Seconds);
            if (silent != null) info += "\n" + silent;
            DialogResult dr;
            using (SaveDialog dlg = new SaveDialog(info)) dr = dlg.ShowDialog(this);

            if (dr == DialogResult.No)
            {
                try { File.Delete(r.Path); } catch { }
                idleNote = null;
                UpdateIdleStatus();
                status.ForeColor = Theme.Dim; status.Text = "Discarded";
                return;
            }
            try
            {
                string ext = Path.GetExtension(r.Path);
                string stem = "Rec_" + DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss");
                string target = Path.Combine(cfg.Folder, stem + ext);
                for (int i = 2; File.Exists(target); i++) target = Path.Combine(cfg.Folder, stem + " (" + i + ")" + ext);
                File.Move(r.Path, target);
                lastSaved = target;
                status.ForeColor = silent != null ? Theme.Red : Theme.Green;
                status.Text = "Saved: " + Path.GetFileName(target) + (silent != null ? "\n" + silent : "") + (err != null ? " (" + err + ")" : "");
            }
            catch (Exception ex)
            {
                status.ForeColor = Theme.Red; status.Text = "Save failed: " + ex.Message;
            }
        }

        void OnClosing(object sender, FormClosingEventArgs e)
        {
            if (rec != null) StopRecording();
            if (micSrc != null) micSrc.Stop();
            if (sysSrc != null) sysSrc.Stop();
            cfg.Save();
        }
    }
}
