"""Backup quotidien de la collection Qdrant SIA.

Qdrant expose une API native de snapshot — un fichier ``.snapshot`` par
collection, contenant l'index HNSW + les payloads. On le télécharge en
local et on applique une rotation FIFO.

Usage :
    python scripts/backup_qdrant.py
    python scripts/backup_qdrant.py --collection scenario_chunks --keep 14
    python scripts/backup_qdrant.py --url http://qdrant:6333

Restauration : uploader le snapshot via l'API ``PUT /collections/
{name}/snapshots/upload`` ou ``qdrant-restore`` (cf. doc Qdrant).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backup_qdrant")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup SIA Qdrant collection.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:6333",
        help="URL de l'API Qdrant. Par défaut : http://127.0.0.1:6333",
    )
    parser.add_argument(
        "--collection",
        default="scenario_chunks",
        help="Collection à sauvegarder.",
    )
    parser.add_argument(
        "--output",
        default="data/backups/qdrant",
        help="Répertoire de destination. Sera créé si absent.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="Nombre de snapshots à conserver (défaut : 14).",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="API key Qdrant si la cible en exige une.",
    )
    return parser.parse_args()


def _request(
    method: str,
    url: str,
    *,
    api_key: str = "",
    timeout: int = 60,
) -> bytes:
    req = urllib.request.Request(url, method=method)
    if api_key:
        req.add_header("api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        logger.error(
            "Qdrant %s %s failed: %s %s",
            method,
            url,
            exc.code,
            exc.reason,
        )
        raise SystemExit(2) from exc
    except urllib.error.URLError as exc:
        logger.error("Qdrant unreachable at %s: %s", url, exc.reason)
        raise SystemExit(2) from exc


def _create_snapshot(base_url: str, collection: str, api_key: str) -> str:
    """Trigger ``POST /collections/{name}/snapshots`` and return its name."""
    import json

    url = urljoin(base_url + "/", f"collections/{collection}/snapshots")
    raw = _request("POST", url, api_key=api_key, timeout=300)
    payload = json.loads(raw.decode("utf-8"))
    result = payload.get("result") or {}
    name = result.get("name")
    if not name:
        logger.error("Unexpected snapshot response: %s", payload)
        raise SystemExit(2)
    logger.info("Snapshot created on the server: %s", name)
    return name


def _download_snapshot(
    base_url: str,
    collection: str,
    snapshot_name: str,
    destination: Path,
    api_key: str,
) -> None:
    url = urljoin(
        base_url + "/",
        f"collections/{collection}/snapshots/{snapshot_name}",
    )
    req = urllib.request.Request(url, method="GET")
    if api_key:
        req.add_header("api-key", api_key)
    logger.info("Downloading snapshot to %s", destination)
    try:
        with (
            urllib.request.urlopen(req, timeout=600) as resp,
            destination.open("wb") as out,
        ):
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.URLError as exc:
        logger.error("Snapshot download failed: %s", exc)
        raise SystemExit(2) from exc


def _rotate(output_dir: Path, prefix: str, keep: int) -> None:
    if keep <= 0:
        return
    snapshots = sorted(
        output_dir.glob(f"{prefix}_*.snapshot"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in snapshots[keep:]:
        logger.info("Removing old snapshot: %s", stale.name)
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", stale, exc)


def main() -> int:
    args = _parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    snapshot_name = _create_snapshot(args.url, args.collection, args.api_key)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    archive = output_dir / f"{args.collection}_{timestamp}.snapshot"

    _download_snapshot(
        base_url=args.url,
        collection=args.collection,
        snapshot_name=snapshot_name,
        destination=archive,
        api_key=args.api_key,
    )

    size_mb = archive.stat().st_size / (1024 * 1024)
    elapsed = time.monotonic() - started
    logger.info(
        "Snapshot saved: %s (%.2f MB in %.1fs)", archive, size_mb, elapsed
    )

    _rotate(output_dir, args.collection, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
