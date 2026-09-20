export interface StagedFile {
  id: string;
  name: string;
  size: number;
  staged_at: string;
  path: string;
}

export interface ApiProfile {
  id: number;
  name: string;
  base_url: string;
  model: string;
  reasoning: string | null;
  thinking: string | null;
  is_default: boolean;
  has_key: boolean;
}

export interface ConnectionTestResult {
  ok: boolean;
  category: string;
  message: string;
  detail?: string;
  profile_id?: number;
  profile_name?: string;
}

export interface ParamSpec {
  key: string;
  group: "common" | "advanced";
  label: string;
  type: "str" | "int" | "float" | "bool" | "pages" | "glossary";
  default: unknown;
  hint?: string;
}

export interface ParamsSchema {
  groups: { id: string; label: string }[];
  items: ParamSpec[];
  defaults: Record<string, unknown>;
}

export interface ValidationResponse {
  ok: boolean;
  errors: Record<string, string>;
  params: Record<string, unknown>;
  engine_fields: Record<string, unknown>;
}

export type TaskStatus =
  | "queued"
  | "preparing"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface TaskOutput {
  kind: "mono" | "dual" | "glossary" | "log";
  path: string;
  size: number;
  exists: boolean;
}

export interface TaskEvent {
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  ts: string | null;
}

export interface TaskInfo {
  id: number;
  status: TaskStatus;
  input_name: string;
  api_profile_id: number | null;
  glossary_version_id: number | null;
  engine_version: string | null;
  stage: string | null;
  stage_label: string | null;
  progress: number | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  params: Record<string, unknown>;
  outputs: TaskOutput[];
  output_dir: string;
  log_dir: string;
  events?: TaskEvent[];
}

export interface Preset {
  id: number;
  name: string;
  params: Record<string, unknown>;
  created_at: string | null;
}
