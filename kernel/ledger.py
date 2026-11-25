"""Local SQLite (WAL) Undo / Audit Ledger.

- Tier 1 actions write an ``undo`` entry **before** they run (ledger first).
  Agentic Ctrl+Z replays the newest ``executed`` undo entry's inverse.
- Tier 2 actions write an ``audit`` entry recording the approval decision.
- Tier 0 reads are not recorded.

Rows are device-only. Nothing in this module talks to the network.
Rows serialize to ``schemas/ledger_entry.schema.json`` via ``LedgerEntry.to_dict()``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, Union

from .contracts import (
    ApprovalAction,
    ApprovalRecord,
    InverseAction,
    LedgerEntry,
    LedgerKind,
    LedgerStatus,
    Tier,
    UndoStrategy,
    from_iso,
    to_iso,
    utc_now,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id          TEXT    NOT NULL UNIQUE,
    kind              TEXT    NOT NULL CHECK (kind IN ('undo', 'audit')),
    tool              TEXT    NOT NULL,
    server            TEXT    NOT NULL,
    tier              TEXT    NOT NULL,
    policy_version    TEXT    NOT NULL,
    intent_redacted   TEXT    NOT NULL,
    arguments_sha256  TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    recorded_at       TEXT    NOT NULL,
    executed_at       TEXT,
    undone_at         TEXT,
    trace_id          TEXT,
    inverse_json      TEXT,
    approval_json     TEXT
);
CREATE INDEX IF NOT EXISTS ledger_undo_idx ON ledger (kind, status, sequence);
"""

# Allowed status transitions per entry kind.
_TRANSITIONS = {
    LedgerKind.UNDO: {
        LedgerStatus.PENDING: {LedgerStatus.EXECUTED, LedgerStatus.FAILED},
        LedgerStatus.EXECUTED: {LedgerStatus.UNDONE},
    },
    LedgerKind.AUDIT: {
        LedgerStatus.PENDING: {
            LedgerStatus.EXECUTED,
            LedgerStatus.FAILED,
            LedgerStatus.DECLINED,
            LedgerStatus.EXPIRED,
        },
    },
}


class LedgerError(RuntimeError):
    """A ledger write failed or an illegal transition was attempted.

    For Tier 1 this is fatal to the action: it must not execute.
    """


class UndoLedger:
    """Append-mostly ledger over a single SQLite file.

    Pass ``":memory:"`` for tests. File-backed ledgers use WAL journaling.
    """

    def __init__(self, path: Union[str, Path], clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if target != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")  # ledger first means durable first
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "UndoLedger":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ writes

    def record_pending(
        self,
        *,
        kind: LedgerKind,
        tool: str,
        server: str,
        tier: Tier,
        policy_version: str,
        intent_redacted: str,
        arguments_sha256: str,
        inverse: Optional[InverseAction] = None,
        trace_id: Optional[str] = None,
    ) -> LedgerEntry:
        """Write a ``pending`` entry. Must succeed before any Tier 1/2 execution."""
        expected_tier = Tier.REVERSIBLE if kind is LedgerKind.UNDO else Tier.CIRCUIT_BREAKER
        if tier is not expected_tier:
            raise LedgerError("{} entries are only for {}".format(kind.value, expected_tier.value))
        if (kind is LedgerKind.UNDO) != (inverse is not None):
            raise LedgerError("undo entries require an inverse; audit entries must not have one")

        entry_id = str(uuid.uuid4())
        try:
            with self._lock:
                self._db.execute(
                    """INSERT INTO ledger (entry_id, kind, tool, server, tier, policy_version,
                           intent_redacted, arguments_sha256, status, recorded_at, trace_id, inverse_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry_id, kind.value, tool, server, tier.value, policy_version,
                        intent_redacted, arguments_sha256, LedgerStatus.PENDING.value,
                        to_iso(self._clock()), trace_id,
                        json.dumps(inverse.to_dict()) if inverse else None,
                    ),
                )
        except sqlite3.Error as exc:
            raise LedgerError("ledger write failed: {}".format(type(exc).__name__)) from exc
        return self.get(entry_id)

    def mark_executed(
        self,
        entry_id: str,
        *,
        inverse_arguments: Optional[Mapping[str, object]] = None,
        approval: Optional[ApprovalRecord] = None,
    ) -> LedgerEntry:
        """Mark success. ``inverse_arguments`` merges result-derived undo data (e.g. a created id)."""
        entry = self.get(entry_id)
        fields = {"executed_at": to_iso(self._clock())}
        if inverse_arguments:
            if entry.inverse is None:
                raise LedgerError("only undo entries carry inverse arguments")
            merged = dict(entry.inverse.arguments)
            merged.update(inverse_arguments)
            fields["inverse_json"] = json.dumps(InverseAction(entry.inverse.strategy, merged).to_dict())
        if approval is not None:
            fields["approval_json"] = json.dumps(approval.to_dict())
        return self._transition(entry, LedgerStatus.EXECUTED, fields)

    def mark_failed(self, entry_id: str, *, approval: Optional[ApprovalRecord] = None) -> LedgerEntry:
        fields = {"approval_json": json.dumps(approval.to_dict())} if approval else {}
        return self._transition(self.get(entry_id), LedgerStatus.FAILED, fields)

    def mark_declined(self, entry_id: str, *, approval: Optional[ApprovalRecord] = None) -> LedgerEntry:
        fields = {"approval_json": json.dumps(approval.to_dict())} if approval else {}
        return self._transition(self.get(entry_id), LedgerStatus.DECLINED, fields)

    def mark_expired(self, entry_id: str, *, approval: Optional[ApprovalRecord] = None) -> LedgerEntry:
        fields = {"approval_json": json.dumps(approval.to_dict())} if approval else {}
        return self._transition(self.get(entry_id), LedgerStatus.EXPIRED, fields)

    def mark_undone(self, entry_id: str) -> LedgerEntry:
        return self._transition(self.get(entry_id), LedgerStatus.UNDONE, {"undone_at": to_iso(self._clock())})

    # ------------------------------------------------------------------ reads

    def get(self, entry_id: str) -> LedgerEntry:
        row = self._db.execute("SELECT * FROM ledger WHERE entry_id = ?", (entry_id,)).fetchone()
        if row is None:
            raise LedgerError("unknown ledger entry {}".format(entry_id))
        return _row_to_entry(row)

    def latest_undoable(self) -> Optional[LedgerEntry]:
        """Newest executed Tier 1 entry: the target of the next Agentic Ctrl+Z."""
        row = self._db.execute(
            "SELECT * FROM ledger WHERE kind = 'undo' AND status = 'executed' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return _row_to_entry(row) if row else None

    def pending(self) -> List[LedgerEntry]:
        """Entries left ``pending`` (e.g. by a crash mid-execution).

        TODO(sprint): reconcile on kernel start — ask the owning server whether the
        action landed, then mark executed/failed. Until then, surface them in the HUD.
        """
        rows = self._db.execute("SELECT * FROM ledger WHERE status = 'pending' ORDER BY sequence").fetchall()
        return [_row_to_entry(r) for r in rows]

    def history(self, limit: int = 50) -> List[LedgerEntry]:
        rows = self._db.execute("SELECT * FROM ledger ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ------------------------------------------------------------------ internals

    def _transition(self, entry: LedgerEntry, to: LedgerStatus, fields: Mapping[str, Optional[str]]) -> LedgerEntry:
        allowed = _TRANSITIONS[entry.kind].get(entry.status, set())
        if to not in allowed:
            raise LedgerError(
                "illegal transition {} -> {} for {} entry".format(entry.status.value, to.value, entry.kind.value)
            )
        columns: Sequence[str] = ["status", *fields.keys()]
        values = [to.value, *fields.values()]
        assignments = ", ".join("{} = ?".format(c) for c in columns)
        try:
            with self._lock:
                cur = self._db.execute(
                    "UPDATE ledger SET {} WHERE entry_id = ? AND status = ?".format(assignments),
                    (*values, entry.entry_id, entry.status.value),
                )
        except sqlite3.Error as exc:
            raise LedgerError("ledger write failed: {}".format(type(exc).__name__)) from exc
        if cur.rowcount != 1:
            raise LedgerError("concurrent modification of entry {}".format(entry.entry_id))
        return self.get(entry.entry_id)


def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
    inverse = None
    if row["inverse_json"]:
        raw = json.loads(row["inverse_json"])
        inverse = InverseAction(UndoStrategy(raw["strategy"]), raw["arguments"])
    approval = None
    if row["approval_json"]:
        raw = json.loads(row["approval_json"])
        approval = ApprovalRecord(raw["request_id"], ApprovalAction(raw["action"]), from_iso(raw["responded_at"]))
    return LedgerEntry(
        entry_id=row["entry_id"],
        sequence=row["sequence"],
        kind=LedgerKind(row["kind"]),
        tool=row["tool"],
        server=row["server"],
        tier=Tier(row["tier"]),
        policy_version=row["policy_version"],
        intent_redacted=row["intent_redacted"],
        arguments_sha256=row["arguments_sha256"],
        status=LedgerStatus(row["status"]),
        recorded_at=from_iso(row["recorded_at"]),
        executed_at=from_iso(row["executed_at"]) if row["executed_at"] else None,
        undone_at=from_iso(row["undone_at"]) if row["undone_at"] else None,
        trace_id=row["trace_id"],
        inverse=inverse,
        approval=approval,
    )


__all__ = ["LedgerError", "UndoLedger"]
