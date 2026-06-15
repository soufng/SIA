# SIA — Plateforme d'analyse de scénarios

API et interface web pour l'analyse de scénarios PDF (CCM) : extraction,
détection de plagiat sémantique, modération multilingue, contrôle des
constantes nationales marocaines, rapport éditorial RAG.

## Stack

| Couche | Technologie |
|---|---|
| Backend | FastAPI 0.11x · Python 3.12 · slowapi |
| Frontend | React 18 · Vite · TypeScript · Tailwind · TanStack Query |
| Base relationnelle | MongoDB 7 |
| Base vectorielle | Qdrant (cosine, HNSW) |
| Embeddings | Sentence Transformers — `intfloat/multilingual-e5-base` (768d) |
| PDF | PyMuPDF (backend) · jsPDF (client) |
| LLM (optionnel) | Ollama (`llama3.2:3b`) · OpenAI · Anthropic |

## Démarrage rapide en développement

```bash
# Bases de données
docker compose up -d

# Backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend && npm install && npm run dev
```

L'API est sous `/api/v1`, l'UI sur http://localhost:5173.

## Variables d'environnement

Copier `.env.example` en `.env` et remplir. Les secrets critiques peuvent
être générés en une commande :

```bash
python scripts/rotate_secrets.py
```

Variables non-évidentes :

| Variable | Effet |
|---|---|
| `SIA_ENV` | `development` (défaut) ou `production`. En prod, l'API refuse de démarrer avec les secrets par défaut. |
| `SIA_UPLOAD_MAX_MB` | Taille max d'un PDF accepté (défaut : 20 Mo). |
| `SIA_PLAGIARISM_SIMILARITY_THRESHOLD` | Seuil cosine pour qu'un match Qdrant soit retenu (défaut : 0.60). |
| `SIA_RATE_LIMIT_STORAGE` | `memory://` (défaut, mono-instance) ou `redis://host:6379` pour du multi-worker. |
| `SIA_RAG_LLM_PROVIDER` | `ollama` / `openai` / `anthropic` / `none`. Tombe sur un template déterministe si l'API est injoignable. |

Cf. [`.env.example`](.env.example) pour la liste complète.

## Tests

```bash
# Backend
pytest tests/

# Frontend (typecheck + build)
cd frontend && npm run typecheck && npm run build
```

## Déploiement de production

```bash
# 1. Générer les secrets
python scripts/rotate_secrets.py > /tmp/secrets.env
cp .env.example .env.production
# Coller les secrets dans .env.production et y mettre SIA_ENV=production

# 2. Builder
docker build -t sia-backend:prod .
cd frontend && npm ci && npm run build && cd ..

# 3. Lancer la stack
export ACME_EMAIL=ops@votre-domaine.fr
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

La stack prod ([`docker-compose.prod.yml`](docker-compose.prod.yml)) lance :

- **Caddy** (port 80/443) — reverse proxy, certificats Let's Encrypt auto,
  HSTS, `X-Forwarded-*` propagé au backend.
- **Backend FastAPI** — image construite via [`Dockerfile`](Dockerfile),
  user non-root, 2 workers uvicorn.
- **MongoDB** — non exposé sur Internet, healthcheck `ping`.
- **Qdrant** — non exposé sur Internet, volumes pour data + snapshots.

Avant le premier `up`, ajustez le domaine et l'e-mail Let's Encrypt dans
[`deploy/Caddyfile`](deploy/Caddyfile).

## Backups

Cron quotidien recommandé :

```cron
0 2 * * * /opt/sia/scripts/backup_all.sh >> /var/log/sia-backup.log 2>&1
```

[`scripts/backup_all.sh`](scripts/backup_all.sh) appelle :

- [`backup_mongodb.py`](scripts/backup_mongodb.py) — `mongodump --gzip --archive`
  avec rotation FIFO (14 jours par défaut).
- [`backup_qdrant.py`](scripts/backup_qdrant.py) — snapshot natif Qdrant
  téléchargé en local, même rotation.

Variables : `BACKUP_ROOT`, `BACKUP_KEEP`, `MONGO_URI`, `QDRANT_URL`.

## Rate limiting

Limites par défaut (configurables via env) :

| Endpoint | Limite |
|---|---|
| `POST /api/v1/auth/login` | 10/min (anti brute-force) |
| `POST /api/v1/uploads/analyze` | 20/h |
| `POST /api/v1/uploads/analyze/async` | 30/h |
| Autre | 120/min |

Réponse 429 en français + header `Retry-After`.

## CI/CD

Workflow GitHub Actions dans
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). Déclenché sur
push / PR vers `main` et `dev`, ou manuellement via
`workflow_dispatch`.

Trois jobs :

1. **Backend** — `pytest tests/` sur Python 3.12.
2. **Frontend** — `tsc --noEmit` strict puis `vite build`.
3. **Docker** — build l'image, smoke test (lance le container et vérifie
   que l'API répond sur `/`), puis sur `push` vers `main` ou `dev` push
   l'image vers GitHub Container Registry sous
   `ghcr.io/<owner>/sia-backend` avec les tags :
   - `dev` ou `main` (selon la branche),
   - `sha-<short-sha>` (immuable, pratique pour rollback),
   - `latest` (uniquement sur `main`).

Les PR ne pushent pas l'image (les secrets GHCR ne sont pas exposés
aux forks). Le `GITHUB_TOKEN` par défaut suffit — aucune configuration
manuelle requise.

Pour récupérer une image publiée :

```bash
docker pull ghcr.io/<owner>/sia-backend:dev
```

## Structure du repo

```
backend/
  api/v1/routes/        Endpoints FastAPI
  pipelines/            DocumentPipeline · PlagiarismPipeline · ModerationPipeline
  services/             Services métier (e5, Qdrant, RAG, LLM provider, …)
  services/pipelines/   PrincipesMarocPipeline (constantes nationales)
  repositories/         AnalysisRepository · JobsRepository (MongoDB)
  core/                 config, auth, rate_limit, totp, exceptions

frontend/
  src/pages/            Accueil · Analyse · Historique · Analytics · Login · OTP
  src/components/       UploadForm (polling), StatusCards, Navbar, …
  src/lib/              api.ts, utils.ts, pdf.ts, types.ts
  src/store/            Zustand analysis/auth

deploy/
  Caddyfile             Reverse proxy de prod

scripts/
  rotate_secrets.py     Génère JWT + hash admin + secret TOTP
  backup_mongodb.py     Dump Mongo + rotation
  backup_qdrant.py      Snapshot Qdrant + rotation
  backup_all.sh         Wrapper cron
  reindex_qdrant.py     Réindexe tous les scénarios après changement de modèle
  reset_qdrant_collection.py
```

## Pages

- **Accueil** : KPIs en temps réel, dernier résultat, analyses récentes.
- **Analyser un scénario** : upload + résultat dans la même page, polling
  async, barre de progression alimentée par le backend.
- **Historique** : liste des analyses MongoDB.
- **Analytics** : statistiques agrégées sur l'ensemble du corpus.
- **2FA TOTP** : enrôlement compatible Google / Microsoft Authenticator.
