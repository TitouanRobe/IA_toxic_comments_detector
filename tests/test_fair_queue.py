"""Tests de la file d'inférence équitable (toxicity/fair_queue.py)."""

import asyncio

from toxicity.fair_queue import FairInferenceQueue


def run(coro):
    return asyncio.run(coro)


def test_spammer_is_evicted_not_newcomer():
    """Une IP occupe le créneau et toute la file : les nouvelles IP prennent la place de ses requêtes."""

    async def scenario():
        q = FairInferenceQueue(concurrency=1, max_waiting=8)
        assert await q.acquire("spam", timeout=5) is None  # créneau occupé par le spammeur
        spam = [asyncio.create_task(q.acquire("spam", timeout=5)) for _ in range(8)]
        await asyncio.sleep(0)
        # File pleine : avec une FIFO simple, ces 3 nouvelles IP seraient refusées (503).
        newcomers = [asyncio.create_task(q.acquire(f"client{i}", timeout=5)) for i in range(3)]
        await asyncio.sleep(0)
        for _ in range(8):  # chaque fin d'inférence passe le créneau au suivant dans la file
            q.release()
            await asyncio.sleep(0)
        return await asyncio.gather(*spam), await asyncio.gather(*newcomers)

    spam, newcomers = run(scenario())
    assert spam.count("evicted") == 3  # 3 requêtes du spammeur éjectées...
    assert spam.count(None) == 5
    assert newcomers == [None, None, None]  # ...et les 3 nouvelles IP servies


def test_tie_is_refused_not_swapped():
    async def scenario():
        q = FairInferenceQueue(concurrency=1, max_waiting=8)
        await q.acquire("a", timeout=5)
        waiting = [asyncio.create_task(q.acquire(ip, timeout=5)) for ip in ["a", "b"] * 4]
        await asyncio.sleep(0)
        refused = await q.acquire("b", timeout=5)  # 4 contre 4 : pas d'échange
        for t in waiting:
            t.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        return refused

    assert run(scenario()) == "full"


def test_timeout_and_cancellation_leave_no_leak():
    async def scenario():
        q = FairInferenceQueue(concurrency=1, max_waiting=8)
        await q.acquire("a", timeout=5)
        assert await q.acquire("b", timeout=0.05) == "timeout"
        cancelled = asyncio.create_task(q.acquire("c", timeout=5))
        await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        q.release()
        # File vide, créneau rendu : une nouvelle requête passe immédiatement.
        assert not q._waiters and not q._per_key
        return await q.acquire("d", timeout=0.01)

    assert run(scenario()) is None
