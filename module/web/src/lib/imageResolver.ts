// Resolves a page-break marker to a concrete image source.
//
// Manifest convention (real example, KR1a0042):
//   metadata.image_base_urls: { wyg: "http://img.kanripo.org/general/skqs/wyg/" }
//   metadata.base_edition:    "WYG"
//   page-break marker:        { id: "KR1a0042_WYG_001-1a", image: "WYG0015/WYG0015-0754c.png" }
//
// → "http://img.kanripo.org/general/skqs/wyg/WYG0015/WYG0015-0754c.png"
//
// The edition key in `image_base_urls` is lowercased (per existing data), so
// we lowercase the lookup key on this side. Some legacy bundles carry a
// malformed shape like `{ baseedition: "HFL" }` (value is an edition code,
// not a URL) — entries whose value isn't a string are ignored.
//
// IIIF declarations are typed but not yet rendered; the resolver returns
// `{ kind: "none" }` for that branch until OpenSeadragon is wired in.

import type { Manifest } from "../api/types";

export interface PageBreak {
  id: string;
  offset: number;
  image?: string;
}

export type ImageSpec =
  | { kind: "direct"; url: string; pageId: string }
  | { kind: "none"; reason: string };

function joinUrl(base: string, rel: string): string {
  const b = base.replace(/\/+$/, "");
  const r = rel.replace(/^\/+/, "");
  return `${b}/${r}`;
}

export function resolveImage(
  page: PageBreak,
  manifest: Manifest | null,
  editionShort: string | null,
): ImageSpec {
  if (!manifest) return { kind: "none", reason: "no manifest" };
  const meta = manifest.metadata ?? {};
  const edition =
    editionShort ??
    (typeof meta.base_edition === "string" ? meta.base_edition : null);
  if (!edition) return { kind: "none", reason: "no edition" };

  if (typeof page.image === "string" && page.image.length > 0) {
    const bases = meta.image_base_urls;
    const base = bases ? bases[edition.toLowerCase()] : undefined;
    if (typeof base === "string" && base.length > 0) {
      return { kind: "direct", url: joinUrl(base, page.image), pageId: page.id };
    }
  }

  // IIIF branch reserved for follow-up (OpenSeadragon).
  return { kind: "none", reason: `no image URL for edition ${edition}` };
}
