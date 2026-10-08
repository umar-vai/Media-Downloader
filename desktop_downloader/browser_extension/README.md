# Media Downloader Browser Capture

Chrome/Edge Manifest V3 extension for Media Downloader.

## Install

1. Install and open Media Downloader.
2. Open **Browser Capture** and click **Open extension folder**.
3. In Chrome or Edge open the Extensions page and enable **Developer mode**.
4. Choose **Load unpacked** and select this `browser_extension` folder.
5. Open the extension popup, paste the pairing code shown by Media Downloader, and click **Pair**.
6. Start video playback. Detected MP4/WebM, HLS and DASH requests will appear in the popup.
7. Click **Send to app**, then use **Download** or **Edit & download** inside Media Downloader.

Captured request headers are kept only in browser session/app memory. The extension does not bypass DRM-protected streams.
