param([Parameter(Mandatory)][string]$Destination)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$qtVersion = "6.8.3"
$toolRoot = Join-Path $env:RUNNER_TEMP "media-downloader-native-tools"
$kit = Join-Path $toolRoot "qt"
$python = (Get-Command python).Source
$qt = Join-Path $kit "$qtVersion/mingw_64"
$compiler = Join-Path $kit "Tools/mingw1310_64/bin"
$source = (Resolve-Path "$PSScriptRoot/../native_desktop").Path
$build = Join-Path $toolRoot "build"

& $python -m pip install "aqtinstall==3.3.0" "cmake==3.31.8" "ninja==1.12.1.4"
if (-not (Test-Path "$qt/bin/windeployqt.exe")) {
    & $python -m aqt install-qt windows desktop $qtVersion win64_mingw --outputdir $kit --archives qtbase
}
if (-not (Test-Path "$compiler/g++.exe")) {
    & $python -m aqt install-tool windows desktop tools_mingw1310 qt.tools.win64_mingw1310 --outputdir $kit
}
$originalPath = $env:PATH
$env:PATH = "$compiler;$qt/bin;$env:PATH"
$env:QT_QPA_PLATFORM = "offscreen"

$cmakeArgs = @("-S", $source, "-B", $build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_PREFIX_PATH=$qt", "-DCMAKE_CXX_COMPILER=$compiler/g++.exe")
& cmake @cmakeArgs
& cmake --build $build --parallel 2
& ctest --test-dir $build --output-on-failure

$package = [IO.Path]::GetFullPath($Destination, $PWD.Path)
New-Item -ItemType Directory -Path $package -Force | Out-Null
$exe = Join-Path $package "MediaDownloaderDesktop.exe"
Copy-Item "$build/MediaDownloaderDesktop.exe" $exe -Force
$deployArgs = @("--release", "--no-translations", "--no-system-d3d-compiler", "--no-opengl-sw",
    "--dir", $package, $exe)
& windeployqt @deployArgs
$plugin = Join-Path $package "platforms/qoffscreen.dll"
if (-not (Test-Path $plugin)) {
    Copy-Item "$qt/plugins/platforms/qoffscreen.dll" $plugin
}
$env:PATH = $originalPath
$process = Start-Process $exe -ArgumentList "--self-test" -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Deployed native desktop failed its smoke test ($($process.ExitCode))."
}
