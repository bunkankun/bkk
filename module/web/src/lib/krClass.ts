export function krClass(textid: string): string {
  const m = textid.match(/^KR(\d)/);
  return m ? `kr${m[1]}` : "";
}
