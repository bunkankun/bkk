// Kanripo PUA codepoint formula.
//
// The on-disk text may contain either raw PUA codepoints
// (`String.fromCodePoint(0x105000 + n)`) directly, or HTML-style
// entity refs of the form `&KRnnnn;` (decimal). v1 decodes the
// entity-ref form using the global formula; we don't fetch
// per-bundle PUA-map.yaml in v1.

const PUA_BASE = 0x105000;

export function krToCodepoint(n: number): number {
  return PUA_BASE + n;
}

export function krRefToChar(nnnn: string): string {
  const n = parseInt(nnnn, 10);
  if (Number.isNaN(n)) return "";
  return String.fromCodePoint(PUA_BASE + n);
}

const KR_REF_RE = /&KR(\d+);/g;

export function decodeKrRefs(text: string): string {
  return text.replace(KR_REF_RE, (_m, nnnn: string) => krRefToChar(nnnn));
}

export function isPuaCodepoint(cp: number): boolean {
  // Supplementary Private Use Area-A is U+F0000..U+FFFFD,
  // Area-B is U+100000..U+10FFFD. Kanripo lives in the 0x105000+ block.
  return cp >= 0xe000 && cp <= 0x10fffd;
}
