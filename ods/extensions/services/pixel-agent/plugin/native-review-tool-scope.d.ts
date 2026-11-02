/**
 * ODS compatibility extension to the pinned 2026.6.33 native finalize protocol.
 * These are structural overlay types, not an ambient augmentation or a claim
 * that stock OpenClaw implements this extension. The protected repair bundle
 * must acknowledge v1 in the actual native event before a caller uses it.
 */
export interface OdsNativeRetryToolScopeEventV1 {
  readonly nativeRetryToolsAllowVersion?: 1;
}

/** Trusted before_agent_run metadata, projected from live native custody. */
export interface OdsNativeRetryToolScopeAdmissionEventV1 {
  readonly nativeRetryToolsAllowState?: Readonly<{
    version: 1;
    ownerRunId: string;
    active: boolean;
    toolsAllow: readonly string[] | null;
  }>;
}

/**
 * Runtime validation requires 1–32 unique literal leaf names, each matching
 * /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/. Wildcards, groups and unknown/ambiguous
 * origins fail closed. This only removes existing authority.
 */
export type OdsNativeRetryToolsAllowV1 = readonly [string, ...string[]];

export interface OdsNativeFinalizeRetryV1 {
  instruction: string;
  idempotencyKey?: string;
  maxAttempts?: number;
  toolsAllow?: OdsNativeRetryToolsAllowV1;
}

export interface OdsNativeFinalizeResultV1 {
  action?: 'continue' | 'revise' | 'finalize';
  reason?: string;
  retry?: OdsNativeFinalizeRetryV1;
}
