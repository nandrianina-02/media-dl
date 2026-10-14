"""Faux yt-dlp pour les tests : mêmes arguments, mêmes sorties, aucun réseau.

Le comportement dépend d'un mot dans l'adresse :
- « privee »   : erreur « Private video » ;
- « filtre »   : média écarté par --match-filter, sans fichier ;
- « lent »     : progression étalée sur plusieurs secondes (annulation) ;
- « playlist » : liste de trois éléments.
"""

import json
import os
import sys
import time

args = sys.argv[1:]
url = args[-1]


def valeur(option):
    return args[args.index(option) + 1] if option in args else None


if "-J" in args:
    if "privee" in url:
        print("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", file=sys.stderr)
        sys.exit(1)
    if "playlist" in url:
        print(json.dumps({
            "_type": "playlist", "title": "Ma playlist", "uploader": "Chaîne",
            "entries": [{"title": f"Morceau {i}", "duration": 100 + i} for i in range(1, 4)],
        }))
    else:
        print(json.dumps({
            "title": "Titre d'essai", "uploader": "Auteur", "thumbnail": "https://img/x.jpg", "duration": 125,
            "formats": [{"height": 360, "vcodec": "avc1"}, {"height": 720, "vcodec": "avc1"}, {"height": None, "vcodec": "none"}],
            "subtitles": {"fr": [], "en": []},
        }))
    sys.exit(0)

dossier = valeur("--paths")
fichier_chemins = args[args.index("--print-to-file") + 2]
format_audio = valeur("--audio-format")
extension = format_audio or valeur("--merge-output-format") or "mp4"

if "privee" in url:
    print("ERROR: [youtube] abc: Private video. Sign in if you've been granted access")
    sys.exit(1)
# Recherche (« ytsearch ») : « introuvable » ne donne rien ; « longue » ne
# passe pas le filtre de durée, seule la recherche sans durée aboutit.
recherche = url.startswith("ytsearch")
if recherche and "introuvable" in url:
    sys.exit(0)
if recherche and "longue" in url and "duration >=?" in (valeur("--match-filter") or ""):
    print("[youtube] abc: does not pass filter, skipping ..")
    sys.exit(0)
if "filtre" in url:
    print("[youtube] abc: Titre does not pass filter (!is_live & duration <=? 10800), skipping ..")
    sys.exit(0)

elements = [1, 2, 3] if "playlist" in url else [None]
if valeur("--playlist-items"):
    elements = [int(i) for i in valeur("--playlist-items").split(",")]
pause = 0.6 if "lent" in url else 0.02

for element in elements:
    for pas in range(1, 6):
        print(f"MDL|dl|{pas * 200}|1000|NA|50000|{5 - pas}|{element if element else 'NA'}", flush=True)
        time.sleep(pause)
    print(f"MDL|pp|FFmpegExtractAudio|{element if element else 'NA'}", flush=True)
    nom = f"{element:03d} - Morceau {element}.{extension}" if element else f"Titre d'essai.{extension}"
    gabarit = valeur("-o") or ""
    if "%(title)" not in gabarit and "%(playlist_index)" not in gabarit:
        nom = gabarit.replace("%(ext)s", extension).replace("%%", "%")
    chemin = os.path.join(dossier, nom)
    with open(chemin, "wb") as f:
        f.write(b"contenu " + nom.encode())
    with open(fichier_chemins, "a", encoding="utf-8") as f:
        f.write(chemin + "\n")
# Comme yt-dlp : --max-downloads atteint se signale par le code 101.
sys.exit(101 if "--max-downloads" in args else 0)
