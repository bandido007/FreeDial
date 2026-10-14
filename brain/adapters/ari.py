"""ARI, Asterisk's REST interface, over aiohttp.

M0 holds just enough to prove the connection. The event WebSocket and the call
operations arrive with the brain in M2 (``lab/brain.py`` is the prototype).
"""

from typing import Any

import aiohttp


class AriError(Exception):
    """ARI answered with an error status."""


class Ari:
    def __init__(self, *, url: str, user: str, password: str, timeout: float = 5.0):
        self.url = url
        self._headers = {"Authorization": aiohttp.encode_basic_auth(user, password)}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "Ari":
        self._session = aiohttp.ClientSession(headers=self._headers, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._session:
            await self._session.close()

    async def request(self, method: str, path: str, *, missing_ok: bool = False, **params: str) -> Any:
        if self._session is None:
            raise RuntimeError("use Ari as an async context manager")
        async with self._session.request(method, self.url + path, params=params) as response:
            if response.status == 404 and missing_ok:
                return None
            if response.status >= 300:
                raise AriError(f"{method} {path} -> {response.status} {await response.text()}")
            return None if response.status == 204 else await response.json()

    async def asterisk_info(self) -> dict[str, Any]:
        return await self.request("GET", "/asterisk/info")
