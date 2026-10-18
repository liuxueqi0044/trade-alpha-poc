"""Format-only business identifier adapter with no registry verification semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from trade_alpha.execution.suppliers.models import (
    BusinessIdentifierClaim,
    IdentifierFormatStatus,
)

IdentifierFormatValidator = Callable[[str], bool]


class FormatOnlyIdentifierAdapter:
    """Apply configured syntax validators without making registry-existence claims."""

    version = "format-only/1.0.0"

    def __init__(
        self,
        validators: Mapping[tuple[str, str], IdentifierFormatValidator] | None = None,
    ) -> None:
        self.validators = dict(validators or {})

    def validate(self, identifier: BusinessIdentifierClaim) -> BusinessIdentifierClaim:
        compact = "".join(
            character for character in identifier.raw_value if character.isalnum()
        ).upper()
        validator = self.validators.get((identifier.country_or_region, identifier.identifier_type))
        if not compact or validator is None:
            status = IdentifierFormatStatus.UNSUPPORTED
        else:
            status = (
                IdentifierFormatStatus.VALID_FORMAT
                if validator(compact)
                else IdentifierFormatStatus.INVALID_FORMAT
            )
        # registry_status is intentionally retained as supplied (normally not_checked).
        return identifier.model_copy(update={"compact_value": compact, "format_status": status})
