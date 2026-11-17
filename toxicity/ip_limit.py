"""Plafond de requêtes simultanées par IP (middleware ASGI), placé DEVANT le délestage.

Une IP qui a déjà `max_in_flight` requêtes en cours sur ce pod (en attente ou en inférence) voit
les suivantes refusées tout de suite en 429, AVANT la lecture du corps, le parsing JSON, le
contrôle de clé et la file d'inférence : le spam d'une IP ne coûte presque rien au pod et ne
prend pas la place des autres clients dans la file.

Complète Cloud Armor (k8s/cloud-armor-setup.sh), qui compte les requêtes PAR MINUTE au load
balancer : ici on compte les requêtes EN COURS, par pod, ce qui coupe une rafale dès la première
seconde, avant que le seuil par minute soit atteint.

L'IP est celle de audit_log.client_ip (avant-dernière entrée de X-Forwarded-For, non falsifiable
derrière le load balancer Google). max_in_flight <= 0 désactive le plafond (tests de charge
depuis une seule machine).
"""

from collections import Counter

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from toxicity import audit_log, metrics_prom


class PerIPInFlightLimit:
    def __init__(self, app: ASGIApp, max_in_flight: int, paths: set[str]) -> None:
        self.app = app
        self.max_in_flight = max_in_flight
        self.paths = paths
        self._in_flight: Counter[str] = Counter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_in_flight <= 0 or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return

        ip = audit_log.client_ip(Request(scope)) or "unknown"
        if self._in_flight[ip] >= self.max_in_flight:
            metrics_prom.SHED.labels("ip_limit").inc()
            response = JSONResponse(
                {"detail": "Trop de requêtes simultanées depuis cette adresse"},
                status_code=429, headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return

        self._in_flight[ip] += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self._in_flight[ip] -= 1
            if self._in_flight[ip] <= 0:
                del self._in_flight[ip]
