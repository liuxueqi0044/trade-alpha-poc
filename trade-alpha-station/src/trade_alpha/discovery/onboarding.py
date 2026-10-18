"""Generate disabled connector drafts, then promote only after M10 and Golden gates."""

from __future__ import annotations

import re

from trade_alpha.governance import DataLicense, SourceGovernance
from trade_alpha.ingestion.models import RatePolicy, SourceRecord

from .models import (
    CandidateState,
    ConnectorDraft,
    GoldenValidation,
    OnboardingBundle,
    PolicyReview,
    SourceCandidate,
)


def _source_id(candidate: SourceCandidate) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", candidate.title.casefold()).strip("-")[:32]
    suffix = candidate.candidate_id.rsplit("-", 1)[-1][:10]
    return f"{readable or 'source'}-{suffix}"


def _content_types(candidate: SourceCandidate) -> tuple[str, ...]:
    mapping = {
        "api": "application/json",
        "json": "application/json",
        "jsonl": "application/x-ndjson",
        "openapi": "application/json",
        "csv": "text/csv",
        "xml": "application/xml",
        "parquet": "application/vnd.apache.parquet",
        "pdf": "application/pdf",
    }
    values = {
        item.media_type or mapping.get(item.format, "application/octet-stream")
        for item in candidate.distributions
    }
    return tuple(sorted(values))


def connector_draft(candidate: SourceCandidate) -> ConnectorDraft:
    formats = {item.format for item in candidate.distributions}
    if "openapi" in formats or any(item.openapi_url for item in candidate.distributions):
        connector_type = "dlt-openapi"
    elif formats & {"api", "json", "jsonl"}:
        connector_type = "dlt-rest-api"
    else:
        connector_type = "m01-download"
    config = {
        "candidate_id": candidate.candidate_id,
        "base_url": str(candidate.canonical_url),
        "distribution_urls": [str(item.url) for item in candidate.distributions],
        "auth_class": candidate.auth_class,
        "secret_ref": None,
        "formal_schedule": False,
    }
    return ConnectorDraft(
        candidate_id=candidate.candidate_id,
        connector_type=connector_type,
        config=config,
        enabled=False,
    )


def draft_source_record(candidate: SourceCandidate, *, owner: str) -> SourceRecord:
    is_api = any(
        item.format in {"api", "json", "jsonl", "openapi"}
        for item in candidate.distributions
    )
    return SourceRecord(
        source_id=_source_id(candidate),
        owner=owner,
        source_type="api" if is_api else "download",
        base_url=candidate.canonical_url,
        access_method="api" if is_api else "download",
        auth_class=candidate.auth_class,
        rate_policy=RatePolicy(max_requests_per_window=1, window_seconds=1),
        robots_policy="review_required",
        terms_url=candidate.terms_url,
        software_license=None,
        # A catalog claim is retained for review, but never converted to approval.
        data_license=candidate.license_claim,
        commercial_use=None,
        redistribution=None,
        retention_policy=None,
        allowed_content_types=_content_types(candidate),
        parser_profile=f"m11-draft:{candidate.candidate_id}",
        status="disabled",
    )


def policy_approved_bundle(
    candidate: SourceCandidate, review: PolicyReview, *, owner: str
) -> OnboardingBundle:
    if candidate.state is not CandidateState.APPROVED:
        raise PermissionError("candidate must be in approved state")
    if not review.approved or review.candidate_id != candidate.candidate_id:
        raise PermissionError("matching approved policy review is required")
    draft = draft_source_record(candidate, owner=owner)
    source_record = draft.model_copy(
        update={
            "robots_policy": "reviewed",
            "terms_url": review.terms_url,
            "data_license": review.data_license_id,
            "commercial_use": review.commercial_use,
            "redistribution": review.data_redistribution,
            "retention_policy": review.retention_policy,
            # Golden validation is still pending.
            "status": "disabled",
        }
    )
    governance = SourceGovernance(
        source_id=source_record.source_id,
        enabled=True,
        api_terms_url=str(review.terms_url),
        api_use_status=review.api_use_status,
        caching_policy=review.caching_policy,
        retention_policy=review.retention_policy,
        redistribution_status=review.redistribution_status,
        data=DataLicense(
            owner=review.data_owner,
            license_id=review.data_license_id,
            attribution=review.attribution,
            commercial_use=review.commercial_use,
            derivatives=review.derivatives,
            redistribution=review.data_redistribution,
            region_restrictions=review.region_restrictions,
        ),
        software=None,
    )
    return OnboardingBundle(
        candidate=candidate.model_copy(update={"state": CandidateState.CONNECTOR_DRAFTED}),
        review=review,
        source_record=source_record,
        governance_record=governance,
        connector=connector_draft(candidate),
    )


def enable_after_golden(bundle: OnboardingBundle, validation: GoldenValidation) -> OnboardingBundle:
    if validation.candidate_id != bundle.candidate.candidate_id or not validation.passed:
        raise PermissionError("matching passed Golden validation is required")
    if bundle.candidate.state is not CandidateState.CONNECTOR_DRAFTED:
        raise PermissionError("connector draft must exist before Golden enablement")
    source_record = bundle.source_record.model_copy(update={"status": "enabled"})
    connector = bundle.connector.model_copy(
        update={
            "enabled": True,
            "config": {**bundle.connector.config, "formal_schedule": True},
        }
    )
    return bundle.model_copy(
        update={
            "candidate": bundle.candidate.model_copy(update={"state": CandidateState.ENABLED}),
            "source_record": source_record,
            "connector": connector,
            "golden_validation": validation,
        }
    )
