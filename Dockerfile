# Base: Node.js 20 LTS (slim) — adicionamos Python 3 via apt
FROM node:20-slim

# Instalar Python 3 e pip
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependências Node.js (cacheia separado do código) ──────────────────────
COPY node/package.json node/package-lock.json* ./node/
RUN cd node && npm install --production --omit=dev

# ── Dependências Python ────────────────────────────────────────────────────
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt --break-system-packages --no-cache-dir

# ── Código-fonte ───────────────────────────────────────────────────────────
COPY . .

# Pasta de dados persistentes (montada via disco no Render)
# Valor padrão: mesma pasta do app (compatível com uso local via Docker)
ENV DATA_DIR=""

EXPOSE 5000

CMD ["python3", "app.py"]
