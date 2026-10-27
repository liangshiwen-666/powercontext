param(
  [int]$AppPort = 8080,
  [int]$PcPort = 8000
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing .venv. Run: uv sync --python 3.12" }
if ((Get-Content ".env" -Raw) -match "SEEKDB_MODE\s*=\s*embedded") {
  Write-Warning "SEEKDB_MODE=embedded is not supported on Windows. Set SEEKDB_MODE=remote and start the seekdb Docker container (see README)."
}

$pcExe = $null
$cmd = Get-Command powercontext -ErrorAction SilentlyContinue
if ($cmd) { $pcExe = $cmd.Source }
if (-not $pcExe) {
  $candidate = Join-Path $env:USERPROFILE ".local\bin\powercontext.exe"
  if (Test-Path $candidate) { $pcExe = $candidate }
}
if (-not $pcExe) { throw 'powercontext not found. Install it with: uv tool install "powercontext[cli,server]==1.4.1"' }

$pcUrl = "http://127.0.0.1:$PcPort"
$up = $false
try { Invoke-RestMethod "$pcUrl/v1/server-info" -TimeoutSec 2 | Out-Null; $up = $true } catch { }
if (-not $up) {
  $pcHome = Join-Path $root "data\powercontext"
  New-Item -ItemType Directory -Force -Path $pcHome | Out-Null
  "POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`nPOWERCONTEXT_SERVER_ACCESS_MODE=disabled" |
    Set-Content -Encoding ASCII (Join-Path $pcHome "pc.env")
  Write-Host "Starting PowerContext at $pcUrl ..."
  Start-Process -FilePath $pcExe `
    -ArgumentList @("server", "run", "--host", "127.0.0.1", "--port", "$PcPort", "--env-file", "pc.env") `
    -WorkingDirectory $pcHome -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $pcHome "server.out.log") `
    -RedirectStandardError (Join-Path $pcHome "server.err.log")
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "$pcUrl/v1/server-info" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 }
  }
}
if (-not $up) { throw "PowerContext failed to start. See data\powercontext\server.err.log" }

$AppHost = $env:APP_HOST
if (-not $AppHost) { $AppHost = "127.0.0.1" }

$env:POWERCONTEXT_BASE_URL = $pcUrl
if (-not $env:HF_ENDPOINT) {
  Write-Host "Tip: in mainland China, set HF_ENDPOINT=https://hf-mirror.com before the first run."
}
Write-Host "PowerContext: $pcUrl"
Write-Host "App: http://${AppHost}:$AppPort"
& $py -m uvicorn app.main:app --host $AppHost --port $AppPort
