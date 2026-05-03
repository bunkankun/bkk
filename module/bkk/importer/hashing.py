"""Hash helpers used across the importer.

Per bunkankun.md §Hash and integrity model:
- Text field hash: SHA-256 over the UTF-8 bytes of the post-canonicalization
  text stream (NOT via JCS).
- Juan / manifest / reference-asset hash: SHA-256 over the JCS canonical
  JSON serialization of the corresponding data tree.

The manifest hash is self-referential: the manifest contains its own
``hash`` field. We follow the standard pattern: serialize with the field
zeroed, hash, then patch the result back in.
"""

from __future__ import annotations

import copy
import hashlib

from . import jcs

ZERO_HASH = "sha256:" + "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + sha256_hex(text.encode("utf-8"))


def sha256_jcs(obj) -> str:
    return "sha256:" + sha256_hex(jcs.canonicalize(obj))


def manifest_hash(manifest: dict) -> str:
    """Compute the self-referential manifest hash.

    Caller passes the manifest dict with whatever value is currently in
    ``hash``; this function does not mutate it. The returned string is the
    hash to write into the ``hash`` field.
    """
    m = copy.deepcopy(manifest)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)
