"""RFC 8785 JSON Canonicalization Scheme (JCS).

Subset implementation: integer/finite-float numbers, strings, booleans, null,
arrays, and dicts with string keys. Sufficient for our YAML-equivalent data
trees (manifests, juan files, ann files).
"""

from __future__ import annotations

import math
import re


def canonicalize(obj) -> bytes:
    """Return RFC 8785 canonical JSON encoding as UTF-8 bytes."""
    return _emit(obj).encode("utf-8")


def _emit(obj) -> str:
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int) and not isinstance(obj, bool):
        return str(obj)
    if isinstance(obj, float):
        return _emit_number(obj)
    if isinstance(obj, str):
        return _emit_string(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_emit(x) for x in obj) + "]"
    if isinstance(obj, dict):
        # RFC 8785 §3.2.3 — sort by UTF-16 code-unit values of keys.
        items = sorted(obj.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(_emit_string(k) + ":" + _emit(v) for k, v in items) + "}"
    raise TypeError(f"cannot canonicalize {type(obj).__name__}")


def _utf16_key(s: str) -> tuple[int, ...]:
    return tuple(s.encode("utf-16-be"))


def _emit_number(n: float) -> str:
    if math.isnan(n) or math.isinf(n):
        raise ValueError("JCS forbids non-finite numbers")
    if n == 0:
        return "0"
    # RFC 8785 references ECMAScript Number.prototype.toString (ES6 §7.1.12.1).
    # Python's repr gives the shortest round-trip representation; reshape it
    # into the ES6 forms.
    if n == int(n) and abs(n) < 1e21:
        return str(int(n))
    s = repr(n)
    # Normalize exponent form: 1e+21 -> 1e+21, 1e-07 -> 1e-7 (no leading zeros)
    m = re.match(r"^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$", s)
    if m:
        sign, ip, fp, exp = m.groups()
        if exp is not None:
            e = int(exp)
            mantissa = ip + (fp or "")
            mantissa = mantissa.lstrip("0") or "0"
            if "." not in mantissa and len(mantissa) > 1:
                mantissa = mantissa[0] + "." + mantissa[1:]
            sign_e = "+" if e >= 0 else "-"
            return f"{sign}{mantissa}e{sign_e}{abs(e)}"
    return s


_ESCAPE_MAP = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _emit_string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        esc = _ESCAPE_MAP.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)
