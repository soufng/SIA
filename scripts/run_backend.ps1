# Lance le backend FastAPI en local sur http://127.0.0.1:8000
# Usage : pwsh scripts/run_backend.ps1   (depuis la racine du repo)

$ErrorActionPreference = "Stop"

# Se place a la racine du repo (parent du dossier scripts/)
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Active l'environnement virtuel s'il existe (.venv puis venv)
$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    $venvActivate = Join-Path $repoRoot "venv\Scripts\Activate.ps1"
}
if (Test-Path $venvActivate) {
    Write-Host "Activation de l'environnement virtuel : $venvActivate"
    . $venvActivate
} else {
    Write-Host "Aucun venv detecte, utilisation du Python systeme."
}

# Charge les variables du fichier .env (lignes KEY=VALUE, hors commentaires)
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $key = $line.Substring(0, $idx).Trim()
            $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
    Write-Host "Variables .env chargees."
}

# Demarre uvicorn avec rechargement automatique
$host_ = if ($env:SIA_BACKEND_HOST) { $env:SIA_BACKEND_HOST } else { "127.0.0.1" }
$port  = if ($env:SIA_BACKEND_PORT) { $env:SIA_BACKEND_PORT } else { "8000" }

Write-Host "Demarrage de uvicorn sur http://${host_}:${port}"
python -m uvicorn backend.main:create_app --factory --host $host_ --port $port --reload
