#!/usr/bin/env bash
# Backup unifié SPM : Mongo + Qdrant en un seul appel.
#
# À planifier via cron (par exemple tous les jours à 2h du matin) :
#
#   0 2 * * * /opt/spm/scripts/backup_all.sh >> /var/log/spm-backup.log 2>&1
#
# Variables d'environnement reconnues (toutes optionnelles) :
#   MONGO_URI         : URI complète, défaut mongodb://127.0.0.1:27017
#   MONGO_DB          : nom de la base, défaut spm
#   QDRANT_URL        : URL du service Qdrant, défaut http://127.0.0.1:6333
#   QDRANT_COLLECTION : collection à sauvegarder, défaut scenario_chunks
#   BACKUP_ROOT       : répertoire racine, défaut /var/backups/spm
#   BACKUP_KEEP       : nombre de copies conservées, défaut 14
#
# Sortie en cas d'erreur : code retour != 0 + log explicite, parfait pour
# qu'un monitoring (Healthchecks.io / Cronitor) déclenche une alerte.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/spm}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:27017}"
MONGO_DB="${MONGO_DB:-spm}"

QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-scenario_chunks}"

mkdir -p "${BACKUP_ROOT}/mongodb" "${BACKUP_ROOT}/qdrant"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === SPM backup starting ==="

"${PYTHON}" scripts/backup_mongodb.py \
  --uri "${MONGO_URI}" \
  --database "${MONGO_DB}" \
  --output "${BACKUP_ROOT}/mongodb" \
  --keep "${BACKUP_KEEP}"

"${PYTHON}" scripts/backup_qdrant.py \
  --url "${QDRANT_URL}" \
  --collection "${QDRANT_COLLECTION}" \
  --output "${BACKUP_ROOT}/qdrant" \
  --keep "${BACKUP_KEEP}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === SPM backup finished ==="
