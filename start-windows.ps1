# =============================================================================
# SOInsight - one-command launcher (Windows / PowerShell)
# Run:  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
# Dev:  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -Dev
# Installs deps, writes config, builds the UI, starts ONE server, opens it.
# =============================================================================
param([switch]$Dev)

# ===================== ONE-TIME CONFIG - edit these once =====================
$SO_BASE_URL             = "https://stackenterprise.co/api/v3"
$SO_API_KEY              = "PASTE_YOUR_API_KEY_HERE"
$SO_TEAM                 = ""
$OLLAMA_URL              = "http://localhost:11434"
$OLLAMA_MODEL            = "llama3.1:8b"
$DEFAULT_TAGS            = "cloudsql,cloudspanner,cloudstorage"
$ENABLE_SCHEDULE         = $true
$SCHEDULE_INTERVAL_HOURS = 24
$SCHEDULE_WINDOW_DAYS    = 90
# =============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ROOT
Write-Host "==> SOInsight launcher  ($ROOT)"

if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --quiet --upgrade pip
try { pip install --quiet -e .\backend }
catch { if (Test-Path "backend\requirements.txt") { pip install --quiet -r backend\requirements.txt } }

if (-not (Test-Path "frontend\node_modules")) {
  Push-Location frontend; npm install --silent; Pop-Location
}

@"
SO_BASE_URL=$SO_BASE_URL
SO_API_KEY=$SO_API_KEY
SO_TEAM=$SO_TEAM
OLLAMA_URL=$OLLAMA_URL
OLLAMA_MODEL=$OLLAMA_MODEL
DEFAULT_TAGS=$DEFAULT_TAGS
ENABLE_SCHEDULE=$($ENABLE_SCHEDULE.ToString().ToLower())
SCHEDULE_INTERVAL_HOURS=$SCHEDULE_INTERVAL_HOURS
SCHEDULE_WINDOW_DAYS=$SCHEDULE_WINDOW_DAYS
DB_PATH=./data/soinsight.db
CHROMA_PATH=./data/chroma
LOG_LEVEL=INFO
"@ | Set-Content -Encoding UTF8 backend\.env

if (Get-Command ollama -ErrorAction SilentlyContinue) {
  $modelRoot = $OLLAMA_MODEL.Split(":")[0]
  if (-not ((ollama list) -match $modelRoot)) { ollama pull $OLLAMA_MODEL }
} else {
  Write-Host "WARN: ollama not found - classification needs it (https://ollama.com)"
}

Remove-Item Env:\SSL_CERT_FILE -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path backend\data | Out-Null

function Enable-Schedule($base) {
  if ($ENABLE_SCHEDULE) {
    $prods = ($DEFAULT_TAGS.Split(",") | ForEach-Object { '"' + $_.Trim() + '"' }) -join ","
    $body  = "{""enabled"":true,""interval_hours"":$SCHEDULE_INTERVAL_HOURS,""products"":[$prods],""window_days"":$SCHEDULE_WINDOW_DAYS}"
    try { Invoke-RestMethod -Method Put -Uri "$base/api/schedule" -ContentType "application/json" -Body $body | Out-Null } catch {}
  }
}

function Wait-Health($base) {
  for ($i=0; $i -lt 40; $i++) {
    try { Invoke-WebRequest -UseBasicParsing "$base/health" -TimeoutSec 2 | Out-Null; return } catch { Start-Sleep 1 }
  }
}

if ($Dev) {
  Write-Host "==> DEV mode: backend :8000 + frontend :5173"
  Start-Process powershell -ArgumentList @("-NoExit","-Command","cd '$ROOT\backend'; `$env:SSL_CERT_FILE=''; ..\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000")
  Wait-Health "http://localhost:8000"
  Enable-Schedule "http://localhost:8000"
  Start-Process powershell -ArgumentList @("-NoExit","-Command","cd '$ROOT\frontend'; npm run dev")
  Start-Sleep 3
  Start-Process "http://localhost:5173"
} else {
  Write-Host "==> Building UI..."
  Push-Location frontend; npm run build --silent; Pop-Location
  Write-Host "==> Starting SOInsight on http://localhost:8000 ..."
  Start-Process powershell -ArgumentList @("-NoExit","-Command","cd '$ROOT\backend'; `$env:SSL_CERT_FILE=''; ..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000")
  Wait-Health "http://localhost:8000"
  Enable-Schedule "http://localhost:8000"
  Start-Process "http://localhost:8000"
  Write-Host ""
  Write-Host "============================================================"
  Write-Host " SOInsight running (single process):  http://localhost:8000"
  Write-Host " API docs:                            http://localhost:8000/docs"
  Write-Host " Close the backend PowerShell window to stop."
  Write-Host "============================================================"
}
