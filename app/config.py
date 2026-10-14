"""Réglages du service, lus dans l'environnement.

Toutes les limites d'un service public sont ici : un hébergeur gratuit tombe
vite si un seul visiteur peut lancer cinquante conversions de trois heures.
"""

import os
import shlex
import tempfile
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _commande(name: str, default: str) -> tuple[str, ...]:
    # Hors POSIX, shlex garde les guillemets autour des arguments : on les
    # retire, sinon Windows cherche un programme dont le nom les contient.
    morceaux = shlex.split(os.environ.get(name, default), posix=os.name != "nt")
    return tuple(m[1:-1] if len(m) > 1 and m[0] == m[-1] == '"' else m for m in morceaux)


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Origines autorisées à appeler l'API depuis un navigateur.
    allowed_origins: list[str] = field(default_factory=lambda: _list("ALLOWED_ORIGINS", "http://localhost:3000"))
    # Dossier de travail : un sous-dossier par téléchargement.
    out_dir: str = os.environ.get("OUT_DIR", os.path.join(tempfile.gettempdir(), "media-dl"))
    # Base SQLite de l'historique (métadonnées seulement, jamais les fichiers).
    db_path: str = os.environ.get("DB_PATH", os.path.join(tempfile.gettempdir(), "media-dl", "historique.sqlite3"))
    # Commande yt-dlp, découpée en arguments : remplaçable par un faux dans
    # les tests (« python tests/faux_ytdlp.py »).
    ytdlp_bin: tuple[str, ...] = _commande("YTDLP_BIN", "yt-dlp")

    # Conversions en parallèle, sur tout le serveur.
    max_concurrent: int = _int("MAX_CONCURRENT", 3)
    # Téléchargements en cours ou en attente, par visiteur.
    max_active_per_visitor: int = _int("MAX_ACTIVE_PER_VISITOR", 3)
    # File d'attente globale : au-delà, on refuse plutôt que de faire attendre une heure.
    max_queue: int = _int("MAX_QUEUE", 40)

    # Durée maximale d'un média, en secondes.
    max_duration: int = _int("MAX_DURATION", 3 * 3600)
    # Taille maximale d'un fichier, au format yt-dlp (« 2G », « 500M »).
    max_filesize: str = os.environ.get("MAX_FILESIZE", "2G")
    # Éléments d'une playlist téléchargeables en une fois.
    max_playlist_items: int = _int("MAX_PLAYLIST_ITEMS", 50)
    # Temps maximal d'un téléchargement, conversion comprise.
    job_timeout: int = _int("JOB_TIMEOUT", 30 * 60)
    # Durée de vie d'un fichier prêt, avant suppression.
    file_ttl: int = _int("FILE_TTL", 30 * 60)

    # Demandes d'aperçu et de téléchargement par adresse IP.
    info_per_minute: int = _int("INFO_PER_MINUTE", 30)
    downloads_per_hour: int = _int("DOWNLOADS_PER_HOUR", 40)

    # Application Spotify (developer.spotify.com), pour lire les titres,
    # albums et playlists. Sans elle, les liens Spotify sont refusés.
    spotify_client_id: str = os.environ.get("SPOTIFY_CLIENT_ID", "")
    spotify_client_secret: str = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    # Pays du catalogue consulté (disponibilité des titres).
    spotify_market: str = os.environ.get("SPOTIFY_MARKET", "FR")


settings = Settings()
