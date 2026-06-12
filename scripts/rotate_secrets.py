"""Génère un jeu complet de secrets frais pour un déploiement.

Sortie : un bloc à coller dans `.env` (ou à pousser dans le gestionnaire
de secrets de votre cible : Vault, AWS Secrets Manager, etc.).

Usage :
    python scripts/rotate_secrets.py
    python scripts/rotate_secrets.py --password "MonMotDePasseTresFort"
    python scripts/rotate_secrets.py --no-otp

Aucun fichier n'est écrit : tout va sur stdout. Vous décidez où placer
les secrets.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from getpass import getpass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.auth import hash_password  # noqa: E402
from backend.core.totp import generate_secret  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère des secrets frais (JWT, mot de passe admin, TOTP)."
    )
    parser.add_argument(
        "--password",
        help="Mot de passe admin en clair. Si omis, sera demandé en interactif.",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Nom d'utilisateur admin (défaut : admin).",
    )
    parser.add_argument(
        "--no-otp",
        action="store_true",
        help="Ne pas générer de secret TOTP (désactive la 2FA).",
    )
    parser.add_argument(
        "--jwt-bytes",
        type=int,
        default=64,
        help="Taille du JWT secret en octets (défaut : 64).",
    )
    return parser.parse_args()


def _resolve_password(provided: str | None) -> str:
    if provided is not None:
        if not provided.strip():
            raise SystemExit("Le mot de passe ne peut pas être vide.")
        return provided
    pw1 = getpass("Mot de passe admin : ")
    if not pw1.strip():
        raise SystemExit("Le mot de passe ne peut pas être vide.")
    pw2 = getpass("Confirmation         : ")
    if pw1 != pw2:
        raise SystemExit("Les mots de passe ne correspondent pas.")
    return pw1


def main() -> int:
    args = _parse_args()

    password = _resolve_password(args.password)
    jwt_secret = secrets.token_urlsafe(args.jwt_bytes)
    password_hash = hash_password(password)
    otp_secret = "" if args.no_otp else generate_secret()

    print()
    print("=" * 72)
    print("Secrets générés — collez ces lignes dans votre .env (ou Vault)")
    print("=" * 72)
    print()
    print(f"SIA_ADMIN_USERNAME={args.username}")
    print(f"SIA_ADMIN_PASSWORD_HASH={password_hash}")
    print(f"SIA_JWT_SECRET={jwt_secret}")
    if otp_secret:
        print(f"SIA_OTP_ENABLED=true")
        print(f"SIA_OTP_SECRET={otp_secret}")
    else:
        print("SIA_OTP_ENABLED=false")
    print()
    print("=" * 72)
    print(
        "Rappel : ne committez JAMAIS le `.env` final. Le fichier est dans "
        ".gitignore mais double-vérifiez avec `git status` avant tout push."
    )
    if otp_secret:
        print(
            "Pour l'enregistrement du TOTP dans une app authenticator, "
            "utilisez le secret ci-dessus en base32."
        )
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
