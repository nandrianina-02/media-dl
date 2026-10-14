import io
import os
import shutil
import subprocess
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main
from app.etiquettes import etiqueter
from app.spotify import ErreurSpotify, Morceau
from app.ytdlp import Options, commande_telechargement

VISITEUR = {"X-Visiteur": "visiteur-spotify-0123456789"}
PISTE = "4uLU6hMCjMI75M1A2tKUQC"
ALBUM = "1DFixLWuPkv3KT3TnV35m3"
PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"
ABSENT = "0000000000000000000000"


def _piste(nom, artiste, duree_ms=200_000, numero=1):
    return {
        "name": nom,
        "artists": [{"name": artiste}, {"name": "Invité"}],
        "duration_ms": duree_ms,
        "track_number": numero,
        "type": "track",
        "album": {"name": "Album d'essai", "release_date": "2021-05-07", "images": [{"url": "https://i.scdn.co/image/grande"}]},
    }


# Réponses de l'API Spotify, sans réseau.
REPONSES = {
    f"/tracks/{PISTE}": _piste("Chanson / Spéciale", "Artiste"),
    f"/playlists/{PLAYLIST}": {
        "name": "Ma sélection",
        "owner": {"display_name": "Quelqu'un"},
        "images": [{"url": "https://i.scdn.co/image/liste"}],
        "tracks": {
            "total": 4,
            "next": None,
            "items": [
                {"track": _piste("Premier", "A")},
                {"track": {**_piste("Épisode", "B"), "type": "episode"}},  # podcast : écarté
                {"track": _piste("introuvable", "C")},
                {"track": _piste("longue", "D")},
            ],
        },
    },
}


@pytest.fixture(scope="module")
def client():
    async def faux_get(chemin, params=None):
        if chemin not in REPONSES:
            raise ErreurSpotify("Introuvable sur Spotify.")
        return REPONSES[chemin]

    sauvegarde = (main.spotify.client_id, main.spotify.secret, main.spotify._get)
    main.spotify.client_id, main.spotify.secret, main.spotify._get = "id", "secret", faux_get
    with TestClient(main.app) as c:
        yield c
    main.spotify.client_id, main.spotify.secret, main.spotify._get = sauvegarde


def attendre(client, ident, delai=20):
    fin = time.time() + delai
    while time.time() < fin:
        etat = client.get(f"/taches/{ident}", headers=VISITEUR).json()
        if etat["statut"] not in ("attente", "telechargement", "conversion"):
            return etat
        time.sleep(0.1)
    raise AssertionError("tâche jamais terminée")


def test_apercu_d_un_titre(client):
    r = client.get("/infos", params={"url": f"https://open.spotify.com/intl-fr/track/{PISTE}?si=xyz"})
    assert r.status_code == 200
    infos = r.json()
    assert infos["type"] == "media" and infos["audio_seul"] and infos["site"] == "Spotify"
    assert infos["titre"] == "Chanson / Spéciale" and infos["auteur"] == "Artiste, Invité" and infos["duree"] == 200


def test_apercu_d_une_playlist_garde_les_positions(client):
    infos = client.get("/infos", params={"url": f"https://open.spotify.com/playlist/{PLAYLIST}"}).json()
    assert infos["type"] == "liste" and infos["titre"] == "Ma sélection"
    # L'épisode de podcast (position 2) est écarté, sans décaler les suivants.
    assert [e["index"] for e in infos["entrees"]] == [1, 3, 4]


def test_contenu_absent(client):
    r = client.get("/infos", params={"url": f"https://open.spotify.com/album/{ABSENT}"})
    assert r.status_code == 422 and "Introuvable" in r.json()["detail"]


def test_video_refusee(client):
    r = client.post("/taches", json={"url": f"https://open.spotify.com/track/{PISTE}", "format": "mp4"}, headers=VISITEUR)
    assert r.status_code == 400 and "audio" in r.json()["detail"]


def test_titre_telecharge_sous_le_nom_spotify(client):
    r = client.post("/taches", json={"url": f"https://open.spotify.com/track/{PISTE}", "format": "mp3"}, headers=VISITEUR)
    assert r.status_code == 201 and r.json()["titre"] == "Chanson / Spéciale"
    etat = attendre(client, r.json()["id"])
    # Le « / » du titre ne doit pas créer de dossier.
    assert etat["statut"] == "pret" and etat["nom"] == "Artiste, Invité - Chanson Spéciale.mp3"


def test_playlist_partielle_en_archive(client):
    r = client.post("/taches", json={"url": f"https://open.spotify.com/playlist/{PLAYLIST}", "liste": True, "format": "m4a"}, headers=VISITEUR)
    etat = attendre(client, r.json()["id"])
    # « introuvable » échoue, « longue » est rattrapé par la recherche sans durée.
    assert etat["statut"] == "pret" and etat["nom"] == "Ma sélection.zip"
    assert etat["erreur"] == "Certains éléments n'ont pas pu être téléchargés."
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/taches/{etat['id']}/fichier").content))
    assert sorted(archive.namelist()) == ["001 - A, Invité - Premier.m4a", "004 - D, Invité - longue.m4a"]


def test_selection_dans_une_playlist(client):
    r = client.post(
        "/taches",
        json={"url": f"https://open.spotify.com/playlist/{PLAYLIST}", "liste": True, "format": "mp3", "elements": [4]},
        headers=VISITEUR,
    )
    etat = attendre(client, r.json()["id"])
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/taches/{etat['id']}/fichier").content))
    assert archive.namelist() == ["004 - D, Invité - longue.mp3"] and etat["erreur"] is None


def test_aucun_resultat(client):
    REPONSES["/tracks/" + "1" * 22] = _piste("introuvable", "X")
    r = client.post("/taches", json={"url": "https://open.spotify.com/track/" + "1" * 22, "format": "mp3"}, headers=VISITEUR)
    etat = attendre(client, r.json()["id"])
    assert etat["statut"] == "erreur" and "Aucun enregistrement correspondant" in etat["erreur"]


def test_spotify_non_configure():
    sauvegarde = main.spotify.client_id
    main.spotify.client_id = ""
    try:
        with TestClient(main.app) as c:
            r = c.get("/infos", params={"url": f"https://open.spotify.com/track/{PISTE}"})
            assert r.status_code == 503 and "pas activée" in r.json()["detail"]
            assert "Spotify" not in c.get("/").json()["sites"]
    finally:
        main.spotify.client_id = sauvegarde


def test_commande_de_recherche():
    cmd = commande_telechargement(
        ("yt-dlp",), "ytsearch5:Artiste - Titre audio", "/tmp/x", Options(format="mp3", pochette=False),
        liste=False, facebook=False, max_duree=3600, max_taille="1G", max_elements=1,
        fichier_chemins="/tmp/x/.c", nom="Artiste - 100% Titre", duree_cible=200,
    )
    filtre = cmd[cmd.index("--match-filter") + 1]
    assert "duration >=? 184" in filtre and "duration <=? 216" in filtre
    assert cmd[cmd.index("--max-downloads") + 1] == "1" and "--no-playlist" not in cmd
    assert cmd[cmd.index("-o") + 1] == "Artiste - 100%% Titre.%(ext)s"
    assert "--embed-thumbnail" not in cmd


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
@pytest.mark.parametrize("extension", ["mp3", "m4a", "flac", "opus"])
def test_etiquettes_et_pochette(tmp_path, extension):
    from mutagen import File

    chemin = str(tmp_path / f"essai.{extension}")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", chemin], check=True
    )
    image = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")  # JPEG minimal
    m = Morceau(1, "Titre", ["Artiste", "Invité"], album="Album", numero=3, annee="2021")
    assert etiqueter(chemin, m, image)
    audio = File(chemin, easy=True)
    assert audio["title"] == ["Titre"] and audio["artist"] == ["Artiste", "Invité"] and audio["album"] == ["Album"]
    complet = File(chemin)
    if extension == "mp3":
        assert complet.tags.getall("APIC")[0].data == image
    elif extension == "m4a":
        assert bytes(complet["covr"][0]) == image
    elif extension == "flac":
        assert complet.pictures[0].data == image
    else:
        assert complet["metadata_block_picture"]


def test_etiquettes_jamais_bloquantes(tmp_path):
    faux = tmp_path / "pas-de-l-audio.mp3"
    faux.write_bytes(b"contenu")
    assert etiqueter(str(faux), Morceau(1, "T", ["A"]), None) is False
    assert os.path.exists(faux)
