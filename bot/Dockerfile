# Contexto de build = raiz do repositório (ver docker-compose.yml), porque a
# imagem precisa de `db/schema.sql`, que é compartilhado com o painel.
FROM python:3.12-slim

WORKDIR /app

COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código do bot.
COPY bot/main.py bot/sources.py bot/bot_control.py bot/filters.py \
     bot/dispatch.py bot/telemetry.py bot/store.py ./

# Configuração do filtro. `main.py` resolve os caminhos a partir de
# `<pasta do main>/config/` — mexer aqui exige mexer no CONFIG_DIR de lá.
COPY bot/config/ ./config/

# Schema do Postgres, aplicado no boot. `store.py` procura em ../db/schema.sql
# relativo a si mesmo; como o código está em /app, o schema vai em /app/../db —
# então copiamos para /db.
COPY db/schema.sql /db/schema.sql

# Estado persistido (seen_ids, fila de envio, contadores do dia) — volume.
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data

CMD ["python", "-u", "main.py"]
