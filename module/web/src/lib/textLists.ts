export const KRID_RE = /^KR[1-6][a-z][0-9]{4}$/;

export interface ParsedTextList {
  name: string | null;
  textids: string[];
  metadata: Record<string, string>;
}

export interface TextListEntry {
  textid: string;
  hitCount?: number | null;
  title?: string | null;
}

export function sanitizeListName(name: string): string {
  const trimmed = name.trim() || "Untitled list";
  return trimmed
    .replace(/[\\/#?%*:|"<>]/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80) || "Untitled-list";
}

export function listPathFromName(name: string): string {
  return `lists/${sanitizeListName(name)}.txt`;
}

export function listNameFromPath(path: string): string {
  const base = path.split("/").pop() ?? path;
  return base.replace(/\.txt$/i, "").replace(/-/g, " ");
}

export function parseTextList(content: string, fallbackName: string): ParsedTextList {
  const metadata: Record<string, string> = {};
  const textids: string[] = [];
  const seen = new Set<string>();
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("#")) {
      const m = /^#\s*([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(trimmed);
      if (m) metadata[m[1].toLowerCase()] = m[2].trim();
      continue;
    }
    const first = trimmed.split(/\s+/, 1)[0];
    if (!KRID_RE.test(first) || seen.has(first)) continue;
    seen.add(first);
    textids.push(first);
  }
  return {
    name: metadata.name || fallbackName || null,
    textids,
    metadata,
  };
}

export function serializeTextList(params: {
  name: string;
  textids: string[];
  entries?: TextListEntry[];
  metadata?: Record<string, string | number | null | undefined>;
  existingContent?: string;
}): string {
  const entryById = new Map(
    (params.entries ?? [])
      .filter((entry) => KRID_RE.test(entry.textid))
      .map((entry) => [entry.textid, entry]),
  );
  const seen = new Set<string>();
  const ids = [
    ...(params.entries ?? []).map((entry) => entry.textid),
    ...params.textids,
  ].filter((id) => {
    if (!KRID_RE.test(id) || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  const meta: Record<string, string | number> = {
    name: params.name,
    ...(params.metadata ?? {}),
    updated_at: new Date().toISOString(),
  };
  const header = Object.entries(meta)
    .filter(([, value]) => value != null && String(value).trim() !== "")
    .map(([key, value]) => `# ${key}: ${value}`);
  const preserved = (params.existingContent ?? "")
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) return false;
      const first = trimmed.split(/\s+/, 1)[0];
      return !KRID_RE.test(first);
    });
  const rows = ids.map((id) => {
    const entry = entryById.get(id);
    if (!entry) return id;
    const cols = [
      id,
      entry.hitCount == null ? "" : String(entry.hitCount),
      entry.title?.trim() ?? "",
    ];
    return cols.join(" ").replace(/\s+$/, "");
  });
  return [...header, "", ...rows, ...preserved].join("\n").replace(/\n*$/, "\n");
}

export function addTextidsToContent(
  content: string,
  name: string,
  textids: string[],
  metadata?: Record<string, string | number | null | undefined>,
  entries?: TextListEntry[],
): string {
  const parsed = parseTextList(content, name);
  return serializeTextList({
    name: parsed.name ?? name,
    textids: [...parsed.textids, ...textids],
    entries,
    metadata,
    existingContent: content,
  });
}

export function listColor(path: string): string {
  let hash = 0;
  for (let i = 0; i < path.length; i++) hash = (hash * 31 + path.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360} 62% 38%)`;
}
