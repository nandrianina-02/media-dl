"""File des téléchargements.

Une file unique pour tout le serveur, avec un nombre borné de conversions
simultanées : l'ancien serveur lançait un fil par demande, sans limite ni
délai maximal, et un visiteur pouvait l'épuiser seul. La progression vient
de yt-dlp lui-même (octets, vitesse, temps restant), et non plus d'un
compteur qui avançait de 5 % toutes les secondes et demie.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import time
import zipfile
from collections import deque
from dataclasses import asdict
from typing import Any

from .config import Settings
from .erreurs import message_clair
from .store import Registre
from .urls import Adresse
from .ytdlp import Avancement, Options, commande_telechargement, lire_ligne, nettoyer_nom

log = logging.getLogger("media-dl")

EN_COURS = ("attente", "telechargement", "conversion")


class Refus(Exception):
    """Demande refusée, avec un message lisible et un code HTTP."""

    def __init__(self, message: str, code: int = 429):
        super().__init__(message)
        self.code = code


class Gestionnaire:
    def __init__(self, settings: Settings, registre: Registre):
        self.s = settings
        self.registre = registre
        self._places = asyncio.Semaphore(settings.max_concurrent)
        self._attente: deque[str] = deque()
        self._vivant: dict[str, dict[str, Any]] = {}
        os.makedirs(settings.out_dir, exist_ok=True)

    # --- Création -----------------------------------------------------------------

    def actives_du_visiteur(self, visiteur: str) -> int:
        return sum(1 for v in self._vivant.values() if v["visiteur"] == visiteur)

    async def creer(self, visiteur: str, adresse: Adresse, options: Options, titre: str | None) -> dict[str, Any]:
        if self.actives_du_visiteur(visiteur) >= self.s.max_active_per_visitor:
            raise Refus(f"Vous avez déjà {self.s.max_active_per_visitor} téléchargements en cours : attendez qu'un se termine.")
        if len(self._attente) >= self.s.max_queue:
            raise Refus("Le service est très demandé en ce moment. Réessayez dans quelques minutes.", 503)

        ident = secrets.token_urlsafe(12)
        tache = {
            "id": ident,
            "visiteur": visiteur,
            "url": adresse.url,
            "site": adresse.site,
            "titre": titre,
            "format": options.format,
            "qualite": options.qualite,
            "options": asdict(options),
            "liste": adresse.liste,
            "statut": "attente",
            "cree_le": time.time(),
        }
        self.registre.creer(tache)
        total = len(options.elements) if options.elements else None
        self._vivant[ident] = {"visiteur": visiteur, "avancement": Avancement(), "processus": None, "annule": False, "total": total}
        self._attente.append(ident)
        asyncio.create_task(self._executer(ident, adresse, options))
        return self.etat(ident) or tache

    # --- Exécution ----------------------------------------------------------------

    async def _executer(self, ident: str, adresse: Adresse, options: Options) -> None:
        async with self._places:
            if ident in self._attente:
                self._attente.remove(ident)
            vivant = self._vivant.get(ident)
            if not vivant or vivant["annule"]:
                self._vivant.pop(ident, None)
                return
            dossier = os.path.join(self.s.out_dir, ident)
            os.makedirs(dossier, exist_ok=True)
            chemins = os.path.join(dossier, ".chemins")
            cmd = commande_telechargement(
                self.s.ytdlp_bin,
                adresse.url,
                dossier,
                options,
                liste=adresse.liste,
                facebook=adresse.domaine in ("facebook.com", "fb.watch"),
                max_duree=self.s.max_duration,
                max_taille=self.s.max_filesize,
                max_elements=self.s.max_playlist_items,
                fichier_chemins=chemins,
            )
            self.registre.maj(ident, statut="telechargement")
            journal: deque[str] = deque(maxlen=80)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )
                vivant["processus"] = proc
                await asyncio.wait_for(self._suivre(ident, proc, journal), timeout=self.s.job_timeout)
                code = await proc.wait()
            except asyncio.TimeoutError:
                self._tuer(ident)
                self._echec(ident, "Le téléchargement a pris trop de temps et a été arrêté.", dossier)
                return
            except FileNotFoundError:
                log.error("yt-dlp introuvable : %s", self.s.ytdlp_bin)
                self._echec(ident, "Le service est momentanément indisponible.", dossier)
                return

            if vivant["annule"]:
                self._echec(ident, None, dossier, statut="annule")
                return

            fichiers = self._fichiers(dossier, chemins)
            texte = "\n".join(journal)
            if not fichiers:
                log.warning("tâche %s sans fichier (code %s) : %s", ident, code, texte[-1500:])
                self._echec(ident, message_clair(texte), dossier)
                return
            if code != 0:
                # Liste : certains éléments ont pu échouer, les autres sont livrés.
                log.info("tâche %s terminée avec des erreurs partielles", ident)

            try:
                fichier, nom = self._livrable(fichiers, dossier, adresse.liste)
            except OSError as erreur:
                log.exception("archive impossible : %s", erreur)
                self._echec(ident, "La préparation du fichier a échoué.", dossier)
                return
            maintenant = time.time()
            self.registre.maj(
                ident,
                statut="pret",
                fichier=fichier,
                nom=nom,
                taille=os.path.getsize(fichier),
                fini_le=maintenant,
                expire_le=maintenant + self.s.file_ttl,
                erreur=None if code == 0 else "Certains éléments n'ont pas pu être téléchargés.",
            )
            self._vivant.pop(ident, None)

    async def _suivre(self, ident: str, proc: asyncio.subprocess.Process, journal: deque[str]) -> None:
        assert proc.stdout
        while True:
            octets = await proc.stdout.readline()
            if not octets:
                break
            ligne = octets.decode("utf-8", errors="replace").rstrip()
            avancement = lire_ligne(ligne)
            vivant = self._vivant.get(ident)
            if avancement and vivant:
                precedent = vivant["avancement"]
                if avancement.etape == "conversion" and precedent.etape != "conversion":
                    self.registre.maj(ident, statut="conversion")
                elif avancement.etape == "telechargement" and precedent.etape == "conversion":
                    self.registre.maj(ident, statut="telechargement")
                vivant["avancement"] = avancement
            elif ligne:
                journal.append(ligne)

    def _fichiers(self, dossier: str, chemins: str) -> list[str]:
        trouves: list[str] = []
        if os.path.exists(chemins):
            with open(chemins, encoding="utf-8", errors="replace") as f:
                for ligne in f:
                    chemin = ligne.strip()
                    # Seuls les fichiers du dossier de la tâche sont livrables.
                    if chemin and os.path.isfile(chemin) and os.path.realpath(chemin).startswith(os.path.realpath(dossier)):
                        trouves.append(chemin)
        if not trouves:
            trouves = [
                os.path.join(dossier, n)
                for n in sorted(os.listdir(dossier))
                if not n.startswith(".") and not n.endswith((".part", ".ytdl", ".jpg", ".webp", ".vtt", ".srt"))
            ]
        return sorted(set(trouves))

    def _livrable(self, fichiers: list[str], dossier: str, liste: bool) -> tuple[str, str]:
        if len(fichiers) == 1 and not liste:
            return fichiers[0], nettoyer_nom(os.path.basename(fichiers[0]))
        # Plusieurs fichiers : une archive. Sans compression, l'audio et la
        # vidéo l'étant déjà ; le gain serait nul et le temps perdu réel.
        archive = os.path.join(dossier, ".archive.zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            for chemin in fichiers:
                zf.write(chemin, arcname=nettoyer_nom(os.path.basename(chemin)))
        for chemin in fichiers:
            os.remove(chemin)
        return archive, "playlist.zip"

    def _echec(self, ident: str, message: str | None, dossier: str, statut: str = "erreur") -> None:
        self.registre.maj(ident, statut=statut, erreur=message, fini_le=time.time())
        self._vivant.pop(ident, None)
        shutil.rmtree(dossier, ignore_errors=True)

    def _tuer(self, ident: str) -> None:
        proc = (self._vivant.get(ident) or {}).get("processus")
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    # --- Lecture et annulation ------------------------------------------------------

    def etat(self, ident: str) -> dict[str, Any] | None:
        tache = self.registre.lire(ident)
        if not tache:
            return None
        vivant = self._vivant.get(ident)
        etat: dict[str, Any] = {k: v for k, v in tache.items() if k not in ("fichier", "visiteur")}
        etat["disponible"] = tache["statut"] == "pret" and bool(tache.get("fichier")) and os.path.exists(tache["fichier"])
        if vivant:
            a: Avancement = vivant["avancement"]
            pourcent = a.pourcentage
            total = vivant.get("total")
            if total and a.element:
                # Une liste : l'avancement couvre tous ses éléments.
                rang = min(a.element, total)
                pourcent = ((rang - 1) + (pourcent or 0) / 100) / total * 100
            etat["progression"] = {
                "etape": a.etape,
                "pourcentage": round(pourcent, 1) if pourcent is not None else None,
                "octets": a.octets,
                "total": a.total,
                "vitesse": a.vitesse,
                "restant": a.restant,
                "element": a.element,
                "elements": total,
            }
            if ident in self._attente:
                etat["position"] = list(self._attente).index(ident) + 1
        return etat

    def appartient(self, ident: str, visiteur: str) -> bool:
        tache = self.registre.lire(ident)
        return bool(tache and tache["visiteur"] == visiteur)

    def annuler(self, ident: str) -> bool:
        vivant = self._vivant.get(ident)
        if not vivant:
            return False
        vivant["annule"] = True
        if ident in self._attente:
            self._attente.remove(ident)
            self.registre.maj(ident, statut="annule", fini_le=time.time())
            self._vivant.pop(ident, None)
        else:
            self._tuer(ident)
        return True

    def fichier(self, ident: str) -> tuple[str, str] | None:
        tache = self.registre.lire(ident)
        if not tache or tache["statut"] != "pret" or not tache.get("fichier") or not os.path.exists(tache["fichier"]):
            return None
        return tache["fichier"], tache["nom"] or "media"

    def effacer_historique(self, visiteur: str) -> None:
        for fichier in self.registre.effacer_historique(visiteur):
            shutil.rmtree(os.path.dirname(fichier), ignore_errors=True)

    # --- Nettoyage ------------------------------------------------------------------

    def nettoyer(self, maintenant: float | None = None) -> int:
        maintenant = time.time() if maintenant is None else maintenant
        effaces = 0
        for tache in self.registre.expires(maintenant):
            if tache.get("fichier"):
                shutil.rmtree(os.path.dirname(tache["fichier"]), ignore_errors=True)
            self.registre.maj(tache["id"], statut="expire", fichier=None)
            effaces += 1
        # Dossiers orphelins (arrêt brutal pendant un téléchargement).
        actifs = set(self._vivant)
        for nom in os.listdir(self.s.out_dir):
            chemin = os.path.join(self.s.out_dir, nom)
            if os.path.isdir(chemin) and nom not in actifs and maintenant - os.path.getmtime(chemin) > self.s.file_ttl * 2:
                shutil.rmtree(chemin, ignore_errors=True)
        self.registre.purger(maintenant - 30 * 86400)
        return effaces
