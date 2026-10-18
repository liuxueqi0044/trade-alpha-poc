"""Deterministic, fail-closed Jinja renderer for EP-05 package views."""

from __future__ import annotations

# ruff: noqa: E501
from pathlib import Path
from typing import Any, cast

from jinja2 import BaseLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from trade_alpha.execution.contracts.fingerprints import canonical_json, stable_fingerprint
from trade_alpha.execution.supplier_rfq.models import SupplierRfqPackageSnapshot

_DEFAULT_TEMPLATE = """# Request for quotation\n\nBuyer: {{ structured_request.buyer_alias }}\nResponse deadline (UTC): {{ structured_request.response_deadline }}\nDelivery place: {{ structured_request.target_delivery_place }}\n\n## Lines\n{% for line in structured_request.lines %}- {{ line.line_id }}: {{ line.quantity_tier.quantity }} {{ line.quantity_tier.unit }}\n{% for requirement in line.requirements %}  - {{ requirement.field_key }}: {{ requirement.value }}\n{% endfor %}{% endfor %}\n\n## Questions\n{% for question in structured_request.open_questions %}- {{ question.question_id }}: {{ question.prompt_key }}\n{% endfor %}\n\n## Commercial terms\n{% for term in structured_request.commercial_terms %}- {{ term.key }}: {{ term.prompt_key }}{% if term.required %} (required){% endif %}\n{% endfor %}\n"""


class JinjaRfqRenderer:
    """Render only the validated supplier view; templates cannot include files."""

    version = "jinja-rfq-renderer/1.0.0"

    def __init__(self, template: str | None = None, *, template_path: str | Path | None = None) -> None:
        if template is not None and template_path is not None:
            raise ValueError("provide template or template_path, not both")
        if template_path is not None:
            # Reading the configured template once is intentional; BaseLoader below
            # prevents includes/imports from accessing arbitrary local paths.
            template = Path(template_path).read_text(encoding="utf-8")
        self.template_source = template if template is not None else _DEFAULT_TEMPLATE
        self.template_version = stable_fingerprint({"source": self.template_source})
        self._environment = SandboxedEnvironment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def _template(self) -> Any:
        return self._environment.from_string(self.template_source)

    @staticmethod
    def _view(package: SupplierRfqPackageSnapshot) -> dict[str, Any]:
        # Deliberately construct a small allow-list.  In particular, package
        # identity, qualification, approval, readiness and internal artifact
        # references are not available to templates.
        return {
            "structured_request": package.structured_request.model_dump(
                mode="json", by_alias=False, exclude_none=False
            ),
            "open_questions": tuple(
                item.model_dump(mode="json", by_alias=False, exclude_none=False)
                for item in package.open_questions
            ),
            "lines": tuple(
                item.model_dump(mode="json", by_alias=False, exclude_none=False)
                for item in package.structured_request.lines
            ),
            "commercial_terms": tuple(
                item.model_dump(mode="json", by_alias=False, exclude_none=False)
                for item in package.structured_request.commercial_terms
            ),
        }

    def render_markdown(self, package: SupplierRfqPackageSnapshot) -> str:
        rendered = cast(str, self._template().render(**self._view(package)))
        missing = tuple(
            item.question_id
            for item in package.open_questions
            if item.required_for_quote and item.question_id not in rendered
        )
        if missing:
            raise ValueError("template omitted required questions: " + ", ".join(missing))
        return rendered

    def render_json(self, package: SupplierRfqPackageSnapshot) -> str:
        return canonical_json(
            {
                "structured_request": package.structured_request,
                "open_questions": package.open_questions,
            }
        )

    def render(self, package: SupplierRfqPackageSnapshot) -> str:
        return self.render_markdown(package)

    def render_with_fingerprint(self, package: SupplierRfqPackageSnapshot) -> tuple[str, str]:
        rendered = self.render_markdown(package)
        return rendered, stable_fingerprint({"template": self.template_version, "content": rendered})


JinjaStrictUndefinedRenderer = JinjaRfqRenderer
