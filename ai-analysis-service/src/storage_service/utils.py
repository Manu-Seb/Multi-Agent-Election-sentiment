from __future__ import annotations

import base64
import json
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any

from .metrics import STORAGE_RETRIES


TRANSIENT_EXCEPTIONS = (ConnectionError, TimeoutError)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def parse_iso_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_window(window: str) -> timedelta:
    if window.endswith("h"):
        return timedelta(hours=int(window[:-1]))
    if window.endswith("m"):
        return timedelta(minutes=int(window[:-1]))
    if window.endswith("d"):
        return timedelta(days=int(window[:-1]))
    raise ValueError("window must end with one of: h, m, d")


def retry(operation, *, attempts: int = 4, base_delay: float = 0.2):
    last_error = None
    for idx in range(attempts):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if idx == attempts - 1:
                break
            STORAGE_RETRIES.inc()
            time.sleep(base_delay * (2 ** idx))
    raise last_error


def compress_json_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    zipped = zlib.compress(raw, level=9)
    encoded = base64.b64encode(zipped).decode("ascii")
    return {"encoding": "zlib+base64", "payload": encoded}


def decompress_json_payload(payload: dict[str, str]) -> dict[str, Any]:
    if payload.get("encoding") != "zlib+base64":
        return payload
    zipped = base64.b64decode(payload["payload"].encode("ascii"))
    raw = zlib.decompress(zipped)
    return json.loads(raw.decode("utf-8"))
