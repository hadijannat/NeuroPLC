from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_envelope(envelope: dict) -> str:
    return sha256_hex(canonical_json(envelope))


def hash_tool_call(name: str, args: dict, result: Any) -> dict:
    return {
        "name": name,
        "args_hash": sha256_hex(canonical_json(args)),
        "result_hash": sha256_hex(canonical_json(result)),
    }
