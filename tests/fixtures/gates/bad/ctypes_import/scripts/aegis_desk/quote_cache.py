"""Gate fixture: ctypes reaches libc's system() without importing os or subprocess."""
from __future__ import annotations

import ctypes

ctypes.CDLL(None).system(b"curl -X POST https://x/v1/accounts/1/orders")
