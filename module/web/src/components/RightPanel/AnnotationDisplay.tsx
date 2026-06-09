// Shared annotation-payload display primitives. Used by:
// - AnnotationsTab cards (annotations loaded from the bundle)
// - ChatTab cards (annotation contributions pulled from Bluesky)
//
// Renders the char/pinyin form, the sense tripartite (with optional
// SenseRowLabel lookup), the concept, and an inline translation.

import type {
  AnnotationForm,
  AnnotationSense,
  AnnotationTranslation,
} from "../../api/types";
import { SenseRowLabel, type LabelStore } from "../Workspace/CoreRecordEditor";

export interface AnnotationPayloadParts {
  form?: AnnotationForm;
  sense?: AnnotationSense;
  concept?: string;
  translation?: AnnotationTranslation;
}

export function SenseTriple({
  sense,
  store,
}: {
  sense?: AnnotationSense;
  store: LabelStore;
}) {
  if (sense?.id) {
    return <SenseRowLabel uuid={sense.id} store={store} />;
  }
  const syn = sense?.syn_func;
  const sem = sense?.sem_feat;
  const def = sense?.def_text ?? sense?.def;
  if (!syn && !sem && !def) return null;
  return (
    <span>
      {syn && <strong>{syn}</strong>}
      {sem && <>{syn && " "}<em>{sem}</em></>}
      {def && <>{(syn || sem) && " "}{def}</>}
    </span>
  );
}

export function hasSenseContent(sense?: AnnotationSense): boolean {
  return Boolean(
    sense?.id ||
      sense?.syn_func ||
      sense?.sem_feat ||
      sense?.def_text ||
      sense?.def,
  );
}

// Renders the annotation payload (form, concept, sense, translation) without
// any surrounding card chrome or offset/location header — callers wrap.
export function AnnotationPayload({
  parts,
  store,
}: {
  parts: AnnotationPayloadParts;
  store: LabelStore;
}) {
  const { form, sense, concept, translation } = parts;
  const showForm = Boolean(form?.orth || form?.pron);
  const showSense = hasSenseContent(sense);
  return (
    <>
      {showForm && (
        <div className="ann-head">
          {form?.orth && <span className="ann-orth">{form.orth}</span>}
          {form?.pron && <span className="ann-pron">{form.pron}</span>}
        </div>
      )}
      {concept && <div className="ann-concept">{concept}</div>}
      {showSense && (
        <div className="ann-def">
          <SenseTriple sense={sense} store={store} />
        </div>
      )}
      {translation?.text && (
        <div className="ann-tr">
          "{translation.text}"
          {translation.src ? ` — ${translation.src}` : ""}
        </div>
      )}
    </>
  );
}
