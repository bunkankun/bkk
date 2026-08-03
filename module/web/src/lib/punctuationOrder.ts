export const PAGE_BREAK_RENDER_TOKEN = "\f";

const PUNCTUATION_RENDER_ORDER = [
  "》",
  "？",
  "；",
  "。",
  "，",
  "、",
  "』",
  "」",
  "：",
  "/",
  ")",
  "\n",
  PAGE_BREAK_RENDER_TOKEN,
  "\u3000",
  "(",
  "「",
  "『",
  "《",
];

const PUNCTUATION_RENDER_RANK = new Map(
  PUNCTUATION_RENDER_ORDER.map((ch, index) => [ch, index]),
);

function punctuationRenderRank(ch: string): number | null {
  return PUNCTUATION_RENDER_RANK.get(ch) ?? null;
}

export function sortPunctuationRenderOrder<T extends { ch: string; index: number }>(
  items: T[],
): T[] {
  const ordered = [...items].sort((left, right) => left.index - right.index);
  const result: T[] = [];
  for (let start = 0; start < ordered.length;) {
    const firstRank = punctuationRenderRank(ordered[start].ch);
    if (firstRank == null) {
      result.push(ordered[start]);
      start += 1;
      continue;
    }
    let end = start + 1;
    while (end < ordered.length && punctuationRenderRank(ordered[end].ch) != null) {
      end += 1;
    }
    result.push(
      ...ordered.slice(start, end).sort((left, right) => {
        const leftRank = punctuationRenderRank(left.ch) ?? 0;
        const rightRank = punctuationRenderRank(right.ch) ?? 0;
        return leftRank - rightRank || left.index - right.index;
      }),
    );
    start = end;
  }
  return result;
}
