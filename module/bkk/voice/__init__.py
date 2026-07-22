"""Voice marker derivation and application.

Public surface:

- :func:`bkk.voice.derive.derive_voice_markers` — turns source punctuation
  pairs such as ``(``…``)`` and ``▲``…``)`` in a bucket's marker list into
  ``voice`` range markers (note / emphasis). TLS inline note bracket
  markers are included by default and can be excluded by option. Used for
  KRP-style sources that fence commentary inline (e.g. KR3a0001).
- :func:`bkk.voice.derive_indent.derive_voice_markers_from_indent` — turns
  ``line-break``/``indent`` markers into ``voice`` range markers
  (root / commentary / head / attribution) by indent depth. Used for
  sources that distinguish layers by layout indentation (e.g. KR5c0095).
- ``bkk voice`` CLI — applies the requested derivation
  (``--source parens|indent|all``) across an already-imported bundle and
  rewrites each juan with refreshed hashes.

The derive functions are importer-agnostic so a future KRP-importer pass
can emit voice markers in the same form without duplicating logic.
"""
