param([Parameter(Mandatory)][string]$Destination)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$source = (Resolve-Path "$PSScriptRoot/../native_desktop").Path
$build = Join-Path $env:RUNNER_TEMP "media-downloader-native-build"

cmake -S $source -B $build -A x64
cmake --build $build --config Release --parallel 2
ctest --test-dir $build -C Release --output-on-failure

$package = [IO.Path]::GetFullPath($Destination, $PWD.Path)
New-Item -ItemType Directory -Path $package -Force | Out-Null
$exe = Join-Path $package "MediaDownloaderDesktop.exe"
Copy-Item (Join-Path $build "Release/MediaDownloaderDesktop.exe") $exe -Force

# The packaged copy must start, build every window and exit cleanly.
$process = Start-Process $exe -ArgumentList "--self-test" -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Packaged native desktop failed its smoke test ($($process.ExitCode))."
}
