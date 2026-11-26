param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [string]$BaseVersion = "5.1.3"
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = Join-Path $env:RUNNER_TEMP ("MediaDownloaderUpgrade-" + [Guid]::NewGuid().ToString("N"))
$installDir = Join-Path $root "installed"
$profile = Join-Path $root "profile"
$previous = Join-Path $root "previous-setup.exe"
$checksum = Join-Path $root "previous.sha256"
$version = "v$BaseVersion"
$asset = "https://github.com/umar-vai/Media-Downloader/releases/download/$version/MediaDownloaderSetup.exe"

function Install-Build([string]$Path) {
    $process = Start-Process -FilePath $Path -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$installDir`""
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Installer $Path exited with code $($process.ExitCode)."
    }
}

New-Item -ItemType Directory -Path $root, $profile -Force | Out-Null
$originalAppData = $env:APPDATA
$originalLocalAppData = $env:LOCALAPPDATA
try {
    $env:APPDATA = $profile
    $env:LOCALAPPDATA = $profile

    & curl.exe --fail --location --silent --show-error --retry 2 --max-time 150 $asset -o $previous
    & curl.exe --fail --location --silent --show-error --retry 2 --max-time 30 "$asset.sha256" -o $checksum
    $expectedHash = ((Get-Content -LiteralPath $checksum -Raw).Trim() -split '\s+')[0]
    $actualHash = (Get-FileHash -LiteralPath $previous -Algorithm SHA256).Hash
    if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$' -or $actualHash -ne $expectedHash) {
        throw "The previous installer checksum did not match."
    }

    Install-Build $previous
    $exe = Join-Path $installDir "MediaDownloader.exe"
    $before = Join-Path $root "before.json"
    & "$PSScriptRoot/packaged_self_test.ps1" -Exe $exe -Report $before -ExpectedMode installer
    if ((Get-Content -LiteralPath $before -Raw | ConvertFrom-Json).version -ne $BaseVersion) {
        throw "The previous installer version could not be verified."
    }

    $config = Join-Path $profile "MediaDownloader"
    $settings = Join-Path $config "settings.json"
    $history = Join-Path $config "history.json"
    New-Item -ItemType Directory -Path $config -Force | Out-Null
    Set-Content -LiteralPath $settings -Value '{"theme":"Dark","auto_check_updates":false}' -Encoding utf8
    Set-Content -LiteralPath $history -Value '{"entries":[]}' -Encoding utf8
    $settingsHash = (Get-FileHash -LiteralPath $settings).Hash
    $historyHash = (Get-FileHash -LiteralPath $history).Hash

    Install-Build $Installer
    $after = Join-Path $root "after.json"
    & "$PSScriptRoot/packaged_self_test.ps1" -Exe $exe -Report $after -ExpectedMode installer
    $report = Get-Content -LiteralPath $after -Raw | ConvertFrom-Json
    if ($report.version -ne $ExpectedVersion -or -not ($report.checks | Where-Object {
        $_.name -eq "native_desktop" -and $_.ok
    })) {
        throw "The upgraded application did not start with the expected version."
    }
    if ((Get-FileHash -LiteralPath $settings).Hash -ne $settingsHash -or
        (Get-FileHash -LiteralPath $history).Hash -ne $historyHash) {
        throw "The installer upgrade modified the local settings or download history."
    }
    Write-Host "Installed upgrade v$BaseVersion -> v$ExpectedVersion passed."
} finally {
    $uninstaller = Join-Path $installDir "unins000.exe"
    if (Test-Path -LiteralPath $uninstaller) {
        $cleanup = Start-Process -FilePath $uninstaller -ArgumentList @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
        ) -Wait -PassThru
        if ($cleanup.ExitCode -ne 0) {
            Write-Warning "Temporary installer upgrade cleanup exited with code $($cleanup.ExitCode)."
        }
    }
    $env:APPDATA = $originalAppData
    $env:LOCALAPPDATA = $originalLocalAppData
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
