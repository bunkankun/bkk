let dirty = false;

export function setBundleEditorDirty(value: boolean): void {
  dirty = value;
}

export function confirmDiscardBundleEditor(): boolean {
  return !dirty || window.confirm("Discard unsaved bundle edits?");
}
