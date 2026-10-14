"""The bus: Redis Streams between web, brain and writer (redis-py, asyncio).

M0 connects and pings. Publishing and consumer-group reading arrive in M2.
"""

import redis.asyncio as redis


class Bus:
    def __init__(self, *, url: str, timeout: float = 2.0):
        self._redis = redis.Redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)

    async def __aenter__(self) -> "Bus":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._redis.aclose()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())
