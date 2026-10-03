using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const string LatestReleaseApi = "https://api.github.com/repos/umar-vai/Media-Downloader/releases/latest";
    private const string ReleasePage = "https://github.com/umar-vai/Media-Downloader/releases/latest";
    private const string UserAgent = "MediaDownloader-LegacyBridge/1.0";

    [STAThread]
    private static void Main()
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        ServicePointManager.Expect100Continue = false;

        string currentExe = Application.ExecutablePath;
        string workDir = Path.Combine(Path.GetTempPath(), "MediaDownloaderUpdateBridge");
        string downloadedExe = Path.Combine(workDir, "MediaDownloader.new.exe");
        string batchFile = Path.Combine(workDir, "finish-update.cmd");

        try
        {
            Directory.CreateDirectory(workDir);
            MessageBox.Show(
                "Media Downloader is completing a one-time updater migration.\n\nThis may take a minute. The app will reopen automatically when finished.",
                "Media Downloader",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );

            string json = DownloadStringWithRetries(LatestReleaseApi, 5);
            string exeUrl = FindBrowserDownloadUrl(json, "MediaDownloader.exe");
            string checksumUrl = FindBrowserDownloadUrl(json, "MediaDownloader.exe.sha256");
            if (string.IsNullOrWhiteSpace(exeUrl) || string.IsNullOrWhiteSpace(checksumUrl))
                throw new InvalidOperationException("The latest release is missing MediaDownloader.exe or its checksum.");

            string checksumText = DownloadStringWithRetries(checksumUrl, 5);
            Match hashMatch = Regex.Match(checksumText ?? string.Empty, "[0-9a-fA-F]{64}");
            if (!hashMatch.Success)
                throw new InvalidOperationException("The release checksum file is invalid.");

            if (File.Exists(downloadedExe)) File.Delete(downloadedExe);
            DownloadFileWithRetries(exeUrl, downloadedExe, 6);

            string actualHash = Sha256(downloadedExe);
            if (!string.Equals(actualHash, hashMatch.Value, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Downloaded update verification failed.");

            string batch = "@echo off\r\n" +
                           "ping 127.0.0.1 -n 3 >nul\r\n" +
                           "copy /Y \"" + currentExe + "\" \"" + currentExe + ".old\" >nul 2>nul\r\n" +
                           "copy /Y \"" + downloadedExe + "\" \"" + currentExe + "\" >nul\r\n" +
                           "if errorlevel 1 (\r\n" +
                           "  copy /Y \"" + currentExe + ".old\" \"" + currentExe + "\" >nul 2>nul\r\n" +
                           "  exit /b 1\r\n" +
                           ")\r\n" +
                           "del /Q \"" + currentExe + ".old\" >nul 2>nul\r\n" +
                           "start \"\" \"" + currentExe + "\"\r\n" +
                           "del /Q \"" + downloadedExe + "\" >nul 2>nul\r\n" +
                           "del /Q \"%~f0\" >nul 2>nul\r\n";

            File.WriteAllText(batchFile, batch);
            Process.Start(new ProcessStartInfo(batchFile)
            {
                UseShellExecute = true,
                WindowStyle = ProcessWindowStyle.Hidden
            });
        }
        catch (Exception ex)
        {
            DialogResult result = MessageBox.Show(
                "The automatic update could not finish yet. Your current app is still safe.\n\n" +
                ex.Message + "\n\nOpen the latest Media Downloader release in your browser?",
                "Media Downloader",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning
            );
            if (result == DialogResult.Yes)
            {
                try { Process.Start(ReleasePage); } catch { }
            }
        }
    }

    private static string FindBrowserDownloadUrl(string json, string assetName)
    {
        string pattern = "\\\"name\\\"\\s*:\\s*\\\"" + Regex.Escape(assetName) +
                         "\\\".*?\\\"browser_download_url\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"";
        Match match = Regex.Match(json ?? string.Empty, pattern, RegexOptions.Singleline | RegexOptions.IgnoreCase);
        return match.Success ? match.Groups[1].Value.Replace("\\/", "/") : string.Empty;
    }

    private static WebClient NewClient()
    {
        WebClient client = new WebClient();
        client.Headers[HttpRequestHeader.UserAgent] = UserAgent;
        client.Headers[HttpRequestHeader.Accept] = "application/vnd.github+json";
        client.Proxy = WebRequest.DefaultWebProxy;
        if (client.Proxy != null)
            client.Proxy.Credentials = CredentialCache.DefaultCredentials;
        return client;
    }

    private static string DownloadStringWithRetries(string url, int attempts)
    {
        Exception last = null;
        for (int i = 1; i <= attempts; i++)
        {
            try
            {
                using (WebClient client = NewClient())
                    return client.DownloadString(url);
            }
            catch (Exception ex)
            {
                last = ex;
                if (i < attempts) Thread.Sleep(Math.Min(10000, 1200 * i * i));
            }
        }
        throw new InvalidOperationException("Could not connect to the update server after multiple attempts.", last);
    }

    private static void DownloadFileWithRetries(string url, string destination, int attempts)
    {
        Exception last = null;
        for (int i = 1; i <= attempts; i++)
        {
            try
            {
                if (File.Exists(destination)) File.Delete(destination);
                using (WebClient client = NewClient())
                    client.DownloadFile(url, destination);
                if (new FileInfo(destination).Length > 0) return;
            }
            catch (Exception ex)
            {
                last = ex;
                try { if (File.Exists(destination)) File.Delete(destination); } catch { }
            }
            if (i < attempts) Thread.Sleep(Math.Min(12000, 1500 * i * i));
        }

        if (TryCurl(url, destination)) return;
        throw new InvalidOperationException("The update file could not be downloaded after multiple attempts.", last);
    }

    private static bool TryCurl(string url, string destination)
    {
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo("curl.exe",
                "-L --fail --retry 6 --retry-all-errors --connect-timeout 20 --max-time 900 " +
                "-A \"" + UserAgent + "\" -o \"" + destination + "\" \"" + url + "\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            using (Process process = Process.Start(psi))
            {
                process.WaitForExit();
                return process.ExitCode == 0 && File.Exists(destination) && new FileInfo(destination).Length > 0;
            }
        }
        catch { return false; }
    }

    private static string Sha256(string path)
    {
        using (SHA256 sha = SHA256.Create())
        using (FileStream stream = File.OpenRead(path))
        {
            byte[] hash = sha.ComputeHash(stream);
            return BitConverter.ToString(hash).Replace("-", string.Empty).ToLowerInvariant();
        }
    }
}
