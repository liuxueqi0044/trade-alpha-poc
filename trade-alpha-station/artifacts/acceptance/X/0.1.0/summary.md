# Cross-module 0.1.0

- Command: `pytest tests/integration -q`
- Result: 5 passed in the final 118-test run.
- Paths: M01→M02→M03；M04→M05→M06；M07→M08→M09→M10；M10→M01/M04/export。
- Full production replay/chaos boundaries are recorded in `docs/acceptance-report.md`.
