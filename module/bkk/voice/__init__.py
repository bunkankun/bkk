"""Voice marker derivation and application.

Public surface:

- :func:`bkk.voice.derive.derive_voice_markers` — pure function that turns the
  ``(``/``)`` punctuation pairs in a bucket's marker list into ``voice``
  range markers (root / commentary) with ``responds-to`` linkage.
- ``bkk voice`` CLI — applies the derivation across an already-imported
  bundle and rewrites each juan with refreshed hashes.

The derive function is importer-agnostic so a future KRP-importer pass can
emit voice markers in the same form without duplicating logic.
"""
