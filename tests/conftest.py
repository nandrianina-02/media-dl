import os
import sys
import tempfile

# Configuration d'essai, posée avant tout import de l'application : un dossier
# jetable, le faux yt-dlp, et des limites courtes.
_DOSSIER = tempfile.mkdtemp(prefix="media-dl-essai-")
_FAUX = os.path.join(os.path.dirname(__file__), "faux_ytdlp.py")
os.environ.update(
    OUT_DIR=os.path.join(_DOSSIER, "fichiers"),
    DB_PATH=os.path.join(_DOSSIER, "historique.sqlite3"),
    YTDLP_BIN=f'"{sys.executable}" "{_FAUX}"',
    MAX_ACTIVE_PER_VISITOR="2",
    DOWNLOADS_PER_HOUR="1000",
    INFO_PER_MINUTE="1000",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
