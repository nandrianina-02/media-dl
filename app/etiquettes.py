"""Étiquettes d'un fichier audio : titre, artistes, album, pochette.

Utilisé pour Spotify : le fichier vient de YouTube, dont les métadonnées
(« Artiste - Titre (Official Audio) ») sont remplacées par celles de
Spotify, plus justes. Un échec ici n'est jamais bloquant : le fichier est
livré, simplement moins bien étiqueté.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx
from mutagen import File as FichierAudio
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

from .spotify import Morceau

log = logging.getLogger("media-dl")
POCHETTE_MAX = 2 * 1024 * 1024


def telecharger_pochette(url: str | None) -> bytes | None:
    if not url or not url.startswith("https://"):
        return None
    try:
        r = httpx.get(url, timeout=10, follow_redirects=False)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/jpeg") and len(r.content) <= POCHETTE_MAX:
            return r.content
    except httpx.HTTPError:
        pass
    return None


def etiqueter(chemin: str, morceau: Morceau, pochette: bytes | None) -> bool:
    extension = os.path.splitext(chemin)[1].lower()
    if extension == ".wav":
        return False  # pas d'étiquettes fiables dans un WAV
    try:
        audio = FichierAudio(chemin, easy=True)
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        audio["title"] = morceau.titre
        if morceau.artistes:
            audio["artist"] = morceau.artistes
            audio["albumartist"] = morceau.artistes[0]
        if morceau.album:
            audio["album"] = morceau.album
        if morceau.annee:
            audio["date"] = morceau.annee
        if morceau.numero:
            audio["tracknumber"] = str(morceau.numero)
        audio.save()
        if pochette:
            _pochette(chemin, extension, pochette)
        return True
    except Exception as erreur:  # noqa: BLE001 — voir la docstring du module
        log.info("étiquettes non écrites pour %s : %s", os.path.basename(chemin), erreur)
        return False


def _pochette(chemin: str, extension: str, image: bytes) -> None:
    if extension == ".mp3":
        try:
            tags = ID3(chemin)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image))
        tags.save(chemin)
    elif extension == ".m4a":
        audio = MP4(chemin)
        audio["covr"] = [MP4Cover(image, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif extension in (".flac", ".ogg", ".opus"):
        image_flac = Picture()
        image_flac.type = 3
        image_flac.mime = "image/jpeg"
        image_flac.data = image
        if extension == ".flac":
            audio = FLAC(chemin)
            audio.clear_pictures()
            audio.add_picture(image_flac)
        else:
            audio = FichierAudio(chemin)
            audio["metadata_block_picture"] = [base64.b64encode(image_flac.write()).decode("ascii")]
        audio.save()
