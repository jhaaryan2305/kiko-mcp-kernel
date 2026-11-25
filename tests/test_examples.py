"""The reference payloads in schemas/examples/ must be accepted by the real kernel code."""
import json
import unittest

from kernel.contracts import ApprovalRequest, ApprovalResponse, SideEffects, canonical_sha256, from_iso
from kernel.elicitation import verify_approval

from ._support import SCHEMAS

EX = SCHEMAS / "examples"


def load(name: str) -> dict:
    with (EX / name).open() as f:
        return json.load(f)


class ExamplePayloadTest(unittest.TestCase):
    def test_example_approval_verifies_against_its_arguments(self) -> None:
        raw = load("elicitation.request.json")
        request = ApprovalRequest(
            request_id=raw["request_id"], tool=raw["tool"], server=raw["server"],
            policy_version=raw["policy_version"], summary=raw["summary"],
            arguments_preview=raw["arguments_preview"], arguments_sha256=raw["arguments_sha256"],
            side_effects=SideEffects(raw["side_effects"]), created_at=from_iso(raw["created_at"]),
            expires_at=from_iso(raw["expires_at"]), trace_id=raw.get("trace_id"),
        )
        response = ApprovalResponse.from_dict(load("elicitation.response.json"))
        verify_approval(request, response, raw["arguments_preview"], now=response.responded_at)  # must not raise

    def test_undo_example_hash_matches_forward_arguments(self) -> None:
        undo = load("ledger_entry.undo.json")
        self.assertEqual(undo["arguments_sha256"], canonical_sha256(undo["inverse"]["arguments"]["forward_arguments"]))


if __name__ == "__main__":
    unittest.main()
