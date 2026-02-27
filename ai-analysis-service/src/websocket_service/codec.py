from __future__ import annotations

import gzip
import json
from typing import Any


def encode_payload(payload: dict[str, Any], mode: str) -> tuple[str, bytes | str]:
    raw_json = json.dumps(payload, separators=(",", ":"))
    if mode == "gzip":
        return "gzip+json", gzip.compress(raw_json.encode("utf-8"))

    if mode == "msgpack":
        try:
            import msgpack  # type: ignore

            return "msgpack", msgpack.packb(payload, use_bin_type=True)
        except ModuleNotFoundError:
            pass

    return "json", raw_json
