"""Parser and lint helpers for bkk-core syntactic-function labels."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unicodedata

import yaml


FULLWIDTH_PUNCTUATION = str.maketrans({
    "＋": "+",
    "－": "-",
    "（": "(",
    "）": ")",
})

CONNECTORS = {".", "+", "-", ":", "=", ">", "|", "/", "&", "!"}
OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSE_TO_OPEN = {v: k for k, v in OPEN_TO_CLOSE.items()}
STRUCTURAL = CONNECTORS | set(OPEN_TO_CLOSE) | set(CLOSE_TO_OPEN) | {"_"}

KNOWN_MORPHEMES = tuple(sorted({
    "CLASSIFIER",
    "CONSTITUENT",
    "COMMENT",
    "ARTICLE",
    "RESULT",
    "QUOTE",
    "TEXT",
    "TIME",
    "SIZE",
    "COMP",
    "PRO",
    "NEG",
    "OBJ",
    "SUBJ",
    "PRED",
    "PASS",
    "PASSIVE",
    "PLACE",
    "PIVOT",
    "Q",
    "TOPIC",
    "TOP",
    "NUM",
    "ACT",
    "COP",
    "PLUR",
    "SUFF",
    "SUF",
    "REFLEX",
    "DISCONT",
    "NOMINALISED",
    "INDIRECT",
    "SUBJECT",
    "OBJECT",
    "BEI4",
    "DENG3",
    "HE2",
    "QIE4",
    "SUO",
    "WEI4",
    "YI",
    "ZHI",
    "ZHO4",
    "YǑU",
    "YOU",
    "ruò",
    "wéi",
    "suo",
    "suǒ",
    "zhě",
    "often",
    "huo",
    "o",
    "postnpro",
    "postadN",
    "postadV",
    "postadS",
    "postN",
    "postS",
    "postVt",
    "postV",
    "post",
    "prep",
    "npropostN",
    "npropost",
    "npro",
    "npost",
    "ncpost",
    "ppostad",
    "ppost",
    "padV",
    "padN",
    "padS",
    "adVtoN",
    "adNab",
    "adNpr",
    "adNP",
    "adVP",
    "adVt",
    "adV",
    "adN",
    "adS",
    "ad",
    "vtonpro",
    "vttonpro",
    "vttoN",
    "vtt",
    "vtoN",
    "vtoS",
    "vtoV",
    "vto",
    "vt0",
    "vt",
    "vi",
    "vadV",
    "vadN",
    "red",
    "pro",
    "VPtt",
    "VPt",
    "VPi",
    "VPad",
    "VPtoN",
    "VPtoS",
    "VPtoV",
    "VPto",
    "VP",
    "Vtt",
    "Vt",
    "VtoN",
    "VtoS",
    "VtoV",
    "NPad",
    "NPpro",
    "NPpost",
    "NPtpost",
    "NP",
    "PPad",
    "PPpost",
    "PP",
    "Nab",
    "Npr",
    "Npl",
    "NadV",
    "NOMINAL",
    "NED",
    "NG",
    "CL",
    "PL",
    "NU",
    "SS",
    "S",
    "N",
    "V",
    "P",
    "R",
    "X",
    "m",
    "c",
    "t",
    "p",
    "n",
    "v",
    "i",
    "ab",
    "pr",
    "nm",
    "nc",
    "nad",
}, key=len, reverse=True))

ROLE_ALIASES = {
    "SUBJECT": "SUBJ",
    "OBJECT": "OBJ",
    "PASSIVE": "PASS",
    "SUF": "SUFF",
    "TOPIC": "TOP",
    "pivot": "PIVOT",
}


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass
class SyntaxNode:
    kind: str
    value: str | None = None
    children: list["SyntaxNode"] = field(default_factory=list)
    annotations: list["SyntaxNode"] = field(default_factory=list)


@dataclass
class ParseResult:
    raw: str
    normalized: str
    tokens: list[Token]
    tree: SyntaxNode
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


@dataclass(frozen=True)
class RecordDiagnostic:
    path: Path
    label: str
    diagnostic: Diagnostic


@dataclass
class SyntacticFunctionLintReport:
    diagnostics: list[RecordDiagnostic] = field(default_factory=list)
    record_count: int = 0
    distinct_label_count: int = 0

    @property
    def errors(self) -> list[RecordDiagnostic]:
        return [d for d in self.diagnostics if d.diagnostic.severity == "error"]

    @property
    def warnings(self) -> list[RecordDiagnostic]:
        return [d for d in self.diagnostics if d.diagnostic.severity != "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize_label(label: str) -> tuple[str, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    normalized = unicodedata.normalize("NFC", label).translate(FULLWIDTH_PUNCTUATION)
    for pos, ch in enumerate(label):
        if ch in "＋－（）":
            diagnostics.append(Diagnostic(
                "warning",
                "fullwidth-punctuation",
                f"replace fullwidth punctuation {ch!r} with ASCII",
                pos,
                pos + 1,
            ))
        if ch.isspace():
            diagnostics.append(Diagnostic(
                "warning",
                "whitespace",
                "syntactic-function labels should not contain whitespace",
                pos,
                pos + 1,
            ))
        elif ord(ch) > 127:
            name = unicodedata.name(ch, "UNKNOWN")
            code = "unicode-confusable" if "CYRILLIC" in name else "non-ascii"
            diagnostics.append(Diagnostic(
                "warning",
                code,
                f"non-ASCII character {ch!r} ({name}) in label",
                pos,
                pos + 1,
            ))
    return normalized, diagnostics


def lex_label(label: str) -> tuple[list[Token], list[Diagnostic]]:
    tokens: list[Token] = []
    diagnostics: list[Diagnostic] = []
    i = 0
    while i < len(label):
        ch = label[i]
        if ch.isspace():
            i += 1
            continue
        if label.startswith("...", i):
            tokens.append(Token("ellipsis", "...", i, i + 3))
            i += 3
            continue
        if ch in STRUCTURAL:
            kind = "connector"
            if ch in OPEN_TO_CLOSE:
                kind = "open"
            elif ch in CLOSE_TO_OPEN:
                kind = "close"
            elif ch == "_":
                diagnostics.append(Diagnostic(
                    "warning",
                    "underscore",
                    "underscore is unusual; prefer attached numeric indices",
                    i,
                    i + 1,
                ))
            tokens.append(Token(kind, ch, i, i + 1))
            i += 1
            continue
        if ch.isdigit():
            start = i
            while i < len(label) and label[i].isdigit():
                i += 1
            tokens.append(Token("number", label[start:i], start, i))
            continue

        matched = False
        for morpheme in KNOWN_MORPHEMES:
            if label.startswith(morpheme, i):
                end = i + len(morpheme)
                tokens.append(Token("atom", morpheme, i, end))
                i = end
                matched = True
                break
        if matched:
            continue

        start = i
        while i < len(label):
            if label.startswith("...", i) or label[i].isspace() or label[i] in STRUCTURAL:
                break
            if label[i].isdigit():
                break
            if any(label.startswith(morpheme, i) for morpheme in KNOWN_MORPHEMES):
                break
            i += 1
        if i == start:
            i += 1
        value = label[start:i]
        tokens.append(Token("unknown", value, start, i))
        diagnostics.append(Diagnostic(
            "warning",
            "unknown-token",
            f"unknown label component {value!r}",
            start,
            i,
        ))
    return tokens, diagnostics


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0
        self.diagnostics: list[Diagnostic] = []

    def parse(self) -> SyntaxNode:
        tree = self._parse_expr(stop=None)
        while self._peek() is not None:
            token = self._advance()
            if token.kind == "close":
                self.diagnostics.append(Diagnostic(
                    "error",
                    "unexpected-close",
                    f"unexpected closing bracket {token.value!r}",
                    token.start,
                    token.end,
                ))
            else:
                tree.children.append(SyntaxNode(token.kind, token.value))
        return tree

    def _parse_expr(self, stop: str | None) -> SyntaxNode:
        children: list[SyntaxNode] = []
        expect_part = True
        while (token := self._peek()) is not None:
            if stop is not None and token.value == stop:
                self._advance()
                return SyntaxNode("sequence", children=children)
            if token.kind == "close":
                if stop is not None:
                    self._advance()
                    self.diagnostics.append(Diagnostic(
                        "error",
                        "mismatched-bracket",
                        f"expected closing bracket {stop!r}, got {token.value!r}",
                        token.start,
                        token.end,
                    ))
                    return SyntaxNode("sequence", children=children)
                return SyntaxNode("sequence", children=children)
            if token.kind == "connector":
                self._advance()
                children.append(SyntaxNode("connector", token.value))
                expect_part = True
                continue
            children.append(self._parse_part())
            expect_part = False
        if stop is not None:
            self.diagnostics.append(Diagnostic(
                "error",
                "unclosed-bracket",
                f"missing closing bracket {stop!r}",
                None,
                None,
            ))
        elif children and children[-1].kind == "connector":
            last = self.tokens[-1]
            self.diagnostics.append(Diagnostic(
                "error",
                "dangling-connector",
                f"connector {last.value!r} has no following part",
                last.start,
                last.end,
            ))
        return SyntaxNode("sequence", children=children)

    def _parse_part(self) -> SyntaxNode:
        token = self._advance()
        if token.kind == "open":
            close = OPEN_TO_CLOSE[token.value]
            kind = {"(": "optional", "[": "elided", "{": "role"}[token.value]
            node = SyntaxNode(kind, token.value, [self._parse_expr(close)])
            if kind == "role":
                self._validate_role_text(node, token)
            return node
        node = SyntaxNode(token.kind, token.value)
        if token.kind == "atom" and self._peek() is not None and self._peek().kind == "number":
            index = self._advance()
            node.annotations.append(SyntaxNode("index", index.value))
        while self._peek() is not None and self._peek().kind == "open":
            opener = self._advance()
            close = OPEN_TO_CLOSE[opener.value]
            kind = {"(": "optional", "[": "elided", "{": "role"}[opener.value]
            annotation = SyntaxNode(kind, opener.value, [self._parse_expr(close)])
            if kind == "role":
                self._validate_role_text(annotation, opener)
            node.annotations.append(annotation)
        return node

    def _validate_role_text(self, node: SyntaxNode, opener: Token) -> None:
        role_text = _flatten_node_text(node).strip("{}[]()")
        if " " in role_text:
            self.diagnostics.append(Diagnostic(
                "warning",
                "role-whitespace",
                "role annotation contains whitespace; use a compact canonical role",
                opener.start,
                opener.end,
            ))
        if role_text in ROLE_ALIASES:
            self.diagnostics.append(Diagnostic(
                "warning",
                "role-alias",
                f"role {role_text!r} is usually written as {ROLE_ALIASES[role_text]!r}",
                opener.start,
                opener.end,
            ))

    def _peek(self) -> Token | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token


def _flatten_node_text(node: SyntaxNode) -> str:
    parts: list[str] = []
    if node.value:
        parts.append(node.value)
    for child in node.children:
        parts.append(_flatten_node_text(child))
    for annotation in node.annotations:
        parts.append(_flatten_node_text(annotation))
    return "".join(parts)


def parse_syntactic_label(label: str) -> ParseResult:
    normalized, diagnostics = normalize_label(label)
    tokens, lex_diagnostics = lex_label(normalized)
    parser = _Parser(tokens)
    tree = parser.parse()
    return ParseResult(
        raw=label,
        normalized=normalized,
        tokens=tokens,
        tree=tree,
        diagnostics=diagnostics + lex_diagnostics + parser.diagnostics,
    )


def _syntactic_function_root(core_root: Path) -> Path:
    if core_root.name == "syntactic-functions":
        return core_root
    return core_root / "syntactic-functions"


def _record_accept_codes(record: dict, path: Path) -> tuple[frozenset[str], Diagnostic | None]:
    raw = record.get("lint_accept")
    if raw is None:
        return frozenset(), None
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        return frozenset(), Diagnostic(
            "warning",
            "lint-accept-malformed",
            "lint_accept must be a list of diagnostic-code strings; ignoring",
        )
    return frozenset(raw), None


def lint_syntactic_function_records(core_root: Path | str) -> SyntacticFunctionLintReport:
    root = _syntactic_function_root(Path(core_root))
    report = SyntacticFunctionLintReport()
    labels_by_code: dict[str, list[Path]] = {}
    accept_by_path: dict[Path, frozenset[str]] = {}
    for path in sorted(root.rglob("*.yml")):
        record = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        label = str(record.get("code") or record.get("labels", {}).get("display") or "")
        if not label:
            report.diagnostics.append(RecordDiagnostic(
                path,
                label,
                Diagnostic("error", "missing-code", "syntactic-function record has no code"),
            ))
            continue
        report.record_count += 1
        labels_by_code.setdefault(label, []).append(path)
        accepted, accept_problem = _record_accept_codes(record, path)
        accept_by_path[path] = accepted
        if accept_problem is not None:
            report.diagnostics.append(RecordDiagnostic(path, label, accept_problem))
        display = record.get("labels", {}).get("display")
        if display and display != label:
            _append_diag(report, accepted, path, label, Diagnostic(
                "warning", "display-mismatch", "code and labels.display differ",
            ))
        result = parse_syntactic_label(label)
        for diagnostic in result.diagnostics:
            _append_diag(report, accepted, path, label, diagnostic)

    report.distinct_label_count = len(labels_by_code)
    for label, paths in labels_by_code.items():
        if len(paths) < 2:
            continue
        first = paths[0]
        for path in paths[1:]:
            _append_diag(
                report,
                accept_by_path.get(path, frozenset()),
                path,
                label,
                Diagnostic(
                    "warning",
                    "duplicate-code",
                    f"duplicate code also appears at {first}",
                ),
            )
    return report


def _append_diag(
    report: SyntacticFunctionLintReport,
    accepted: frozenset[str],
    path: Path,
    label: str,
    diagnostic: Diagnostic,
) -> None:
    if diagnostic.severity == "warning" and diagnostic.code in accepted:
        return
    report.diagnostics.append(RecordDiagnostic(path, label, diagnostic))
