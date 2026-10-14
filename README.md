# media-dl — serveur

API FastAPI qui pilote yt-dlp et ffmpeg : aperçu d'un média ou d'une playlist,
file de téléchargements avec progression réelle, historique par visiteur.

## Démarrer

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows
# .venv/bin/pip install -r requirements-dev.txt     # Linux, macOS
uvicorn main:app --reload --port 8000
```

ffmpeg doit être dans le PATH. La documentation interactive est sur
`http://localhost:8000/docs`.

## Tests

```bash
python -m pytest -q
```

Les tests n'appellent aucun site : `tests/faux_ytdlp.py` remplace yt-dlp
(même interface, sorties simulées : progression, vidéo privée, filtre de
durée, playlist, téléchargement lent pour l'annulation).

## Fonctionnement

| Route | Rôle |
|---|---|
| `GET /infos?url=&liste=` | Aperçu : titre, durée, hauteurs vidéo, sous-titres ; ou contenu d'une playlist |
| `POST /taches` | Ajoute un téléchargement à la file |
| `GET /taches/{id}` | État : attente, téléchargement (%, débit, temps restant), conversion, prêt, erreur |
| `DELETE /taches/{id}` | Annule |
| `GET /taches/{id}/fichier` | Fichier prêt (archive ZIP pour une playlist) |
| `GET /historique`, `DELETE /historique` | Historique du visiteur |

Chaque navigateur envoie un identifiant anonyme dans l'en-tête `X-Visiteur` :
il sépare les files et les historiques, sans compte. Les identifiants de
téléchargement sont aléatoires, un lien de fichier ne se devine pas.

## Spotify

L'audio de Spotify est chiffré (DRM) : il n'est ni lu ni contourné. Pour un
titre, un album ou une playlist Spotify, le serveur :

1. lit les métadonnées par l'API Spotify (titre, artistes, album, durée,
   pochette) ;
2. cherche chaque morceau sur YouTube et garde, parmi les cinq premiers
   résultats, le premier dont la durée est proche (±10 s ou ±8 %) ; à défaut,
   le premier résultat ;
3. réécrit les étiquettes du fichier et y intègre la pochette de Spotify
   (`app/etiquettes.py`, avec mutagen).

Il faut une application Spotify : la créer sur
<https://developer.spotify.com/dashboard> (« Web API »), puis renseigner
`SPOTIFY_CLIENT_ID` et `SPOTIFY_CLIENT_SECRET` sur le serveur. Sans elles, les
liens Spotify sont refusés avec un message clair. Les playlists créées par
Spotify lui-même (« Top 50 », « Daily Mix »…) ne sont pas accessibles aux
nouvelles applications : seules les playlists d'utilisateurs le sont.

## Limites d'un service public

Toutes réglables par variables d'environnement (voir `.env.example`) :
conversions simultanées, téléchargements actifs par visiteur, file globale,
durée et taille maximales, nombre d'éléments par playlist, délai maximal,
durée de conservation des fichiers, demandes par adresse IP. Les directs sont
refusés.

Les fichiers sont supprimés après `FILE_TTL` secondes ; l'historique ne garde
que des métadonnées, et pas au-delà de 30 jours.

## Déploiement

L'image Docker tourne sans droits root et écrit dans `/data` (à monter en
volume pour garder l'historique entre deux déploiements). Le port est lu dans
`$PORT`. Derrière un proxy, l'adresse du visiteur est la dernière entrée de
`X-Forwarded-For` (celle qu'ajoute le proxy de l'hébergeur), pour que les
limites par IP ne se contournent pas en inventant l'en-tête.
