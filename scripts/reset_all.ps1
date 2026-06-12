# Remet a zero toutes les traces d'analyses precedentes.
# Usage : .\scripts\reset_all.ps1   (depuis C:\sia)
#
# Vide : MongoDB (analyses + jobs), data/raw/*.pdf, collection Qdrant.
# A faire en plus : localStorage.clear() dans la console navigateur.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== 1/3 MongoDB ===" -ForegroundColor Cyan
python -c "from pymongo import MongoClient
db=MongoClient('mongodb://127.0.0.1:27017')['sia']
print('  analyses supprimees :', db.analyses.delete_many({}).deleted_count)
print('  jobs supprimes      :', db.analysis_jobs.delete_many({}).deleted_count)"

Write-Host "`n=== 2/3 Dossier raw ===" -ForegroundColor Cyan
$rawDir = Join-Path $repoRoot "data\raw"
$pdfs = Get-ChildItem $rawDir -Filter *.pdf -ErrorAction SilentlyContinue
if ($pdfs) {
    $pdfs | Remove-Item -Force
    Write-Host "  $($pdfs.Count) PDF(s) supprime(s)."
} else {
    Write-Host "  Deja vide."
}

Write-Host "`n=== 3/3 Qdrant ===" -ForegroundColor Cyan
try {
    Invoke-RestMethod -Method Delete -Uri http://localhost:6333/collections/scenario_chunks -ErrorAction Stop | Out-Null
    Write-Host "  Collection scenario_chunks supprimee."
} catch {
    Write-Host "  Collection deja absente (ou Qdrant injoignable)."
}

Write-Host "`nReset termine." -ForegroundColor Green
Write-Host "Pense a :" -ForegroundColor Yellow
Write-Host "  - Redemarrer le backend pour recreer la collection Qdrant vide."
Write-Host "  - Vider le cache navigateur : F12 > Console > localStorage.clear(); location.reload();"
