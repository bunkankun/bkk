"""YAML emission for BKK files.

Uses PyYAML's ``SafeDumper`` with custom representers so that:
- mapping order follows insertion order (we build dicts in the desired order),
- marker dicts and other "leaf" mappings render in flow style on one line,
- strings always quote-disambiguate values like ``y``, ``n`` that would
  otherwise be parsed as booleans on round-trip.

The sample uses a few distinctive style choices we mirror:
- ``front:`` header keeps a trailing space (cosmetic — we accept divergence),
- markers are emitted as inline flow-style mappings,
- the ``hash:`` field appears last in manifests,
- string scalars use plain style when safe; quotes are added where needed.
"""

from __future__ import annotations

from typing import Any

import yaml


class _FlowDict(dict):
    """Marker subclass: mappings of this type render in flow style."""


def _represent_str(dumper, data):
    # Force quoting only for ambiguous values (booleans/null look-alikes,
    # the empty string, anything containing leading/trailing whitespace).
    style = None
    ambiguous = {
        "", "y", "Y", "n", "N",
        "yes", "Yes", "YES", "no", "No", "NO",
        "true", "True", "TRUE", "false", "False", "FALSE",
        "on", "On", "ON", "off", "Off", "OFF",
        "null", "Null", "NULL", "~",
    }
    if data in ambiguous:
        style = "'"
    elif data and (data[0] in "*&!|>%@`" or ":" in data and data.startswith("tls:")):
        style = "'"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _represent_flow_dict(dumper, data):
    return dumper.represent_mapping(
        "tag:yaml.org,2002:map", data.items(), flow_style=True,
    )


class BkkDumper(yaml.SafeDumper):
    pass


BkkDumper.add_representer(str, _represent_str)
BkkDumper.add_representer(_FlowDict, _represent_flow_dict)


def marker_to_flow(m: dict) -> _FlowDict:
    """Convert a plain dict into a flow-styled marker dict."""
    return _FlowDict(m)


def reflow_manifest(manifest: dict) -> None:
    """Restore the canonical flow style of compact manifest leaf mappings.

    PyYAML does not retain block-vs-flow presentation through ``safe_load``.
    Any code that reads, modifies, and rewrites a manifest must call this
    before :func:`dump` so high-churn lists remain one entry per line.
    """
    assets = manifest.get("assets")
    if isinstance(assets, dict):
        for key in ("parts", "markers"):
            entries = assets.get(key)
            if isinstance(entries, list):
                assets[key] = [
                    marker_to_flow(entry) if isinstance(entry, dict) else entry
                    for entry in entries
                ]
    editions = manifest.get("editions")
    if isinstance(editions, list):
        manifest["editions"] = [
            marker_to_flow(entry) if isinstance(entry, dict) else entry
            for entry in editions
        ]


def dump(obj: Any) -> str:
    """Serialize ``obj`` to a YAML string with stable, BKK-shaped formatting."""
    return yaml.dump(
        obj,
        Dumper=BkkDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10**9,  # never wrap long Chinese-text lines
    )
