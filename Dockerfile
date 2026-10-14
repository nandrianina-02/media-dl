FROM python:3.12-slim

# ffmpeg pour les conversions ; Node.js pour bgutil-ytdlp-pot-provider.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl gnupg ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .

# Le service ne tourne pas en root : un fichier mal nommé par yt-dlp ne peut
# rien écraser hors du dossier de travail.
RUN useradd --create-home --uid 10001 mediadl \
    && mkdir -p /data && chown mediadl /data
USER mediadl

ENV OUT_DIR=/data/fichiers \
    DB_PATH=/data/historique.sqlite3 \
    PORT=8000
VOLUME ["/data"]
EXPOSE 8000

# Forme shell : l'hébergeur (Railway, Render) impose le port par $PORT.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers
