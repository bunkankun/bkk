"""Tests for the vendored JCS (RFC 8785) implementation."""

from bkk.importer.jcs import canonicalize


def c(obj) -> str:
    return canonicalize(obj).decode("utf-8")


def test_primitives():
    assert c(None) == "null"
    assert c(True) == "true"
    assert c(False) == "false"
    assert c(0) == "0"
    assert c(-1) == "-1"
    assert c(42) == "42"


def test_strings():
    assert c("") == '""'
    assert c("hello") == '"hello"'
    assert c('a"b') == '"a\\"b"'
    assert c("a\\b") == '"a\\\\b"'
    assert c("\n") == '"\\n"'
    assert c("\t") == '"\\t"'
    # Non-ASCII passes through unescaped.
    assert c("臨濟") == '"臨濟"'
    # Control char that has no shorthand escape.
    assert c("\x01") == '"\\u0001"'


def test_arrays():
    assert c([]) == "[]"
    assert c([1, 2, 3]) == "[1,2,3]"
    assert c(["a", "b"]) == '["a","b"]'


def test_objects_sort_keys_by_utf16():
    # RFC 8785 §3.2.3 — keys sorted by UTF-16 code units.
    assert c({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    # ASCII case difference.
    assert c({"B": 1, "a": 2}) == '{"B":1,"a":2}'
    # Code-point order on BMP characters reflects UTF-16 order.
    assert c({"序": 1, "本": 2}) == '{"序":1,"本":2}'


def test_nesting():
    obj = {"outer": {"b": [1, {"y": True, "x": False}], "a": None}}
    expected = '{"outer":{"a":null,"b":[1,{"x":false,"y":true}]}}'
    assert c(obj) == expected


def test_numbers_finite_floats():
    # Integers fold to integer form when value is integral and bounded.
    assert c(1.0) == "1"
    # Fraction.
    assert c(0.5) == "0.5"
    # Negative.
    assert c(-1.5) == "-1.5"


def test_no_whitespace():
    s = c({"a": [1, 2], "b": "c"})
    assert " " not in s
    assert "\n" not in s


def test_deterministic_repeated_calls():
    obj = {"b": 1, "a": 2, "c": [3, 2, 1]}
    assert c(obj) == c(obj)
