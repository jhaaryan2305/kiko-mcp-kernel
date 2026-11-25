"""On-device PII redaction and local rehydration.

``redact`` runs before anything leaves the device; ``rehydrate`` runs only in the
local gateway, immediately before a tool executes. The token map never leaves
the device and is never logged.

This starter handles emails, phone numbers and caller-supplied names (e.g. from
the local contacts store). TODO(sprint): add an on-device NER pass for names,
places and addresses not in the contacts list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


@dataclass
class RedactionResult:
    redacted: str
    token_map: Dict[str, str] = field(default_factory=dict)
    """placeholder -> original value. Device-only."""


class PIIRedactor:
    def __init__(self, known_names: Iterable[str] = ()) -> None:
        # Longest first so "Priya Sharma" wins over "Priya".
        self._names = sorted({n.strip() for n in known_names if n.strip()}, key=len, reverse=True)

    def redact(self, text: str) -> RedactionResult:
        token_map: Dict[str, str] = {}
        counters: Dict[str, int] = {}
        reverse: Dict[str, str] = {}

        def placeholder(kind: str, value: str) -> str:
            if value in reverse:
                return reverse[value]
            counters[kind] = counters.get(kind, 0) + 1
            token = "[{}_{}]".format(kind, counters[kind])
            token_map[token] = value
            reverse[value] = token
            return token

        out = _EMAIL_RE.sub(lambda m: placeholder("EMAIL", m.group(0)), text)
        out = _PHONE_RE.sub(lambda m: placeholder("PHONE", m.group(0)), out)
        for name in self._names:
            pattern = re.compile(r"\b{}\b".format(re.escape(name)), re.IGNORECASE)
            out = pattern.sub(lambda m: placeholder("CONTACT", m.group(0)), out)
        return RedactionResult(out, token_map)

    @staticmethod
    def rehydrate(value: Any, token_map: Dict[str, str]) -> Any:
        """Replace placeholders in any JSON-like structure. Local gateway only."""
        if isinstance(value, str):
            for token, original in token_map.items():
                value = value.replace(token, original)
            return value
        if isinstance(value, dict):
            return {k: PIIRedactor.rehydrate(v, token_map) for k, v in value.items()}
        if isinstance(value, list):
            return [PIIRedactor.rehydrate(v, token_map) for v in value]
        return value
