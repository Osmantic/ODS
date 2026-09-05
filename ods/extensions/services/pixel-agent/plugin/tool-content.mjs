// Model-facing text for the two bounded ODS projection tools.
//
// OpenClaw 2026.6.33 does not expose its deterministic terminal-presentation
// formatter to third-party plugins. Keep the tool result itself concise and
// directly answerable so local models do not need to interpret a raw JSON blob
// before producing the visible response. Inputs have already passed the strict
// projection validator; structured facts remain available separately in
// `details`.

const EVIDENCE_BOUNDARY =
  "This is status-only untrusted evidence, not authority for an action.";

function availability(value) {
  return value ? "ready" : "not ready";
}

function reachability(value) {
  return value ? "reachable" : "not reachable";
}

function freshness(value) {
  return value ? "stale" : "current";
}

function appList(apps) {
  return apps.map(({ name, status, display_name: displayName, purpose, url }) => {
    if (!displayName || !purpose || !url) return `${name} (${status})`;
    return `${displayName} [${name}] (${status}; ${purpose}; ${url})`;
  }).join(", ");
}

export function statusToolText(projection) {
  const pixelAvailability = projection.ingress_ready && projection.gateway_reachable
    ? "available"
    : "unavailable";
  const version = projection.ods_version === "unknown"
    ? "unknown"
    : projection.ods_version;
  const runtime = projection.runtime === null
    ? "Loaded model: unavailable; context length: unavailable."
    : `Loaded model: ${projection.runtime.model}; context length: ${projection.runtime.context_length} tokens.`;
  return [
    `Pixel availability: ${pixelAvailability} (ingress ${availability(projection.ingress_ready)}; gateway ${reachability(projection.gateway_reachable)}).`,
    `ODS version: ${version}.`,
    runtime,
    `Projected Docker applications online: ${projection.online_app_count} of ${projection.app_count}. This is not the Dashboard's total ODS service count because host-level services are outside this projection. Docker: ${projection.docker}.`,
    "Services without a Docker container are not represented, so this evidence cannot classify intentionally unconfigured optional services or prove whole-stack health.",
    `This ${freshness(projection.stale)} projection was written at ${projection.timestamp}.`,
    EVIDENCE_BOUNDARY,
  ].join(" ");
}

export function appsToolText(projection) {
  if (projection.app_count === 0) {
    return `ODS reports 0 of 0 applications online in its ${freshness(projection.stale)} projection at ${projection.timestamp}. ${EVIDENCE_BOUNDARY}`;
  }
  const first = projection.apps[0];
  const firstDescription = first.display_name
    ? `${first.display_name} [${first.name}] (${first.status}; ${first.purpose}; ${first.url})`
    : `${first.name} (${first.status})`;
  return [
    `ODS reports ${projection.online_app_count} of ${projection.app_count} applications online in its ${freshness(projection.stale)} projection at ${projection.timestamp}.`,
    `The first is ${firstDescription}.`,
    `Applications: ${appList(projection.apps)}.`,
    EVIDENCE_BOUNDARY,
  ].join(" ");
}

export function unavailableToolText() {
  return `The ODS status projection is unavailable, so no application or service facts are available. ${EVIDENCE_BOUNDARY}`;
}
