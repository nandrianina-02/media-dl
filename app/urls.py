"""Validation des adresses reçues.

Une adresse ne vient jamais telle quelle à yt-dlp : elle doit être une URL
http(s) dont l'hôte appartient à un site pris en charge. L'ancien contrôle
cherchait le nom de domaine n'importe où dans la chaîne, si bien que
« https://autre.site/?x.com » passait, et qu'une « adresse » commençant par
un tiret pouvait être lue par yt-dlp comme une option.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

# Domaine -> nom affiché. Un sous-domaine (www., m., music.) est accepté.
SITES: dict[str, str] = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "soundcloud.com": "SoundCloud",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion",
    "dai.ly": "Dailymotion",
    "twitch.tv": "Twitch",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
}


class AdresseInvalide(ValueError):
    """Adresse refusée, avec un message lisible par le visiteur."""


@dataclass(frozen=True)
class Adresse:
    url: str
    site: str
    domaine: str
    # Vrai si l'adresse désigne une liste (playlist YouTube, set SoundCloud…).
    liste: bool


def _domaine(hote: str) -> str | None:
    hote = hote.lower().rstrip(".")
    for domaine in SITES:
        if hote == domaine or hote.endswith("." + domaine):
            return domaine
    return None


def analyser(brute: str, *, garder_liste: bool = False) -> Adresse:
    """Vérifie et normalise une adresse. Lève AdresseInvalide sinon."""
    texte = (brute or "").strip()
    if not texte:
        raise AdresseInvalide("Collez l'adresse d'une vidéo ou d'un morceau.")
    if len(texte) > 2048:
        raise AdresseInvalide("Adresse trop longue.")
    if not texte.lower().startswith(("http://", "https://")):
        texte = "https://" + texte
    parties = urllib.parse.urlparse(texte)
    if parties.scheme not in ("http", "https") or not parties.hostname:
        raise AdresseInvalide("Adresse invalide.")
    if parties.username or parties.password:
        raise AdresseInvalide("Adresse invalide.")
    domaine = _domaine(parties.hostname)
    if not domaine:
        raise AdresseInvalide(f"Site non pris en charge. Sites acceptés : {', '.join(sorted(set(SITES.values())))}.")

    params = dict(urllib.parse.parse_qsl(parties.query))
    liste = False

    if domaine in ("youtube.com", "youtu.be"):
        video = params.get("v")
        if domaine == "youtu.be":
            video = parties.path.strip("/").split("/")[0] or None
        elif parties.path.startswith("/shorts/"):
            video = parties.path.split("/")[2] if len(parties.path.split("/")) > 2 else None
        playlist = params.get("list")
        # Une liste de lecture explicite, ou une vidéo dont on veut toute la liste.
        if playlist and (not video or garder_liste) and not playlist.startswith(("RD", "UL")):
            return Adresse(f"https://www.youtube.com/playlist?list={playlist}", "YouTube", domaine, True)
        if video:
            return Adresse(f"https://www.youtube.com/watch?v={video}", "YouTube", domaine, False)
        texte = urllib.parse.urlunparse(parties._replace(fragment=""))
        return Adresse(texte, "YouTube", domaine, "/playlist" in parties.path)

    if domaine == "soundcloud.com":
        liste = "/sets/" in parties.path or parties.path.rstrip("/").endswith(("/tracks", "/albums", "/likes"))

    propre = urllib.parse.urlunparse(parties._replace(fragment=""))
    return Adresse(propre, SITES[domaine], domaine, liste)


def a_une_liste(brute: str) -> bool:
    """Vrai si une adresse de vidéo YouTube porte aussi une playlist (« &list= »)."""
    try:
        parties = urllib.parse.urlparse(brute if "://" in brute else "https://" + brute)
    except ValueError:
        return False
    params = dict(urllib.parse.parse_qsl(parties.query))
    return bool(params.get("v") and params.get("list") and not params["list"].startswith(("RD", "UL")))
