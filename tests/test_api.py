import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app

VISITEUR = {"X-Visiteur": "visiteur-essai-0123456789"}
AUTRE = {"X-Visiteur": "autre-visiteur-0123456789"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def attendre(client, ident, entete=VISITEUR, delai=20):
    fin = time.time() + delai
    while time.time() < fin:
        etat = client.get(f"/taches/{ident}", headers=entete).json()
        if etat["statut"] not in ("attente", "telechargement", "conversion"):
            return etat
        time.sleep(0.1)
    raise AssertionError("tâche jamais terminée")


def test_infos_media(client):
    r = client.get("/infos", params={"url": "https://www.youtube.com/watch?v=abc"})
    assert r.status_code == 200
    assert r.json()["titre"] == "Titre d'essai" and r.json()["hauteurs"] == [360, 720] and r.json()["sous_titres"] == ["en", "fr"]


def test_infos_erreur_lisible(client):
    r = client.get("/infos", params={"url": "https://www.youtube.com/watch?v=privee"})
    assert r.status_code == 422 and "privée" in r.json()["detail"]


def test_infos_site_refuse(client):
    r = client.get("/infos", params={"url": "https://exemple.com/video"})
    assert r.status_code == 400 and "Site non pris en charge" in r.json()["detail"]


def test_identifiant_visiteur_obligatoire(client):
    r = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert r.status_code == 400


def test_telechargement_complet(client):
    r = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc", "format": "mp3", "qualite": "320", "titre": "Titre"}, headers=VISITEUR)
    assert r.status_code == 201
    ident = r.json()["id"]
    assert len(ident) >= 16  # impossible à deviner
    etat = attendre(client, ident)
    assert etat["statut"] == "pret" and etat["disponible"] and etat["nom"] == "Titre d'essai.mp3"
    fichier = client.get(f"/taches/{ident}/fichier")
    assert fichier.status_code == 200 and fichier.content == b"contenu Titre d'essai.mp3"
    # Toujours disponible ensuite, jusqu'à l'expiration.
    assert client.get(f"/taches/{ident}/fichier").status_code == 200


def test_un_visiteur_ne_voit_pas_les_taches_d_un_autre(client):
    ident = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=VISITEUR).json()["id"]
    assert client.get(f"/taches/{ident}", headers=AUTRE).status_code == 404
    assert client.delete(f"/taches/{ident}", headers=AUTRE).status_code == 404
    attendre(client, ident)


def test_erreur_traduite(client):
    ident = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=privee"}, headers=VISITEUR).json()["id"]
    etat = attendre(client, ident)
    assert etat["statut"] == "erreur" and "privée" in etat["erreur"]


def test_media_ecarte_par_le_filtre(client):
    ident = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=filtre"}, headers=VISITEUR).json()["id"]
    etat = attendre(client, ident)
    assert etat["statut"] == "erreur" and "durée maximale" in etat["erreur"]


def test_progression_reelle_et_annulation(client):
    ident = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=lent"}, headers=VISITEUR).json()["id"]
    vu = None
    fin = time.time() + 10
    while time.time() < fin and not vu:
        etat = client.get(f"/taches/{ident}", headers=VISITEUR).json()
        prog = etat.get("progression") or {}
        if prog.get("pourcentage"):
            vu = prog
        time.sleep(0.1)
    assert vu and 0 < vu["pourcentage"] <= 100 and vu["vitesse"] == 50000
    assert client.delete(f"/taches/{ident}", headers=VISITEUR).status_code == 200
    assert attendre(client, ident)["statut"] == "annule"


def test_limite_par_visiteur(client):
    v = {"X-Visiteur": "visiteur-limite-0123456789"}
    ids = [client.post("/taches", json={"url": "https://www.youtube.com/watch?v=lent"}, headers=v).json()["id"] for _ in range(2)]
    refus = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=lent"}, headers=v)
    assert refus.status_code == 429 and "déjà 2" in refus.json()["detail"]
    for ident in ids:
        client.delete(f"/taches/{ident}", headers=v)
        attendre(client, ident, v)


def test_playlist_en_archive(client):
    r = client.post(
        "/taches",
        json={"url": "https://www.youtube.com/playlist?list=PLplaylist", "liste": True, "format": "m4a", "elements": [1, 3]},
        headers=VISITEUR,
    )
    etat = attendre(client, r.json()["id"])
    assert etat["statut"] == "pret" and etat["nom"] == "playlist.zip"
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/taches/{etat['id']}/fichier").content))
    assert sorted(archive.namelist()) == ["001 - Morceau 1.m4a", "003 - Morceau 3.m4a"]


def test_requete_invalide(client):
    assert client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc", "format": "exe"}, headers=VISITEUR).status_code == 422
    assert client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc", "debut": 50, "fin": 10}, headers=VISITEUR).status_code == 400
    assert client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc", "format": "mp4", "sous_titres": ["fr;rm"]}, headers=VISITEUR).status_code == 422


def test_historique_et_effacement(client):
    taches = client.get("/historique", headers=VISITEUR).json()["taches"]
    assert len(taches) >= 3 and all("fichier" not in t and "visiteur" not in t for t in taches)
    assert client.get("/historique", headers=AUTRE).json()["taches"] == [] or all(
        t["id"] not in {x["id"] for x in taches} for t in client.get("/historique", headers=AUTRE).json()["taches"]
    )
    assert client.delete("/historique", headers=VISITEUR).status_code == 200
    assert client.get("/historique", headers=VISITEUR).json()["taches"] == []


def test_nettoyage_des_fichiers_expires(client):
    from app.main import gestionnaire

    ident = client.post("/taches", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=VISITEUR).json()["id"]
    attendre(client, ident)
    assert gestionnaire.nettoyer(time.time() + 10**6) >= 1
    assert client.get(f"/taches/{ident}", headers=VISITEUR).json()["statut"] == "expire"
    assert client.get(f"/taches/{ident}/fichier").status_code == 404
