import type { BundleTextSplice, JuanMarker } from "../api/types";

export interface EditableMarker {
  key: string;
  data: JuanMarker;
  originalId: string | null;
  unresolved: boolean;
}

export function codepoints(text: string): string[] {
  return Array.from(text);
}

export function findTextSplice(before: string, after: string): BundleTextSplice | null {
  const oldChars = codepoints(before);
  const newChars = codepoints(after);
  if (before === after) return null;
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
  return {
    start: prefix,
    delete_count: oldChars.length - prefix - suffix,
    insert: newChars.slice(prefix, newChars.length - suffix).join(""),
  };
}

export function transformMarkers(
  markers: EditableMarker[],
  splice: BundleTextSplice,
): EditableMarker[] {
  const editStart = splice.start;
  const editEnd = splice.start + splice.delete_count;
  const delta = codepoints(splice.insert).length - splice.delete_count;
  return markers.map((marker) => {
    const offset = marker.data.offset;
    if (typeof offset !== "number") return { ...marker, unresolved: true };
    const length = typeof marker.data.length === "number" ? marker.data.length : null;
    const markerEnd = length == null ? offset : offset + length;

    if (length == null) {
      if (offset < editStart) return marker;
      if (offset >= editEnd && !(splice.delete_count === 0 && offset === editStart)) {
        return { ...marker, data: { ...marker.data, offset: offset + delta } };
      }
      return { ...marker, unresolved: true };
    }

    if (markerEnd <= editStart) return marker;
    if (offset >= editEnd && !(splice.delete_count === 0 && offset === editStart)) {
      return { ...marker, data: { ...marker.data, offset: offset + delta } };
    }
    const strictlyInside =
      editStart > offset &&
      (splice.delete_count === 0 ? editStart < markerEnd : editEnd < markerEnd);
    if (strictlyInside) {
      return {
        ...marker,
        data: { ...marker.data, length: Math.max(0, length + delta) },
      };
    }
    return { ...marker, unresolved: true };
  });
}
