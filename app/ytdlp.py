"""Construction des commandes yt-dlp et lecture de sa progression.

Les commandes sont des listes d'arguments, jamais une chaîne passée à un
shell ; l'adresse vient en dernier, après « -- », pour qu'aucune valeur
fournie par le visiteur ne puisse être lue comme une option.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

AUDIO = ("mp3", "m4a", "ogg", "wav", "opus", "flac")
VIDEO = ("mp4", "webm")
FORMATS = AUDIO + VIDEO

# Débit demandé -> qualité VBR de yt-dlp (0 = meilleure, 9 = plus légère).
QUALITES_AUDIO = {"64": "9", "128": "5", "192": "3", "256": "1", "320": "0"}
QUALITES_VIDEO = ("360", "480", "720", "1080", "1440", "2160", "best")

# Ligne de progression émise par yt-dlp, une par mise à jour (--newline).
MARQUE = "MDL|"
GABARIT_PROGRESSION = (
    "download:" + MARQUE + "dl|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(info.playlist_index)s"
)
GABARIT_CONVERSION = "postprocess:" + MARQUE + "pp|%(progress.postprocessor)s|%(info.playlist_index)s"


@dataclass
class Options:
    format: str = "mp3"
    qualite: str = "192"
    # Découpe, en secondes (vidéo ou morceau seul).
    debut: float | None = None
    fin: float | None = None
    # Sous-titres incrustés dans la vidéo (langues, ex. ["fr", "en"]).
    sous_titres: list[str] = field(default_factory=list)
    # Pochette et métadonnées dans le fichier audio.
    pochette: bool = True
    # Éléments retenus d'une playlist (index à partir de 1) ; vide : tous.
    elements: list[int] = field(default_factory=list)


def _horodatage(secondes: float) -> str:
    secondes = max(0.0, secondes)
    h, reste = divmod(secondes, 3600)
    m, s = divmod(reste, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def selecteur_video(qualite: str, facebook: bool) -> str:
    """Sélecteur de format vidéo. Facebook ne fournit pas de flux séparés fiables."""
    hauteur = None if qualite == "best" else int(qualite)
    if facebook:
        return f"best[height<={hauteur}]/best" if hauteur else "best"
    if hauteur:
        return (
            f"bestvideo[height<={hauteur}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={hauteur}]+bestaudio/best[height<={hauteur}]/best"
        )
    return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"


def commande_telechargement(
    binaire: tuple[str, ...],
    url: str,
    dossier: str,
    options: Options,
    *,
    liste: bool,
    facebook: bool,
    max_duree: int,
    max_taille: str,
    max_elements: int,
    fichier_chemins: str,
) -> list[str]:
    gabarit = "%(playlist_index)03d - %(title).120B.%(ext)s" if liste else "%(title).150B.%(ext)s"
    cmd = [
        *binaire,
        "--newline",
        "--no-color",
        "--no-warnings",
        "--progress-template", GABARIT_PROGRESSION,
        "--progress-template", GABARIT_CONVERSION,
        "--print-to-file", "after_move:filepath", fichier_chemins,
        "--paths", dossier,
        "-o", gabarit,
        "--max-filesize", max_taille,
        # Ni direct ni média trop long : vérifié avant tout téléchargement.
        "--match-filter", f"!is_live & duration <=? {max_duree}",
        "--socket-timeout", "30",
        "--retries", "3",
        "--no-mtime",
    ]
    if liste:
        cmd += ["--yes-playlist", "--playlist-end", str(max_elements), "--ignore-errors"]
        if options.elements:
            choisis = sorted({i for i in options.elements if 1 <= i <= max_elements})
            cmd += ["--playlist-items", ",".join(str(i) for i in choisis)]
    else:
        cmd += ["--no-playlist"]

    if options.format in VIDEO:
        cmd += ["-f", selecteur_video(options.qualite, facebook), "--merge-output-format", options.format]
        if options.sous_titres:
            cmd += ["--write-subs", "--sub-langs", ",".join(options.sous_titres), "--embed-subs"]
        cmd += ["--embed-metadata"]
    else:
        cmd += ["-f", "bestaudio/best", "-x", "--audio-format", options.format]
        if options.format not in ("wav", "flac"):
            cmd += ["--audio-quality", QUALITES_AUDIO.get(options.qualite, "3")]
        cmd += ["--embed-metadata"]
        # Pochette intégrée : seuls ces conteneurs la portent correctement.
        if options.pochette and options.format in ("mp3", "m4a", "flac", "opus"):
            cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]

    if not liste and (options.debut is not None or options.fin is not None):
        debut = _horodatage(options.debut or 0)
        fin = _horodatage(options.fin) if options.fin is not None else "inf"
        cmd += ["--download-sections", f"*{debut}-{fin}", "--force-keyframes-at-cuts"]

    return cmd + ["--", url]


def commande_infos(binaire: tuple[str, ...], url: str, *, liste: bool, max_elements: int) -> list[str]:
    cmd = [*binaire, "-J", "--no-warnings", "--no-color", "--socket-timeout", "20"]
    if liste:
        cmd += ["--flat-playlist", "--yes-playlist", "--playlist-end", str(max_elements)]
    else:
        cmd += ["--no-playlist"]
    return cmd + ["--", url]


# --- Progression -------------------------------------------------------------------


@dataclass
class Avancement:
    etape: str = "telechargement"  # telechargement | conversion
    octets: int = 0
    total: int | None = None
    vitesse: float | None = None
    restant: float | None = None
    element: int | None = None
    convertisseur: str | None = None

    @property
    def pourcentage(self) -> float | None:
        if self.total:
            return min(100.0, 100.0 * self.octets / self.total)
        return None


def _nombre(valeur: str) -> float | None:
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


def lire_ligne(ligne: str) -> Avancement | None:
    """Interprète une ligne de progression ; None si ce n'en est pas une."""
    if MARQUE not in ligne:
        return None
    morceaux = ligne.split(MARQUE, 1)[1].strip().split("|")
    if morceaux[0] == "dl" and len(morceaux) >= 7:
        octets = _nombre(morceaux[1]) or 0
        total = _nombre(morceaux[2]) or _nombre(morceaux[3])
        element = _nombre(morceaux[6])
        return Avancement(
            etape="telechargement",
            octets=int(octets),
            total=int(total) if total else None,
            vitesse=_nombre(morceaux[4]),
            restant=_nombre(morceaux[5]),
            element=int(element) if element else None,
        )
    if morceaux[0] == "pp" and len(morceaux) >= 2:
        element = _nombre(morceaux[2]) if len(morceaux) > 2 else None
        return Avancement(etape="conversion", convertisseur=morceaux[1], element=int(element) if element else None)
    return None


# --- Infos ---------------------------------------------------------------------


def hauteurs_disponibles(formats: list[dict]) -> list[int]:
    hauteurs = {int(f["height"]) for f in formats or [] if f.get("height") and f.get("vcodec") not in (None, "none")}
    return sorted(hauteurs)


def resume_infos(data: dict, *, liste: bool) -> dict:
    """Ce que l'interface affiche d'un média ou d'une liste."""
    if liste or data.get("_type") == "playlist":
        entrees = [
            {
                "index": i + 1,
                "titre": e.get("title") or f"Élément {i + 1}",
                "duree": e.get("duration"),
                "auteur": e.get("uploader") or e.get("channel"),
            }
            for i, e in enumerate(data.get("entries") or [])
            if e
        ]
        return {
            "type": "liste",
            "titre": data.get("title") or "Playlist",
            "auteur": data.get("uploader") or data.get("channel") or "",
            "miniature": (data.get("thumbnails") or [{}])[-1].get("url") if data.get("thumbnails") else None,
            "entrees": entrees,
            "nombre": data.get("playlist_count") or len(entrees),
        }
    sous_titres = sorted(set((data.get("subtitles") or {}).keys()))
    return {
        "type": "media",
        "titre": data.get("title") or "",
        "auteur": data.get("uploader") or data.get("channel") or "",
        "miniature": data.get("thumbnail"),
        "duree": data.get("duration"),
        "direct": bool(data.get("is_live")),
        "hauteurs": hauteurs_disponibles(data.get("formats") or []),
        "sous_titres": sous_titres[:40],
    }


def nettoyer_nom(nom: str) -> str:
    """Nom de fichier proposé au navigateur : sans caractères de contrôle ni chemin."""
    nom = re.sub(r"[\x00-\x1f\\/]", "", nom).strip()
    return nom[:200] or "media"
