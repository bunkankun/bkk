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

export interface TimelineBucket {
  key: string;
  label: string;
  start: number;
  end: number;
  bundle_count: number;
}

export interface TimelineResponse {
  buckets: TimelineBucket[];
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
  marker_id?: string;
  bucket?: string;
  concept?: string;
  concept_id?: string;
  seg_id?: string;
  pos?: number;
  form?: AnnotationForm;
  sense?: AnnotationSense;
  translation?: AnnotationTranslation;
  metadata?: Record<string, unknown>;
}

// Annotation write path (Bluesky).
export interface BlueskyStatus {
  handle: string | null;
  did: string | null;
}

export interface BlueskyLoginRequest {
  handle: string;
  app_password: string;
}

export interface AnnotationAnchor {
  marker_id: string;
  offset: number;
  length: number;
  end_marker_id?: string | null;
  end_length?: number | null;
}

export interface AnnotationPostRequest {
  text_id: string;
  edition: string;
  anchor: AnnotationAnchor;
  payload: Record<string, unknown>;
  source_role?: string | null;
  supersedes?: string | null;
}

export interface AnnotationPostResponse {
  uri: string;
  cid: string;
  did: string;
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
  excluded: boolean;
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

export interface SearchTextidsResponse {
  query: string;
  hit_count: number;
  text_count: number;
  textids: string[];
  entries?: {
    textid: string;
    hit_count: number;
    title?: string | null;
  }[];
}

// Translation overlays
export interface OverlayFamily {
  id: string;
  label: string;
  count: number;
}

export interface OverlaysResponse {
  overlays: OverlayFamily[];
}

export interface TranslationResponsibility {
  role?: string | null;
  name?: string | null;
}

export interface TranslationSummary {
  id: string;
  source_textid: string;
  canonical_identifier?: string | null;
  source_canonical_identifier?: string | null;
  language?: string | null;
  title?: string | null;
  original_title?: string | null;
  responsibility: TranslationResponsibility[];
  date?: string | null;
  license?: string | null;
  juan_count: number;
  segment_count: number;
  source_juans: number[];
}

export interface TranslationListResponse {
  translations: TranslationSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface TranslationAlignedRow {
  corresp: string;
  source_marker_id: string;
  source_offset: number;
  source_end: number;
  source_text: string;
  translation_text: string;
  translation_refs: string[];
  continued: boolean;
  resp?: string | null;
}

export interface SegmentTranslationEntry {
  bundle_id: string;
  title?: string | null;
  language?: string | null;
  translator?: string | null;
  text: string;
}

export interface SegmentTranslationsResponse {
  corresp: string;
  source_text: string;
  entries: SegmentTranslationEntry[];
}

export interface TranslationAlignmentResponse {
  textid: string;
  juan_seq: number;
  translation: TranslationSummary | null;
  status: string;
  rows: TranslationAlignedRow[];
}

export type TranslationSort = "textid" | "trans_date" | "source_date";

export interface TranslationSegmentHit {
  bundle_id: string;
  source_textid: string;
  juan_seq: number;
  corresp: string | null;
  text: string;
  source_text: string | null;
  language: string | null;
  title: string | null;
  responsibility: TranslationResponsibility[];
  date: string | null;
  is_ai: boolean;
}

export interface TranslationSearchFacets {
  language: SearchFacetValue[];
  category: SearchFacetValue[];
  date: SearchDateFacets;
  type: SearchFacetValue[];
}

export interface TranslationSearchResponse {
  hits: TranslationSegmentHit[];
  total: number;
  offset: number;
  limit: number;
  q: string;
  facets: TranslationSearchFacets;
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

export interface ServerWelcome {
  markdown: string;
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

export interface WorkspaceDeleteResult {
  path: string;
  commit?: unknown;
}

export interface AuthUser {
  login: string;
  name?: string | null;
  avatar_url?: string | null;
  html_url?: string | null;
  workspace: WorkspaceInfo;
  bluesky?: { handle: string; did: string } | null;
}

export interface AuthSession {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface CoreCollectionInfo {
  id: string;
  label: string;
  count: number;
}

export interface CoreCollectionsResponse {
  collections: CoreCollectionInfo[];
}

export interface CoreMatch {
  uuid: string;
  type: string;
  display_label: string;
  alt_labels: string[];
}

export interface CoreSuperEntryMatch {
  super_entry_uuid: string;
  orth: string;
  word_count: number;
}

export interface CoreListResponse {
  collection: string;
  total: number;
  offset: number;
  limit: number;
  matches: CoreMatch[];
  super_entries: CoreSuperEntryMatch[];
}

export interface CoreSuperEntryWord {
  uuid: string;
  display_label: string | null;
  concept: string | null;
  n: string | null;
}

export interface CoreSuperEntryExpansion {
  uuid: string;
  orth: string;
  words: CoreSuperEntryWord[];
}

export interface CoreRecordLink {
  target_uuid: string;
  target_type: string | null;
  target_collection: string | null;
  target_label: string | null;
  relation: string | null;
}

export interface CoreRecordResponse {
  uuid: string;
  type: string;
  collection: string;
  display_label: string;
  path: string;
  frontmatter: Record<string, unknown>;
  body_markdown: string;
  links: CoreRecordLink[];
}

export interface CoreSuperEntryByOrth {
  uuid: string;
  orth: string;
}

export interface CoreFullSense {
  uuid: string;
  body_number: number | null;
  pos: string | null;
  syn_func: string | null;
  sem_feat: string | null;
  def: string | null;
}

export interface CoreFullWord {
  uuid: string;
  display_label: string | null;
  concept: string | null;
  concept_uuid: string | null;
  pinyin: string | null;
  n: string | null;
  senses: CoreFullSense[];
}

export interface CoreSuperEntryFull {
  uuid: string;
  orth: string;
  words: CoreFullWord[];
}

export interface AnnotationBySenseLocation {
  text_id: string;
  seq: number;
  text_title: string | null;
  marker_id: string | null;
  offset: number | null;
  bucket: string | null;
  length: number | null;
  id: string | null;
  concept: string | null;
  concept_id: string | null;
  orth: string | null;
  pron: string | null;
  sense_def: string | null;
  note: string | null;
  translation_title: string | null;
  translation_text: string | null;
  resp: string | null;
  curation_state: string | null;
  context_left: string | null;
  context_match: string | null;
  context_right: string | null;
}

export interface AnnotationsBySenseResponse {
  sense_uuid: string;
  total: number;
  locations: AnnotationBySenseLocation[];
}

export interface AnnotationsBySenseCountsResponse {
  counts: Record<string, number>;
}

export interface CoreConceptWord {
  uuid: string;
  display_label: string | null;
  super_entry_uuid: string | null;
  super_entry_orth: string | null;
  n: string | null;
}

export interface CoreConceptWordsResponse {
  concept_uuid: string;
  words: CoreConceptWord[];
}

export interface CoreBacklinkItem {
  uuid: string;
  type: string;
  collection: string;
  display_label: string;
  relation: string | null;
}

export interface CoreBacklinkGroup {
  collection: string;
  type: string;
  total: number;
  items: CoreBacklinkItem[];
}

export interface CoreBacklinksResponse {
  uuid: string;
  total: number;
  groups: CoreBacklinkGroup[];
}
