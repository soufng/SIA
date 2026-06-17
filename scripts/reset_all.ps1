# Remet a zero toutes les traces d'analyses precedentes.
# Usage : .\scripts\reset_all.ps1   (depuis C:\sia)
#
# Sequence :
#   1. Nettoie les PDF de data/raw/ (host + container, pendant que le
#      backend tourne encore — sinon docker exec echoue).
#   2. Arrete sia_backend pour figer toute reindexation pendant le reset.
#   3. Vide MongoDB (analyses + jobs).
#   4. Supprime la collection Qdrant scenario_chunks.
#   5. Redemarre sia_backend (recree une collection Qdrant vide).
#
# Detecte automatiquement si Mongo / backend tournent dans Docker
# (containers db_mongodb / sia_backend) ou en natif.
#
# A faire en plus cote navigateur :
#   F12 > Console > localStorage.clear(); location.reload();
#   puis Ctrl+Shift+R sur Historique / Statistiques / Accueil.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Test-DockerContainer($name) {
    try {
        $out = docker ps -a --filter "name=^/$name$" --format "{{.Names}}" 2>$null
        return ($out -eq $name)
    } catch {
        return $false
    }
}

function Test-DockerContainerRunning($name) {
    try {
        $out = docker ps --filter "name=^/$name$" --format "{{.Names}}" 2>$null
        return ($out -eq $name)
    } catch {
        return $false
    }
}

# --- 1. Dossier raw (avant d'arreter le backend, sinon docker exec ko) ---
Write-Host "=== 1/5 Dossier raw ===" -ForegroundColor Cyan
$rawDir = Join-Path $repoRoot "data\raw"
$pdfs = Get-ChildItem $rawDir -Filter *.pdf -ErrorAction SilentlyContinue
if ($pdfs) {
    $pdfs | Remove-Item -Force
    Write-Host "  $($pdfs.Count) PDF(s) supprime(s) cote host."
} else {
    Write-Host "  data\raw\ (host) deja vide."
}
if (Test-DockerContainerRunning "sia_backend") {
    $count = (docker exec sia_backend sh -c "ls /app/data/raw 2>/dev/null | wc -l" 2>$null) -as [string]
    docker exec sia_backend sh -c "rm -f /app/data/raw/*.pdf" 2>$null | Out-Null
    Write-Host "  $($count.Trim()) PDF(s) supprime(s) dans le container sia_backend."
} elseif (Test-DockerContainer "sia_backend") {
    Write-Host "  Container sia_backend arrete : nettoyage interne ignore."
    Write-Host "  Demarrez-le et relancez le script si /app/data/raw doit etre vide."
}

# --- 2. Arret du backend : evite qu'il reindexe pendant qu'on vide DB ---
$backendWasRunning = $false
Write-Host "`n=== 2/5 Arret du backend ===" -ForegroundColor Cyan
if (Test-DockerContainerRunning "sia_backend") {
    Write-Host "  Conteneur sia_backend en cours, arret..."
    docker stop sia_backend | Out-Null
    $backendWasRunning = $true
    Write-Host "  Arrete."
} elseif (Test-DockerContainer "sia_backend") {
    Write-Host "  Conteneur sia_backend deja arrete."
} else {
    Write-Host "  Pas de conteneur Docker (backend natif). Pensez a l'arreter si actif."
}

Write-Host "`n=== 3/5 MongoDB ===" -ForegroundColor Cyan
if (Test-DockerContainerRunning "db_mongodb") {
    Write-Host "  Conteneur Docker db_mongodb detecte."
    docker exec db_mongodb mongosh --quiet --eval @"
db=db.getSiblingDB('sia');
print('  analyses supprimees :', db.analyses.deleteMany({}).deletedCount);
print('  jobs supprimes      :', db.analysis_jobs.deleteMany({}).deletedCount);
"@
} else {
    Write-Host "  Mongo natif (127.0.0.1:27017)."
    python -c "from pymongo import MongoClient
db=MongoClient('mongodb://127.0.0.1:27017')['sia']
print('  analyses supprimees :', db.analyses.delete_many({}).deleted_count)
print('  jobs supprimes      :', db.analysis_jobs.delete_many({}).deleted_count)"
}

Write-Host "`n=== 4/5 Qdrant ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Method Delete -Uri http://localhost:6333/collections/scenario_chunks -ErrorAction Stop | Out-Null
    Write-Host "  Collection scenario_chunks supprimee."
} catch {
    Write-Host "  Collection deja absente (ou Qdrant injoignable)."
}

Write-Host "`n=== 5/5 Redemarrage du backend ===" -ForegroundColor Cyan
if ($backendWasRunning) {
    Write-Host "  Redemarrage de sia_backend..."
    docker start sia_backend | Out-Null
    Write-Host "  Redemarre. La collection Qdrant sera recreee vide au prochain demarrage."
} elseif (Test-DockerContainer "sia_backend") {
    Write-Host "  Backend Docker non redemarre (il etait arrete au depart)."
} else {
    Write-Host "  Pensez a redemarrer manuellement votre backend natif."
}

Write-Host "`nReset termine." -ForegroundColor Green
Write-Host "Cote navigateur :" -ForegroundColor Yellow
Write-Host "  F12 > Console > localStorage.clear(); location.reload();"
Write-Host "  Puis Ctrl+Shift+R sur Historique / Statistiques / Accueil."
