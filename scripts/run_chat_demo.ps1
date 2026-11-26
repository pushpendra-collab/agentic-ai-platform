# Local test chat: the agent API with the chat page at http://127.0.0.1:8010/chat
# (port 8010: 8000 is used by another local project).
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_chat_demo.ps1
#
# Uses the InvenLogic TEST replica (docker/erp-test, TEST data only) and the LLM from .env
# (Ollama). Settings below apply to this process only; .env is not changed. Development
# mode only: the chat page and its development tokens never exist in production.
# Stop with Ctrl+C.

param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

# 1. The TEST replica must be running.
$state = (docker inspect -f "{{.State.Health.Status}}" agentic-erp-test 2>$null)
if ($state -ne "healthy") {
    Write-Host "Starting the InvenLogic TEST replica (docker/erp-test)..."
    docker compose -f docker/erp-test/compose.yml up -d | Out-Null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if ((docker inspect -f "{{.State.Health.Status}}" agentic-erp-test 2>$null) -eq "healthy") { break }
    }
}

# 2. The VERIFIED contract for the replica (the real InvenLogic contract + replica databases).
$contract = Join-Path $env:TEMP "agentic_erp_replica_contract.json"
& $python -c "import sys; sys.path.insert(0, '.'); from tests.erp_db import replica_contract; open(sys.argv[1], 'w', encoding='utf-8').write(replica_contract().model_dump_json(indent=2))" $contract

# 3. Settings for this process only.
$reader = "postgresql://agentic_erp_reader:erp_reader_dev@127.0.0.1:5434"
$port = 8010
$env:API_PORT = "$port"
$env:APP_ENV = "development"
$env:ERP_ADAPTER = "postgres"
$env:ERP_CONTRACT_PATH = $contract
$env:ERP_DATABASE_URLS = (@{
    master_data = "$reader/master_data_db"
    sales       = "$reader/sales_db"
    inventory   = "$reader/inventory_db"
} | ConvertTo-Json -Compress)
$env:ERP_SSL_MODE = "disable"
$env:ERP_BUSINESS_TIMEZONE = "Asia/Kolkata"
# A fresh development token key per run (tokens die with the process).
$env:AUTH_TOKEN_SECRET = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
$env:AUTH_ROLE_CATALOG_JSON = & $python -c "import json; from agentic_platform.security.permissions import DEFAULT_ROLES as R; r = {k: sorted(v) for k, v in R.items()}; r['sales_viewer'] = ['business.customer.read', 'business.sales.read']; r['stock_viewer'] = ['business.inventory.read', 'business.warehouse.all']; print(json.dumps(r))"
# Ollama on CPU: one answer can take several minutes.
if (-not $env:LLM_TIMEOUT_SECONDS) { $env:LLM_TIMEOUT_SECONDS = "900" }
$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
Write-Host "Chat page: http://127.0.0.1:$port/chat   (demo company id 900000001; Ctrl+C to stop)"
Write-Host ""
if (-not $NoBrowser) {
    # Open the page once the server answers.
    Start-Job -ScriptBlock {
        $p = $using:port
        for ($i = 0; $i -lt 60; $i++) {
            try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$p/health" -TimeoutSec 2 | Out-Null; break }
            catch { Start-Sleep -Seconds 1 }
        }
        Start-Process "http://127.0.0.1:$p/chat"
    } | Out-Null
}
& $python -m agentic_platform.api
