"""Passphrase encryption shared with the dashboard (WebCrypto compatible).

PBKDF2-SHA256 derives an AES-256-GCM key; the envelope is plain JSON so the
browser can decrypt it with ``crypto.subtle`` and nothing else.
"""

import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 250_000


def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(data: bytes, passphrase: str) -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(passphrase, salt, ITERATIONS)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return json.dumps({"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)})


def decrypt(envelope: str, passphrase: str) -> bytes:
    env = json.loads(envelope)
    b = lambda k: base64.b64decode(env[k])
    return AESGCM(_key(passphrase, b("salt"), int(env["iter"]))).decrypt(b("iv"), b("ct"), None)


def encrypt_json(obj, passphrase: str) -> str:
    return encrypt(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), passphrase)


def decrypt_json(envelope: str, passphrase: str):
    return json.loads(decrypt(envelope, passphrase))
