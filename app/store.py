"""Historique des téléchargements, en SQLite.

Seules les métadonnées sont gardées (titre, format, état, date) : les
fichiers, eux, sont effacés après quelques minutes. Chaque visiteur est
identifié par un jeton anonyme tiré au hasard par son navigateur ; il ne
voit que son propre historique.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS taches (
  id TEXT PRIMARY KEY,
  visiteur TEXT NOT NULL,
  url TEXT NOT NULL,
  site TEXT NOT NULL,
  titre TEXT,
  format TEXT NOT NULL,
  qualite TEXT NOT NULL,
  options TEXT NOT NULL,
  liste INTEGER NOT NULL DEFAULT 0,
  statut TEXT NOT NULL,
  erreur TEXT,
  fichier TEXT,
  nom TEXT,
  taille INTEGER,
  cree_le REAL NOT NULL,
  fini_le REAL,
  expire_le REAL
);
CREATE INDEX IF NOT EXISTS taches_visiteur ON taches (visiteur, cree_le DESC);
"""

COLONNES = ("id", "visiteur", "url", "site", "titre", "format", "qualite", "options", "liste", "statut", "erreur", "fichier", "nom", "taille", "cree_le", "fini_le", "expire_le")


class Registre:
    def __init__(self, chemin: str):
        os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
        self._db = sqlite3.connect(chemin, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._verrou = threading.Lock()
        with self._verrou:
            self._db.executescript(_SCHEMA)
            # Ce qui tournait au moment d'un redémarrage ne reprendra pas.
            self._db.execute(
                "UPDATE taches SET statut = 'erreur', erreur = ?, fini_le = ? WHERE statut IN ('attente', 'telechargement', 'conversion')",
                ("Interrompu par un redémarrage du serveur. Relancez le téléchargement.", time.time()),
            )
            self._db.commit()

    def creer(self, tache: dict[str, Any]) -> None:
        valeurs = {**tache, "options": json.dumps(tache.get("options", {})), "liste": int(bool(tache.get("liste")))}
        with self._verrou:
            self._db.execute(
                f"INSERT INTO taches ({', '.join(COLONNES)}) VALUES ({', '.join('?' for _ in COLONNES)})",
                [valeurs.get(c) for c in COLONNES],
            )
            self._db.commit()

    def maj(self, ident: str, **champs: Any) -> None:
        if not champs:
            return
        with self._verrou:
            self._db.execute(
                f"UPDATE taches SET {', '.join(f'{k} = ?' for k in champs)} WHERE id = ?",
                [*champs.values(), ident],
            )
            self._db.commit()

    def lire(self, ident: str) -> dict[str, Any] | None:
        with self._verrou:
            ligne = self._db.execute("SELECT * FROM taches WHERE id = ?", (ident,)).fetchone()
        return _dict(ligne) if ligne else None

    def historique(self, visiteur: str, limite: int = 50) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._db.execute(
                "SELECT * FROM taches WHERE visiteur = ? ORDER BY cree_le DESC LIMIT ?", (visiteur, limite)
            ).fetchall()
        return [_dict(l) for l in lignes]

    def effacer_historique(self, visiteur: str) -> list[str]:
        """Supprime l'historique terminé d'un visiteur ; renvoie les fichiers à effacer."""
        with self._verrou:
            lignes = self._db.execute(
                "SELECT fichier FROM taches WHERE visiteur = ? AND statut NOT IN ('attente', 'telechargement', 'conversion')",
                (visiteur,),
            ).fetchall()
            self._db.execute(
                "DELETE FROM taches WHERE visiteur = ? AND statut NOT IN ('attente', 'telechargement', 'conversion')", (visiteur,)
            )
            self._db.commit()
        return [l["fichier"] for l in lignes if l["fichier"]]

    def expires(self, maintenant: float) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._db.execute(
                "SELECT * FROM taches WHERE statut = 'pret' AND expire_le IS NOT NULL AND expire_le < ?", (maintenant,)
            ).fetchall()
        return [_dict(l) for l in lignes]

    def purger(self, avant: float) -> None:
        """Oublie les entrées de plus de trente jours."""
        with self._verrou:
            self._db.execute("DELETE FROM taches WHERE cree_le < ?", (avant,))
            self._db.commit()


def _dict(ligne: sqlite3.Row) -> dict[str, Any]:
    d = dict(ligne)
    d["options"] = json.loads(d.get("options") or "{}")
    d["liste"] = bool(d.get("liste"))
    return d
