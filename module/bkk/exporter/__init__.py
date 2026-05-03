"""BKK exporter: bundle + sidecar + recipe → TEI/XML.

The inverse of ``bkk.importer``. Reads a BKK bundle directory and its
``<text-id>.source.yaml`` sidecar, then emits TEI/XML files that — when
fed back through the importer — produce the same bundle. Round-trip is the
contract; byte-equality with the original input source is a non-goal.

Driven by a recipe file (see ``recipe.py``). Run as
``python -m bkk.exporter --recipe <path>``.
"""
