"""API de MediaDL.

Service public : pas de clé (une clé envoyée au navigateur ne protège rien),
mais des limites par adresse IP et par visiteur, des origines CORS
déclarées, et des identifiants de téléchargement impossibles à deviner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .erreurs import message_clair
from .limites import Limiteur
from .spotify import ErreurSpotify, Spotify, resume as resume_spotify
from .store import Registre
from .taches import Gestionnaire, Refus
from .urls import SITES, AdresseInvalide, a_une_liste, analyser
from .ytdlp import AUDIO, FORMATS, QUALITES_AUDIO, QUALITES_VIDEO, VIDEO, Options, commande_infos, resume_infos

VERSION = "5.0.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("media-dl")

registre = Registre(settings.db_path)
gestionnaire = Gestionnaire(settings, registre)
limite_infos = Limiteur(settings.info_per_minute, 60)
limite_telechargements = Limiteur(settings.downloads_per_hour, 3600)
spotify = Spotify(settings.spotify_client_id, settings.spotify_client_secret, settings.spotify_market)


async def _nettoyage_periodique() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            gestionnaire.nettoyer()
        except Exception:  # noqa: BLE001 — le nettoyage ne doit jamais arrêter le service
            log.exception("nettoyage")


@asynccontextmanager
async def cycle_de_vie(_: FastAPI):
    tache = asyncio.create_task(_nettoyage_periodique())
    yield
    tache.cancel()


app = FastAPI(title="MediaDL", version=VERSION, lifespan=cycle_de_vie)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Visiteur"],
    expose_headers=["Content-Disposition"],
)

# --- Utilitaires -------------------------------------------------------------------

JETON = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def visiteur_de(valeur: str | None) -> str:
    """Jeton anonyme tiré par le navigateur ; sans lui, pas d'historique."""
    if not valeur or not JETON.match(valeur):
        raise HTTPException(400, "Identifiant de visiteur manquant : rechargez la page.")
    return valeur


def ip_de(requete: Request) -> str:
    # Derrière Railway ou Render, le proxy ajoute l'adresse réelle à la fin de
    # X-Forwarded-For. Les entrées précédentes viennent du visiteur lui-même :
    # s'y fier permettrait de contourner les limites par IP en les inventant.
    transmise = requete.headers.get("x-forwarded-for")
    if transmise:
        return transmise.split(",")[-1].strip()
    return requete.client.host if requete.client else "inconnue"


def _type_spotify(url: str) -> tuple[str, str]:
    # Adresse déjà normalisée par analyser() : https://open.spotify.com/{type}/{id}
    type_, ident = url.rstrip("/").split("/")[-2:]
    return type_, ident


def limiter(limiteur: Limiteur, cle: str) -> None:
    ok, attente = limiteur.autoriser(cle)
    if not ok:
        raise HTTPException(429, f"Trop de demandes. Réessayez dans {attente} s.", headers={"Retry-After": str(attente)})


@app.exception_handler(Refus)
async def _refus(_: Request, erreur: Refus):
    return JSONResponse({"detail": str(erreur)}, status_code=erreur.code)


# --- Routes ------------------------------------------------------------------------


@app.get("/")
def accueil():
    return {
        "nom": "MediaDL",
        "version": VERSION,
        "sites": sorted({s for s in SITES.values() if s != "Spotify" or spotify.configure}),
        "formats": {"audio": AUDIO, "video": VIDEO},
        "qualites": {"audio": list(QUALITES_AUDIO), "video": list(QUALITES_VIDEO)},
        "limites": {
            "duree_max": settings.max_duration,
            "taille_max": settings.max_filesize,
            "elements_max": settings.max_playlist_items,
            "conservation": settings.file_ttl,
            "actifs_max": settings.max_active_per_visitor,
        },
    }


@app.get("/ping")
def ping():
    return {"ok": True, "version": VERSION}


@app.get("/infos")
async def infos(requete: Request, url: str = Query(..., max_length=2048), liste: bool = Query(False)):
    limiter(limite_infos, ip_de(requete))
    try:
        adresse = analyser(url, garder_liste=liste)
    except AdresseInvalide as erreur:
        raise HTTPException(400, str(erreur)) from None

    if adresse.site == "Spotify":
        try:
            collection = await spotify.lire(*_type_spotify(adresse.url), settings.max_playlist_items)
        except ErreurSpotify as erreur:
            raise HTTPException(erreur.code, str(erreur)) from None
        return resume_spotify(collection, adresse.url, settings.max_playlist_items)

    cmd = commande_infos(settings.ytdlp_bin, adresse.url, liste=adresse.liste, max_elements=settings.max_playlist_items)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        sortie, erreurs = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Le site met trop de temps à répondre. Réessayez.") from None
    except FileNotFoundError:
        raise HTTPException(503, "Le service est momentanément indisponible.") from None

    if proc.returncode != 0:
        texte = erreurs.decode("utf-8", errors="replace")
        log.info("infos refusées pour %s : %s", adresse.url, texte[-600:])
        raise HTTPException(422, message_clair(texte))
    try:
        data = json.loads(sortie)
    except json.JSONDecodeError:
        raise HTTPException(502, "Réponse inattendue du site.") from None

    resume = resume_infos(data, liste=adresse.liste)
    resume.update(site=adresse.site, url=adresse.url, choix_liste=a_une_liste(url) and not liste)
    if resume.get("duree") and resume["duree"] > settings.max_duration:
        resume["avertissement"] = f"Ce média dépasse la durée maximale ({settings.max_duration // 3600} h)."
    return resume


class Demande(BaseModel):
    url: str = Field(..., max_length=2048)
    format: str = "mp3"
    qualite: str = "192"
    liste: bool = False
    titre: str | None = Field(None, max_length=300)
    debut: float | None = Field(None, ge=0, le=86400)
    fin: float | None = Field(None, gt=0, le=86400)
    sous_titres: list[str] = Field(default_factory=list, max_length=5)
    pochette: bool = True
    elements: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("format")
    @classmethod
    def _format(cls, valeur: str) -> str:
        if valeur not in FORMATS:
            raise ValueError(f"Format non pris en charge : {', '.join(FORMATS)}.")
        return valeur

    @field_validator("sous_titres")
    @classmethod
    def _langues(cls, valeurs: list[str]) -> list[str]:
        for v in valeurs:
            if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", v):
                raise ValueError("Langue de sous-titres invalide.")
        return valeurs


@app.post("/taches", status_code=201)
async def creer(demande: Demande, requete: Request, x_visiteur: str | None = Header(None)):
    visiteur = visiteur_de(x_visiteur)
    limiter(limite_telechargements, ip_de(requete))
    try:
        adresse = analyser(demande.url, garder_liste=demande.liste)
    except AdresseInvalide as erreur:
        raise HTTPException(400, str(erreur)) from None

    qualites = QUALITES_VIDEO if demande.format in VIDEO else tuple(QUALITES_AUDIO)
    qualite = demande.qualite if demande.qualite in qualites else ("720" if demande.format in VIDEO else "192")
    if demande.debut is not None and demande.fin is not None and demande.fin <= demande.debut:
        raise HTTPException(400, "La fin de l'extrait doit suivre son début.")

    morceaux = None
    titre = demande.titre
    if adresse.site == "Spotify":
        if demande.format in VIDEO:
            raise HTTPException(400, "Spotify ne fournit que de l'audio : choisissez un format audio.")
        try:
            collection = await spotify.lire(*_type_spotify(adresse.url), settings.max_playlist_items)
        except ErreurSpotify as erreur:
            raise HTTPException(erreur.code, str(erreur)) from None
        morceaux = collection.morceaux
        if adresse.liste and demande.elements:
            choisis = set(demande.elements)
            morceaux = [m for m in morceaux if m.index in choisis]
        if not morceaux:
            raise HTTPException(400, "Aucun morceau à télécharger dans cette sélection.")
        titre = titre or collection.titre

    options = Options(
        format=demande.format,
        qualite=qualite,
        debut=demande.debut,
        fin=demande.fin,
        sous_titres=demande.sous_titres if demande.format in VIDEO else [],
        pochette=demande.pochette,
        elements=demande.elements if adresse.liste else [],
    )
    return await gestionnaire.creer(visiteur, adresse, options, titre, morceaux)


def _tache_du_visiteur(ident: str, x_visiteur: str | None) -> str:
    visiteur = visiteur_de(x_visiteur)
    if not gestionnaire.appartient(ident, visiteur):
        raise HTTPException(404, "Téléchargement introuvable.")
    return visiteur


@app.get("/taches/{ident}")
def etat(ident: str, x_visiteur: str | None = Header(None)):
    _tache_du_visiteur(ident, x_visiteur)
    return gestionnaire.etat(ident)


@app.delete("/taches/{ident}")
def annuler(ident: str, x_visiteur: str | None = Header(None)):
    _tache_du_visiteur(ident, x_visiteur)
    if not gestionnaire.annuler(ident):
        raise HTTPException(409, "Ce téléchargement est déjà terminé.")
    return {"ok": True}


@app.get("/taches/{ident}/fichier")
def fichier(ident: str):
    """Téléchargement du fichier prêt.

    Ouvert par un simple lien (le navigateur ne peut pas y joindre d'en-tête) :
    l'identifiant, tiré au hasard sur 96 bits, en tient lieu. Le fichier reste
    disponible jusqu'à son expiration, pour pouvoir le reprendre.
    """
    trouve = gestionnaire.fichier(ident)
    if not trouve:
        raise HTTPException(404, "Fichier expiré ou introuvable : relancez le téléchargement.")
    chemin, nom = trouve
    return FileResponse(chemin, filename=nom, media_type="application/octet-stream")


@app.get("/historique")
def historique(x_visiteur: str | None = Header(None)):
    visiteur = visiteur_de(x_visiteur)
    return {"taches": [gestionnaire.etat(t["id"]) for t in registre.historique(visiteur)]}


@app.delete("/historique")
def effacer_historique(x_visiteur: str | None = Header(None)):
    gestionnaire.effacer_historique(visiteur_de(x_visiteur))
    return {"ok": True}
