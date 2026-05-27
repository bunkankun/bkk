// Hand-written types mirroring module/bkk/serve/schemas.py.
// Kept narrow enough for v1 read mode + annotations.

export interface EditionInfo {
  short: string;
  label?: string | null;
}

export interface BundleSummary {
  textid: string;
  canonical_identifier?: string | null;
  title?: string | null;
  edition_short?: string | null;
  editions: EditionInfo[];
}

export interface BundleListResponse {
  bundles: BundleSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface CatalogMatch {
  textid: string;
  canonical_identifier?: string | null;
  title?: string | null;
  edition_short?: string | null;
  base_edition?: string | null;
  metadata: Record<string, unknown>;
}

export interface CatalogResponse {
  total: number;
  offset: number;
  limit: number;
  next_offset?: number | null;
  filters_applied: Record<string, string[]>;
  matches: CatalogMatch[];
  recipe: Record<string, unknown>;
}

export interface CategoryNode {
  code: string;
  label: string;
  zh: string;
  bundle_count: number;
  subcategories: CategoryNode[];
}

export interface CategoriesResponse {
  categories: CategoryNode[];
}

// Manifest is a passthrough dict — type the parts we actually read.
export interface ManifestPart {
  seq: number;
  filename: string;
  hash?: string;
  [k: string]: unknown;
}

export interface TocSpan {
  // [bucket, start, end]
  0: string;
  1: number;
  2: number;
}

export interface TocRef {
  seq: number;
  marker_id?: string;
  span?: [string, number, number];
  [k: string]: unknown;
}

export interface TocEntry {
  ref: TocRef;
  label?: string;
  [k: string]: unknown;
}

export interface ManifestEdition {
  short?: string;
  label?: string;
  [k: string]: unknown;
}

export interface ManifestIiifEntry {
  info_url_pattern?: string;
  manifest_url?: string;
  canvas_pattern?: string;
}

export interface ManifestMetadata {
  title?: string;
  edition?: ManifestEdition;
  base_edition?: string;
  image_base_urls?: { [editionShort: string]: string };
  iiif?: { [editionShort: string]: ManifestIiifEntry };
  [k: string]: unknown;
}

export interface Manifest {
  canonical_identifier?: string;
  editions?: { short: string; label?: string }[];
  metadata?: ManifestMetadata;
  assets?: {
    parts?: ManifestPart[];
    references?: { filename?: string; name?: string; role?: string }[];
  };
  table_of_contents?: TocEntry[];
  [k: string]: unknown;
}

// Juan body shape from /bundles/{textid}/juan/{seq}
export interface JuanMarker {
  type: string;
  offset?: number;
  content?: string;
  id?: string;
  [k: string]: unknown;
}

export interface JuanBucket {
  text: string;
  hash?: string;
  markers?: JuanMarker[];
  [k: string]: unknown;
}

export interface Juan {
  seq: number;
  canonical_identifier?: string;
  hash?: string;
  body?: JuanBucket;
  front?: JuanBucket;
  back?: JuanBucket;
  [k: string]: unknown;
}

// Annotations
export interface AnnotationForm {
  orig?: string;
  orth?: string;
  pron?: string;
}

export interface AnnotationSense {
  id?: string;
  pos?: string;
  syn_func?: string;
  sem_feat?: string;
  def?: string;
  usage?: Record<string, unknown>;
}

export interface AnnotationTranslation {
  text?: string;
  title?: string;
  src?: string;
}

export interface Annotation {
  id?: string;
  offset: number;
  length?: number;
  concept?: string;
  concept_id?: string;
  seg_id?: string;
  pos?: number;
  form?: AnnotationForm;
  sense?: AnnotationSense;
  translation?: AnnotationTranslation;
  metadata?: Record<string, unknown>;
}

// Search
export type SearchSort =
  | "match"
  | "textid"
  | "reverse_prematch"
  | "date"
  | "closeness";

export interface VariantOverlay {
  master_offset: number;
  length: number;
  content: string;
  witness: string;
  witness_form: string;
}

export interface SearchHit {
  textid: string;
  juan_seq: number;
  bucket: string;
  master_offset: number;
  master_length: number;
  matched_via: string;
  matched_text: string;
  left: string;
  match: string;
  right: string;
  witness_left?: string;
  witness_right?: string;
  witness_left_variant_offset?: number;
  witness_right_variant_end?: number;
  overlays: VariantOverlay[];
  toc_label?: string | null;
  voice: string;
  voice_stack: string[];
  recipe: Record<string, unknown>;
}

export interface SearchFacetValue {
  value: string;
  label?: string | null;
  count: number;
  selected: boolean;
}

export interface SearchDateFacets {
  min?: number | null;
  max?: number | null;
  current_textid?: string | null;
  current_text_date?: number | null;
  before_count?: number | null;
  after_count?: number | null;
}

export interface SearchFacets {
  textid: SearchFacetValue[];
  category: SearchFacetValue[];
  witness: SearchFacetValue[];
  voice: SearchFacetValue[];
  left_char: SearchFacetValue[];
  right_char: SearchFacetValue[];
  left_bigram: SearchFacetValue[];
  right_bigram: SearchFacetValue[];
  around_binom: SearchFacetValue[];
  date: SearchDateFacets;
}

export interface SearchResponse {
  query: string;
  total: number;
  offset: number;
  limit: number;
  sort: SearchSort;
  facets?: SearchFacets;
  hits: SearchHit[];
}

// Server identity
export interface ServerInfo {
  service?: string;
  version?: string;
  corpus_root?: string;
  index_path?: string;
  catalog_path?: string;
  upstream_repo?: string | null;
  docs?: string;
  openapi?: string;
}

export interface WorkspaceInfo {
  repo: string;
  html_url: string;
  branch: string;
  private: boolean;
}

export interface WorkspaceFileEntry {
  path: string;
  name?: string;
  type?: string;
  sha?: string;
  size?: number;
}

export interface WorkspaceFileList {
  prefix: string;
  files: WorkspaceFileEntry[];
}

export interface WorkspaceFile {
  path: string;
  sha?: string;
  content: string;
  encoding: "utf-8";
}

export interface WorkspaceWriteResult {
  path: string;
  sha?: string | null;
  commit?: unknown;
}

export interface AuthUser {
  login: string;
  name?: string | null;
  avatar_url?: string | null;
  html_url?: string | null;
  workspace: WorkspaceInfo;
}

export interface AuthSession {
  authenticated: boolean;
  user: AuthUser | null;
}
