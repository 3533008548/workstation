/**
 * TypeScript mirror of the workbench contracts.
 *
 * GENERATED -- do not hand-edit. Regenerate with:
 *   python scripts/gen_schema.py && npm run gen:types
 *
 * Source of truth is contracts/python/workstation_contracts.
 * The knowledge desktop (TS) and PPTAgent (TS) consume this file, which is
 * what lets them speak the same wire format as the Python base without either
 * side importing the other's code.
 */

export const CONTRACT_VERSION = "0.1.0" as const;

export type SourceType =
  | "paper" | "note" | "web" | "dataset" | "attachment" | "deck" | "generated";

export type Origin = "primary" | "derived";

export type Confidence = "high" | "medium" | "low" | "unknown";

export type FactKind =
  | "claim" | "metric" | "method" | "finding" | "definition" | "quote" | "hypothesis";

export type RunStatus =
  | "pending" | "running" | "waiting" | "succeeded" | "failed" | "cancelled" | "partial";

export type StepStatus =
  | "pending" | "running" | "succeeded" | "failed" | "skipped" | "cancelled";

export type StepKind = "llm" | "tool" | "retrieve" | "render" | "verify" | "write";

export type EventType =
  | "run.created" | "run.started" | "run.progress" | "run.completed"
  | "run.failed" | "run.cancelled" | "step.started" | "step.finished"
  | "artifact.produced" | "budget.warning" | "approval.required";

export type Priority = "interactive" | "normal" | "batch";

export type RuntimeKind = "inprocess" | "subprocess" | "http" | "bridge";

export type PermissionResource =
  | "llm" | "net" | "fs:primary" | "fs:derived" | "subprocess" | "vault:write";

export type ArtifactKind =
  | "document" | "deck" | "markdown" | "image" | "table" | "json" | "report";

export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
  "partial",
];

export type Id = string; // "<prefix>_<13 digit epoch ms>_<8 hex>"

export interface Locator {
  page?: number | null;
  section?: string | null;
  slide?: number | null;
  charStart?: number | null;
  charEnd?: number | null;
  lineStart?: number | null;
  lineEnd?: number | null;
  timestampS?: number | null;
  extra?: Record<string, unknown>;
}

export interface SourceRef {
  sourceId: Id;
  sourceType: SourceType;
  uri: string;
  title: string;
  origin: Origin;
  producer: string;
  locator?: Locator;
  contentHash?: string | null;
  language?: string | null;
  createdAt: string;
  updatedAt: string;
  meta?: Record<string, unknown>;
}

export interface Fact {
  factId: Id;
  text: string;
  kind: FactKind;
  sourceIds: Id[]; // REQUIRED, min 1
  confidence: Confidence;
  qualifiers?: Record<string, unknown>;
  evidence?: string | null;
  createdAt: string;
  meta?: Record<string, unknown>;
}

export interface FactSet {
  setId: Id;
  label: string;
  facts: Fact[];
  meta?: Record<string, unknown>;
}

export interface Usage {
  promptTokens: number;
  completionTokens: number;
  toolCalls: number;
  llmCalls: number;
  costCny: number;
  wallClockS: number;
  retries: number;
}

export interface Budget {
  maxTokens?: number | null;
  maxCostCny?: number | null;
  maxToolCalls?: number | null;
  maxWallClockS?: number | null;
  maxLlmCalls?: number | null;
}

export interface TaskOptions {
  priority: Priority;
  timeoutS?: number | null;
  budget: Budget;
  idempotencyKey?: string | null;
  dryRun: boolean;
  requireApproval: boolean;
  locale: string;
}

export interface TaskRequest {
  taskId: Id;
  skill: string;
  inputs: Record<string, unknown>;
  options: TaskOptions;
  parentRunId?: Id | null;
  requestedAt: string;
}

export interface Step {
  stepId: Id;
  name: string;
  kind: StepKind;
  status: StepStatus;
  startedAt?: string | null;
  endedAt?: string | null;
  usage: Usage;
  message?: string | null;
  error?: string | null;
  meta?: Record<string, unknown>;
}

export interface Artifact {
  artifactId: Id;
  kind: ArtifactKind;
  uri: string;
  mediaType: string;
  origin: Origin;
  producer: string;
  title: string;
  sizeBytes?: number | null;
  sourceIds: Id[];
  factIds: Id[];
  meta?: Record<string, unknown>;
}

export interface Run {
  runId: Id;
  taskId?: Id | null;
  skill: string;
  skillVersion: string;
  status: RunStatus;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  error?: string | null;
  options: TaskOptions;
  steps: Step[];
  artifacts: Artifact[];
  usage: Usage;
  sourceIds: Id[];
  parentRunId?: Id | null;
  checkpointRef?: string | null;
  idempotencyKey?: string | null;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
}

export interface RunEvent {
  eventId: Id;
  runId: Id;
  seq: number;
  ts: string;
  type: EventType;
  payload: Record<string, unknown>;
}

export interface Permission {
  resource: PermissionResource;
  scope: string;
  requiresConfirm: boolean;
  reason: string;
}

export interface RuntimeSpec {
  kind: RuntimeKind;
  entrypoint: string;
  transportOptions?: Record<string, unknown>;
  isolated: boolean;
  startupTimeoutS: number;
}

export interface SkillManifest {
  name: string;
  version: string;
  description: string;
  whenToUse: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  runtime: RuntimeSpec;
  permissions: Permission[];
  defaultBudget: Budget;
  tags: string[];
  deprecated: boolean;
  meta?: Record<string, unknown>;
}

const EVENT_TYPES: readonly string[] = [
  "run.created", "run.started", "run.progress", "run.completed",
  "run.failed", "run.cancelled", "step.started", "step.finished",
  "artifact.produced", "budget.warning", "approval.required",
];

/** Narrow an unknown SSE payload to a RunEvent without pulling in a validator. */
export function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.runId === "string" &&
    v.runId.startsWith("run_") &&
    typeof v.seq === "number" &&
    typeof v.type === "string" &&
    EVENT_TYPES.includes(v.type)
  );
}

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}
