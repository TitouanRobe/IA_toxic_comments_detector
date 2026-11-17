"""Limite de taille des corps de requête (middleware ASGI), avant toute lecture par FastAPI.

Sans elle, FastAPI lit et parse le corps JSON EN ENTIER avant de le valider (max_length du
texte) et avant même de vérifier la clé d'API : uvicorn n'impose aucune taille maximale, donc
un corps de plusieurs Mo est reçu et décodé par le pod, clé valide ou non (« sponge input »).

- Avec Content-Length : refus immédiat (413) si la taille annoncée dépasse la limite, sans rien
  lire du corps.
- Sans Content-Length (Transfer-Encoding: chunked, que la règle Cloud Armor sur l'en-tête
  content-length ne voit pas) : lecture comptée, refus dès que la limite est franchie ; sinon le
  corps (petit, puisque sous la limite) est transmis tel quel à l'application.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_METHODS_WITHOUT_BODY = {"GET", "HEAD", "OPTIONS", "DELETE"}


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _METHODS_WITHOUT_BODY:
            await self.app(scope, receive, send)
            return

        length = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if length is not None:
            if not length.isdigit() or int(length) > self.max_bytes:
                await self._reject(scope, receive, send)
            else:
                # Le serveur HTTP (h11/httptools) garantit qu'un client ne peut pas envoyer plus
                # d'octets qu'annoncé : pas besoin de recompter.
                await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()  # ensuite : attente de la déconnexion, comme d'habitude

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": f"Corps de requête trop volumineux (maximum {self.max_bytes} octets)"},
            status_code=413,
        )
        await response(scope, receive, send)
