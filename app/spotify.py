"""Spotify : métadonnées seulement.

Le flux audio de Spotify est chiffré (DRM) : on ne le lit pas et on ne
cherche pas à le contourner. On lit le titre, les artistes, l'album, la
durée et la pochette par l'API publique, on retrouve le même morceau sur
YouTube, puis on étiquette le fichier avec les informations de Spotify.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx

API = "https://api.spotify.com/v1"
JETON = "https://accounts.spotify.com/api/token"
TYPES = ("track", "album", "playlist")
IDENTIFIANT = re.compile(r"^[A-Za-z0-9]{22}$")


class ErreurSpotify(Exception):
    """Erreur avec un message lisible par le visiteur, et un code HTTP."""

    def __init__(self, message: str, code: int = 422):
        super().__init__(message)
        self.code = code


@dataclass
class Morceau:
    index: int
    titre: str
    artistes: list[str]
    album: str = ""
    duree: float | None = None
    numero: int | None = None
    annee: str | None = None
    pochette: str | None = None

    @property
    def auteur(self) -> str:
        return ", ".join(self.artistes)

    def recherche(self) -> str:
        # L'artiste principal et le titre suffisent ; « audio » écarte souvent
        # les clips dont l'introduction allonge le morceau.
        principal = self.artistes[0] if self.artistes else ""
        return f"{principal} - {self.titre} audio".strip(" -")


@dataclass
class Collection:
    type: str  # track | album | playlist
    titre: str
    auteur: str
    pochette: str | None
    morceaux: list[Morceau] = field(default_factory=list)
    nombre: int = 0


def analyser_adresse(parties: urllib.parse.ParseResult) -> tuple[str, str]:
    """(type, identifiant) d'une adresse open.spotify.com ; ValueError sinon."""
    segments = [s for s in parties.path.split("/") if s]
    # Adresses localisées : /intl-fr/track/…
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]
    if len(segments) >= 2 and segments[0] in TYPES and IDENTIFIANT.match(segments[1]):
        return segments[0], segments[1]
    raise ValueError("Collez l'adresse d'un titre, d'un album ou d'une playlist Spotify.")


def _image(images: list[dict] | None) -> str | None:
    # Spotify classe les images de la plus grande à la plus petite.
    return images[0]["url"] if images else None


def _annee(date: str | None) -> str | None:
    return date[:4] if date else None


class Spotify:
    def __init__(self, client_id: str, secret: str, marche: str = "FR"):
        self.client_id = client_id
        self.secret = secret
        self.marche = marche
        self._jeton: str | None = None
        self._expire = 0.0
        self._verrou = asyncio.Lock()

    @property
    def configure(self) -> bool:
        return bool(self.client_id and self.secret)

    async def _jeton_valide(self, client: httpx.AsyncClient) -> str:
        async with self._verrou:
            if self._jeton and time.time() < self._expire - 60:
                return self._jeton
            r = await client.post(JETON, data={"grant_type": "client_credentials"}, auth=(self.client_id, self.secret))
            if r.status_code != 200:
                raise ErreurSpotify("Spotify est momentanément indisponible sur ce service.", 503)
            corps = r.json()
            self._jeton = corps["access_token"]
            self._expire = time.time() + int(corps.get("expires_in", 3600))
            return self._jeton

    async def _get(self, chemin_ou_url: str, params: dict | None = None) -> dict:
        url = chemin_ou_url if chemin_ou_url.startswith("https://") else API + chemin_ou_url
        async with httpx.AsyncClient(timeout=15) as client:
            jeton = await self._jeton_valide(client)
            r = await client.get(url, params=params, headers={"Authorization": f"Bearer {jeton}"})
        if r.status_code == 404:
            raise ErreurSpotify(
                "Introuvable sur Spotify. Le contenu est peut-être privé, retiré, ou c'est une playlist "
                "créée par Spotify (celles-ci ne sont pas accessibles)."
            )
        if r.status_code == 429:
            raise ErreurSpotify("Spotify limite les demandes en ce moment. Réessayez dans une minute.", 429)
        if r.status_code in (400, 401, 403):
            raise ErreurSpotify("Spotify est momentanément indisponible sur ce service.", 503)
        if r.status_code != 200:
            raise ErreurSpotify("Spotify ne répond pas. Réessayez dans un instant.", 502)
        return r.json()

    async def lire(self, type_: str, ident: str, maximum: int) -> Collection:
        if not self.configure:
            raise ErreurSpotify("La prise en charge de Spotify n'est pas activée sur ce serveur.", 503)
        if type_ == "track":
            t = await self._get(f"/tracks/{ident}", {"market": self.marche})
            album = t.get("album") or {}
            m = self._morceau(1, t, album)
            return Collection("track", m.titre, m.auteur, m.pochette, [m], 1)

        if type_ == "album":
            a = await self._get(f"/albums/{ident}", {"market": self.marche})
            pistes = a.get("tracks") or {}
            elements, suivante = list(pistes.get("items") or []), pistes.get("next")
            while suivante and len(elements) < maximum:
                page = await self._get(suivante)
                elements += page.get("items") or []
                suivante = page.get("next")
            morceaux = [self._morceau(i + 1, t, a) for i, t in enumerate(elements[:maximum]) if t]
            auteur = ", ".join(x["name"] for x in a.get("artists") or [])
            return Collection("album", a.get("name") or "Album", auteur, _image(a.get("images")), morceaux, pistes.get("total") or len(morceaux))

        champs = "name,owner(display_name),images,tracks(total,next,items(track(name,artists(name),album(name,images,release_date),duration_ms,track_number,is_local,type)))"
        p = await self._get(f"/playlists/{ident}", {"market": self.marche, "fields": champs})
        pistes = p.get("tracks") or {}
        elements, suivante = list(pistes.get("items") or []), pistes.get("next")
        while suivante and len(elements) < maximum:
            page = await self._get(suivante)
            elements += page.get("items") or []
            suivante = page.get("next")
        morceaux: list[Morceau] = []
        # Positions conservées même quand un élément est écarté (épisode de
        # podcast, fichier local) : elles servent à la sélection.
        for i, element in enumerate(elements[:maximum]):
            t = (element or {}).get("track")
            if not t or t.get("is_local") or t.get("type", "track") != "track":
                continue
            morceaux.append(self._morceau(i + 1, t, t.get("album") or {}))
        return Collection(
            "playlist",
            p.get("name") or "Playlist",
            (p.get("owner") or {}).get("display_name") or "",
            _image(p.get("images")),
            morceaux,
            pistes.get("total") or len(morceaux),
        )

    @staticmethod
    def _morceau(index: int, t: dict, album: dict) -> Morceau:
        return Morceau(
            index=index,
            titre=t.get("name") or f"Morceau {index}",
            artistes=[a["name"] for a in t.get("artists") or [] if a.get("name")],
            album=album.get("name") or "",
            duree=(t["duration_ms"] / 1000) if t.get("duration_ms") else None,
            numero=t.get("track_number"),
            annee=_annee(album.get("release_date")),
            pochette=_image(album.get("images")),
        )


def resume(collection: Collection, url: str, maximum: int) -> dict:
    """Même forme que l'aperçu des autres sites ; l'interface n'a pas à distinguer."""
    if collection.type == "track":
        m = collection.morceaux[0]
        return {
            "type": "media",
            "titre": m.titre,
            "auteur": m.auteur,
            "miniature": m.pochette,
            "duree": m.duree,
            "direct": False,
            "hauteurs": [],
            "sous_titres": [],
            "audio_seul": True,
            "site": "Spotify",
            "url": url,
            "choix_liste": False,
        }
    infos = {
        "type": "liste",
        "titre": collection.titre,
        "auteur": collection.auteur,
        "miniature": collection.pochette,
        "entrees": [{"index": m.index, "titre": m.titre, "duree": m.duree, "auteur": m.auteur} for m in collection.morceaux],
        "nombre": collection.nombre,
        "audio_seul": True,
        "site": "Spotify",
        "url": url,
        "choix_liste": False,
    }
    if collection.nombre > maximum:
        infos["avertissement"] = f"Seuls les {maximum} premiers morceaux sont proposés."
    return infos
