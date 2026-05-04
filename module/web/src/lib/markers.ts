// Marker id format: `<text-id>_<edition>_<location>` (e.g.
// `KR3a0013_WYG_001-1a`). Some text ids contain underscores
// (rare, but conservative parsing matters), so split into at
// most three parts from the LEFT for textid + edition, and
// keep the remainder as the location.

export interface ParsedMarkerId {
  textid: string;
  edition: string;
  location: string;
}

export function parseMarkerId(id: string): ParsedMarkerId | null {
  if (!id) return null;
  const parts = id.split("_");
  if (parts.length < 3) return null;
  // Expected: KR3a0013_WYG_001-1a → ["KR3a0013", "WYG", "001-1a"]
  // If a textid contained an underscore (we don't expect this in
  // KR ids), the leading parts would absorb it. Keep last segment
  // as location, second-to-last as edition, the rest as textid.
  const location = parts[parts.length - 1];
  const edition = parts[parts.length - 2];
  const textid = parts.slice(0, parts.length - 2).join("_");
  return { textid, edition, location };
}
