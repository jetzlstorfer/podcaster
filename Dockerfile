# Multi-stage build: compile the React SPA with Node, then run the FastAPI
# backend (which serves both the API and the built SPA) on Python.

# --- Stage 1: build the SPA ---
FROM node:22-slim AS spa
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
# Same-origin API: the built SPA calls /podcast on its own host (no CORS).
ENV VITE_BACKEND_URL=""
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=80

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Shared package + backend entry points.
COPY podcaster/ ./podcaster/
COPY server.py main.py ./
# Built SPA from stage 1 (FastAPI mounts ./frontend/dist at "/").
COPY --from=spa /app/frontend/dist ./frontend/dist

EXPOSE 80
# Shell form so the container honors the WEB_PORT env (set by the Container App
# to match the ingress targetPort). Defaults to 80 for local `docker run`.
CMD ["sh", "-c", "exec python -m uvicorn server:app --host \"${WEB_HOST:-0.0.0.0}\" --port \"${WEB_PORT:-80}\""]
