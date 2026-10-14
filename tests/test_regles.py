import pytest

from app.erreurs import message_clair
from app.limites import Limiteur
from app.urls import AdresseInvalide, a_une_liste, analyser
from app.ytdlp import Options, commande_telechargement, lire_ligne, resume_infos


# --- Adresses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "brute, attendu",
    [
        ("https://www.youtube.com/watch?v=abc123&t=42s", "https://www.youtube.com/watch?v=abc123"),
        ("youtu.be/abc123?si=xyz", "https://www.youtube.com/watch?v=abc123"),
        ("https://youtube.com/shorts/abc123", "https://www.youtube.com/watch?v=abc123"),
        ("https://music.youtube.com/watch?v=abc123", "https://www.youtube.com/watch?v=abc123"),
        ("https://soundcloud.com/artiste/titre", "https://soundcloud.com/artiste/titre"),
    ],
)
def test_adresses_normalisees(brute, attendu):
    assert analyser(brute).url == attendu


@pytest.mark.parametrize(
    "brute",
    [
        "https://autre-site.com/?x.com",  # le nom du site ailleurs que dans l'hôte
        "https://notyoutube.com/watch?v=abc",  # domaine qui se termine pareil
        "https://youtube.com.pirate.net/watch?v=abc",
        "ftp://youtube.com/video",
        "-o /etc/passwd youtube.com",  # tentative d'option yt-dlp
        "https://user:mdp@youtube.com/watch?v=abc",
        "",
    ],
)
def test_adresses_refusees(brute):
    with pytest.raises(AdresseInvalide):
        analyser(brute)


def test_playlist_youtube():
    assert analyser("https://www.youtube.com/playlist?list=PL123").liste
    melange = "https://www.youtube.com/watch?v=abc&list=PL123"
    assert not analyser(melange).liste  # par défaut : la vidéo seule
    assert analyser(melange, garder_liste=True).url == "https://www.youtube.com/playlist?list=PL123"
    assert a_une_liste(melange)
    # Les « mix » générés par YouTube ne sont pas des listes à télécharger.
    assert not a_une_liste("https://www.youtube.com/watch?v=abc&list=RDabc")


def test_set_soundcloud():
    assert analyser("https://soundcloud.com/artiste/sets/album").liste


# --- Erreurs -------------------------------------------------------------------------


def test_messages_clairs():
    assert "privée" in message_clair("ERROR: Private video. Sign in if you've been granted access")
    assert "limite d'âge" in message_clair("Sign in to confirm your age. This video may be inappropriate")
    assert "bloqué le serveur" in message_clair("Sign in to confirm you’re not a bot")
    # Le filtre cite « is_live » : il ne doit pas passer pour un direct.
    assert "durée maximale" in message_clair("does not pass filter (!is_live & duration <=? 10800), skipping")
    assert message_clair("une erreur inconnue").startswith("Le téléchargement a échoué")


# --- Commandes -----------------------------------------------------------------------


def _cmd(url="https://www.youtube.com/watch?v=abc", liste=False, **options):
    return commande_telechargement(
        ("yt-dlp",), url, "/tmp/x", Options(**options), liste=liste, facebook=False,
        max_duree=3600, max_taille="1G", max_elements=50, fichier_chemins="/tmp/x/.chemins",
    )


def test_adresse_toujours_apres_le_separateur():
    cmd = _cmd()
    assert cmd[-2:] == ["--", "https://www.youtube.com/watch?v=abc"]
    assert "--match-filter" in cmd and "--max-filesize" in cmd


def test_audio_avec_pochette_et_qualite():
    cmd = _cmd(format="mp3", qualite="320")
    assert cmd[cmd.index("--audio-format") + 1] == "mp3"
    assert cmd[cmd.index("--audio-quality") + 1] == "0"
    assert "--embed-thumbnail" in cmd
    wav = _cmd(format="wav")
    assert "--audio-quality" not in wav and "--embed-thumbnail" not in wav


def test_video_sous_titres_et_decoupe():
    cmd = _cmd(format="mp4", qualite="720", sous_titres=["fr"], debut=65, fin=130.5)
    assert "height<=720" in cmd[cmd.index("-f") + 1]
    assert cmd[cmd.index("--sub-langs") + 1] == "fr"
    assert cmd[cmd.index("--download-sections") + 1] == "*00:01:05.000-00:02:10.500"


def test_elements_de_playlist_bornes():
    cmd = _cmd(url="https://www.youtube.com/playlist?list=PL1", liste=True, elements=[3, 1, 3, 99])
    assert cmd[cmd.index("--playlist-items") + 1] == "1,3"
    assert "--download-sections" not in _cmd(liste=True, debut=10)


def test_lecture_de_la_progression():
    a = lire_ligne("MDL|dl|250|1000|NA|51200.5|12|2")
    assert a and a.pourcentage == 25 and a.vitesse == 51200.5 and a.restant == 12 and a.element == 2
    estime = lire_ligne("MDL|dl|500|NA|2000|NA|NA|NA")
    assert estime and estime.pourcentage == 25 and estime.vitesse is None
    conversion = lire_ligne("MDL|pp|FFmpegExtractAudio|NA")
    assert conversion and conversion.etape == "conversion"
    assert lire_ligne("[youtube] abc: Downloading webpage") is None


def test_resume_media_et_liste():
    media = resume_infos({"title": "T", "formats": [{"height": 1080, "vcodec": "vp9"}, {"height": 480, "vcodec": "avc1"}]}, liste=False)
    assert media["hauteurs"] == [480, 1080]
    liste = resume_infos({"_type": "playlist", "title": "P", "entries": [{"title": "a"}, None, {"title": "b"}]}, liste=True)
    # Un élément indisponible est omis sans décaler les positions : elles
    # servent telles quelles à --playlist-items.
    assert [e["index"] for e in liste["entrees"]] == [1, 3]


# --- Limites -------------------------------------------------------------------------


def test_limiteur_fenetre_glissante():
    l = Limiteur(2, 60)
    assert l.autoriser("ip", 0)[0] and l.autoriser("ip", 1)[0]
    ok, attente = l.autoriser("ip", 2)
    assert not ok and attente > 0
    assert l.autoriser("autre", 2)[0]
    assert l.autoriser("ip", 61)[0]
