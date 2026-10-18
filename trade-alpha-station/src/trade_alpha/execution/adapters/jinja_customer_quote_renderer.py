"""Local-only, allow-listed Jinja renderer for EP-10 customer quote drafts."""

from __future__ import annotations

from typing import Any

from jinja2 import BaseLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from trade_alpha.execution.contracts.fingerprints import stable_fingerprint
from trade_alpha.execution.customer_quotes.models import CustomerQuoteRenderView

_QUOTE_TEMPLATE = (
    "# Quotation\n\nCustomer: {{ customer_alias }}\n\n"
    "{% for line in lines %}- {{ line.description }}: {{ line.quantity }} {{ line.unit }} "
    "at {{ line.unit_price.amount }} {{ line.unit_price.currency }}\n{% endfor %}\n"
    "## Terms\nIncoterm: {{ terms.incoterm.code }} {{ terms.incoterm.named_place }}\n"
    "Validity: {{ terms.valid_from }} to {{ terms.valid_until }}\n"
    "Payment: {{ terms.payment_terms }}\nDelivery: {{ terms.delivery_terms }}\n\n"
    "{% if terms.assumptions %}## Assumptions\n"
    "{% for item in terms.assumptions %}- {{ item }}\n{% endfor %}{% endif %}"
    "{% if terms.exclusions %}## Exclusions\n"
    "{% for item in terms.exclusions %}- {{ item }}\n{% endfor %}{% endif %}"
    "## Disclosures\n{% for disclosure in disclosures %}- {{ disclosure }}\n{% endfor %}"
)
_COMMUNICATION_TEMPLATE = (
    "Hello {{ customer_alias }},\n\n"
    "Please find the attached local quotation draft for your human review.\n\n"
    "{% for disclosure in disclosures %}- {{ disclosure }}\n{% endfor %}"
)


class JinjaCustomerQuoteRenderer:
    """No file loader and no package object exposure: customer view is deliberately small."""

    def __init__(
        self, quote_template: str | None = None, communication_template: str | None = None
    ) -> None:
        self._quote_template = quote_template or _QUOTE_TEMPLATE
        self._communication_template = communication_template or _COMMUNICATION_TEMPLATE
        self._environment = SandboxedEnvironment(
            loader=BaseLoader(), undefined=StrictUndefined, autoescape=False
        )
        self._template_fingerprint = stable_fingerprint(
            {"quote": self._quote_template, "communication": self._communication_template}
        )

    @property
    def template_fingerprint(self) -> str:
        return self._template_fingerprint

    @staticmethod
    def _view(view: CustomerQuoteRenderView) -> dict[str, Any]:
        if type(view) is not CustomerQuoteRenderView:
            raise TypeError("renderer accepts only the allow-listed customer quote view")
        return {
            "customer_alias": view.customer_alias,
            "lines": tuple(
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit": line.unit,
                    "unit_price": line.unit_price.model_dump(mode="json"),
                }
                for line in view.lines
            ),
            "terms": view.terms.model_dump(mode="json"),
            "disclosures": view.disclosures,
        }

    def render_quote(self, view: CustomerQuoteRenderView) -> str:
        return self._environment.from_string(self._quote_template).render(**self._view(view))

    def render_communication(self, view: CustomerQuoteRenderView) -> str:
        return self._environment.from_string(self._communication_template).render(
            **self._view(view)
        )
