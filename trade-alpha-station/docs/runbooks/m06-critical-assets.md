# M06 critical asset recovery runbook

This runbook applies to the deterministic reference executor and its Dagster
asset declarations. Do not write gold data outside the orchestrator.

## Common failures

- `TransientFailure` represents network, 429, and 5xx failures; the executor retries it.
- `SchemaFailure`, incomplete input, failed quality, raw hash corruption, and unauthorized actions
  are terminal until the input/policy is repaired.

## Automatic retry

Only pure compute and idempotent retrieval writes may retry. The reference policy retries a
`TransientFailure` at most two times and never retries a schema failure.

## Safe rerun

Resume the exact asset/partition/configuration after verifying its checkpoint. A successful
publication is idempotent for the same asset, partition, and configuration hash.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/m06 -q
```

## Input completeness

Inspect the upstream run manifests: every input needs a snapshot ID/hash, `complete=true`, and a
quality status other than `failed`. A blocked upstream must never be bypassed for a gold run.

## Rollback publication alias

Do not delete snapshots. Point the publication alias to the prior successful manifest/output hash,
record the incident, and schedule a partition-scoped backfill.

## Rebuild lineage

For source corrections, start at the affected source×date or reporter×period partition and traverse
only downstream partitions. For entity-link revocation, use the recorded exact feature/signal
partitions from `LineageIndex.entity_revocation_plan`.

## Escalate to human approval

Escalate Critical raw-hash, future-data-leak, unauthorized-action, or policy events immediately.
Escalate Error schema/contract failures after creating the issue. Warning freshness can be labelled
as degraded only under an approved policy.

## Asset inventory

### `source_registry`

Validate source policy and snapshot identity before rerun.

### `raw_trade_snapshots`

Validate retrieval fingerprints and raw hashes before rerun.

### `raw_web_snapshots`

Validate retrieval fingerprints and raw hashes before rerun.

### `trade_observations_pit`

Resume from the reporter×period checkpoint; validate temporal completeness.

### `document_blocks`

Resume the artifact×profile checkpoint; validate extraction quality.

### `candidate_claims`

Verify source blocks remain immutable before rerun.

### `resolved_entities`

Use the exact resolution version and record revocation impacts before rerun.

### `feature_inputs`

Backfill only the affected as-of×feature-version partitions.

### `signals`

Backfill only the affected as-of×signal-version partitions.

### `opportunity_snapshot`

Publish only after passed feature/signal quality gates.

### `research_publication`

Use the prior successful alias for rollback; never bypass quality gates.

### `experiment_outcomes`

Resume only the failed experiment ID×fold.

### `evaluation`

Rebuild from the evaluation-fold input manifest.
