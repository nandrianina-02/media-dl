"""Limitation de débit, par adresse IP et par fenêtre glissante.

En mémoire : suffisant pour une instance unique, ce qu'est ce service.
Plusieurs instances demanderaient un compteur partagé (Redis).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class Limiteur:
    def __init__(self, nombre: int, fenetre: float):
        self.nombre = nombre
        self.fenetre = fenetre
        self._appels: dict[str, deque[float]] = defaultdict(deque)

    def autoriser(self, cle: str, maintenant: float | None = None) -> tuple[bool, int]:
        """(autorisé, secondes avant la prochaine place)."""
        maintenant = time.monotonic() if maintenant is None else maintenant
        appels = self._appels[cle]
        while appels and appels[0] <= maintenant - self.fenetre:
            appels.popleft()
        if len(appels) >= self.nombre:
            return False, max(1, int(appels[0] + self.fenetre - maintenant) + 1)
        appels.append(maintenant)
        # Nettoyage occasionnel des clés inactives.
        if len(self._appels) > 10_000:
            for k in [k for k, v in self._appels.items() if not v]:
                del self._appels[k]
        return True, 0
