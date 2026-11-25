import copy
import unittest

from kernel.contracts import Tier
from kernel.policy import Action, PolicyEngine, PolicyError

from ._support import load_policy_doc


class PolicyLoadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = load_policy_doc()
        self.engine = PolicyEngine(self.doc)

    def test_every_tool_evaluates_to_its_declared_tier(self) -> None:
        for name, spec in self.doc["tools"].items():
            d = self.engine.evaluate(name)
            self.assertEqual(d.tier.value, spec["tier"], name)
            self.assertEqual(d.server, spec["server"], name)
            self.assertTrue(d.listed)

    def test_actions_by_tier(self) -> None:
        self.assertIs(self.engine.evaluate("os.semantic_file_search").action, Action.EXECUTE)
        self.assertIs(self.engine.evaluate("comms.create_draft").action, Action.EXECUTE_WITH_UNDO)
        self.assertIs(self.engine.evaluate("comms.send_message").action, Action.REQUIRE_APPROVAL)

    def test_unlisted_tool_fails_closed_to_tier_2(self) -> None:
        d = self.engine.evaluate("os.format_disk")
        self.assertIs(d.tier, Tier.CIRCUIT_BREAKER)
        self.assertIs(d.action, Action.REQUIRE_APPROVAL)
        self.assertFalse(d.listed)
        self.assertEqual(d.server, "kiko-os-mcp")

    def test_malformed_tool_name_has_no_server(self) -> None:
        d = self.engine.evaluate("evil.Drop-Tables")
        self.assertIs(d.tier, Tier.CIRCUIT_BREAKER)
        self.assertIsNone(d.server)

    def test_from_file_uses_repo_policy(self) -> None:
        self.assertEqual(PolicyEngine.from_file().version, self.doc["kernel_policy_version"])


class PolicyInvariantTest(unittest.TestCase):
    """A hand-edited policy that weakens the firewall must be rejected at load."""

    def mutated(self, mutate) -> dict:
        doc = copy.deepcopy(load_policy_doc())
        mutate(doc)
        return doc

    def assertRejected(self, mutate) -> None:
        with self.assertRaises(PolicyError):
            PolicyEngine(self.mutated(mutate))

    def test_default_tier_must_fail_closed(self) -> None:
        self.assertRejected(lambda d: d.update(default_tier="tier_0_silent"))

    def test_tier_2_cannot_be_bypassable(self) -> None:
        self.assertRejected(lambda d: d["tiers"]["tier_2_circuit_breaker"].update(bypassable=True))

    def test_tier_1_must_write_ledger_first(self) -> None:
        self.assertRejected(lambda d: d["tiers"]["tier_1_reversible"].update(ledger_write_before_execute=False))

    def test_raw_content_logging_is_forbidden(self) -> None:
        self.assertRejected(lambda d: d["privacy"].update(log_raw_content=True))

    def test_tier_1_requires_undo(self) -> None:
        self.assertRejected(lambda d: d["tools"]["os.organize_files"].pop("undo"))

    def test_external_side_effects_require_tier_2(self) -> None:
        def demote(d):
            d["tools"]["comms.send_message"]["tier"] = "tier_0_silent"
        self.assertRejected(demote)

    def test_server_must_match_domain(self) -> None:
        self.assertRejected(lambda d: d["tools"]["os.delete_file"].update(server="kiko-web-mcp"))

    def test_ttl_bounds(self) -> None:
        self.assertRejected(lambda d: d["tiers"]["tier_2_circuit_breaker"].update(approval_ttl_seconds=3600))


if __name__ == "__main__":
    unittest.main()
