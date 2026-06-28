"""Destructive bundle mutations driven by admin decisions.

So far the only entry points are in :mod:`bkk.edit.sections`, which the
duplications editor calls to excise a juan bucket or a list of spans
within a bucket. Each operation rewrites the affected juan YAML, updates
the manifest's part / marker / TOC entries, and recomputes hashes.
"""
