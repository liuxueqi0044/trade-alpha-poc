"""Pure projection service for auditable product-specification versions."""

from __future__ import annotations

# ruff: noqa: E501, I001, SIM102

from datetime import datetime
import re
from typing import Any

from trade_alpha.contracts.common import VersionRef, require_utc
from trade_alpha.execution.contracts import stable_fingerprint
from trade_alpha.knowledge import Claim, ReviewStatus

from .models import (
    DecisionAction,
    FieldState,
    GateStatus,
    ProductFamilyProfile,
    ProductSpec,
    ProductSpecVersion,
    ReadinessResult,
    SpecConflict,
    SpecDiff,
    SpecFieldDecision,
    SpecFieldProjection,
    SpecState,
)
from .normalization import equivalence_groups, normalize_claim
from .ports import ClaimRepositoryPort, DecisionAuthorizationPort, ProductSpecRepositoryPort
from .repository import InMemoryProductSpecRepository


class SpecificationService:
    """Project M03 claims without treating accepted claims as confirmed choices."""

    def __init__(
        self,
        claims: ClaimRepositoryPort,
        profiles: tuple[ProductFamilyProfile, ...],
        repository: ProductSpecRepositoryPort | None = None,
        authorization: DecisionAuthorizationPort | None = None,
    ) -> None:
        self.claims = claims
        self.profiles = {(item.profile_id, item.profile_version): item for item in profiles}
        self.repository = repository or InMemoryProductSpecRepository()
        self.authorization = authorization or _ApproverAuthorization()

    def create_spec(
        self,
        spec_id: str,
        profile_ref: VersionRef,
        *,
        created_at: datetime,
        rfq_id: str | None = None,
        rfq_item_id: str | None = None,
        created_by: str = "system",
    ) -> ProductSpecVersion:
        spec = self.repository.save_spec(
            ProductSpec(
                spec_id=spec_id,
                rfq_id=rfq_id,
                rfq_item_id=rfq_item_id,
                product_family_ref=profile_ref,
                created_at=require_utc(created_at),
            )
        )
        return self.reproject(spec.spec_id, as_of=created_at, created_by=created_by)

    def create_from_rfq(
        self, rfq_item_id: str, profile_ref: VersionRef, *, created_at: datetime
    ) -> ProductSpecVersion:
        return self.create_spec(
            spec_id=rfq_item_id,
            rfq_item_id=rfq_item_id,
            profile_ref=profile_ref,
            created_at=created_at,
        )

    def reproject(
        self,
        spec_id: str,
        *,
        as_of: datetime,
        created_by: str = "system",
        profile_ref: VersionRef | None = None,
    ) -> ProductSpecVersion:
        moment = require_utc(as_of)
        spec = self._require_spec(spec_id)
        profile = self._profile(profile_ref or self._active_profile_ref(spec, moment))
        claims = tuple(
            item
            for item in self.claims.claims_for(f"product-spec:{spec_id}", moment)
            if item.status is not ReviewStatus.REJECTED and item.predicate.startswith("spec.")
        )
        decisions = self.repository.decisions_for(spec_id, moment)
        fields, conflicts = self._project_fields(spec_id, profile, claims, decisions)
        readiness = self._readiness(profile, fields, conflicts)
        state = self._state(readiness, fields)
        prior = self.repository.versions_for(spec_id)
        fingerprint = stable_fingerprint(
            {
                "spec_id": spec_id,
                "profile": profile.model_dump(mode="json"),
                "claim_ids": sorted(item.claim_id for item in claims),
                "decision_ids": sorted(item.decision_id for item in decisions),
                "fields": [item.model_dump(mode="json") for item in fields],
                "normalizers": [
                    item.model_dump(mode="json") for item in profile.normalizer_versions
                ],
                "state": state.value,
            }
        )
        version = ProductSpecVersion(
            spec_version_id=f"{spec_id}:{fingerprint.removeprefix('sha256:')}",
            spec_id=spec_id,
            sequence=len(prior) + 1,
            parent_version_id=prior[-1].spec_version_id if prior else None,
            profile_ref=profile.ref,
            fields=fields,
            conflicts=conflicts,
            claim_ids=tuple(sorted(item.claim_id for item in claims)),
            decision_ids=tuple(sorted(item.decision_id for item in decisions)),
            missing_fields=readiness.missing_fields,
            open_proposal_fields=readiness.open_proposal_fields,
            unresolved_conflict_ids=readiness.conflict_ids,
            risk_flags=readiness.risk_flags,
            readiness=readiness,
            state=state,
            normalizer_versions=profile.normalizer_versions,
            fingerprint=fingerprint,
            created_at=moment,
            created_by=created_by,
        )
        return self.repository.save_version(version)

    def record_decision(self, decision: SpecFieldDecision) -> ProductSpecVersion:
        spec = self._require_spec(decision.spec_id)
        profile = self._profile(self._active_profile_ref(spec, decision.decided_at))
        definition = profile.field(decision.field_key)
        if definition is None:
            raise ValueError(f"unknown product profile field {decision.field_key}")
        claims = tuple(
            item
            for item in self.claims.claims_for(
                f"product-spec:{decision.spec_id}", decision.decided_at
            )
            if item.status is not ReviewStatus.REJECTED
            and item.predicate == f"spec.{decision.field_key}"
        )
        self._validate_decision(definition, claims, decision, strict_current=True)
        if decision.action is DecisionAction.NOT_APPLICABLE and not definition.allow_not_applicable:
            raise ValueError(f"{definition.key} does not allow not_applicable")
        self.repository.save_decision(decision)
        return self.reproject(
            decision.spec_id, as_of=decision.decided_at, created_by=decision.actor_id
        )

    def register_profile(self, profile: ProductFamilyProfile) -> None:
        """Register an immutable profile version for explicit future reprojections."""

        self.profiles[(profile.profile_id, profile.profile_version)] = profile

    def freeze_for_supplier_rfq(
        self, spec_id: str, *, actor_id: str, frozen_at: datetime
    ) -> ProductSpecVersion:
        """Freeze a version only; this method deliberately has no external-action port."""

        if not self.authorization.may_freeze(actor_id, spec_id):
            raise PermissionError("actor is not authorized to freeze product specifications")
        draft = self.reproject(spec_id, as_of=frozen_at, created_by=actor_id)
        if draft.readiness.status is not GateStatus.READY:
            raise ValueError("only a ready specification can be frozen for supplier RFQ")
        frozen_fingerprint = stable_fingerprint(
            {"draft_fingerprint": draft.fingerprint, "freeze_actor": actor_id, "state": "frozen"}
        )
        frozen = draft.model_copy(
            update={
                "spec_version_id": f"{spec_id}:{frozen_fingerprint.removeprefix('sha256:')}",
                "sequence": len(self.repository.versions_for(spec_id)) + 1,
                "parent_version_id": draft.spec_version_id,
                "state": SpecState.FROZEN_FOR_SUPPLIER_RFQ,
                "fingerprint": frozen_fingerprint,
                "created_at": require_utc(frozen_at),
                "created_by": actor_id,
            }
        )
        return self.repository.save_version(frozen)

    def get_as_of(self, spec_id: str, as_of: datetime) -> ProductSpecVersion:
        version = self.repository.get_as_of(spec_id, as_of)
        if version is None:
            raise KeyError(f"no specification version for {spec_id} at {as_of.isoformat()}")
        return version

    def diff(self, left: str, right: str) -> SpecDiff:
        left_version = self.repository.get_version(left)
        right_version = self.repository.get_version(right)
        if left_version is None or right_version is None:
            raise KeyError("unknown product specification version")
        left_fields = {item.field_key: item for item in left_version.fields}
        right_fields = {item.field_key: item for item in right_version.fields}
        common = set(left_fields) & set(right_fields)
        return SpecDiff(
            left_version_id=left,
            right_version_id=right,
            added_fields=tuple(sorted(set(right_fields) - set(left_fields))),
            removed_fields=tuple(sorted(set(left_fields) - set(right_fields))),
            changed_fields=tuple(
                sorted(
                    key
                    for key in common
                    if left_fields[key].canonical_value != right_fields[key].canonical_value
                )
            ),
            state_changed_fields=tuple(
                sorted(key for key in common if left_fields[key].state != right_fields[key].state)
            ),
        )

    def _project_fields(
        self,
        spec_id: str,
        profile: ProductFamilyProfile,
        claims: tuple[Claim, ...],
        decisions: tuple[SpecFieldDecision, ...],
    ) -> tuple[tuple[SpecFieldProjection, ...], tuple[SpecConflict, ...]]:
        fields: list[SpecFieldProjection] = []
        conflicts: list[SpecConflict] = []
        for definition in profile.field_definitions:
            field_claims = tuple(
                item for item in claims if item.predicate == f"spec.{definition.key}"
            )
            decision = next(
                (item for item in reversed(decisions) if item.field_key == definition.key), None
            )
            projection, conflict = self._field_projection(
                spec_id, definition, field_claims, decision
            )
            fields.append(projection)
            if conflict is not None:
                conflicts.append(conflict)
        return tuple(fields), tuple(conflicts)

    def _field_projection(
        self,
        spec_id: str,
        definition: Any,
        claims: tuple[Claim, ...],
        decision: SpecFieldDecision | None,
    ) -> tuple[SpecFieldProjection, SpecConflict | None]:
        normalizer = definition.normalizer
        self._validate_decision(definition, claims, decision, strict_current=False)
        if decision and decision.action is DecisionAction.OPEN_TO_SUPPLIER_PROPOSAL:
            return self._projection(
                definition.key, FieldState.OPEN_TO_SUPPLIER_PROPOSAL, (), normalizer, decision
            ), None
        if decision and decision.action is DecisionAction.NOT_APPLICABLE:
            if not definition.allow_not_applicable:
                raise ValueError(f"{definition.key} does not allow not_applicable")
            return self._projection(
                definition.key, FieldState.NOT_APPLICABLE, (), normalizer, decision
            ), None
        if decision and decision.action is DecisionAction.REJECT:
            return self._projection(
                definition.key, FieldState.REJECTED, claims, normalizer, decision
            ), None
        if decision and decision.action is DecisionAction.REQUEST_CLARIFICATION:
            return self._projection(
                definition.key, FieldState.UNKNOWN, claims, normalizer, decision
            ), None
        if not claims:
            return self._projection(
                definition.key, FieldState.MISSING, (), normalizer, decision
            ), None
        normalized = [normalize_claim(item, definition) for item in claims]
        groups = equivalence_groups(claims, [item[2] for item in normalized], definition)
        if decision and not self._decision_covers_current_claims(decision, claims, groups):
            decision = None
        selected = next(
            (item for item in claims if decision and item.claim_id == decision.selected_claim_id),
            None,
        )
        conflict = self._conflict(spec_id, definition, claims, groups)
        if selected is not None:
            assert decision is not None
            index = claims.index(selected)
            raw, typed, canonical, display = normalized[index]
            if conflict is not None:
                conflict = conflict.model_copy(
                    update={"state": "resolved", "resolution_decision_id": decision.decision_id}
                )
            return self._projection(
                definition.key,
                FieldState.CONFIRMED,
                claims,
                normalizer,
                decision,
                raw,
                typed,
                canonical,
                display,
            ), conflict
        if decision and decision.action is DecisionAction.MERGE_EQUIVALENT:
            index = next(
                index for index, item in enumerate(claims) if item.claim_id == decision.input_claim_ids[0]
            )
            raw, typed, canonical, display = normalized[index]
            if conflict is not None:
                conflict = conflict.model_copy(
                    update={"state": "resolved", "resolution_decision_id": decision.decision_id}
                )
            return self._projection(
                definition.key,
                FieldState.CONFIRMED,
                claims,
                normalizer,
                decision,
                raw,
                typed,
                canonical,
                display,
            ), conflict
        if any(item[2] == "unknown" for item in normalized):
            return self._projection(
                definition.key, FieldState.UNKNOWN, claims, normalizer, decision
            ), conflict
        if len(groups) > 1:
            return self._projection(
                definition.key, FieldState.CONFLICT, claims, normalizer, decision
            ), conflict
        raw, typed, canonical, display = normalized[0]
        return self._projection(
            definition.key,
            FieldState.CANDIDATE,
            claims,
            normalizer,
            decision,
            raw,
            typed,
            canonical,
            display,
        ), None

    @staticmethod
    def _projection(
        field_key: str,
        state: FieldState,
        claims: tuple[Claim, ...],
        normalizer: VersionRef,
        decision: SpecFieldDecision | None,
        raw: Any = None,
        typed: Any = None,
        canonical: Any = None,
        display: str | None = None,
    ) -> SpecFieldProjection:
        return SpecFieldProjection(
            field_key=field_key,
            state=state,
            raw_value=raw,
            typed_value=typed,
            canonical_value=canonical,
            display_value=display,
            source_claim_ids=tuple(item.claim_id for item in claims),
            selected_claim_id=decision.selected_claim_id if decision else None,
            decision_id=decision.decision_id if decision else None,
            artifact_ids=tuple(item.artifact_id for item in claims if item.artifact_id),
            evidence_block_ids=tuple(block for item in claims for block in item.evidence_block_ids),
            normalizer_version=normalizer,
        )

    def _readiness(
        self,
        profile: ProductFamilyProfile,
        fields: tuple[SpecFieldProjection, ...],
        conflicts: tuple[SpecConflict, ...],
    ) -> ReadinessResult:
        map_fields = {item.field_key: item for item in fields}
        risk: set[str] = set()
        for key in {"dust_toxicity", "dust_combustibility", "atex_required"}:
            projection = map_fields.get(key)
            if projection is not None and projection.canonical_value is True:
                risk.add(f"hard_exclusion:{key}")
        scope = map_fields.get("application_scope")
        if scope is not None and scope.state is FieldState.CONFIRMED:
            normalized_scope = self._scope_value(scope.canonical_value)
            exclusions = {self._scope_value(value) for value in profile.hard_exclusions}
            allowed = {self._scope_value(value) for value in profile.allowed_scope}
            if normalized_scope in exclusions:
                risk.add("hard_exclusion:application_scope")
            elif allowed and normalized_scope not in allowed:
                risk.add("out_of_allowed_scope:application_scope")
        missing = tuple(
            sorted(
                item.field_key
                for item in fields
                if self._required(profile, item.field_key)
                and not self._field_is_ready(profile, item)
            )
        )
        safety_unknown = tuple(
            definition.key
            for definition in profile.field_definitions
            if definition.criticality == "safety_scope"
            and map_fields[definition.key].state in {FieldState.MISSING, FieldState.UNKNOWN}
        )
        open_fields = tuple(
            sorted(
                item.field_key
                for item in fields
                if item.state is FieldState.OPEN_TO_SUPPLIER_PROPOSAL
            )
        )
        alternatives_missing: list[str] = []
        if profile.identity_alternatives and not any(
            all(self._field_is_ready(profile, map_fields[key]) for key in alternative)
            for alternative in profile.identity_alternatives
        ):
            alternatives_missing.append("product_identity")
        if profile.quantity_alternatives and not any(
            all(self._field_is_ready(profile, map_fields[key]) for key in alternative)
            for alternative in profile.quantity_alternatives
        ):
            alternatives_missing.append("order_quantity")
        unresolved = tuple(sorted(item.conflict_id for item in conflicts if item.state == "open"))
        status = (
            GateStatus.BLOCKED
            if risk
            else GateStatus.NEEDS_CLARIFICATION
            if (missing or safety_unknown or unresolved or alternatives_missing)
            else GateStatus.READY
        )
        return ReadinessResult(
            status=status,
            missing_fields=tuple(sorted(set(missing) | set(safety_unknown) | set(alternatives_missing))),
            conflict_ids=unresolved,
            risk_flags=tuple(sorted(risk)),
            open_proposal_fields=open_fields,
            profile_version=profile.profile_version,
        )

    @staticmethod
    def _required(profile: ProductFamilyProfile, key: str) -> bool:
        definition = profile.field(key)
        return definition is not None and "supplier_rfq" in definition.required_at

    @staticmethod
    def _field_is_ready(profile: ProductFamilyProfile, field: SpecFieldProjection) -> bool:
        definition = profile.field(field.field_key)
        if field.state is FieldState.CONFIRMED:
            return True
        if field.state is FieldState.OPEN_TO_SUPPLIER_PROPOSAL:
            return definition is not None and definition.requirement_mode == "supplier_proposable"
        return field.state is FieldState.NOT_APPLICABLE and bool(
            definition and definition.allow_not_applicable
        )

    @staticmethod
    def _scope_value(value: Any) -> str:
        """Normalize delimiters for exact scope matching, never substring matching."""

        return " ".join(re.split(r"[\s_\-/]+", str(value).casefold().strip()))

    @staticmethod
    def _conflict(
        spec_id: str,
        definition: Any,
        claims: tuple[Claim, ...],
        groups: tuple[tuple[str, ...], ...],
    ) -> SpecConflict | None:
        if len(groups) < 2:
            return None
        return SpecConflict(
            conflict_id=f"{spec_id}:{definition.key}:{stable_fingerprint({'groups': groups})}",
            spec_id=spec_id,
            field_key=definition.key,
            claim_ids=tuple(item.claim_id for item in claims),
            equivalence_groups=groups,
            severity=definition.criticality,
            reason="different_value",
        )

    @staticmethod
    def _validate_decision(
        definition: Any,
        claims: tuple[Claim, ...],
        decision: SpecFieldDecision | None,
        *,
        strict_current: bool,
    ) -> None:
        if decision is None:
            return
        claim_ids = {item.claim_id for item in claims}
        if not set(decision.input_claim_ids).issubset(claim_ids):
            raise ValueError(f"decision input_claim_ids do not belong to spec.{definition.key}")
        if decision.selected_claim_id is not None and decision.selected_claim_id not in claim_ids:
            raise ValueError(f"selected_claim_id does not belong to spec.{definition.key}")
        if strict_current and decision.action is DecisionAction.SELECT_CLAIM and (
            set(decision.input_claim_ids) != claim_ids
            or decision.selected_claim_id not in set(decision.input_claim_ids)
        ):
            raise ValueError("select_claim must cover the complete current field claim set")
        if (
            decision.action is DecisionAction.OPEN_TO_SUPPLIER_PROPOSAL
            and definition.requirement_mode != "supplier_proposable"
        ):
            raise ValueError(f"{definition.key} cannot be opened to supplier proposal")
        if decision.action is DecisionAction.MERGE_EQUIVALENT:
            normalized = [normalize_claim(item, definition)[2] for item in claims]
            groups = equivalence_groups(claims, normalized, definition)
            inputs = set(decision.input_claim_ids)
            if not any(inputs.issubset(set(group)) for group in groups):
                raise ValueError("merge_equivalent input claims are not equivalent")

    @staticmethod
    def _decision_covers_current_claims(
        decision: SpecFieldDecision,
        claims: tuple[Claim, ...],
        groups: tuple[tuple[str, ...], ...],
    ) -> bool:
        current = {item.claim_id for item in claims}
        inputs = set(decision.input_claim_ids)
        if decision.action is DecisionAction.SELECT_CLAIM:
            return inputs == current and decision.selected_claim_id in inputs
        if decision.action is DecisionAction.MERGE_EQUIVALENT:
            return len(groups) == 1 and inputs == current
        return True

    @staticmethod
    def _state(readiness: ReadinessResult, fields: tuple[SpecFieldProjection, ...]) -> SpecState:
        if readiness.status is GateStatus.BLOCKED:
            return SpecState.BLOCKED
        if readiness.status is GateStatus.NEEDS_CLARIFICATION or any(
            item.state is FieldState.CONFLICT for item in fields
        ):
            return SpecState.REVIEW_REQUIRED
        return SpecState.CONFIRMED

    def _profile(self, ref: VersionRef) -> ProductFamilyProfile:
        try:
            return self.profiles[(ref.name, ref.version)]
        except KeyError as error:
            raise KeyError(f"unknown product family profile {ref.name}@{ref.version}") from error

    def _active_profile_ref(self, spec: ProductSpec, as_of: datetime) -> VersionRef:
        """Use the latest known version lineage at ``as_of``, not the root's stale default."""

        current = self.repository.get_as_of(spec.spec_id, as_of)
        return current.profile_ref if current is not None else spec.product_family_ref

    def _require_spec(self, spec_id: str) -> ProductSpec:
        spec = self.repository.get_spec(spec_id)
        if spec is None:
            raise KeyError(f"unknown product specification {spec_id}")
        return spec


class _ApproverAuthorization:
    """Minimal PoC authorization boundary; production supplies a policy adapter."""

    def may_freeze(self, actor_id: str, spec_id: str) -> bool:
        del spec_id
        return actor_id == "approver"
