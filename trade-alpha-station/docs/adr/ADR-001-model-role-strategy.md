# ADR-001: Model role and implementation delegation strategy

Status: Accepted  
Date: 2026-08-24

## Context

The system is large and long-lived. Architectural context must remain coherent while routine implementation should use lower-cost coding agents where appropriate.

## Decision

- SOL acts as senior architect, integration owner, reviewer, and final escalation path.
- Terra and Luna act as implementation programmers for bounded modules.
- Every delegated task references frozen module specifications and acceptance IDs.
- SOL reviews code and test evidence. Defects are returned to the implementation agent first; unresolved defects are taken over by SOL.
- Architecture and P0 acceptance changes require an ADR and cannot be made silently by an implementation agent.

## Consequences

- More context remains concentrated in the architect role.
- Implementers can work in parallel on non-overlapping directories.
- Integration review and acceptance evidence become mandatory handoff steps.
- Model assignment is a delivery policy, not a claim that generated code is trusted without tests.

