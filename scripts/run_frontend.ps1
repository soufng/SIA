# Lance le frontend Vite en local sur http://127.0.0.1:5173
# Usage : pwsh scripts/run_frontend.ps1   (depuis la racine du repo)

$ErrorActionPreference = "Stop"

# Se place dans le dossier frontend (frere du dossier scripts/)
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "frontend"
Set-Location $frontendDir

# Installe les dependances si node_modules est absent
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "node_modules absent, execution de npm install..."
    npm install
}

# Vite ecoute par defaut sur localhost (IPv6 sur Windows). On force IPv4 pour
# matcher l'origine 127.0.0.1:5173 listee dans la CORS du backend.
$vitePort = if ($env:SIA_FRONTEND_PORT) { $env:SIA_FRONTEND_PORT } else { "5173" }

Write-Host "Demarrage de Vite sur http://127.0.0.1:${vitePort}"
npm run dev -- --host 127.0.0.1 --port $vitePort
