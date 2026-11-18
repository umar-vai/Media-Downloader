using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;

namespace LiteRecorder
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }

    static class Theme
    {
        public static readonly Color Bg = Color.FromArgb(26, 27, 34);
        public static readonly Color Panel = Color.FromArgb(40, 42, 54);
        public static readonly Color Text = Color.FromArgb(235, 236, 242);
        public static readonly Color Dim = Color.FromArgb(145, 148, 165);
        public static readonly Color Red = Color.FromArgb(229, 56, 75);
        public static readonly Color Green = Color.FromArgb(70, 200, 120);
    }

    class Settings
    {
        public string Folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Lite Recorder");
        public int Kbps = 128;
        public bool Pinned = true;
        public bool Mic = true;
        public bool Sys = true;

        static string File_ { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "LiteRecorder", "settings.ini"); } }

        public static Settings Load()
        {
            Settings s = new Settings();
            try
            {
                foreach (string line in File.ReadAllLines(File_))
                {
                    int i = line.IndexOf('=');
                    if (i < 0) continue;
                    string k = line.Substring(0, i), v = line.Substring(i + 1);
                    if (k == "folder" && v.Length > 0) s.Folder = v;
                    else if (k == "kbps") { int n; if (int.TryParse(v, out n) && (n == 96 || n == 128 || n == 192)) s.Kbps = n; }
                    else if (k == "pinned") s.Pinned = v == "1";
                    else if (k == "mic") s.Mic = v == "1";
                    else if (k == "sys") s.Sys = v == "1";
                }
            }
            catch { }
            return s;
        }

        public void Save()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(File_));
                File.WriteAllLines(File_, new string[] {
                    "folder=" + Folder, "kbps=" + Kbps, "pinned=" + (Pinned ? "1" : "0"),
                    "mic=" + (Mic ? "1" : "0"), "sys=" + (Sys ? "1" : "0") });
            }
            catch { }
        }
    }

    // Big round record button with the microphone icon.
    class RecordButton : Control
    {
        public bool Recording;
        bool hover, down;
        public RecordButton()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            Cursor = Cursors.Hand;
            Size = new Size(130, 130);
        }
        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Parent != null ? Parent.BackColor : Theme.Bg);
            int d = Math.Min(Width, Height) - 14;
            RectangleF c = new RectangleF((Width - d) / 2f, (Height - d) / 2f, d, d);
            if (Recording)
            {
                double t = (DateTime.Now.Ticks / 10000 % 1400) / 1400.0;
                float grow = (float)(t * 7);
                int alpha = (int)(110 * (1 - t));
                using (Pen p = new Pen(Color.FromArgb(alpha, Theme.Red), 3f))
                    g.DrawEllipse(p, c.X - grow, c.Y - grow, c.Width + 2 * grow, c.Height + 2 * grow);
            }
            Color fill = Recording ? Theme.Red : Theme.Panel;
            if (hover) fill = ControlPaint.Light(fill, 0.12f);
            if (down) fill = ControlPaint.Dark(fill, 0.08f);
            using (Brush b = new SolidBrush(fill)) g.FillEllipse(b, c);
            using (Pen p = new Pen(Recording ? Color.FromArgb(255, 140, 150) : Theme.Red, 3f)) g.DrawEllipse(p, c);
            float s = d * 0.62f;
            MicIcon.Draw(g, new RectangleF(c.X + (d - s) / 2, c.Y + (d - s) / 2, s, s), Color.White, fill);
        }
    }

    class LevelBar : Control
    {
        float value;
        public LevelBar()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
            Height = 8;
        }
        public void Reset() { value = 0; Invalidate(); }
        public void SetLevel(float v)
        {
            value = v > value ? v : Math.Max(0, value - 0.08f);
            Invalidate();
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Theme.Panel);
            int w = (int)(Width * Math.Min(1f, value));
            Color c = value > 0.9f ? Theme.Red : Theme.Green;
            using (Brush b = new SolidBrush(c)) e.Graphics.FillRectangle(b, 0, 0, w, Height);
        }
    }

    class SaveDialog : Form
    {
        public SaveDialog(string info)
        {
            Text = "Recording finished";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = false; MinimizeBox = false; ShowInTaskbar = false;
            ClientSize = new Size(300, 130);
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            Font = new Font("Segoe UI", 9.5f);
            Label l = new Label();
            l.Text = info + "\nSave it or discard it?";
            l.SetBounds(16, 14, 268, 50);
            Controls.Add(l);
            Button save = MakeButton("Save", 16, Theme.Red, DialogResult.Yes);
            Button discard = MakeButton("Discard", 154, Theme.Panel, DialogResult.No);
            Controls.Add(save); Controls.Add(discard);
            AcceptButton = save;
            CancelButton = save; // closing the window never loses a recording
        }
        Button MakeButton(string text, int x, Color back, DialogResult r)
        {
            Button b = new Button();
            b.Text = text; b.DialogResult = r; b.SetBounds(x, 78, 130, 36);
            b.FlatStyle = FlatStyle.Flat; b.FlatAppearance.BorderSize = 0;
            b.BackColor = back; b.ForeColor = Color.White; b.Cursor = Cursors.Hand;
            return b;
        }
    }

    class MainForm : Form
    {
        readonly Settings cfg = Settings.Load();
        readonly RecordButton btn = new RecordButton();
        readonly Label timeLbl = new Label();
        readonly Label status = new Label();
        readonly LevelBar level = new LevelBar();
        readonly CheckBox micChk = new CheckBox();
        readonly CheckBox sysChk = new CheckBox();
        readonly ComboBox qual = new ComboBox();
        readonly Label folderLbl = new Label();
        readonly Button folderBtn = new Button();
        readonly Button pinBtn = new Button();
        readonly Timer ui = new Timer();
        Recorder rec;
        string tmpPath, lastSaved;

        public MainForm()
        {
            Text = "Lite Recorder";
            Font = new Font("Segoe UI", 9f);
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            ClientSize = new Size(300, 400);
            MinimumSize = new Size(280, 420);
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
            status.Text = "Ready"; status.ForeColor = Theme.Dim; status.TextAlign = ContentAlignment.MiddleCenter; status.AutoSize = false;
            status.Click += delegate { if (lastSaved != null && File.Exists(lastSaved)) Process.Start("explorer.exe", "/select,\"" + lastSaved + "\""); };

            foreach (CheckBox c in new CheckBox[] { micChk, sysChk })
            {
                c.AutoSize = true; c.FlatStyle = FlatStyle.Flat; c.Cursor = Cursors.Hand;
            }
            micChk.Text = "Microphone"; micChk.Checked = cfg.Mic;
            sysChk.Text = "PC sound"; sysChk.Checked = cfg.Sys;
            micChk.CheckedChanged += delegate { cfg.Mic = micChk.Checked; cfg.Save(); };
            sysChk.CheckedChanged += delegate { cfg.Sys = sysChk.Checked; cfg.Save(); };

            qual.DropDownStyle = ComboBoxStyle.DropDownList; qual.FlatStyle = FlatStyle.Flat;
            qual.BackColor = Theme.Panel; qual.ForeColor = Theme.Text;
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

            Controls.AddRange(new Control[] { pinBtn, btn, timeLbl, status, level, micChk, sysChk, qual, folderLbl, folderBtn });
            Resize += delegate { DoLayout(); };
            DoLayout();

            ui.Interval = 60;
            ui.Tick += delegate { OnTick(); };
            ui.Start();
            FormClosing += OnClosing;
        }

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
            status.SetBounds(12, y, w - 24, 20); y += 28;
            level.SetBounds(24, y, w - 48, 8); y += 24;
            micChk.Location = new Point(24, y);
            sysChk.Location = new Point(w - 24 - sysChk.Width, y); y += 34;
            qual.SetBounds(24, y, w - 48, 26); y += 36;
            folderBtn.SetBounds(w - 24 - 80, y, 80, 28);
            folderLbl.SetBounds(24, y, w - 24 - 80 - 32, 28);
        }

        void OnTick()
        {
            if (rec == null) return;
            TimeSpan t = rec.Elapsed;
            timeLbl.Text = string.Format("{0:00}:{1:00}:{2:00}", (int)t.TotalHours, t.Minutes, t.Seconds);
            level.SetLevel(Math.Min(1f, rec.Peak / 20000f));
            btn.Invalidate();
        }

        void Toggle()
        {
            if (rec == null) StartRecording(); else StopRecording();
        }

        void StartRecording()
        {
            try
            {
                Directory.CreateDirectory(cfg.Folder);
                Recorder r = new Recorder();
                string basePath = Path.Combine(cfg.Folder, ".recording_" + Guid.NewGuid().ToString("N").Substring(0, 8));
                r.Start(basePath, cfg.Mic, cfg.Sys, cfg.Kbps);
                rec = r; tmpPath = r.Path;
                btn.Recording = true;
                micChk.Enabled = sysChk.Enabled = qual.Enabled = folderBtn.Enabled = false;
                status.ForeColor = Theme.Red;
                status.Text = r.Warning != null ? "Recording... " + r.Warning : "Recording...";
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
            micChk.Enabled = sysChk.Enabled = qual.Enabled = folderBtn.Enabled = true;
            level.Reset();
            timeLbl.Text = "00:00:00";

            string info = string.Format("Length {0:00}:{1:00}:{2:00}", (int)dur.TotalHours, dur.Minutes, dur.Seconds);
            DialogResult dr;
            using (SaveDialog dlg = new SaveDialog(info)) dr = dlg.ShowDialog(this);

            if (dr == DialogResult.No)
            {
                try { File.Delete(r.Path); } catch { }
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
                status.ForeColor = Theme.Green;
                status.Text = "Saved: " + Path.GetFileName(target) + (err != null ? " (" + err + ")" : "");
            }
            catch (Exception ex)
            {
                status.ForeColor = Theme.Red; status.Text = "Save failed: " + ex.Message;
            }
        }

        void OnClosing(object sender, FormClosingEventArgs e)
        {
            if (rec != null) StopRecording();
            cfg.Save();
        }
    }
}
