using System;
using System.Drawing;
using System.Drawing.Drawing2D;

namespace LiteRecorder
{
    // Podcast-style condenser microphone on a stand, drawn with GDI+ (no image files needed).
    static class MicIcon
    {
        public static void Draw(Graphics g, RectangleF box, Color fg, Color cut)
        {
            GraphicsState st = g.Save();
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TranslateTransform(box.X, box.Y);
            float s = Math.Min(box.Width, box.Height) / 100f;
            g.ScaleTransform(s, s);

            using (Brush b = new SolidBrush(fg))
            using (Pen p = new Pen(fg, 5f))
            using (Pen grille = new Pen(cut, 2f))
            {
                p.StartCap = LineCap.Round;
                p.EndCap = LineCap.Round;
                // capsule
                using (GraphicsPath path = new GraphicsPath())
                {
                    path.AddArc(36, 8, 28, 28, 180, 180);
                    path.AddLine(64, 22, 64, 40);
                    path.AddArc(36, 26, 28, 28, 0, 180);
                    path.CloseFigure();
                    g.FillPath(b, path);
                }
                // pop-filter lines
                for (int i = 0; i < 4; i++)
                {
                    float y = 20 + i * 8;
                    g.DrawLine(grille, 41, y, 59, y);
                }
                // yoke, stem, base
                g.DrawArc(p, 27, 24, 46, 44, 0, 180);
                g.DrawLine(p, 50, 68, 50, 84);
                g.DrawLine(p, 36, 86, 64, 86);
            }
            g.Restore(st);
        }
    }
}
