import type { SelectionRange } from "../state/useWorkspace";

export interface SavedSubLocation {
  offset: string;
  note?: string;
  content?: string;
}

export interface SavedLocation {
  id: string;
  location: string;
  date: string;
  content?: string;
  title?: string;
  tags?: string[];
  note?: string;
  sub?: SavedSubLocation[];
}

const CONTENT_PREVIEW_LIMIT = 200;
const CONTENT_PREVIEW_EDGE = 100;

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}

export function locationFilePath(date: Date): string {
  const stamp = [
    date.getUTCFullYear(),
    pad(date.getUTCMonth() + 1, 2),
    pad(date.getUTCDate(), 2),
    "T",
    pad(date.getUTCHours(), 2),
    pad(date.getUTCMinutes(), 2),
    pad(date.getUTCSeconds(), 2),
    pad(date.getUTCMilliseconds(), 3),
  ].join("");
  return `locations/${stamp}.yaml`;
}

export function selectionLocationRef(selection: SelectionRange): string {
  const length = Math.max(0, selection.end - selection.start);
  return `${selection.textid}/${selection.seq}/${selection.bucket}/@${selection.start}+${length}`;
}

export function selectionContentPreview(chars: string[]): string {
  if (chars.length <= CONTENT_PREVIEW_LIMIT) return chars.join("");
  return `${chars.slice(0, CONTENT_PREVIEW_EDGE).join("")}...${chars.slice(-CONTENT_PREVIEW_EDGE).join("")}`;
}

export function subLocationOffset(start: number, end: number): string {
  const safeStart = Math.max(0, Math.min(start, end));
  const safeLength = Math.max(0, end - safeStart);
  return `@${safeStart}+${safeLength}`;
}

function normalizedLines(value: string): string[] {
  return value.replace(/\r\n?/g, "\n").split("\n");
}

function yamlQuoted(value: string): string {
  return JSON.stringify(value);
}

function appendStringField(lines: string[], indent: number, key: string, value: string | undefined): void {
  if (value == null || value === "") return;
  const prefix = `${" ".repeat(indent)}${key}: `;
  if (value.includes("\n") || value.includes("\r")) {
    lines.push(`${prefix}|`);
    for (const line of normalizedLines(value)) {
      lines.push(`${" ".repeat(indent + 2)}${line}`);
    }
    return;
  }
  lines.push(`${prefix}${yamlQuoted(value)}`);
}

export function serializeSavedLocation(location: SavedLocation): string {
  const lines: string[] = [];
  lines.push(`- id: ${yamlQuoted(location.id)}`);
  appendStringField(lines, 2, "location", location.location);
  appendStringField(lines, 2, "date", location.date);
  appendStringField(lines, 2, "content", location.content);
  appendStringField(lines, 2, "title", location.title);
  const tags = location.tags?.filter((tag) => tag.trim().length > 0) ?? [];
  if (tags.length > 0) {
    lines.push("  tags:");
    for (const tag of tags) {
      lines.push(`    - ${yamlQuoted(tag.trim())}`);
    }
  }
  appendStringField(lines, 2, "note", location.note);
  const sub = location.sub?.filter((item) => item.offset.length > 0) ?? [];
  if (sub.length > 0) {
    lines.push("  sub:");
    for (const item of sub) {
      lines.push(`    - offset: ${yamlQuoted(item.offset)}`);
      appendStringField(lines, 6, "note", item.note);
      appendStringField(lines, 6, "content", item.content);
    }
  }
  return `${lines.join("\n")}\n`;
}
