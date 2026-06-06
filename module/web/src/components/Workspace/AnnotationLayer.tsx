// Pure helper: given a list of annotations, return the set of
// offsets that should be marked, and a quick lookup from offset
// to the list of annotations at that offset.
//
// This is consumed by TextViewer to decorate char spans rather
// than being a separate React component — keeping it as a tiny
// module makes the offset logic easy to test by hand later.

import type { Annotation } from "../../api/types";

export interface AnnotationIndex {
  byOffset: Map<number, Annotation[]>;
}

export function buildAnnotationIndex(anns: Annotation[]): AnnotationIndex {
  const byOffset = new Map<number, Annotation[]>();
  for (const a of anns) {
    const list = byOffset.get(a.offset);
    if (list) list.push(a);
    else byOffset.set(a.offset, [a]);
  }
  return { byOffset };
}

export function annTooltip(a: Annotation): string {
  const bits: string[] = [];
  if (a.form?.orth) bits.push(a.form.orth);
  if (a.form?.pron) bits.push(`(${a.form.pron})`);
  const def = a.sense?.def_text ?? a.sense?.def;
  if (def) bits.push(def);
  if (bits.length === 0 && a.concept) bits.push(a.concept);
  return bits.join(" ");
}
