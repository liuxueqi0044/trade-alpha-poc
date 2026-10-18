# Agent collaboration contract

This project follows an explicit model-role strategy.

## Roles

- **SOL / root agent** is the senior architect and final reviewer. It owns cross-module contracts, architecture decisions, acceptance interpretation, integration, security review, and final verification.
- **Terra and Luna agents** are implementation programmers. They implement bounded modules and tests against frozen specifications.
- If an implementation fails review or tests, SOL returns it to the implementation agent with concrete defects and acceptance IDs. If the defect remains unresolved, SOL takes over the fix.

## Working rules

1. Read the relevant specification under `../trade-alpha-poc-specs/` before editing.
2. Do not weaken P0 acceptance criteria or change cross-module schemas without an ADR approved by SOL.
3. Keep module edits inside the assigned package and test paths unless the task explicitly grants a shared file.
4. Raw evidence is immutable; candidate claims are not facts; point-in-time timestamps are mandatory.
5. All external writes/actions must be mocked in tests and require explicit authorization in implementation.
6. Use `apply_patch` for edits. Preserve other agents' concurrent work.
7. Report implemented acceptance IDs, commands run, failures, and unresolved risks.

## Module ownership for the first implementation wave

- M01: `src/trade_alpha/ingestion/`, `tests/m01/`
- M02: `src/trade_alpha/extraction/`, `tests/m02/`
- M03: `src/trade_alpha/knowledge/`, `tests/m03/`
- SOL shared/integration ownership: root configuration, `src/trade_alpha/contracts/`, `tests/integration/`, ADRs, final review.

## Module ownership for the second implementation wave

- M04 (Terra): `src/trade_alpha/storage/`, `tests/m04/`
- M05 (Luna): `src/trade_alpha/quality/`, `tests/m05/`, `dbt/`
- M06 (Terra): `src/trade_alpha/orchestration/`, `tests/m06/`, `docs/runbooks/`
- SOL shared/integration ownership remains unchanged. Implementers must not edit root dependency files.

## Module ownership for the third implementation wave

- M07 (Terra): `src/trade_alpha/research/`, `tests/m07/`
- M08 (Luna): `src/trade_alpha/ui/`, `tests/m08/`, `apps/streamlit/`
- M09 (Terra): `src/trade_alpha/experiments/`, `tests/m09/`
- SOL owns M10, all cross-wave integration, root configuration, reports, and final acceptance.

## Module ownership for the execution-plane wave

- EP-01 (Terra): `src/trade_alpha/execution/contracts/`, the Pint adapter, and `tests/ep01/`.
- EP-02 (Luna): `src/trade_alpha/execution/rfq/`, the local-intake adapter, and `tests/ep02/`.
- EP-03 (Terra or Luna as assigned by SOL): `src/trade_alpha/execution/specifications/`, the product-family profile, and `tests/ep03/`.
- SOL owns execution-plane specifications, dependencies, ADRs, cross-module integration tests, acceptance interpretation, and final review.
- A frozen product specification is internal readiness state, not authorization for an external action.

## Module ownership for the quote-readiness wave

- EP-07 (Terra): `src/trade_alpha/execution/costing/`, `tests/ep07/`, and bounded cost-policy fixtures.
- EP-08 (Luna): `src/trade_alpha/execution/commercial_risk/`, `tests/ep08/`, and bounded risk-policy fixtures.
- EP-09 and EP-10 (Terra): `src/trade_alpha/execution/operational_readiness/`, `src/trade_alpha/execution/customer_quotes/`, their dedicated adapters, and `tests/ep09/` / `tests/ep10/`.
- SOL owns M10 action extensions, shared configuration, EP-01～EP-10 integration, architecture/spec indexes, security review, and final acceptance.
- EP-07 cannot rank suppliers; EP-08 cannot issue legal conclusions; EP-09 approvals cannot authorize production actions; EP-10 has no real outbound capability.
