import type { JuanMarker } from "../api/types";
import type { EditableMarker } from "./editSplices";

// An empty string is an internal name for the backward-compatible default
// punctuation set. Persisted default markers deliberately omit `set`.
export const DEFAULT_PUNCTUATION_SET = "";

export interface RenderedEditorUnit {
  ch: string;
  start: number;
  end: number;
  canonicalOffset: number;
  kind: "text" | "punctuation";
  markerKey?: string;
}

export interface RenderedEditorText {
  text: string;
  units: RenderedEditorUnit[];
  canonicalLength: number;
  // UTF-16 positions immediately before punctuation injected at an offset.
  caretBefore: number[];
  // UTF-16 positions after punctuation, at the canonical character itself.
  charStart: number[];
  markerSpans: Map<string, { start: number; end: number }>;
}

export interface EditorPosition {
  offset: number;
  ch: string | null;
  cp: number | null;
}

export interface CanonicalSelection {
  offset: number;
  length: number | null;
}

export interface ParsedPunctuation {
  ok: true;
  punctuation: Map<number, string>;
}

export interface InvalidPunctuation {
  ok: false;
  message: string;
}

export function punctuationSetOf(marker: JuanMarker): string | null {
  if (marker.type !== "punctuation") return null;
  const set = marker.set;
  return typeof set === "string" && set ? set : DEFAULT_PUNCTUATION_SET;
}

export function punctuationSetLabel(set: string): string {
  return set === DEFAULT_PUNCTUATION_SET ? "Default" : set;
}

export function punctuationSets(markers: EditableMarker[]): string[] {
  const named = new Set<string>();
  for (const marker of markers) {
    const set = punctuationSetOf(marker.data);
    if (set == null) continue;
    if (set !== DEFAULT_PUNCTUATION_SET) named.add(set);
  }
  // Default is always available so an unpunctuated text can acquire it.
  return [DEFAULT_PUNCTUATION_SET, ...[...named].sort()];
}

export function renderEditorText(
  canonicalText: string,
  markers: EditableMarker[],
  punctuationSet: string | null,
): RenderedEditorText {
  const canonical = Array.from(canonicalText);
  const atOffset = new Map<number, EditableMarker[]>();
  if (punctuationSet != null) {
    for (const marker of markers) {
      if (punctuationSetOf(marker.data) !== punctuationSet) continue;
      const offset = marker.data.offset;
      const content = marker.data.content;
      if (
        typeof offset !== "number" ||
        offset < 0 ||
        offset > canonical.length ||
        typeof content !== "string" ||
        !content
      ) continue;
      const list = atOffset.get(offset) ?? [];
      list.push(marker);
      atOffset.set(offset, list);
    }
  }

  const parts: string[] = [];
  const units: RenderedEditorUnit[] = [];
  const caretBefore: number[] = [];
  const charStart: number[] = [];
  const markerSpans = new Map<string, { start: number; end: number }>();
  let utf16 = 0;
  const append = (
    ch: string,
    canonicalOffset: number,
    kind: RenderedEditorUnit["kind"],
    markerKey?: string,
  ) => {
    const start = utf16;
    utf16 += ch.length;
    parts.push(ch);
    units.push({ ch, start, end: utf16, canonicalOffset, kind, markerKey });
  };

  for (let offset = 0; offset <= canonical.length; offset += 1) {
    caretBefore[offset] = utf16;
    for (const marker of atOffset.get(offset) ?? []) {
      const start = utf16;
      for (const ch of Array.from(String(marker.data.content ?? ""))) {
        append(ch, offset, "punctuation", marker.key);
      }
      markerSpans.set(marker.key, { start, end: utf16 });
    }
    charStart[offset] = utf16;
    if (offset < canonical.length) append(canonical[offset], offset, "text");
  }
  return {
    text: parts.join(""),
    units,
    canonicalLength: canonical.length,
    caretBefore,
    charStart,
    markerSpans,
  };
}

export function editorPositionAt(
  view: RenderedEditorText,
  utf16Position: number,
): EditorPosition {
  const position = Math.max(0, Math.min(utf16Position, view.text.length));
  const unit =
    view.units.find((candidate) =>
      position >= candidate.start && position < candidate.end
    ) ??
    view.units.find((candidate) => candidate.start >= position) ??
    null;
  if (!unit) {
    return { offset: view.canonicalLength, ch: null, cp: null };
  }
  return {
    offset: unit.canonicalOffset,
    ch: unit.ch,
    cp: unit.ch.codePointAt(0) ?? null,
  };
}

export function canonicalOffsetAt(
  view: RenderedEditorText,
  utf16Position: number,
): number {
  const position = Math.max(0, Math.min(utf16Position, view.text.length));
  for (const unit of view.units) {
    if (position <= unit.start) return unit.canonicalOffset;
    if (position < unit.end) return unit.canonicalOffset;
  }
  return view.canonicalLength;
}

export function canonicalSelectionFromDom(
  view: RenderedEditorText,
  start: number,
  end: number,
): CanonicalSelection {
  const lo = Math.min(start, end);
  const hi = Math.max(start, end);
  if (hi > lo) {
    const selected = view.units.filter(
      (unit) => unit.kind === "text" && unit.start < hi && unit.end > lo,
    );
    if (selected.length > 0) {
      const first = selected[0].canonicalOffset;
      const last = selected[selected.length - 1].canonicalOffset;
      return { offset: first, length: last - first + 1 };
    }
  }
  return { offset: canonicalOffsetAt(view, lo), length: null };
}

export function markerDomSelection(
  view: RenderedEditorText,
  marker: EditableMarker,
): { start: number; end: number } {
  const punctuationSpan = view.markerSpans.get(marker.key);
  if (punctuationSpan) return punctuationSpan;
  const rawOffset = marker.data.offset;
  const offset =
    typeof rawOffset === "number"
      ? Math.max(0, Math.min(rawOffset, view.canonicalLength))
      : 0;
  const rawLength = marker.data.length;
  const length =
    typeof rawLength === "number" && rawLength > 0
      ? Math.min(rawLength, view.canonicalLength - offset)
      : offset < view.canonicalLength ? 1 : 0;
  if (length === 0) {
    const caret = view.charStart[offset] ?? view.text.length;
    return { start: caret, end: caret };
  }
  return {
    start: view.charStart[offset],
    end: view.caretBefore[offset + length],
  };
}

function isCanonicalCharacter(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return (
    (cp >= 0x4e00 && cp <= 0x9fff) ||
    (cp >= 0x3400 && cp <= 0x4dbf) ||
    (cp >= 0x20000 && cp <= 0x2a6df) ||
    (cp >= 0x2a700 && cp <= 0x2ebef) ||
    (cp >= 0xf900 && cp <= 0xfaff) ||
    (cp >= 0x105000 && cp < 0x106000)
  );
}

function isEditablePunctuation(ch: string): boolean {
  return /[\p{P}\p{S}\p{Z}\s]/u.test(ch);
}

export function punctuationInputAllowed(before: string, after: string): boolean {
  const oldChars = Array.from(before);
  const newChars = Array.from(after);
  let prefix = 0;
  while (
    prefix < oldChars.length &&
    prefix < newChars.length &&
    oldChars[prefix] === newChars[prefix]
  ) prefix += 1;
  let suffix = 0;
  while (
    suffix < oldChars.length - prefix &&
    suffix < newChars.length - prefix &&
    oldChars[oldChars.length - 1 - suffix] === newChars[newChars.length - 1 - suffix]
  ) suffix += 1;
  return newChars
    .slice(prefix, newChars.length - suffix)
    .every(isEditablePunctuation);
}

export function parsePunctuatedText(
  canonicalText: string,
  displayedText: string,
): ParsedPunctuation | InvalidPunctuation {
  const canonical = Array.from(canonicalText);
  const punctuation = new Map<number, string>();
  let offset = 0;
  for (const ch of Array.from(displayedText)) {
    if (offset < canonical.length && ch === canonical[offset]) {
      offset += 1;
      continue;
    }
    if (isCanonicalCharacter(ch)) {
      return {
        ok: false,
        message: `Canonical character changes are disabled while punctuation is loaded (offset ${offset}).`,
      };
    }
    punctuation.set(offset, (punctuation.get(offset) ?? "") + ch);
  }
  if (offset !== canonical.length) {
    return {
      ok: false,
      message: `Canonical character changes are disabled while punctuation is loaded (offset ${offset}).`,
    };
  }
  return { ok: true, punctuation };
}

function sameCharsAt(
  target: string[],
  start: number,
  content: string,
): boolean {
  const chars = Array.from(content);
  if (start + chars.length > target.length) return false;
  return chars.every((ch, index) => target[start + index] === ch);
}

export function reconcilePunctuationMarkers(
  markers: EditableMarker[],
  set: string,
  punctuation: Map<number, string>,
  newKey: () => string,
): EditableMarker[] {
  const selectedByOffset = new Map<number, EditableMarker[]>();
  for (const marker of markers) {
    if (punctuationSetOf(marker.data) !== set) continue;
    const offset = marker.data.offset;
    if (typeof offset !== "number") continue;
    const list = selectedByOffset.get(offset) ?? [];
    list.push(marker);
    selectedByOffset.set(offset, list);
  }

  const removedKeys = new Set<string>();
  const insertBefore = new Map<string, EditableMarker>();
  const insertAfter = new Map<string, EditableMarker>();
  const unanchored: EditableMarker[] = [];
  const offsets = new Set([...selectedByOffset.keys(), ...punctuation.keys()]);
  for (const offset of [...offsets].sort((a, b) => a - b)) {
    const old = selectedByOffset.get(offset) ?? [];
    const target = Array.from(punctuation.get(offset) ?? "");
    let prefixLength = 0;
    let prefixCount = 0;
    while (prefixCount < old.length) {
      const content = old[prefixCount].data.content;
      if (typeof content !== "string" || !content) break;
      if (!sameCharsAt(target, prefixLength, content)) break;
      prefixLength += Array.from(content).length;
      prefixCount += 1;
    }

    let suffixStart = target.length;
    let suffixIndex = old.length;
    while (suffixIndex > prefixCount) {
      const content = old[suffixIndex - 1].data.content;
      if (typeof content !== "string" || !content) break;
      const length = Array.from(content).length;
      if (suffixStart - length < prefixLength) break;
      if (!sameCharsAt(target, suffixStart - length, content)) break;
      suffixStart -= length;
      suffixIndex -= 1;
    }

    const middle = target.slice(prefixLength, suffixStart).join("");
    let inserted: EditableMarker | null = null;
    if (middle) {
      const data: JuanMarker = {
        type: "punctuation",
        offset,
        content: middle,
        id: "",
      };
      if (set !== DEFAULT_PUNCTUATION_SET) data.set = set;
      inserted = {
        key: newKey(),
        data,
        originalId: null,
        unresolved: false,
        generatedId: true,
      };
    }

    const removed = old.slice(prefixCount, suffixIndex);
    for (const marker of removed) removedKeys.add(marker.key);
    if (inserted) {
      // Replace changed punctuation in its original slot. If this is a pure
      // insertion, put it between the retained prefix/suffix punctuation
      // without moving any unrelated equal-offset markers.
      if (removed.length > 0) {
        insertBefore.set(removed[0].key, inserted);
      } else if (suffixIndex < old.length) {
        insertBefore.set(old[suffixIndex].key, inserted);
      } else if (prefixCount > 0) {
        insertAfter.set(old[prefixCount - 1].key, inserted);
      } else {
        unanchored.push(inserted);
      }
    }
  }

  const result: EditableMarker[] = [];
  for (const marker of markers) {
    const before = insertBefore.get(marker.key);
    if (before) result.push(before);
    if (!removedKeys.has(marker.key)) result.push(marker);
    const after = insertAfter.get(marker.key);
    if (after) result.push(after);
  }
  // New punctuation at an offset that previously had none is appended to
  // that equal-offset group by the stable sort; existing markers never move.
  result.push(...unanchored);
  return result.sort(
    (left, right) =>
      Number(left.data.offset ?? 0) - Number(right.data.offset ?? 0),
  );
}
