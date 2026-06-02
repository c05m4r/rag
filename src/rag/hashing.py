from __future__ import annotations

import hashlib
import zlib
from typing import Literal

import xxhash

HashAlgorithm = Literal["xxhash", "sha-256", "md5", "crc32"]

_ALGORITHM_ALIASES: dict[str, HashAlgorithm] = {
    "xxhash": "xxhash",
    "xxh64": "xxhash",
    "sha-256": "sha-256",
    "sha256": "sha-256",
    "md5": "md5",
    "crc": "crc32",
    "crc32": "crc32",
}


def normalize_hash_algorithm(value: str) -> HashAlgorithm:
    normalized = value.strip().lower()
    try:
        return _ALGORITHM_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(set(_ALGORITHM_ALIASES.values())))
        raise ValueError(
            f"Unsupported hash algorithm '{value}'. Supported values: {supported}"
        ) from exc


def hash_bytes(payload: bytes, algorithm: str) -> str:
    normalized = normalize_hash_algorithm(algorithm)
    if normalized == "xxhash":
        return xxhash.xxh64_hexdigest(payload)
    if normalized == "sha-256":
        return hashlib.sha256(payload).hexdigest()
    if normalized == "md5":
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()
    return f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"


def hash_text(content: str, algorithm: str) -> str:
    return hash_bytes(content.encode("utf-8"), algorithm)
