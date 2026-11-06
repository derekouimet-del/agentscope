# ---- build the frontend ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AGENTSCOPE_DATA_DIR=/data AGENTSCOPE_FRONTEND_DIST=/app/frontend-dist
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/agentscope ./agentscope
COPY --from=web /web/dist ./frontend-dist
RUN useradd -m -u 10001 agentscope && mkdir /data && chown agentscope /data
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
# The Docker socket (if mounted) is group-owned by the host's docker group; compose adds that group via DOCKER_GID.
USER agentscope
CMD ["uvicorn", "agentscope.main:app", "--host", "0.0.0.0", "--port", "8000"]
