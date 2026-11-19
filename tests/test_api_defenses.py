"""Tests des défenses de l'API (TP2, étape 3) : chaque test correspond à un constat d'attaque.

Lancement : uv run pytest -v
"""

import asyncio

import httpx
import pytest

SECURITY_HEADERS = ["strict-transport-security", "content-security-policy", "x-frame-options",
                    "x-content-type-options", "referrer-policy"]


# --- Constat vague 6 : en-têtes de sécurité manquants -----------------------------------------
def test_security_headers_on_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    for header in SECURITY_HEADERS:
        assert header in r.headers, header
    assert r.headers["x-frame-options"] == "DENY"


# --- Constat vague 6 : /model découvert via /openapi.json ------------------------------------
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_closed_by_default(client, path):
    assert client.get(path).status_code == 404


# --- Authentification ------------------------------------------------------------------------
def test_model_requires_api_key(client):
    assert client.post("/model", json={"text": "hello"}).status_code == 401
    assert client.post("/model", json={"text": "hello"}, headers={"X-API-Key": "mauvaise"}).status_code == 401


# --- Constat vague 6 : modèle extractible via les scores -------------------------------------
def test_response_contains_only_the_decision(client, auth):
    toxic = client.post("/model", json={"text": "You are an idiot"}, headers=auth)
    clean = client.post("/model", json={"text": "Have a nice day"}, headers=auth)
    assert toxic.status_code == clean.status_code == 200
    assert toxic.json() == {"is_toxic": True}
    assert clean.json() == {"is_toxic": False}


# --- Constat étape 2 : entrées surdimensionnées (« sponge input ») ---------------------------
def test_text_longer_than_1500_chars_rejected(client, auth):
    assert client.post("/model", json={"text": "a" * 1501}, headers=auth).status_code == 422


def test_large_body_rejected_before_auth(client):
    # Pas de clé : le refus 413 arrive AVANT le contrôle de clé, le corps n'est jamais lu.
    r = client.post("/model", content=b'{"text": "' + b"a" * 6000 + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert "content-security-policy" in r.headers


def test_large_chunked_body_rejected(client, auth):
    def chunks():  # corps sans Content-Length (Transfer-Encoding: chunked)
        yield b'{"text": "'
        for _ in range(10):
            yield b"a" * 1000
        yield b'"}'

    r = client.post("/model", content=chunks(), headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 413


# --- Constat vague 6 : une IP qui spamme --------------------------------------------------------
def test_per_ip_in_flight_limit():
    """5 requêtes simultanées de la même IP : la 5e reçoit 429 tout de suite ; une autre IP passe."""
    from toxicity.ip_limit import PerIPInFlightLimit

    release = asyncio.Event()

    async def slow_app(scope, receive, send):
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = PerIPInFlightLimit(slow_app, max_in_flight=4, paths={"/model"})

    def xff(ip):
        return {"X-Forwarded-For": f"{ip}, 130.211.0.1"}

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            held = [asyncio.create_task(c.post("/model", headers=xff("203.0.113.9"))) for _ in range(4)]
            await asyncio.sleep(0.05)
            fifth = await c.post("/model", headers=xff("203.0.113.9"))
            other = asyncio.create_task(c.post("/model", headers=xff("10.1.0.1")))
            await asyncio.sleep(0.05)
            release.set()
            done = await asyncio.gather(*held, other)
            return fifth, done

    fifth, done = asyncio.run(scenario())
    assert fifth.status_code == 429
    assert all(r.status_code == 200 for r in done)
