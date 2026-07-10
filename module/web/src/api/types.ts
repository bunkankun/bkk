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

export type UserTextFormat = "krp" | "tls" | "cbeta";

export interface UserTextSourceFile {
  name: string;
  content: string;
}

export interface UserTextFinding {
  rule_id: string;
  severity: "error" | "warning";
  path: string;
  message: string;
}

export interface UserTextPreview {
  preview_token: string;
  format: UserTextFormat;
  detected_text_id?: string | null;
  suggested_text_id: string;
  title?: string | null;
  source_files: string[];
  substitution_count: number;
  findings: UserTextFinding[];
  first_seq: number;
}

export interface UserTextCreateResult {
  text_id: string;
  title: string;
  repository: string;
  repository_url?: string | null;
  commit_sha: string;
  first_seq: number;
  substitution_count: number;
  findings: UserTextFinding[];
  index_status: "pending" | "indexing" | "ready" | "failed";
}

export interface UserTextListItem {
  text_id: string;
  title: string;
  index_status: "pending" | "indexing" | "ready" | "failed";
  sync_status: "ready" | "registry-error" | "failed" | "syncing";
  repository?: string;
  repository_url?: string | null;
  commit_sha?: string;
  index_error?: string;
  sync_error?: string;
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

export interface BundleEditBucket {
  text: string;
  markers: JuanMarker[];
}

export interface BundleEditDocument {
  repository: string;
  branch: string;
  base_commit_sha: string;
  seq: number;
  buckets: Partial<Record<"front" | "body" | "back", BundleEditBucket>>;
  toc_marker_ids: string[];
  marker_id_context: {
    edition: string;
    juan_label: string;
  };
}

export interface BundleTextSplice {
  start: number;
  delete_count: number;
  insert: string;
}

export interface BundleEditSaveRequest {
  base_commit_sha: string;
  bucket: "front" | "body" | "back";
  text: string;
  markers: JuanMarker[];
  text_splices: BundleTextSplice[];
  renamed_marker_ids: Record<string, string>;
  acknowledge_toc_deletions: boolean;
  unresolved_marker_indexes: number[];
  message?: string;
}

export interface BundleEditSaveResponse {
  kind: "commit" | "pull_request";
  commit_sha: string;
  url: string;
  pull_request_number?: number | null;
  removed_toc_marker_ids: string[];
}

export interface BundleMarkerIdAllocationRequest {
  base_commit_sha: string;
  bucket: "front" | "body" | "back";
  marker_types: string[];
  occupied_ids: string[];
}

export interface BundleMarkerIdAllocationResponse {
  ids: string[];
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
  def_text?: string;
  usage?: Record<string, unknown>;
  // Pre-resolved syn/sem labels from the core index. Present on annotations
  // served by the API; absent on annotations originated client-side. When
  // present, the UI can render the triple without any per-card fetches.
  syntactic_function_label?: string | null;
  semantic_feature_label?: string | null;
  // Legacy fields preserved for annotations created before the bkk-core overhaul.
  syn_func?: string;
  sem_feat?: string;
  def?: string;
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
  // author DID — lets the UI decide who may delete or self-reject.
  did?: string;
  // at-URI for bsky-native records; absent for legacy/synth.
  uri?: string;
  // resolved curation state; absent when the default "proposed".
  curation_state?: "proposed" | "accepted" | "rejected" | "superseded";
}

// Annotation write path (Bluesky).
export interface BlueskyStatus {
  handle: string | null;
  did: string | null;
  avatar_url?: string | null;
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

// Shared response shape across all three lexicons.
export type PostResponse = AnnotationPostResponse;

export interface StrongRef {
  uri: string;
  cid: string;
}

export interface CommentPostRequest {
  text_id: string;
  // Exactly one of `anchor` or `parent` must be present. `edition` is
  // required when `anchor` is set. Enforced server-side; the SPA should
  // surface a friendly error if the user manages to submit both.
  edition?: string | null;
  anchor?: AnnotationAnchor | null;
  parent?: StrongRef | null;
  root?: StrongRef | null;
  body: string;
  lang?: string;
  supersedes?: string | null;
}

export interface TranslationPostRequest {
  text_id: string;
  edition: string;
  anchor: AnnotationAnchor;
  translation_id: string;
  text: string;
  lang: string;
  title?: string | null;
  note?: string | null;
  supersedes?: string | null;
}

// Live feed of BKK records on Bluesky. The shape is a union discriminated
// by `kind`; only the fields relevant to each kind are populated.
export interface Contribution {
  kind: "annotation" | "comment" | "translation";
  did: string;
  cid: string;
  uri: string;
  text_id: string;
  created_at?: string | null;
  time_us: number;

  // Anchor (annotation + translation always; comment when not a reply).
  edition?: string | null;
  marker_id?: string | null;
  offset?: number | null;
  length?: number | null;
  end_marker_id?: string | null;
  end_length?: number | null;

  // Annotation-only.
  payload?: {
    concept?: string;
    concept_id?: string;
    form?: AnnotationForm;
    sense?: AnnotationSense;
    translation?: AnnotationTranslation;
    metadata?: Record<string, unknown>;
  };
  source_role?: string | null;

  // Comment-only.
  body?: string | null;
  parent?: StrongRef | null;

  // Translation-only.
  translation_id?: string | null;
  text?: string | null;

  // Shared between comment + translation.
  lang?: string | null;

  // Server-enriched location fields (resolved at read-time from the
  // marker_id). Null when the bundle is unknown or the marker can't be
  // located.
  title?: string | null;
  juan_seq?: number | null;
  bucket?: string | null;
  master_offset?: number | null;

  // Server-side curation gate (proposed / accepted / rejected / superseded).
  curation_state?: string | null;
  rating?: Rating | null;

  // Author profile resolved from Bluesky AppView.
  handle?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
}

export interface ContributionsResponse {
  items: Contribution[];
  truncated: boolean;
}

export type Rating = 0 | 1 | 2;

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

export interface TrigramExtension {
  gram: string;
  count: number;
}

export interface SearchOverview {
  approximate: boolean;
  threshold: number;
  trigram_left: TrigramExtension[];
  trigram_right: TrigramExtension[];
  kwic_filters_ignored: boolean;
}

export interface SearchResponse {
  query: string;
  query_mode: "literal" | "regex" | "near" | "not";
  total: number;
  offset: number;
  limit: number;
  sort: SearchSort;
  facets?: SearchFacets;
  hits: SearchHit[];
  overview?: SearchOverview | null;
}

export interface BundleSearchResponse {
  query: string;
  total: number;
  capped: boolean;
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

export type ParallelBucket = "front" | "body" | "back" | "all";
export type ParallelSort = "frequency" | "length";

// One diff operation against the cluster representative.
//   ["=", n]              n consecutive matching characters
//   ["s", rep_ch, occ_ch] substitution
//   ["i", occ_ch]         insertion (occurrence only)
//   ["d", rep_ch]         deletion (representative only)
export type DiffOp =
  | ["=", number]
  | ["s", string, string]
  | ["i", string]
  | ["d", string];

export interface ParallelLocation {
  textid: string;
  juan_seq: number;
  bucket: string;
  bucket_id: number;
  start: number;
  end: number;
  toc_label: string | null;
  left: string;
  right: string;
  edit_distance: number;
  text?: string;
  diff?: DiffOp[];
}

export interface ParallelCluster {
  cluster_id: string;
  length: number;
  occurrence_count: number;
  text: string;
  locations: ParallelLocation[];
  representative_edits: number;
}

export interface ParallelSearchResponse {
  query: string;
  bucket: ParallelBucket;
  min_length: number;
  min_occurrences: number;
  max_edits: number;
  sort: ParallelSort;
  total: number;
  offset: number;
  limit: number;
  clusters: ParallelCluster[];
}

export interface JuanParallelLocation {
  id: string;
  source: string;
  local_bucket: "front" | "body" | "back";
  local_offset: number;
  local_length: number;
  local_text?: string;
  local_gap?: number | null;
  remote_gap?: number | null;
  textid: string;
  juan_seq: number;
  bucket: "front" | "body" | "back";
  offset: number;
  length: number;
  toc_label: string | null;
  title: string | null;
  edit_distance: number;
  left: string;
  text: string;
  right: string;
  diff?: DiffOp[];
  available: boolean;
}

export interface JuanParallelRemoteText {
  textid: string;
  title: string | null;
  count: number;
  overlap_length: number;
}

export interface JuanParallelsResponse {
  textid: string;
  juan_seq: number;
  source_title: string | null;
  source_char_count: number;
  sort: "local" | "remote";
  remote_textid: string | null;
  total: number;
  offset: number;
  limit: number;
  available_min_length: number;
  available_max_length: number;
  remote_texts: JuanParallelRemoteText[];
  locations: JuanParallelLocation[];
}

export interface JuanParallelsStatus {
  textid: string;
  juan_seq: number;
  has_assets: boolean;
  has_parallels: boolean;
  sources: Array<"corpus" | "bundle">;
  can_generate: boolean;
}

export interface JuanParallelsGeneration {
  textid: string;
  juan_seq: number;
  generated: boolean;
  has_parallels: boolean;
  clusters: number;
  markers: number;
  files: number;
  message: string;
}

export interface JuanParallelsGenerationParams {
  bucket: "front" | "body" | "back" | "all";
  minLength: number;
  maxLength: number | null;
  minOccurrences: number;
  maxPostings: number;
  maxEdits: number;
  context: number;
  includeContained: boolean;
}

// Server identity
export interface ServerInfo {
  service?: string;
  version?: string;
  corpus_root?: string;
  index_path?: string;
  catalog_path?: string;
  upstream_repo?: string | null;
  bluesky_enabled?: boolean;
  parallels_enabled?: boolean;
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
  is_admin: boolean;
  is_editor: boolean;
  bluesky?: { handle: string; did: string } | null;
}

export interface AdminJob {
  id: string;
  kind: string;
  target?: string | null;
  status: "pending" | "running" | "success" | "error";
  started_at: number | null;
  finished_at: number | null;
  result: unknown;
  error: string | null;
}

export interface AdminInfoIndex {
  path: string;
  built: boolean;
  size_bytes?: number;
  schema_version?: number;
  schema_current?: number;
  schema_ok?: boolean;
  counts?: Record<string, number | null>;
  voices?: string[];
  per_bundle_indices_checked?: number;
  per_bundle_indices_stale?: number;
  error?: string;
}

export interface AdminInfoCatalog {
  path: string;
  built: boolean;
  size_bytes?: number;
  schema_version?: number;
  schema_current?: number;
  schema_ok?: boolean;
  counts?: Record<string, number | null>;
  date_min?: number;
  date_max?: number;
  source_csv?: string;
  error?: string;
}

export interface AdminInfoCorpus {
  path: string;
  exists: boolean;
  bundle_count: number;
  by_section: Record<string, number>;
}

export interface AdminInfoExtras {
  path: string;
  built: boolean;
}

export interface AdminInfoCore {
  path: string;
  built: boolean;
  root: string | null;
  upstream_repo: string | null;
  pr_base: string | null;
  editing_enabled: boolean;
}

export interface AdminInfoSource {
  path: string;
  branch: string;
  is_git: boolean;
}

export interface AdminInfoConfig {
  files: string[];
  sections: Record<string, Record<string, string>>;
}

// Duplications editor (admin)
export interface DuplicationRowSummary {
  id: number;
  textid_a: string;
  juan_seq_a: number;
  bucket_a: string;
  textid_b: string;
  juan_seq_b: number;
  bucket_b: string;
  chars_a: number;
  chars_b: number;
  juan_length_a: number;
  juan_length_b: number;
  coverage_a: number;
  coverage_b: number;
  longest_span: number;
  cluster_count: number;
  intra_juan: boolean;
  action: string | null;
  action_actor: string | null;
  action_at: string | null;
}

export interface DuplicationRowFull extends DuplicationRowSummary {
  longest_a: [number, number];
  longest_b: [number, number];
  spans_a: [number, number][];
  spans_b: [number, number][];
}

export interface DuplicationListResponse {
  total: number;
  offset: number;
  limit: number;
  returned: number;
  rows: DuplicationRowSummary[];
}

export interface DuplicationSnippet {
  offset: number;
  end: number;
  text: string;
  markers: JuanMarker[];
}

export interface DuplicationSide {
  textid: string;
  juan_seq: number;
  bucket: string;
  bucket_length: number;
  longest: [number, number];
  head: DuplicationSnippet;
  tail: DuplicationSnippet;
}

export interface DuplicationDetailResponse {
  row: DuplicationRowFull;
  sides: { a: DuplicationSide; b: DuplicationSide };
}

export type DuplicationAction =
  | "keep"
  | "delete_a_juan"
  | "delete_b_juan"
  | "delete_a_span"
  | "delete_b_span"
  | "delete_span";

export interface AdminInfoResponse {
  server_version: string;
  corpus: AdminInfoCorpus;
  index: AdminInfoIndex;
  catalog: AdminInfoCatalog;
  core: AdminInfoCore | null;
  source: AdminInfoSource | null;
  annotations: AdminInfoExtras | null;
  config: AdminInfoConfig;
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

export interface CoreWordRelationRelType {
  rel_type: string;
  count: number;
}

export interface CoreWordRelationRelTypesResponse {
  rel_types: CoreWordRelationRelType[];
}

export interface CoreJumpTargetResponse {
  text_id: string;
  seq: number;
  bucket: string;
  offset: number;
  length: number;
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
  data: Record<string, unknown>;
  links: CoreRecordLink[];
}

export interface CoreSuperEntryByOrth {
  uuid: string;
  orth: string;
}

export interface CoreEditExtraFile {
  path: string;
  data: Record<string, unknown> | null;
}

export interface CoreEditRequest {
  data: Record<string, unknown>;
  message?: string;
  extra_files?: CoreEditExtraFile[];
}

export interface CoreEditExtraFileResult {
  path: string;
  commit_sha: string;
  deleted: boolean;
}

export interface CoreEditResponse {
  commit_sha: string;
  commit_url: string;
  data: Record<string, unknown>;
  extras: CoreEditExtraFileResult[];
}

export interface CoreDeleteRequest {
  message?: string;
}

export interface CoreDeleteResponse {
  commit_sha: string;
  commit_url: string;
}

export interface CoreFullSense {
  uuid: string;
  sense_ord: number | null;
  n: string | null;
  pos: string | null;
  def_text: string | null;
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
  syntactic_function_label?: string | null;
  semantic_feature_label?: string | null;
  note: string | null;
  translation_title: string | null;
  translation_text: string | null;
  resp: string | null;
  curation_state: string | null;
  rating: Rating;
  uri: string | null;
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

export interface AnnotationsByRhetoricalDeviceResponse {
  rhet_dev_uuid: string;
  total: number;
  locations: AnnotationBySenseLocation[];
}

export interface AnnotationsByRhetoricalDeviceCountsResponse {
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

export interface CoreSenseUnderCharRow {
  uuid: string;
  word_uuid: string;
  super_entry_uuid: string | null;
  super_entry_orth: string | null;
  def_text: string | null;
  pos: string | null;
  n: string | null;
  syntactic_function_labels: string | null;
  semantic_feature_labels: string | null;
}

export interface CoreSensesUnderCharResponse {
  concept_uuid: string;
  orth: string;
  senses: CoreSenseUnderCharRow[];
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

export interface CoreLintDiagnostic {
  severity: "error" | "warning";
  code: string;
  message: string;
  start: number | null;
  end: number | null;
}

export interface CoreLintItem {
  uuid: string;
  collection: string;
  path: string;
  label: string;
  diagnostic: CoreLintDiagnostic;
}

export interface SyntacticFunctionLintResponse {
  record_count: number;
  distinct_label_count: number;
  error_count: number;
  warning_count: number;
  items: CoreLintItem[];
}

export interface SyntacticFunctionUsageItem {
  uuid: string;
  label: string;
  sense_count: number;
  attestation_count: number;
}

export interface SyntacticFunctionUsageResponse {
  record_count: number;
  unused_count: number;
  items: SyntacticFunctionUsageItem[];
}
