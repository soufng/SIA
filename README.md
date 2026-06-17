# SIA — Plateforme d'analyse de scénarios

API et interface web pour l'analyse de scénarios PDF (CCM) : extraction,
détection de plagiat sémantique, modération multilingue, contrôle des
constantes nationales marocaines, rapport éditorial RAG.

## Vue d'ensemble

```
                  ┌─────────────────────────────────────────────────┐
PDF ─────────────►│ DocumentPipeline                                │
                  │   PyMuPDF → TextCleaning → Chunking             │
                  └────────┬────────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┬─────────────────┐
            ▼              ▼              ▼                 ▼
   ┌────────────────┐ ┌──────────┐ ┌─────────────┐ ┌──────────────────┐
   │ Plagiat        │ │ Modé-    │ │ Constantes  │ │ Rapport RAG      │
   │ ─────────      │ │ ration   │ │ marocaines  │ │ (Ollama / OpenAI │
   │ ┌────────────┐ │ │ (FR / AR │ │ (drapeau,   │ │  / Anthropic)    │
   │ │ MinHash    │ │ │ /darija) │ │  hymne, …)  │ │                  │
   │ │ ↔ LSH      │ │ └──────────┘ └─────────────┘ └──────────────────┘
   │ │ Jaccard    │ │
   │ └────────────┘ │           (modules parallèles)
   │ ┌────────────┐ │
   │ │ e5 + Qdrant│ │
   │ │ cosinus    │ │
   │ └────────────┘ │
   │ ┌────────────┐ │
   │ │ Local hash │ │
   │ │ doublon    │ │
   │ └────────────┘ │
   └────────┬───────┘
            ▼
   ┌────────────────────────────────────────────────────────────────┐
   │ Verdict combiné + filtrage des matches sémantiques sans preuve │
   │ → Rapport HTML / PDF + persistance MongoDB                     │
   └────────────────────────────────────────────────────────────────┘
```

Détail du sous-système plagiat dans
[docs/PLAGIARISM_PIPELINE.md](docs/PLAGIARISM_PIPELINE.md).

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
| Plagiat textuel | `datasketch` — MinHash 128 perm. + LSH (Jaccard sur shingles) |

## Détection de plagiat — pipeline hybride

La détection repose sur **deux moteurs complémentaires** qui tournent en
parallèle puis se combinent au moment du verdict. Cette architecture
résout le problème classique des moteurs purement sémantiques sur
corpus stylistiquement homogène (ici, des scénarios marocains : même
format, même mélange FR / arabe / darija, même registre dramatique).

### 1. Moteur MinHash (signal **principal**)

Mesure la **reprise textuelle réelle** — c'est l'équivalent maison de ce
que Turnitin / Copyleaks utilisent en production.

```
Chunk PDF
   ↓ tokenisation + suppression stopwords (slug lines, boilerplate darija)
   ↓ shingles 5-grammes
   ↓ MinHash 128 permutations  (datasketch.MinHash)
   ↓ insertion dans l'index LSH partagé (datasketch.MinHashLSH)
   ↓
   Search → candidats avec Jaccard estimé ≥ 0.05
   ↓ Jaccard exact recalculé
   ↓ ≥ 10 % → match conservé
```

- **Index** : en mémoire, par worker uvicorn. Bootstrappé depuis
  Qdrant au démarrage et **resynchronisé à chaque analyse** (les
  nouveaux chunks upsertés par un worker arrivent à l'autre via la
  prochaine sync — voir [`minhash_service.py`](backend/services/minhash_service.py)).
- **Persistance** : aucune. Qdrant reste la source de vérité ; un
  `reset_all.ps1` qui vide Qdrant invalide aussi l'index MinHash au
  prochain démarrage.
- **Tolérance aux pannes** : si `datasketch` est absent ou si Qdrant
  est injoignable, le pipeline retombe silencieusement sur le seul
  signal sémantique (Phase 1 de la migration restée fonctionnelle).

### 2. Moteur sémantique (signal **secondaire**)

Embeddings e5-base 768d + Qdrant cosine, complété par un **composite
scoring** qui combine lexical (Jaccard sur tokens), n-grammes exacts,
overlap d'entités nommées et dialogues. Voir
[`composite_scoring.py`](backend/utils/composite_scoring.py).

Ce moteur capture **la proximité de sens et de style** — il est très
sensible aux scénarios du même registre, ce qui produisait
historiquement beaucoup de faux positifs. Il sert désormais à confirmer
les matches MinHash et à détecter les **paraphrases pures** (réécriture
totale sans réutilisation textuelle).

### Verdict combiné

Le score affiché en haut du rapport est dérivé de MinHash dès que ce
moteur trouve quelque chose :

| MinHash Jaccard | Niveau de risque |
|---|---|
| ≥ 40 % | **very_high** — copie quasi-verbatim |
| ≥ 20 % | **high** — reprise substantielle |
| ≥ 10 % | **medium** — paraphrase ou copie partielle |
| < 10 % | **low** — pas de plagiat textuel |

Le verdict UI ([`ResultsPage.tsx`](frontend/src/pages/ResultsPage.tsx))
croise les deux scores :

| MinHash | Sémantique | Verdict |
|---|---|---|
| ≥ 25 % | — | 🔴 *Plagiat textuel confirmé* |
| 10–25 % + sém. ≥ 30 % | | 🟡 *Reprise partielle ou paraphrase* |
| < 10 % + sém. ≥ 30 % | | 🟢 *Ressemblance de style — pas un plagiat* |
| < 10 % + sém. < 30 % | | 🟢 *Aucun plagiat textuel détecté* |

### Filtrage des matches affichés

Quand MinHash retourne un score nul (vrai négatif sémantique), tous les
matches issus uniquement du moteur e5 sont **filtrés du rapport**
([`plagiarism_pipeline.py`](backend/pipelines/plagiarism_pipeline.py)).
On évite ainsi de polluer le rapport avec des lignes "35 % MODÉRÉ" qui
contrediraient visuellement le verdict "pas un plagiat" affiché en
haut. Les seuls matches qui passent ce filtre sont ceux qu'au moins un
des trois signaux confirme : MinHash, doublon exact, hash de fichier.

### Affinage et knobs

| Constante | Lieu | Effet |
|---|---|---|
| `SHINGLE_SIZE` | [`minhash_service.py`](backend/services/minhash_service.py) | Taille du shingle (défaut 5). Plus court = plus sensible aux copies partielles. |
| `NUM_PERM` | idem | Précision de la signature MinHash (défaut 128). |
| `LSH_THRESHOLD` | idem | Filtre LSH lâche (défaut 0.05). Le filtrage fin se fait après. |
| `DEFAULT_MIN_JACCARD` | [`minhash_plagiarism_service.py`](backend/services/minhash_plagiarism_service.py) | Seuil minimal pour qu'un match soit reporté (défaut 0.10). |
| `SIA_PLAGIARISM_MAX_MATCHES_PER_SOURCE` | env | Plafond d'affichage par source (défaut 20). |
| `SIA_PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED` | env | Plafond total (défaut 100). |

### Crédibilité — validation empirique

Quatre cas de référence validés (voir conversation de mise au point) :

| Cas | MinHash | Verdict | Correct ? |
|---|---|---|---|
| Paraphrase volontaire (Villa A vs Villa B — noms changés, intrigue identique) | 76 % | 🔴 Plagiat confirmé | ✅ |
| Deux drames marocains sans rapport (Omar/Zahra vs Douae/Hiba) | 0 % | 🟢 Pas un plagiat | ✅ |
| Scénario avec deux passages copiés depuis une autre source | 25 % (peak 57 %) | 🔴 Plagiat confirmé | ✅ |
| Doublon exact (même PDF) | 100 % via hash | 🔴 Doublon exact | ✅ |

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
| `SIA_PLAGIARISM_MAX_MATCHES_PER_SOURCE` | Plafond d'affichage par source dans le rapport (défaut : 20). |
| `SIA_PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED` | Plafond total de matches affichés (défaut : 100). |
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
                        + minhash_service (index LSH), minhash_plagiarism_service
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
