"""Backup quotidien de la base MongoDB SIA.

Stratégie : un dump par jour avec ``mongodump``, compressé, horodaté,
rotation automatique (rétention configurable). Aucune dépendance Python
exotique — on shell-out vers ``mongodump`` qui est dispo dans l'image
``mongo:7`` (et installable via ``mongodb-database-tools`` ailleurs).

Usage :
    python scripts/backup_mongodb.py
    python scripts/backup_mongodb.py --output /var/backups/mongo --keep 14
    python scripts/backup_mongodb.py --uri mongodb://user:pw@host:27017/sia

Sortie : ``OUTPUT/sia_YYYYMMDD_HHMMSS.archive.gz``. Pour restaurer :
    mongorestore --gzip --archive=sia_20260101_023000.archive.gz
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backup_mongodb")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup SIA MongoDB.")
    parser.add_argument(
        "--uri",
        default="mongodb://127.0.0.1:27017",
        help="URI MongoDB. Par défaut : mongodb://127.0.0.1:27017",
    )
    parser.add_argument(
        "--database",
        default="sia",
        help="Nom de la base à dumper (défaut : sia).",
    )
    parser.add_argument(
        "--output",
        default="data/backups/mongodb",
        help="Répertoire de destination. Sera créé si absent.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="Nombre de dumps les plus récents à conserver (défaut : 14).",
    )
    return parser.parse_args()


def _ensure_tool_available() -> None:
    if shutil.which("mongodump") is None:
        logger.error(
            "mongodump introuvable dans le PATH. Installez "
            "mongodb-database-tools (https://www.mongodb.com/try/download/database-tools)."
        )
        raise SystemExit(2)


def _run_dump(uri: str, database: str, archive_path: Path) -> None:
    cmd = [
        "mongodump",
        "--uri",
        uri,
        "--db",
        database,
        "--gzip",
        "--archive",
        str(archive_path),
    ]
    logger.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        logger.error("mongodump failed with exit code %s", exc.returncode)
        raise SystemExit(exc.returncode) from exc


def _rotate(output_dir: Path, keep: int) -> None:
    if keep <= 0:
        return
    archives = sorted(
        output_dir.glob("sia_*.archive.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in archives[keep:]:
        logger.info("Removing old archive: %s", stale.name)
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", stale, exc)


def main() -> int:
    args = _parse_args()
    _ensure_tool_available()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    archive = output_dir / f"sia_{timestamp}.archive.gz"

    _run_dump(args.uri, args.database, archive)
    size_mb = archive.stat().st_size / (1024 * 1024)
    logger.info("Backup written: %s (%.2f MB)", archive, size_mb)

    _rotate(output_dir, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
