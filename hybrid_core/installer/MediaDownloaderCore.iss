#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "."
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{7B6D0A1E-EE6F-4B31-A0A0-1A7337CE52C0}
AppName=Media Downloader Core
AppVersion={#AppVersion}
AppPublisher=Team Fahad
DefaultDirName={localappdata}\Programs\MediaDownloader
DefaultGroupName=Media Downloader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=MediaDownloaderCoreSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MediaDownloaderCore.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Launch Media Downloader Core when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\MediaDownloaderCore.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\MediaDownloaderCoreUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Media Downloader"; Filename: "{app}\MediaDownloaderCore.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Media Downloader"; Filename: "{app}\MediaDownloaderCore.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MediaDownloaderCore"; ValueData: """{app}\MediaDownloaderCore.exe"" --background"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MediaDownloaderCore.exe"; Description: "Launch Media Downloader"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MediaDownloaderCore');
end;
