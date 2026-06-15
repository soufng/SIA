# SIA — déploiement local PowerShell.
#
# Reproduit ce que fait le job ``deploy:local`` du .gitlab-ci.yml, mais
# en exécution manuelle. Utile quand :
#   - le runner GitLab est down / pas démarré
#   - tu veux tester un déploiement sans pousser sur dev
#   - le pipeline CI est désactivé pour une raison ou une autre
#
# Pré-requis :
#   - Docker Desktop démarré
#   - Node.js + npm dans le PATH
#   - Stack ``db`` (Mongo/Qdrant) déjà up via docker-compose.db.yml
#
# Usage (depuis la racine du projet) :
#   .\scripts\deploy.ps1                  # build + restart
#   .\scripts\deploy.ps1 -SkipFrontend    # rebuild backend uniquement
#   .\scripts\deploy.ps1 -SkipBackend     # rebuild frontend dist uniquement

param(
  [switch]$SkipFrontend,
  [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "==> SIA deploy — racine: $ProjectRoot" -ForegroundColor Cyan

if (-not $SkipFrontend) {
  Write-Host "==> Rebuild frontend (vite)" -ForegroundColor Cyan
  Set-Location frontend
  npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
  Set-Location $ProjectRoot
} else {
  Write-Host "==> Frontend skip" -ForegroundColor Yellow
}

if (-not $SkipBackend) {
  Write-Host "==> Rebuild backend image + restart stack" -ForegroundColor Cyan
  docker compose -f docker-compose.full.yml up --build -d
  if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
} else {
  Write-Host "==> Backend skip" -ForegroundColor Yellow
}

Write-Host "==> État final des conteneurs" -ForegroundColor Cyan
docker compose -f docker-compose.full.yml ps

Write-Host ""
Write-Host "Frontend : http://localhost:8080" -ForegroundColor Green
Write-Host "Backend  : http://localhost:8000" -ForegroundColor Green
Write-Host "Docs API : http://localhost:8000/docs" -ForegroundColor Green
