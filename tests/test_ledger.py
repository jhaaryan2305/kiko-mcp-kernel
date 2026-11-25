import tempfile
import unittest
from pathlib import Path

from kernel.contracts import (
    ApprovalAction,
    ApprovalRecord,
    InverseAction,
    LedgerKind,
    LedgerStatus,
    Tier,
    UndoStrategy,
)
from kernel.ledger import LedgerError, UndoLedger

from ._support import T0, Clock, schema_validator

SHA = "a" * 64


def pending_undo(ledger: UndoLedger, tool: str = "comms.create_draft"):
    return ledger.record_pending(
        kind=LedgerKind.UNDO, tool=tool, server="kiko-comms-mcp", tier=Tier.REVERSIBLE,
        policy_version="1.1.0", intent_redacted="draft to [CONTACT_1]", arguments_sha256=SHA,
        inverse=InverseAction(UndoStrategy.DELETE_CREATED, {}),
    )


def pending_audit(ledger: UndoLedger):
    return ledger.record_pending(
        kind=LedgerKind.AUDIT, tool="comms.send_message", server="kiko-comms-mcp",
        tier=Tier.CIRCUIT_BREAKER, policy_version="1.1.0", intent_redacted="message [CONTACT_1]",
        arguments_sha256=SHA,
    )


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.ledger = UndoLedger(":memory:", clock=self.clock)

    def tearDown(self) -> None:
        self.ledger.close()

    def test_undo_lifecycle_and_newest_first(self) -> None:
        first = pending_undo(self.ledger)
        self.ledger.mark_executed(first.entry_id, inverse_arguments={"draft_id": "d1"})
        second = pending_undo(self.ledger)
        self.ledger.mark_executed(second.entry_id, inverse_arguments={"draft_id": "d2"})

        target = self.ledger.latest_undoable()
        assert target is not None
        self.assertEqual(target.entry_id, second.entry_id)
        self.assertEqual(target.inverse.arguments, {"draft_id": "d2"})

        self.ledger.mark_undone(second.entry_id)
        self.assertEqual(self.ledger.latest_undoable().entry_id, first.entry_id)

    def test_illegal_transitions(self) -> None:
        e = pending_undo(self.ledger)
        with self.assertRaises(LedgerError):
            self.ledger.mark_undone(e.entry_id)  # not executed yet
        with self.assertRaises(LedgerError):
            self.ledger.mark_declined(e.entry_id)  # undo entries are never declined
        self.ledger.mark_failed(e.entry_id)
        with self.assertRaises(LedgerError):
            self.ledger.mark_executed(e.entry_id)
        self.assertIsNone(self.ledger.latest_undoable())

    def test_kind_and_tier_must_agree(self) -> None:
        with self.assertRaises(LedgerError):
            self.ledger.record_pending(
                kind=LedgerKind.UNDO, tool="comms.send_message", server="kiko-comms-mcp",
                tier=Tier.CIRCUIT_BREAKER, policy_version="1.1.0", intent_redacted="x",
                arguments_sha256=SHA, inverse=InverseAction(UndoStrategy.DELETE_CREATED),
            )
        with self.assertRaises(LedgerError):
            self.ledger.record_pending(
                kind=LedgerKind.UNDO, tool="comms.create_draft", server="kiko-comms-mcp",
                tier=Tier.REVERSIBLE, policy_version="1.1.0", intent_redacted="x", arguments_sha256=SHA,
            )

    def test_audit_entry_records_approval(self) -> None:
        e = pending_audit(self.ledger)
        record = ApprovalRecord("9a7c3d12-1e5b-4f8a-b6d0-3c2e7f9a8b41", ApprovalAction.ACCEPT, T0)
        done = self.ledger.mark_executed(e.entry_id, approval=record)
        self.assertIs(done.status, LedgerStatus.EXECUTED)
        self.assertEqual(done.approval, record)
        self.assertIsNone(self.ledger.latest_undoable())  # audit entries are not undoable

    def test_pending_entries_are_listed_for_reconciliation(self) -> None:
        pending_undo(self.ledger)
        pending_audit(self.ledger)
        self.assertEqual(len(self.ledger.pending()), 2)

    def test_rows_conform_to_ledger_schema(self) -> None:
        validator = schema_validator("ledger_entry.schema.json")
        if validator is None:
            self.skipTest("jsonschema>=4.18 not installed")
        u = pending_undo(self.ledger)
        self.ledger.mark_executed(u.entry_id, inverse_arguments={"draft_id": "d1"})
        self.ledger.mark_undone(u.entry_id)
        a = pending_audit(self.ledger)
        self.ledger.mark_executed(
            a.entry_id, approval=ApprovalRecord("9a7c3d12-1e5b-4f8a-b6d0-3c2e7f9a8b41", ApprovalAction.ACCEPT, T0)
        )
        for entry in self.ledger.history():
            errors = [e.message for e in validator.iter_errors(entry.to_dict())]
            self.assertEqual(errors, [], entry.to_dict())

    def test_file_backed_ledger_uses_wal_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "ledger.sqlite3"
            with UndoLedger(path) as ledger:
                mode = ledger._db.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(mode, "wal")
                pending_undo(ledger)
            with UndoLedger(path) as reopened:
                self.assertEqual(len(reopened.pending()), 1)


if __name__ == "__main__":
    unittest.main()
