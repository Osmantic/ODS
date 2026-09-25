import { createHash, randomBytes } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { createResearchBatch } from "./broker.mjs";
import { completeResearchQuery, failReservedResearchQuery, reserveResearchQuery } from "./ledger.mjs";
import { attachResearchRetrievals, retrieveResearchSource } from "./retrieval.mjs";
import { searchPublicSources } from "./search.mjs";

export class ResearchPipelineError extends Error {
  constructor(message, { code = "research-pipeline-failed", knownUsage = null, cause } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.code = code;
    this.knownUsage = knownUsage;
  }
}

function fail(message, options) {
  throw new ResearchPipelineError(message, options);
}

function fingerprint(error) {
  return createHash("sha256").update(canonical({
    name: error instanceof Error ? error.name : "NonError",
    code: typeof error?.code === "string" ? error.code.slice(0, 100) : "unknown",
  })).digest("hex");
}

function nextTime(clock, minimumExclusive) {
  const value = clock();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail("research pipeline clock is invalid", { code: "pipeline-clock" });
  return new Date(Math.max(value.getTime(), minimumExclusive + 1));
}

function zeroUsage() {
  return { searchRequests: 0, retrievalRequests: 0, sources: 0, networkBytes: 0, sourceBytes: 0, rejectedSources: 0 };
}

function addUsage(target, delta) {
  for (const key of Object.keys(target)) target[key] += delta[key] ?? 0;
  return target;
}

export async function runResearchQuery({
  stateRoot, queueRoot, objectRoot, query, plan, lease, claim, endpoint,
  maxSourcesToFetch = 5, maximumSourceBytes = plan?.research?.maxSourceBytes,
  timeoutMilliseconds = 90000, fetchImpl = globalThis.fetch,
  searchImpl = searchPublicSources, retrieveImpl = retrieveResearchSource,
  clock = () => new Date(), suffixes = {}, requestIds = [],
}) {
  if (
    !Number.isSafeInteger(maxSourcesToFetch) || maxSourcesToFetch < 1 || maxSourcesToFetch > 20
    || !Number.isSafeInteger(maximumSourceBytes) || maximumSourceBytes < 1024 || maximumSourceBytes > plan?.research?.maxSourceBytes
    || typeof clock !== "function"
  ) {
    fail("research pipeline configuration is invalid", { code: "pipeline-config" });
  }
  let reservation;
  let knownUsage = zeroUsage();
  try {
    const reserveNow = nextTime(clock, Date.parse(query.createdAt) - 1);
    reservation = await reserveResearchQuery({
      stateRoot, query, plan, lease, claim, now: reserveNow,
      suffix: suffixes.reserve ?? randomBytes(6).toString("hex"),
    });
    const previous = reservation.record.usage;
    const remainingSources = plan.research.maxSources - previous.sources;
    const remainingNetworkBytes = plan.budgets.maxNetworkBytes - previous.networkBytes;
    const remainingSourceBytes = plan.research.maxTotalSourceBytes - previous.sourceBytes;
    if (remainingSources < 1 || remainingNetworkBytes < 1 || remainingSourceBytes < 1024) {
      fail("research pipeline has no remaining source or byte budget", { code: "pipeline-budget", knownUsage });
    }
    const searched = await searchImpl({
      query, plan, lease, claim, endpoint, maximumNetworkBytes: remainingNetworkBytes,
      timeoutMilliseconds: Math.min(timeoutMilliseconds, 120000), fetchImpl,
    });
    addUsage(knownUsage, searched.usage);
    const metadataTime = nextTime(clock, Date.parse(reservation.record.createdAt));
    const metadata = createResearchBatch({
      query, plan, lease, claim, rawResults: searched.rawResults, adapter: searched.adapter,
      networkBytes: searched.networkBytes, resultLimit: Math.min(query.maxResults, remainingSources),
      now: metadataTime, suffix: suffixes.metadata ?? randomBytes(6).toString("hex"),
    });
    knownUsage.sources = metadata.sources.length;
    knownUsage.rejectedSources = metadata.usage.rejectedSources;
    if (metadata.sources.length === 0) fail("research query produced no policy-eligible public sources", { code: "no-public-sources", knownUsage });
    const selected = metadata.sources.slice(0, Math.min(maxSourcesToFetch, metadata.sources.length));
    const retrievals = [];
    let previousTime = metadataTime.getTime();
    for (let index = 0; index < selected.length; index += 1) {
      const networkLeft = remainingNetworkBytes - knownUsage.networkBytes;
      const sourceBytesLeft = remainingSourceBytes - knownUsage.sourceBytes;
      const maximumBytes = Math.min(maximumSourceBytes, networkLeft, sourceBytesLeft);
      if (maximumBytes < 1024) fail("research retrieval budget is exhausted", { code: "pipeline-budget", knownUsage });
      const retrievalNow = nextTime(clock, previousTime);
      let retrieved;
      try {
        retrieved = await retrieveImpl({
          stateRoot, queueRoot, objectRoot, query, batch: metadata, source: selected[index], plan, lease, claim,
          maximumBytes, timeoutMilliseconds, now: retrievalNow,
          requestId: requestIds[index] ?? randomBytes(16).toString("hex"),
          suffix: suffixes.retrieval?.[index] ?? randomBytes(6).toString("hex"),
        });
      } catch (error) {
        if (error?.knownUsage) {
          addUsage(knownUsage, error.knownUsage);
          throw new ResearchPipelineError("research source retrieval failed with exact accounting", { code: error.code, knownUsage, cause: error });
        }
        throw error;
      }
      retrievals.push(retrieved);
      addUsage(knownUsage, {
        retrievalRequests: retrieved.usage.retrievalRequests,
        networkBytes: retrieved.usage.networkBytes,
        sourceBytes: retrieved.usage.sourceBytes,
      });
      const observedAt = Date.parse(retrieved.observedAt ?? retrieved.retrieval.retrievedAt);
      if (!Number.isSafeInteger(observedAt) || observedAt < retrievalNow.getTime()) fail("research retrieval completion time is invalid", { code: "pipeline-clock", knownUsage });
      previousTime = Math.max(retrievalNow.getTime(), observedAt);
    }
    if (!retrievals.some((entry) => entry.retrieval.status === "fetched")) {
      fail("research query produced no retrievable public sources", { code: "no-public-sources", knownUsage });
    }
    const finalTime = nextTime(clock, previousTime);
    const batch = attachResearchRetrievals(metadata, retrievals, {
      now: finalTime, suffix: suffixes.final ?? randomBytes(6).toString("hex"),
    });
    const completionTime = nextTime(clock, finalTime.getTime());
    const completion = await completeResearchQuery({
      stateRoot, query, plan, lease, claim, batch, now: completionTime,
      suffix: suffixes.complete ?? randomBytes(6).toString("hex"),
    });
    return { batch, completion, retrievals };
  } catch (error) {
    if (!reservation) throw error;
    const failureTime = nextTime(clock, Date.parse(reservation.record.createdAt));
    const exactUsage = error instanceof ResearchPipelineError ? error.knownUsage : error?.knownUsage ?? null;
    try {
      await failReservedResearchQuery({
        stateRoot, plan, claim, failureFingerprintSha256: fingerprint(error), knownUsage: exactUsage,
        now: failureTime, suffix: suffixes.failure ?? randomBytes(6).toString("hex"),
      });
    } catch (closureError) {
      throw new ResearchPipelineError("research pipeline failed and its reservation could not be closed", {
        code: "reservation-closure", cause: new AggregateError([error, closureError]),
      });
    }
    throw error;
  }
}
