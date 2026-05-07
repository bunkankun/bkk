"""Bundle-repair maintenance commands.

Currently provides ``rebuild_manifests`` for the rare TLS case where a
single canonical text is split across multiple TEI XML files (xml:id
ending in [a-z]). Each sub-file's importer run shares one canonical
``text_id`` and writes its juan files into the same bundle directory,
but the importer overwrites the manifest on every run — so after a bulk
import the manifest only lists the last sub-file's juans. The rebuild
scans the juan YAMLs on disk and reconstructs both the master and the
per-edition manifests.
"""
