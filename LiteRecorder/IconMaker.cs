using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;

namespace LiteRecorder
{
    // Build-time helper: writes app.ico (PNG-compressed sizes) so the exe gets a microphone icon.
    static class IconMaker
    {
        static void Main(string[] args)
        {
            int[] sizes = { 16, 24, 32, 48, 64, 128, 256 };
            byte[][] pngs = new byte[sizes.Length][];
            for (int i = 0; i < sizes.Length; i++)
            {
                int n = sizes[i];
                using (Bitmap bmp = new Bitmap(n, n, PixelFormat.Format32bppArgb))
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.Clear(Color.Transparent);
                    float r = n * 0.22f;
                    using (GraphicsPath gp = new GraphicsPath())
                    {
                        gp.AddArc(0, 0, 2 * r, 2 * r, 180, 90);
                        gp.AddArc(n - 1 - 2 * r, 0, 2 * r, 2 * r, 270, 90);
                        gp.AddArc(n - 1 - 2 * r, n - 1 - 2 * r, 2 * r, 2 * r, 0, 90);
                        gp.AddArc(0, n - 1 - 2 * r, 2 * r, 2 * r, 90, 90);
                        gp.CloseFigure();
                        using (Brush b = new SolidBrush(Color.FromArgb(229, 56, 75))) g.FillPath(b, gp);
                    }
                    MicIcon.Draw(g, new RectangleF(n * 0.12f, n * 0.1f, n * 0.76f, n * 0.8f), Color.White, Color.FromArgb(229, 56, 75));
                    using (MemoryStream ms = new MemoryStream())
                    {
                        bmp.Save(ms, ImageFormat.Png);
                        pngs[i] = ms.ToArray();
                    }
                }
            }
            using (BinaryWriter w = new BinaryWriter(File.Create(args[0])))
            {
                w.Write((short)0); w.Write((short)1); w.Write((short)sizes.Length);
                int offset = 6 + 16 * sizes.Length;
                for (int i = 0; i < sizes.Length; i++)
                {
                    byte dim = (byte)(sizes[i] >= 256 ? 0 : sizes[i]);
                    w.Write(dim); w.Write(dim); w.Write((byte)0); w.Write((byte)0);
                    w.Write((short)1); w.Write((short)32);
                    w.Write(pngs[i].Length); w.Write(offset);
                    offset += pngs[i].Length;
                }
                for (int i = 0; i < sizes.Length; i++) w.Write(pngs[i]);
            }
        }
    }
}
