/** Typed helpers for the service's HTTP API (see server/baja_studio/core/api.py). */

export type HealthState = "ok" | "degraded" | "offline" | "stopped";

export interface ModuleHealth {
  name: string;
  title: string;
  state: HealthState;
  message: string;
}

export interface SettingsSection {
  section: string;
  title: string;
}

export type SettingsValues = Record<string, unknown>;

export interface SettingsDocument {
  section: string;
  version: number;
  updated_at: string;
  values: SettingsValues;
}

/** The subset of JSON Schema that Pydantic produces for settings models. */
export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  $ref?: string;
  anyOf?: JsonSchemaProperty[];
  secret?: boolean;
  /** Example values; the first is shown greyed out in an empty text box. */
  examples?: unknown[];
  /** Edited on the module's own page (e.g. the agents list), so the form skips it. */
  managed_elsewhere?: boolean;
  /** The fields of a nested model (`type: "object"`). */
  properties?: Record<string, JsonSchemaProperty>;
}

export interface SettingsSchema {
  title?: string;
  properties: Record<string, JsonSchemaProperty>;
  required?: string[];
  $defs?: Record<string, JsonSchemaProperty>;
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  studio_module?: string;
  traceback?: string;
  [extra: string]: unknown;
}

export interface CommandLogEntry {
  ts: string;
  module: string;
  source: string;
  command: string;
  args: Record<string, unknown>;
  result: string;
}

/** Any non-2xx response; `detail` is FastAPI's error detail. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `request failed with status ${status}`);
  }
}

/** A save was refused because someone else saved first (HTTP 409). */
export class SettingsConflictError extends ApiError {
  constructor(
    readonly currentVersion: number,
    message: string,
  ) {
    super(409, message);
    this.message = message;
  }
}

/** One field problem reported by the server's validation (HTTP 422). */
export interface FieldError {
  field: string;
  message: string;
}

export class SettingsValidationError extends ApiError {
  constructor(readonly fieldErrors: FieldError[]) {
    super(422, fieldErrors);
    this.message = fieldErrors.map((error) => `${error.field}: ${error.message}`).join("; ");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail ?? body;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export function getHealth(): Promise<{
  status: string;
  version: string;
  modules: ModuleHealth[];
}> {
  return request("/api/health");
}

export function listSettingsSections(): Promise<SettingsSection[]> {
  return request("/api/settings");
}

export function getSettings(section: string): Promise<SettingsDocument> {
  return request(`/api/settings/${encodeURIComponent(section)}`);
}

export function getSettingsSchema(section: string): Promise<SettingsSchema> {
  return request(`/api/settings/${encodeURIComponent(section)}/schema`);
}

/**
 * Save settings edited from `version`.
 *
 * Throws `SettingsConflictError` if the server has a newer version and
 * `SettingsValidationError` if a value was rejected.
 */
export async function saveSettings(
  section: string,
  version: number,
  values: SettingsValues,
): Promise<SettingsDocument> {
  try {
    return await request(`/api/settings/${encodeURIComponent(section)}`, {
      method: "PUT",
      body: JSON.stringify({ version, values }),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      const detail = error.detail as { message: string; current_version: number };
      throw new SettingsConflictError(detail.current_version, detail.message);
    }
    if (error instanceof ApiError && error.status === 422 && Array.isArray(error.detail)) {
      const problems = error.detail as { loc: (string | number)[]; msg: string }[];
      throw new SettingsValidationError(
        problems.map((problem) => ({ field: problem.loc.join("."), message: problem.msg })),
      );
    }
    throw error;
  }
}

export function getRecentLogs(limit = 200): Promise<LogEntry[]> {
  return request(`/api/logs?limit=${limit}`);
}

export function getCommandLog(module?: string, limit = 50): Promise<CommandLogEntry[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (module) query.set("module", module);
  return request(`/api/commands?${query.toString()}`);
}
