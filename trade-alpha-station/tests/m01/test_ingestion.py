from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_alpha.ingestion import (
    EvidenceStore,
    ImmutableEvidenceError,
    IngestionService,
    PageResponse,
    SourceRecord,
    SourceRegistry,
)
from trade_alpha.ingestion.models import RatePolicy


def source(*, status: str = "enabled", commercial_use: str | None = "approved") -> SourceRecord:
    return SourceRecord(
        source_id="fixture_api",
        owner="Fixture",
        source_type="api",
        base_url="https://fixture.invalid/api",
        access_method="api",
        rate_policy=RatePolicy(max_requests_per_window=100, window_seconds=1),
        robots_policy="not_applicable",
        terms_url="https://fixture.invalid/terms",
        data_license="fixture-license",
        commercial_use=commercial_use,
        redistribution="approved",
        retention_policy="retain_raw",
        allowed_content_types=("application/json",),
        parser_profile="fixture",
        status=status,  # type: ignore[arg-type]
    )


def service(tmp_path: Path, *, record: SourceRecord | None = None) -> IngestionService:
    return IngestionService(
        SourceRegistry([record or source()]), EvidenceStore(tmp_path), code_sha="test"
    )


def page(number: int, next_cursor: str | None, total: int = 3) -> PageResponse:
    return PageResponse(
        url=f"https://fixture.invalid/api?cursor={number}&api_key=not-for-manifest",
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps({"page": number}).encode(),
        next_cursor=next_cursor,
        declared_total_pages=total,
    )


def test_m01_ac_001_paginated_fixture_manifest_matches_declared_count(tmp_path: Path) -> None:
    pages = {None: page(1, "p2"), "p2": page(2, "p3"), "p3": page(3, None)}
    batch = service(tmp_path).run_paginated(
        "fixture_api", lambda cursor, _: pages[cursor], connector_version="1"
    )
    assert batch.status == "complete"
    assert len(batch.retrieval_ids) == 3
    assert len(batch.completed_page_numbers) == batch.expected_total_pages == 3


def test_m01_ac_002_idempotent_content_but_new_retrieval(tmp_path: Path) -> None:
    runner = service(tmp_path)
    response = page(1, None, 1)
    first = runner.run_paginated("fixture_api", lambda *_: response, connector_version="1")
    second = runner.run_paginated("fixture_api", lambda *_: response, connector_version="1")
    assert len(first.retrieval_ids) == len(second.retrieval_ids) == 1
    artifact_catalog = list((tmp_path / "manifests" / "artifacts").glob("*.json"))
    assert len(artifact_catalog) == 1


def test_m01_ac_003_resume_from_batch_checkpoint_without_early_cursor(tmp_path: Path) -> None:
    attempts = {"p2": 0}

    def flaky(cursor: str | None, _: int) -> PageResponse:
        if cursor is None:
            return page(1, "p2")
        attempts["p2"] += 1
        if attempts["p2"] == 1:
            raise RuntimeError("network token=must-not-leak")
        return page(2, None, 2)

    runner = service(tmp_path)
    failed = runner.run_paginated("fixture_api", flaky, connector_version="1")
    assert failed.status == "incomplete"
    assert runner.store.get_checkpoint("fixture_api") is None
    complete = runner.run_paginated(
        "fixture_api", flaky, connector_version="1", batch_id=failed.batch_id
    )
    assert complete.status == "complete"
    assert complete.completed_page_numbers == [1, 2]
    # The failed attempt is retained alongside the two successful page retrievals.
    assert len(complete.retrieval_ids) == 3


def test_complete_page_traversal_never_becomes_the_next_run_cursor(tmp_path: Path) -> None:
    calls: list[tuple[str | None, int]] = []
    pages = {None: page(1, "p2"), "p2": page(2, "p3"), "p3": page(3, None)}

    def fetch(cursor: str | None, page_number: int) -> PageResponse:
        calls.append((cursor, page_number))
        return pages[cursor]

    runner = service(tmp_path)
    assert runner.run_paginated("fixture_api", fetch, connector_version="1").status == "complete"
    assert runner.store.get_checkpoint("fixture_api") is None
    calls.clear()
    assert runner.run_paginated("fixture_api", fetch, connector_version="1").status == "complete"
    assert calls == [(None, 1), ("p2", 2), ("p3", 3)]


def test_m01_ac_004_versions_and_dedupe(tmp_path: Path) -> None:
    runner = service(tmp_path)
    payloads = [b'{"v":"A"}', b'{"v":"A"}', b'{"v":"B"}']
    batches = [
        runner.run_paginated(
            "fixture_api",
            lambda *_args, payload=payload: PageResponse(
                url="https://fixture.invalid/current",
                status_code=200,
                headers={"content-type": "application/json"},
                content=payload,
                declared_total_pages=1,
            ),
            connector_version="1",
        )
        for payload in payloads
    ]
    assert len(list((tmp_path / "manifests" / "artifacts").glob("*.json"))) == 2
    lines = list((tmp_path / "manifests" / "retrievals").rglob("*.jsonl"))
    values = [json.loads(line) for path in lines for line in path.read_text().splitlines()]
    assert len(values) == 3
    assert values[-1]["supersedes_artifact_id"] == values[0]["artifact_id"]
    assert all(batch.status == "complete" for batch in batches)


def test_m01_ac_005_evidence_cannot_be_overwritten(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    path = store.write_immutable("raw/fixture/one.bin", b"one")
    before = path.read_bytes()
    with pytest.raises(ImmutableEvidenceError):
        store.write_immutable("raw/fixture/one.bin", b"two")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "content, mime",
    [
        (b"<html>maintenance</html>", "application/json"),
        (b'{"cut":', "application/json"),
        (b'{"ok":true}', "text/html"),
    ],
)
def test_m01_ac_006_invalid_payload_is_quarantined(
    tmp_path: Path, content: bytes, mime: str
) -> None:
    runner = service(tmp_path)
    batch = runner.run_paginated(
        "fixture_api",
        lambda *_: PageResponse(
            url="https://fixture.invalid/x",
            status_code=200,
            headers={"content-type": mime},
            content=content,
        ),
        connector_version="1",
    )
    assert batch.status == "incomplete"
    assert list((tmp_path / "quarantine").rglob("*.jsonl"))
    events = [
        json.loads(line)
        for path in (tmp_path / "events").rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert [event["event_type"] for event in events].count("retrieval.failed") == 1


def test_m01_ac_007_and_012_policy_or_disabled_prevents_request(tmp_path: Path) -> None:
    called = False

    def fetch(*_: object) -> PageResponse:
        nonlocal called
        called = True
        raise AssertionError("must not request")

    runner = service(tmp_path)
    successful = runner.run_paginated(
        "fixture_api", lambda *_: page(1, None, 1), connector_version="1"
    )
    assert successful.status == "complete"
    raw_before_disable = next((tmp_path / "raw").rglob("*.*"))
    blocked = service(tmp_path, record=source(commercial_use=None)).run_paginated(
        "fixture_api", fetch, connector_version="1"
    )
    assert blocked.status == "blocked_policy"
    assert not called
    disabled = service(tmp_path, record=source(status="disabled")).run_paginated(
        "fixture_api", fetch, connector_version="1"
    )
    assert disabled.status == "blocked_policy"
    assert not called
    assert raw_before_disable.read_bytes() == b'{"page": 1}'


def test_304_without_a_prior_artifact_is_incomplete(tmp_path: Path) -> None:
    batch = service(tmp_path).run_paginated(
        "fixture_api",
        lambda *_: PageResponse(url="https://fixture.invalid/missing", status_code=304),
        connector_version="1",
    )
    assert batch.status == "incomplete"
    assert batch.error == "not_modified_without_prior_artifact"
    assert len(batch.retrieval_ids) == 1
    assert list((tmp_path / "quarantine").rglob("*.jsonl"))


@pytest.mark.parametrize("status", [429, 503, 404])
def test_error_responses_are_represented_in_the_batch_manifest(tmp_path: Path, status: int) -> None:
    batch = service(tmp_path).run_paginated(
        "fixture_api",
        lambda *_: PageResponse(url="https://fixture.invalid/error", status_code=status),
        connector_version="1",
    )
    assert len(batch.retrieval_ids) == 1


def test_m01_ac_008_secrets_are_absent_from_persisted_evidence(tmp_path: Path) -> None:
    runner = service(tmp_path)
    runner.run_paginated(
        "fixture_api",
        lambda *_: PageResponse(
            url="https://fixture.invalid/x?token=TOP_SECRET&n=1",
            status_code=200,
            headers={"content-type": "application/json", "authorization": "Bearer TOP_SECRET"},
            content=b'{"ok":true}',
            request_body={"api_key": "TOP_SECRET"},
        ),
        connector_version="1",
    )
    all_text = "\n".join(
        path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file()
    )
    assert "TOP_SECRET" not in all_text
