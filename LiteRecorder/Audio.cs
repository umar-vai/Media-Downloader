using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace LiteRecorder
{
    // ---- WASAPI COM interop (only the methods we call, in vtable order) ----
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorComObject { }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        void EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
        void GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        void Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    }

    [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioClient
    {
        void Initialize(int shareMode, int streamFlags, long bufferDuration, long periodicity, IntPtr format, IntPtr sessionGuid);
        void GetBufferSize(out uint frames);
        void GetStreamLatency(out long latency);
        void GetCurrentPadding(out uint padding);
        void IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
        void GetMixFormat(out IntPtr format);
        void GetDevicePeriod(out long defaultPeriod, out long minPeriod);
        void Start();
        void Stop();
        void Reset();
        void SetEventHandle(IntPtr handle);
        void GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
    }

    [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioCaptureClient
    {
        void GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devicePos, out ulong qpcPos);
        void ReleaseBuffer(uint frames);
        void GetNextPacketSize(out uint frames);
    }

    // Everything is captured as 44.1 kHz / 16-bit / stereo; Windows converts from the device format for us.
    static class Fmt
    {
        public const int Rate = 44100;
        public const int Channels = 2;
        public const int FrameBytes = 4;
    }

    // One capture stream (microphone, or "what you hear" loopback) feeding a thread-safe byte queue.
    class Source
    {
        readonly bool loopback;
        readonly object lk = new object();
        readonly ManualResetEvent ready = new ManualResetEvent(false);
        byte[] buf = new byte[1 << 18];
        int len;
        Thread thread;
        volatile bool stop;
        public string Error;
        public bool Ok;

        public Source(bool loopback) { this.loopback = loopback; }

        public void Start()
        {
            thread = new Thread(Run);
            thread.IsBackground = true;
            thread.Start();
            ready.WaitOne(5000);
        }

        public void Stop()
        {
            stop = true;
            if (thread != null) thread.Join(2000);
        }

        void Run()
        {
            IAudioClient client = null;
            IAudioCaptureClient cap = null;
            IntPtr fmt = Marshal.AllocHGlobal(18);
            try
            {
                Marshal.WriteInt16(fmt, 0, 1);
                Marshal.WriteInt16(fmt, 2, Fmt.Channels);
                Marshal.WriteInt32(fmt, 4, Fmt.Rate);
                Marshal.WriteInt32(fmt, 8, Fmt.Rate * Fmt.FrameBytes);
                Marshal.WriteInt16(fmt, 12, Fmt.FrameBytes);
                Marshal.WriteInt16(fmt, 14, 16);
                Marshal.WriteInt16(fmt, 16, 0);

                IMMDeviceEnumerator en = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                IMMDevice dev;
                en.GetDefaultAudioEndpoint(loopback ? 0 : 1, 0, out dev);
                Guid iid = typeof(IAudioClient).GUID;
                object o;
                dev.Activate(ref iid, 23, IntPtr.Zero, out o);
                client = (IAudioClient)o;
                // AUTOCONVERTPCM | SRC_DEFAULT_QUALITY (| LOOPBACK)
                int flags = unchecked((int)0x80000000) | 0x08000000 | (loopback ? 0x00020000 : 0);
                client.Initialize(0, flags, 2000000, 0, fmt, IntPtr.Zero);
                Guid iidc = typeof(IAudioCaptureClient).GUID;
                object oc;
                client.GetService(ref iidc, out oc);
                cap = (IAudioCaptureClient)oc;
                client.Start();
                Ok = true;
                ready.Set();

                while (!stop)
                {
                    Thread.Sleep(15);
                    uint n;
                    cap.GetNextPacketSize(out n);
                    while (n > 0)
                    {
                        IntPtr p; uint frames, fl; ulong a, b;
                        cap.GetBuffer(out p, out frames, out fl, out a, out b);
                        Append(p, (int)frames * Fmt.FrameBytes, (fl & 2) != 0);
                        cap.ReleaseBuffer(frames);
                        cap.GetNextPacketSize(out n);
                    }
                }
                client.Stop();
            }
            catch (Exception ex)
            {
                if (!Ok) Error = ex.Message;
            }
            finally
            {
                Marshal.FreeHGlobal(fmt);
                if (cap != null) Marshal.ReleaseComObject(cap);
                if (client != null) Marshal.ReleaseComObject(client);
                ready.Set();
            }
        }

        void Append(IntPtr p, int bytes, bool silent)
        {
            lock (lk)
            {
                if (len + bytes > buf.Length) Array.Resize(ref buf, Math.Max(buf.Length * 2, len + bytes));
                if (silent) Array.Clear(buf, len, bytes);
                else Marshal.Copy(p, buf, len, bytes);
                len += bytes;
            }
        }

        // Returns exactly n bytes (zero padded when the device had less); drops stale data if it ran far ahead.
        public byte[] Take(int n)
        {
            byte[] res = new byte[n];
            lock (lk)
            {
                int keep = n + 88200;
                if (len > keep)
                {
                    int drop = (len - keep) & ~3;
                    Buffer.BlockCopy(buf, drop, buf, 0, len - drop);
                    len -= drop;
                }
                int take = Math.Min(len, n);
                Buffer.BlockCopy(buf, 0, res, 0, take);
                Buffer.BlockCopy(buf, take, buf, 0, len - take);
                len -= take;
            }
            return res;
        }
    }

    interface IEncoder { void Write(byte[] pcm, int count); void Close(); }

    // Fallback when the PC has no MP3 codec: plain WAV.
    class WavEncoder : IEncoder
    {
        readonly FileStream file;
        long data;
        public WavEncoder(string path)
        {
            file = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
            file.Write(new byte[44], 0, 44);
        }
        public void Write(byte[] pcm, int count) { file.Write(pcm, 0, count); data += count; }
        public void Close()
        {
            BinaryWriter w = new BinaryWriter(file);
            file.Seek(0, SeekOrigin.Begin);
            w.Write(new char[] { 'R', 'I', 'F', 'F' }); w.Write((int)(36 + data));
            w.Write(new char[] { 'W', 'A', 'V', 'E', 'f', 'm', 't', ' ' }); w.Write(16);
            w.Write((short)1); w.Write((short)Fmt.Channels); w.Write(Fmt.Rate);
            w.Write(Fmt.Rate * Fmt.FrameBytes); w.Write((short)Fmt.FrameBytes); w.Write((short)16);
            w.Write(new char[] { 'd', 'a', 't', 'a' }); w.Write((int)data);
            w.Flush(); file.Close();
        }
    }

    // Mixes microphone + system sound on a wall-clock timeline and streams it to the encoder.
    class Recorder
    {
        Source mic, sys;
        IEncoder enc;
        Thread mixer;
        volatile bool stop;
        readonly Stopwatch sw = new Stopwatch();
        long framesOut;
        public volatile int Peak;
        public string Path;
        public string Warning;
        public bool IsWav;

        public TimeSpan Elapsed { get { return sw.Elapsed; } }

        // basePath has no extension; Path gets the real file path.
        public void Start(string basePath, bool useMic, bool useSys, int kbps)
        {
            if (!useMic && !useSys) throw new InvalidOperationException("Turn on Microphone or PC sound first.");
            if (useMic) { mic = new Source(false); mic.Start(); if (!mic.Ok) { Warning = "No microphone found" + (mic.Error != null ? " (" + mic.Error + ")" : "") + "."; mic = null; } }
            if (useSys) { sys = new Source(true); sys.Start(); if (!sys.Ok) { Warning = (Warning != null ? Warning + " " : "") + "PC sound could not be captured."; sys = null; } }
            if (mic == null && sys == null) throw new InvalidOperationException(Warning ?? "No audio device available.");

            // The encoder lives entirely on the mixer thread (COM objects must not be created on the UI thread
            // and used from another one, or Stop() would deadlock).
            ManualResetEvent ready = new ManualResetEvent(false);
            mixer = new Thread(delegate() { Loop(basePath, kbps, ready); });
            mixer.IsBackground = true;
            mixer.Start();
            ready.WaitOne();
            if (startError != null)
            {
                if (mic != null) mic.Stop();
                if (sys != null) sys.Stop();
                throw new InvalidOperationException(startError);
            }
        }

        string startError;

        void Loop(string basePath, int kbps, ManualResetEvent ready)
        {
            try
            {
                try { enc = new Mp3Encoder(basePath + ".mp3", kbps); Path = basePath + ".mp3"; }
                catch (Exception)
                {
                    enc = new WavEncoder(basePath + ".wav"); Path = basePath + ".wav"; IsWav = true;
                    Warning = (Warning != null ? Warning + " " : "") + "MP3 codec missing, saving as WAV.";
                }
                sw.Start();
            }
            catch (Exception ex) { startError = "Cannot create the file: " + ex.Message; }
            ready.Set();
            if (startError != null) return;

            while (!stop)
            {
                Thread.Sleep(40);
                try { MixTo((sw.ElapsedMilliseconds - 150) * Fmt.Rate / 1000); }
                catch (Exception ex) { WriteError = ex.Message; stop = true; }
            }
            if (mic != null) mic.Stop();
            if (sys != null) sys.Stop();
            sw.Stop();
            try { MixTo(sw.ElapsedMilliseconds * Fmt.Rate / 1000); } catch (Exception ex) { WriteError = WriteError ?? ex.Message; }
            try { enc.Close(); } catch (Exception ex) { WriteError = WriteError ?? ex.Message; }
        }

        public string WriteError;

        void MixTo(long target)
        {
            long frames = target - framesOut;
            if (frames <= 0) return;
            int bytes = (int)Math.Min(frames, Fmt.Rate * 4L) * Fmt.FrameBytes;
            byte[] a = mic != null ? mic.Take(bytes) : null;
            byte[] b = sys != null ? sys.Take(bytes) : null;
            byte[] o = new byte[bytes];
            int peak = 0;
            for (int i = 0; i < bytes; i += 2)
            {
                int s = 0;
                if (a != null) s += (short)(a[i] | (a[i + 1] << 8));
                if (b != null) s += (short)(b[i] | (b[i + 1] << 8));
                if (s > 32767) s = 32767; else if (s < -32768) s = -32768;
                o[i] = (byte)s; o[i + 1] = (byte)(s >> 8);
                int m = s < 0 ? -s : s;
                if (m > peak) peak = m;
            }
            Peak = peak;
            enc.Write(o, bytes);
            framesOut += bytes / Fmt.FrameBytes;
        }

        public void Stop()
        {
            stop = true;
            if (mixer != null) mixer.Join();
            Peak = 0;
        }
    }
}

