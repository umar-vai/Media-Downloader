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
        public string MicId = "";   // empty = Windows default
        public string SysId = "";

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
                    else if (k == "micid") s.MicId = v;
                    else if (k == "sysid") s.SysId = v;
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
                    "mic=" + (Mic ? "1" : "0"), "sys=" + (Sys ? "1" : "0"), "micid=" + MicId, "sysid=" + SysId });
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
            ClientSize = new Size(300, 150);
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            Font = new Font("Segoe UI", 9.5f);
            Label l = new Label();
            l.Text = info + "\nSave it or discard it?";
            l.SetBounds(16, 14, 268, 70);
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
            b.Text = text; b.DialogResult = r; b.SetBounds(x, 98, 130, 36);
            b.FlatStyle = FlatStyle.Flat; b.FlatAppearance.BorderSize = 0;
            b.BackColor = back; b.ForeColor = Color.White; b.Cursor = Cursors.Hand;
            return b;
        }
    }

}
