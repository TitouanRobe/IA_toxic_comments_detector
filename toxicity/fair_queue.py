"""File d'attente des inférences, bornée et équitable entre IP (un pod).

Pourquoi : avec une simple file FIFO bornée, une IP qui spamme remplit la file et ce sont les
NOUVEAUX arrivants (souvent des clients légitimes) qui reçoivent le 503. Ici, quand la file est
pleine, on retire une requête de l'IP qui occupe le plus de places, pas celle qui arrive.

Règles :
- au plus `concurrency` inférences en cours, au plus `max_waiting` requêtes en attente ;
- file pleine : si l'IP qui a le plus de requêtes en attente en a au moins 2 de plus que l'IP du
  nouvel arrivant, sa requête la plus récente est éjectée (503) et le nouvel arrivant prend la
  place ; sinon le nouvel arrivant est refusé (503). Le seuil de 2 évite d'échanger deux IP à
  égalité sans rien gagner ;
- l'attente est bornée par `timeout` (filet de sécurité si une inférence est anormalement lente).

Tout tourne dans la boucle asyncio (une seule à la fois) : pas de verrou nécessaire.
"""

import asyncio
from collections import Counter, deque
from dataclasses import dataclass


@dataclass(eq=False)
class _Waiter:
    key: str
    future: asyncio.Future  # résultat : None (créneau obtenu) ou "evicted"


class FairInferenceQueue:
    def __init__(self, concurrency: int, max_waiting: int) -> None:
        self._free = concurrency
        self._max_waiting = max_waiting
        self._waiters: deque[_Waiter] = deque()
        self._per_key: Counter[str] = Counter()  # requêtes en attente par IP

    async def acquire(self, key: str, timeout: float) -> str | None:
        """Attend un créneau d'inférence. Renvoie None si le créneau est obtenu (appeler release()
        ensuite), sinon la raison du refus : "full", "evicted" ou "timeout"."""
        if self._free > 0 and not self._waiters:
            self._free -= 1
            return None
        if len(self._waiters) >= self._max_waiting and not self._evict_for(key):
            return "full"

        waiter = _Waiter(key, asyncio.get_running_loop().create_future())
        self._waiters.append(waiter)
        self._per_key[key] += 1
        try:
            # asyncio.wait n'annule pas la future à l'expiration : on garde la main sur le nettoyage.
            done, _ = await asyncio.wait({waiter.future}, timeout=timeout)
        except asyncio.CancelledError:
            # Client déconnecté pendant l'attente. Si le créneau lui avait déjà été donné, le rendre.
            if waiter.future.done() and waiter.future.result() is None:
                self.release()
            else:
                self._remove(waiter)
            raise
        if not done:
            self._remove(waiter)
            return "timeout"
        return waiter.future.result()

    def release(self) -> None:
        """Libère un créneau : le donne directement au premier en attente, sinon le rend libre."""
        while self._waiters:
            waiter = self._waiters.popleft()
            self._decrement(waiter.key)
            if not waiter.future.done():
                waiter.future.set_result(None)
                return
        self._free += 1

    def _evict_for(self, key: str) -> bool:
        """File pleine : éjecte la requête la plus récente de l'IP la plus présente, si c'est plus
        équitable que de refuser le nouvel arrivant. True si une place a été libérée."""
        victim, count = self._per_key.most_common(1)[0]
        if count < self._per_key[key] + 2:
            return False
        # La plus récente de cette IP : c'est elle qui a le moins attendu.
        waiter = next(w for w in reversed(self._waiters) if w.key == victim)
        self._remove(waiter)
        waiter.future.set_result("evicted")
        return True

    def _remove(self, waiter: _Waiter) -> None:
        if waiter in self._waiters:
            self._waiters.remove(waiter)  # au plus max_waiting éléments : coût négligeable
            self._decrement(waiter.key)

    def _decrement(self, key: str) -> None:
        self._per_key[key] -= 1
        if self._per_key[key] <= 0:
            del self._per_key[key]
