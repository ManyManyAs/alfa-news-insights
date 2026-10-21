"""Нагрузочный тест ленты: N запросов в секунду в течение T секунд, отчёт p50/p95/p99 и доля ошибок.

  uv run python scripts/loadtest.py --url http://127.0.0.1:8000 --rps 50 --seconds 30

Логинится демо-пользователем (как браузер), затем равномерно шлёт запросы к ленте, профилю и дайджесту
(смесь как у реального пользователя: лента чаще всего). Тяжёлые LLM-операции не нагружаются — они ограничены
по частоте на пользователя и строятся один раз на событие.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--rps", type=float, default=50)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--password", default="alfa2026")
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=a.url, timeout=30) as c:
        users = (await c.get("/api/demo-users")).json()["users"]
        u = users[0]
        r = await c.post("/api/auth/login", json={"login": u["user_id"], "password": a.password})
        r.raise_for_status()
        cid, role = u["primary_company"], u["role"]
        paths = [(f"/api/feed?company_id={cid}&role={role}", 6), (f"/api/companies/{cid}?role={role}", 2),
                 (f"/api/digest?company_id={cid}&role={role}", 1), ("/api/health", 1)]
        weighted = [p for p, w in paths for _ in range(w)]
        lat: list[float] = []
        errors = 0

        async def one(path: str) -> None:
            nonlocal errors
            t0 = time.perf_counter()
            try:
                resp = await c.get(path)
                if resp.status_code != 200:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            lat.append((time.perf_counter() - t0) * 1000)

        await one(weighted[0])  # прогрев кэша событий
        lat.clear()
        tasks, start, n = [], time.perf_counter(), int(a.rps * a.seconds)
        for i in range(n):
            delay = start + i / a.rps - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(random.choice(weighted))))
        await asyncio.gather(*tasks)
        took = time.perf_counter() - start
    q = statistics.quantiles(lat, n=100)
    print(f"Запросов: {n} за {took:.1f} с (фактически {n / took:.1f} rps), ошибок: {errors} ({errors / n:.1%})")
    print(f"Латентность, мс: p50={q[49]:.0f} p95={q[94]:.0f} p99={q[98]:.0f} max={max(lat):.0f}")


if __name__ == "__main__":
    asyncio.run(main())
