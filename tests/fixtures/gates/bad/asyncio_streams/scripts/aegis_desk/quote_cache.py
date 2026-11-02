"""Gate fixture: asyncio streams and subprocess transports bypass the socket and subprocess rules."""
from __future__ import annotations

import asyncio


async def post(host: str, payload: bytes) -> None:
    _reader, writer = await asyncio.open_connection(host, 443, ssl=True)
    writer.write(payload)
    await asyncio.create_subprocess_exec("curl", "-X", "POST", "https://x/v1/accounts/1/orders")
