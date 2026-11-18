using System;
using System.Runtime.InteropServices;

namespace LiteRecorder
{
    // MP3 through the Windows built-in Media Foundation MP3 encoder + MP3 file sink: no extra DLL to ship.
    [ComImport, Guid("2CD2D921-C447-44A7-A13C-4ADABFC247E3"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFAttributes
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare(); void GetUINT32(); void GetUINT64();
        void GetDouble(); void GetGUID(); void GetStringLength(); void GetString(); void GetAllocatedString();
        void GetBlobSize(); void GetBlob(); void GetAllocatedBlob(); void GetUnknown(); void SetItem(); void DeleteItem();
        void DeleteAllItems(); void SetUINT32(); void SetUINT64(); void SetDouble(); void SetGUID(); void SetString();
        void SetBlob(); void SetUnknown(); void LockStore(); void UnlockStore(); void GetCount(); void GetItemByIndex();
        void CopyAllItems();
    }

    [ComImport, Guid("44AE0FA8-EA31-4109-8D2E-4CAE4997C555"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFMediaType
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare(); void GetUINT32(); void GetUINT64();
        void GetDouble(); void GetGUID(); void GetStringLength(); void GetString(); void GetAllocatedString();
        void GetBlobSize(); void GetBlob(); void GetAllocatedBlob(); void GetUnknown(); void SetItem(); void DeleteItem();
        void DeleteAllItems(); void SetUINT32(); void SetUINT64(); void SetDouble(); void SetGUID(); void SetString();
        void SetBlob(); void SetUnknown(); void LockStore(); void UnlockStore(); void GetCount(); void GetItemByIndex();
        void CopyAllItems();
    }

    [ComImport, Guid("44AE0FA8-EA31-4109-8D2E-4CAE4997C555"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFMediaTypeW
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare(); void GetUINT32(); void GetUINT64();
        void GetDouble(); void GetGUID(); void GetStringLength(); void GetString(); void GetAllocatedString();
        void GetBlobSize(); void GetBlob(); void GetAllocatedBlob(); void GetUnknown(); void SetItem(); void DeleteItem();
        void DeleteAllItems();
        void SetUINT32(ref Guid key, int value);
        void SetUINT64();
        void SetDouble();
        void SetGUID(ref Guid key, ref Guid value);
    }
    [ComImport, Guid("045FA593-8799-42B8-BC8D-8968C6453507"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFMediaBuffer
    {
        void Lock(out IntPtr buffer, out int maxLen, out int curLen);
        void Unlock();
        void GetCurrentLength(out int len);
        void SetCurrentLength(int len);
        void GetMaxLength(out int len);
    }

    [ComImport, Guid("C40A00F2-B93A-4D80-AE8C-5A1C634F58E4"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFSample
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare(); void GetUINT32(); void GetUINT64();
        void GetDouble(); void GetGUID(); void GetStringLength(); void GetString(); void GetAllocatedString();
        void GetBlobSize(); void GetBlob(); void GetAllocatedBlob(); void GetUnknown(); void SetItem(); void DeleteItem();
        void DeleteAllItems(); void SetUINT32(); void SetUINT64(); void SetDouble(); void SetGUID(); void SetString();
        void SetBlob(); void SetUnknown(); void LockStore(); void UnlockStore(); void GetCount(); void GetItemByIndex();
        void CopyAllItems();
        void GetSampleFlags(out int flags);
        void SetSampleFlags(int flags);
        void GetSampleTime(out long t);
        void SetSampleTime(long t);
        void GetSampleDuration(out long d);
        void SetSampleDuration(long d);
        void GetBufferCount(out int n);
        void GetBufferByIndex(int i, out IMFMediaBuffer b);
        void ConvertToContiguousBuffer(out IMFMediaBuffer b);
        void AddBuffer(IMFMediaBuffer b);
    }

    [ComImport, Guid("3137F1CD-FE5E-4805-A5D8-FB477448CB3D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFSinkWriter
    {
        void AddStream(IMFMediaType type, out int index);
        void SetInputMediaType(int index, IMFMediaType type, IMFAttributes parameters);
        void BeginWriting();
        void WriteSample(int index, IMFSample sample);
        void SendStreamTick(int index, long ts);
        void PlaceMarker(int index, IntPtr ctx);
        void NotifyEndOfSegment(int index);
        void Flush(int index);
        void DoFinalize();
    }

    [ComImport, Guid("7FEE9E9A-4A89-47a6-899C-B6A53A70FB67"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFActivate
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare(); void GetUINT32(); void GetUINT64();
        void GetDouble(); void GetGUID(); void GetStringLength(); void GetString(); void GetAllocatedString();
        void GetBlobSize(); void GetBlob(); void GetAllocatedBlob(); void GetUnknown(); void SetItem(); void DeleteItem();
        void DeleteAllItems(); void SetUINT32(); void SetUINT64(); void SetDouble(); void SetGUID(); void SetString();
        void SetBlob(); void SetUnknown(); void LockStore(); void UnlockStore(); void GetCount(); void GetItemByIndex();
        void CopyAllItems();
        void ActivateObject(ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object obj);
        void ShutdownObject();
        void DetachObject();
    }

    [ComImport, Guid("BF94C121-5B05-4E6F-8000-BA598961414D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFTransform
    {
        void GetStreamLimits(); void GetStreamCount(); void GetStreamIDs(); void GetInputStreamInfo(); void GetOutputStreamInfo();
        void GetAttributes(); void GetInputStreamAttributes(); void GetOutputStreamAttributes(); void DeleteInputStream();
        void AddInputStreams(); void GetInputAvailableType();
        [PreserveSig] int GetOutputAvailableType(int streamId, int index, out IMFMediaType type);
    }

    [ComImport, Guid("44AE0FA8-EA31-4109-8D2E-4CAE4997C555"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMFMediaTypeR
    {
        void GetItem(); void GetItemType(); void CompareItem(); void Compare();
        void GetUINT32(ref Guid key, out int value);
    }
    class Mp3Encoder : IEncoder
    {
        [DllImport("mfplat.dll")] static extern int MFStartup(int version, int flags);
        [DllImport("mfplat.dll")] static extern int MFShutdown();
        [DllImport("mfplat.dll")] static extern int MFCreateMediaType(out IMFMediaType t);
        [DllImport("mfplat.dll")] static extern int MFCreateSample(out IMFSample s);
        [DllImport("mfplat.dll")] static extern int MFCreateMemoryBuffer(int size, out IMFMediaBuffer b);
        [DllImport("mfplat.dll")] static extern int MFInitMediaTypeFromWaveFormatEx(IMFMediaType t, IntPtr wfx, int size);
        [DllImport("mfreadwrite.dll", CharSet = CharSet.Unicode)]
        static extern int MFCreateSinkWriterFromURL(string url, IntPtr byteStream, IMFAttributes attrs, out IMFSinkWriter w);

        [StructLayout(LayoutKind.Sequential)] struct RegType { public Guid major, sub; }
        [DllImport("mfplat.dll")] static extern int MFTEnumEx(Guid cat, int flags, IntPtr inType, ref RegType outType, out IntPtr acts, out int count);

        // Ask the Windows MP3 encoder for the exact output type it accepts at this bitrate.
        static IMFMediaType FindOutputType(int kbps)
        {
            RegType o = new RegType();
            o.major = new Guid("73647561-0000-0010-8000-00AA00389B71");
            o.sub = new Guid("00000055-0000-0010-8000-00AA00389B71");
            IntPtr acts; int n;
            Check(MFTEnumEx(new Guid("91C64BD0-F91E-4D8C-9276-DB248279D975"), 0x13, IntPtr.Zero, ref o, out acts, out n), "find encoder");
            if (n == 0) throw new InvalidOperationException("MP3 encoder not installed");
            IMFActivate act = (IMFActivate)Marshal.GetObjectForIUnknown(Marshal.ReadIntPtr(acts));
            Marshal.FreeCoTaskMem(acts);
            Guid iid = typeof(IMFTransform).GUID;
            object obj;
            act.ActivateObject(ref iid, out obj);
            IMFTransform mft = (IMFTransform)obj;
            Guid kRate = new Guid("5faeeae7-0290-4c31-9e8a-c534f68d9dba"), kCh = new Guid("37e48bf5-645e-4c5b-89de-ada9e29b696a"),
                 kAvg = new Guid("1aab75c8-cfef-451c-ab95-ac034b8e1731");
            IMFMediaType best = null;
            for (int i = 0; ; i++)
            {
                IMFMediaType t;
                if (mft.GetOutputAvailableType(0, i, out t) != 0) break;
                IMFMediaTypeR r = (IMFMediaTypeR)t;
                int rate, ch, avg;
                r.GetUINT32(ref kRate, out rate); r.GetUINT32(ref kCh, out ch); r.GetUINT32(ref kAvg, out avg);
                if (rate == Fmt.Rate && ch == Fmt.Channels && avg == kbps * 125) { best = t; break; }
            }
            if (best == null) throw new InvalidOperationException("MP3 encoder has no " + kbps + " kbps mode");
            return best;
        }
        IMFSinkWriter writer;
        long frames;
        bool started;

        static void Check(int hr, string what)
        {
            if (hr < 0) throw new InvalidOperationException("MP3 encoder: " + what + " failed (0x" + hr.ToString("X8") + ")");
        }

        public Mp3Encoder(string path, int kbps)
        {
            Check(MFStartup(0x00020070, 0), "startup");
            started = true;
            IntPtr src = Marshal.AllocHGlobal(18), dst = Marshal.AllocHGlobal(30);
            try
            {
                Marshal.WriteInt16(src, 0, 1);
                Marshal.WriteInt16(src, 2, Fmt.Channels);
                Marshal.WriteInt32(src, 4, Fmt.Rate);
                Marshal.WriteInt32(src, 8, Fmt.Rate * Fmt.FrameBytes);
                Marshal.WriteInt16(src, 12, Fmt.FrameBytes);
                Marshal.WriteInt16(src, 14, 16);
                Marshal.WriteInt16(src, 16, 0);

                Marshal.WriteInt16(dst, 0, 0x55);
                Marshal.WriteInt16(dst, 2, Fmt.Channels);
                Marshal.WriteInt32(dst, 4, Fmt.Rate);
                Marshal.WriteInt32(dst, 8, kbps * 1000 / 8);
                Marshal.WriteInt16(dst, 12, 1);
                Marshal.WriteInt16(dst, 14, 0);
                Marshal.WriteInt16(dst, 16, 12);
                Marshal.WriteInt16(dst, 18, 1);
                Marshal.WriteInt32(dst, 20, 2);
                Marshal.WriteInt16(dst, 24, (short)(144 * kbps * 1000 / Fmt.Rate));
                Marshal.WriteInt16(dst, 26, 1);
                Marshal.WriteInt16(dst, 28, 1393);

                IMFMediaType outT = FindOutputType(kbps), inT;
                Check(MFCreateMediaType(out inT), "media type");
                Check(MFInitMediaTypeFromWaveFormatEx(inT, src, 18), "input format");
                Check(MFCreateSinkWriterFromURL(path, IntPtr.Zero, null, out writer), "create writer");
                int idx;
                writer.AddStream(outT, out idx);
                writer.SetInputMediaType(idx, inT, null);
                writer.BeginWriting();
            }
            catch (Exception)
            {
                Shutdown();
                throw;
            }
            finally
            {
                Marshal.FreeHGlobal(src);
                Marshal.FreeHGlobal(dst);
            }
        }

        public void Write(byte[] pcm, int count)
        {
            if (count <= 0) return;
            IMFMediaBuffer buf; IMFSample sample;
            Check(MFCreateMemoryBuffer(count, out buf), "buffer");
            IntPtr p; int max, cur;
            buf.Lock(out p, out max, out cur);
            Marshal.Copy(pcm, 0, p, count);
            buf.Unlock();
            buf.SetCurrentLength(count);
            Check(MFCreateSample(out sample), "sample");
            sample.AddBuffer(buf);
            long n = count / Fmt.FrameBytes;
            sample.SetSampleTime(frames * 10000000L / Fmt.Rate);
            sample.SetSampleDuration(n * 10000000L / Fmt.Rate);
            writer.WriteSample(0, sample);
            frames += n;
            Marshal.ReleaseComObject(sample);
            Marshal.ReleaseComObject(buf);
        }

        public void Close()
        {
            try { writer.DoFinalize(); } catch { }
            Shutdown();
        }

        void Shutdown()
        {
            if (writer != null) { Marshal.ReleaseComObject(writer); writer = null; }
            if (started) { MFShutdown(); started = false; }
        }
    }
}


