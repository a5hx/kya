"""Small crypto toolkit: canonical JSON, Ed25519 signing, hashing.

Keys and signatures are strings of the form ``ed25519:<base64url>`` so they
can live inside JSON documents without extra wrapping.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

PREFIX = "ed25519:"


def canonical(obj: Any) -> bytes:
    """Deterministic JSON bytes (sorted keys, no whitespace). Signatures are over these."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(obj: Any) -> str:
    return "sha256:" + sha256_hex(canonical(obj))


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_id(prefix: str, nbytes: int = 9) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


def generate_key() -> SigningKey:
    return SigningKey.generate()


def key_to_str(sk: SigningKey) -> str:
    return b64e(bytes(sk))


def key_from_str(text: str) -> SigningKey:
    return SigningKey(b64d(text))


def pubkey(sk: SigningKey) -> str:
    return PREFIX + b64e(bytes(sk.verify_key))


def sign(sk: SigningKey, obj: Any) -> str:
    return PREFIX + b64e(sk.sign(canonical(obj)).signature)


def verify(pub: str, obj: Any, sig: str) -> bool:
    """True iff ``sig`` is a valid signature by ``pub`` over canonical(obj). Never raises."""
    try:
        if not (pub.startswith(PREFIX) and sig.startswith(PREFIX)):
            return False
        VerifyKey(b64d(pub[len(PREFIX):])).verify(canonical(obj), b64d(sig[len(PREFIX):]))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False
