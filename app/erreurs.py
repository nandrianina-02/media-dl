"""Traduction des erreurs de yt-dlp en messages lisibles.

Avant, le visiteur voyait les 400 derniers caractères du journal de yt-dlp,
en anglais, trois secondes durant. Ici, chaque cas courant reçoit une phrase
qui dit quoi faire ; le texte brut ne part que dans les journaux du serveur.
"""

from __future__ import annotations

import re

# (motif, message) : le premier motif trouvé l'emporte.
CAS: list[tuple[str, str]] = [
    # En tête : le message du filtre cite « is_live » et « duration », qui
    # déclencheraient sinon les cas suivants.
    (r"does not pass filter|exceeds max.?duration", "Ce média est un direct, ou il dépasse la durée maximale acceptée."),
    (r"private video|this video is private|is private", "Cette vidéo est privée : elle ne peut pas être téléchargée."),
    (r"sign in to confirm your age|age-restricted|inappropriate for some users", "Contenu soumis à une limite d'âge : il demande une connexion au site, ce que ce service ne fait pas."),
    (r"sign in to confirm you.?re not a bot|confirm you are not a bot", "Le site a temporairement bloqué le serveur. Réessayez dans quelques minutes."),
    (r"login required|requires authentication|log in|logged-in|cookies", "Ce contenu demande une connexion au site : il ne peut pas être téléchargé ici."),
    (r"not available in your country|geo.?restrict|blocked it in your country", "Ce contenu n'est pas disponible depuis le pays du serveur."),
    (r"video unavailable|has been removed|no longer available|does not exist|404", "Contenu introuvable : il a peut-être été supprimé."),
    (r"is a live|live event|premieres in|is_live", "Les directs et les premières à venir ne sont pas pris en charge."),
    (r"larger than max-filesize|file is larger", "Le fichier dépasse la taille maximale acceptée."),
    (r"requested format is not available|no video formats", "Ce format n'est pas disponible pour ce contenu. Essayez une autre qualité."),
    (r"unsupported url", "Ce lien n'est pas reconnu par le service."),
    (r"http error 429|too many requests", "Le site limite les demandes du serveur. Réessayez dans quelques minutes."),
    (r"unable to download|connection|timed out|network", "Le site n'a pas répondu. Réessayez dans un instant."),
    (r"ffmpeg|postprocessing|conversion", "La conversion du fichier a échoué. Essayez un autre format."),
]

DEFAUT = "Le téléchargement a échoué. Vérifiez le lien, puis réessayez."


def message_clair(journal: str) -> str:
    texte = (journal or "").lower()
    for motif, message in CAS:
        if re.search(motif, texte):
            return message
    return DEFAUT
