import asyncio
import unittest
from dataclasses import replace
from unittest import mock

from kernel.contracts import ApprovalAction, LedgerStatus, Tier, UndoStrategy
from kernel.firewall import Firewall, Outcome, ToolCall
from kernel.ledger import LedgerError, UndoLedger
from kernel.policy import PolicyEngine

from ._support import Clock, FakeApprover, FakeExecutor, FakeInverseExecutor, load_policy_doc, schema_validator


def run(coro):
    return asyncio.run(coro)


class FirewallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.policy = PolicyEngine(load_policy_doc())
        self.ledger = UndoLedger(":memory:", clock=self.clock)
        self.executor = FakeExecutor(results={"comms.create_draft": {"draft_id": "d1", "undo": {"draft_id": "d1"}}})
        self.approver = FakeApprover(clock=self.clock)

    def tearDown(self) -> None:
        self.ledger.close()

    def firewall(self) -> Firewall:
        return Firewall(self.policy, self.ledger, self.executor, self.approver, clock=self.clock)

    # ---------------------------------------------------------------- Tier 0

    def test_tier_0_executes_silently_without_ledger(self) -> None:
        r = run(self.firewall().dispatch(ToolCall("os.semantic_file_search", {"query": "q"}, "find [DOC_1]")))
        self.assertIs(r.outcome, Outcome.EXECUTED)
        self.assertIsNone(r.ledger_entry_id)
        self.assertEqual(self.approver.requests, [])
        self.assertEqual(self.ledger.history(), [])

    # ---------------------------------------------------------------- Tier 1

    def test_tier_1_writes_ledger_before_execution(self) -> None:
        seen = []
        self.executor.on_call = lambda tool: seen.append([e.status for e in self.ledger.pending()])
        r = run(self.firewall().dispatch(ToolCall("comms.create_draft", {"to": "Priya", "body": "late"}, "draft")))
        self.assertEqual(seen, [[LedgerStatus.PENDING]])
        self.assertIs(r.outcome, Outcome.EXECUTED)
        self.assertNotIn("undo", r.result)  # undo data stays in the ledger
        entry = self.ledger.get(r.ledger_entry_id)
        self.assertIs(entry.status, LedgerStatus.EXECUTED)
        self.assertIs(entry.inverse.strategy, UndoStrategy.DELETE_CREATED)
        self.assertEqual(entry.inverse.arguments["draft_id"], "d1")

    def test_tier_1_does_not_run_if_ledger_write_fails(self) -> None:
        with mock.patch.object(self.ledger, "record_pending", side_effect=LedgerError("disk full")):
            with self.assertRaises(LedgerError):
                run(self.firewall().dispatch(ToolCall("comms.create_draft", {"to": "x", "body": "y"}, "draft")))
        self.assertEqual(self.executor.calls, [])

    def test_tier_1_failure_is_recorded_and_not_undoable(self) -> None:
        self.executor.fail = RuntimeError("secret user text")
        r = run(self.firewall().dispatch(ToolCall("os.organize_files", {"moves": []}, "tidy")))
        self.assertIs(r.outcome, Outcome.FAILED)
        self.assertEqual(r.reason, "RuntimeError")  # exception messages never surface
        self.assertIs(self.ledger.get(r.ledger_entry_id).status, LedgerStatus.FAILED)
        self.assertIsNone(self.ledger.latest_undoable())

    def test_undo_last_runs_inverse_and_marks_undone(self) -> None:
        fw = self.firewall()
        r = run(fw.dispatch(ToolCall("comms.create_draft", {"to": "x", "body": "y"}, "draft")))
        inverse = FakeInverseExecutor()
        undone = run(fw.undo_last(inverse))
        self.assertEqual(undone.entry_id, r.ledger_entry_id)
        self.assertIs(undone.status, LedgerStatus.UNDONE)
        server, tool, action = inverse.calls[0]
        self.assertEqual((server, tool), ("kiko-comms-mcp", "comms.create_draft"))
        self.assertEqual(action.arguments["draft_id"], "d1")
        self.assertIsNone(run(fw.undo_last(inverse)))

    # ---------------------------------------------------------------- Tier 2

    def test_tier_2_executes_only_after_accept(self) -> None:
        r = run(self.firewall().dispatch(
            ToolCall("comms.send_message", {"to": "Priya", "body": "late"}, "message [CONTACT_1]",
                     summary="Send 'late' to Priya")
        ))
        self.assertIs(r.outcome, Outcome.EXECUTED)
        self.assertEqual(len(self.approver.requests), 1)
        self.assertEqual(self.approver.requests[0].summary, "Send 'late' to Priya")
        entry = self.ledger.get(r.ledger_entry_id)
        self.assertIs(entry.tier, Tier.CIRCUIT_BREAKER)
        self.assertIs(entry.approval.action, ApprovalAction.ACCEPT)

    def test_tier_2_decline_never_executes(self) -> None:
        self.approver.action = ApprovalAction.DECLINE
        r = run(self.firewall().dispatch(ToolCall("comms.send_message", {"to": "x", "body": "y"}, "msg")))
        self.assertIs(r.outcome, Outcome.DECLINED)
        self.assertEqual(self.executor.calls, [])
        self.assertIs(self.ledger.get(r.ledger_entry_id).status, LedgerStatus.DECLINED)

    def test_tier_2_expired_approval_never_executes(self) -> None:
        self.approver.delay_seconds = 121  # policy TTL is 120 s
        r = run(self.firewall().dispatch(ToolCall("os.delete_file", {"path": "/tmp/x"}, "delete [FILE_1]")))
        self.assertIs(r.outcome, Outcome.EXPIRED)
        self.assertEqual(self.executor.calls, [])
        self.assertIs(self.ledger.get(r.ledger_entry_id).status, LedgerStatus.EXPIRED)

    def test_tier_2_approval_bound_to_exact_arguments(self) -> None:
        self.approver.respond = lambda req: replace(_accept(req, self.clock), arguments_sha256="b" * 64)
        r = run(self.firewall().dispatch(ToolCall("comms.send_message", {"to": "x", "body": "y"}, "msg")))
        self.assertIs(r.outcome, Outcome.DECLINED)
        self.assertEqual(r.reason, "mismatch")
        self.assertEqual(self.executor.calls, [])

    def test_tier_2_rejects_untrusted_source_and_unconfirmed(self) -> None:
        for patch, reason in (({"source": "aws_lambda"}, "untrusted_source"), ({"confirm": None}, "unconfirmed")):
            self.approver.respond = lambda req, p=patch: replace(_accept(req, self.clock), **p)
            r = run(self.firewall().dispatch(ToolCall("comms.send_message", {"to": "x", "body": "y"}, "msg")))
            self.assertEqual((r.outcome, r.reason), (Outcome.DECLINED, reason))
        self.assertEqual(self.executor.calls, [])

    def test_unlisted_tool_requires_approval(self) -> None:
        self.approver.action = ApprovalAction.DECLINE
        r = run(self.firewall().dispatch(ToolCall("os.format_disk", {}, "format")))
        self.assertIs(r.tier, Tier.CIRCUIT_BREAKER)
        self.assertEqual(len(self.approver.requests), 1)
        self.assertEqual(self.executor.calls, [])

    def test_malformed_tool_is_rejected_without_routing(self) -> None:
        r = run(self.firewall().dispatch(ToolCall("shell.rm_rf", {}, "x")))
        self.assertIs(r.outcome, Outcome.REJECTED)
        self.assertEqual(self.approver.requests, [])
        self.assertEqual(self.ledger.history(), [])

    def test_approval_request_conforms_to_elicitation_schema(self) -> None:
        validator = schema_validator("elicitation.schema.json")
        if validator is None:
            self.skipTest("jsonschema>=4.18 not installed")
        run(self.firewall().dispatch(ToolCall("comms.send_message", {"to": "x", "body": "y"}, "msg", trace_id="4bf92f3577b34da6a3ce929d0e0e4736")))
        req = self.approver.requests[0]
        self.assertEqual([e.message for e in validator.iter_errors(req.to_dict())], [])
        resp = _accept(req, self.clock)
        self.assertEqual([e.message for e in validator.iter_errors(resp.to_dict())], [])


def _accept(req, clock):
    from kernel.contracts import ApprovalResponse

    return ApprovalResponse(req.request_id, req.arguments_sha256, ApprovalAction.ACCEPT, clock(), confirm=True)


if __name__ == "__main__":
    unittest.main()
