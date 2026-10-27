"""
PII redaction via Azure AI Language. Separate service from Content Safety —
Content Safety does not do PII. This is the video's "Sensitive Salary Data ->
Applying redaction" step, run on tool results before they go back to the
agent/user.
"""
from __future__ import annotations

import os

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential


def redact(text: str, categories: list[str] | None = None) -> str:
    """
    Redact PII from a string. `categories` optionally restricts detection to
    specific entity types (e.g. ["USPersonName"] is too narrow for salary —
    for structured fields like salary, prefer field-level redaction in the
    tool itself; use this for free-text tool output).
    """
    endpoint = os.environ["LANGUAGE_ENDPOINT"]
    key = os.environ["LANGUAGE_KEY"]
    client = TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    [result] = client.recognize_pii_entities([text], categories_filter=categories)
    if result.is_error:
        raise RuntimeError(f"PII redaction failed: {result.error}")
    return result.redacted_text


def redact_field(value: str, label: str = "REDACTED") -> str:
    """Hard redaction for a known-sensitive structured field (e.g. a salary number)."""
    return f"[{label}]"


# Financial and government identifiers only. Used on mail and documents, where names and
# email addresses must stay readable (Kacy needs them to reply) but card and account numbers must not.
FINANCIAL_ID_CATEGORIES = [
    "CreditCardNumber", "USSocialSecurityNumber", "InternationalBankingAccountNumber",
    "ABARoutingNumber", "USBankAccountNumber", "SWIFTCode",
]


def redact_many(texts: list[str], categories: list[str] | None = None) -> list[str]:
    """Redact several strings. The service takes at most 5 documents per request, so batch."""
    if not texts:
        return []
    client = TextAnalyticsClient(
        endpoint=os.environ["LANGUAGE_ENDPOINT"], credential=AzureKeyCredential(os.environ["LANGUAGE_KEY"]))
    out: list[str] = []
    for i in range(0, len(texts), 5):
        for result in client.recognize_pii_entities(texts[i:i + 5], categories_filter=categories):
            if result.is_error:
                raise RuntimeError(f"PII redaction failed: {result.error}")
            out.append(result.redacted_text)
    return out
