# SPM Backend — image de production
#
# Multi-stage : on installe les deps Python dans une étape "builder" puis
# on ne garde que le strict nécessaire dans l'image finale. Le modèle
# d'embedding e5-base est téléchargé au premier démarrage (cache HF dans
# /root/.cache) — pour un cold start rapide, montez un volume sur ce path.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH \
    PORT=8000

WORKDIR /app

# On copie uniquement les site-packages du builder.
COPY --from=builder /root/.local /root/.local

# Code applicatif. ``backend/`` + ``data/moderation_lists/`` + ``scripts/``
# sont nécessaires au runtime ; le reste (tests, docs, frontend) reste
# dehors pour limiter la taille d'image.
COPY backend/ ./backend/
COPY data/moderation_lists/ ./data/moderation_lists/
COPY scripts/ ./scripts/

# Tournera comme un user non-root.
RUN useradd --create-home --uid 1000 spm \
    && mkdir -p data/raw data/processed \
    && chown -R spm:spm /app /root/.local

USER spm

EXPOSE 8000

# 2 workers uvicorn — ajuster selon la machine cible. Pour scale-out
# horizontal, préférez un orchestrateur (Kubernetes, Nomad) avec
# 1 worker par conteneur.
CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
