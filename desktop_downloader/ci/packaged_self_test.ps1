param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(Mandatory = $true)][string]$Report,
  [ValidateSet("portable", "installer", "any")][string]$ExpectedMode = "any",
  [ValidateRange(30, 900)][int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$ProgressReport = [System.IO.Path]::ChangeExtension($Report, "progress.json")
Remove-Item -LiteralPath $Report, $ProgressReport -Force -ErrorAction SilentlyContinue

Write-Host "Starting packaged EXE self-test: $Exe"
$process = Start-Process -FilePath $Exe -ArgumentList @("--self-test", "--report", $Report) -PassThru
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$timedOut = $false

try {
  while ($true) {
    $process.Refresh()
    if ($process.HasExited) { break }
    if ((Get-Date) -ge $deadline) {
      $timedOut = $true
      break
    }
    Start-Sleep -Seconds 2
  }
  if ($timedOut) {
    Write-Warning "Packaged self-test timed out after $TimeoutSeconds seconds (pid $($process.Id))."
    if (Test-Path -LiteralPath $ProgressReport) {
      Write-Host "===== Self-test last checkpoint ====="
      Get-Content -LiteralPath $ProgressReport
    } else {
      Write-Warning "No progress file. The EXE is blocked before the test module starts."
    }
    throw "Packaged EXE self-test did not exit; failing fast rather than blocking the Windows release indefinitely."
  }

  Write-Host "Self-test process exit code: $($process.ExitCode)"
  if (Test-Path -LiteralPath $ProgressReport) {
    Write-Host "===== Self-test checkpoints ====="
    Get-Content -LiteralPath $ProgressReport
  }
  if (-not (Test-Path -LiteralPath $Report)) {
    throw "Packaged EXE exited without producing its self-test report."
  }

  $payload = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
  Get-Content -LiteralPath $Report
  if ($process.ExitCode -ne 0 -or $payload.ok -ne $true) {
    throw "Packaged EXE self-test failed (exit $($process.ExitCode))."
  }
  if ($ExpectedMode -ne "any" -and $payload.install_mode -ne $ExpectedMode) {
    throw "Expected $ExpectedMode install mode; got $($payload.install_mode)."
  }
  Write-Host "Packaged EXE self-test PASS ($ExpectedMode)."
} finally {
  if (-not $process.HasExited) {
    try {
      & taskkill.exe /PID $process.Id /T /F | Out-Host
    } catch {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
  }
}
