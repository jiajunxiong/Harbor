"""Paper audit log tests (MVP 4 / SP 4.11).

Verifies that signals, approvals, orders, fills, circuit breakers and manual
operations are all recorded as immutable UTC-stamped audit events, that the
log is deterministic and replayable (fingerprint stable for equal event
streams), and that malformed or hand-edited logs are rejected.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_audit import (
    AuditActor,
    AuditEventType,
    PaperAuditError,
    PaperAuditEvent,
    PaperAuditLog,
    audit_fingerprint,
    audit_json,
    log_audit_event,
    new_paper_audit_log,
)

_UTC = timezone.utc


def _at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=_UTC)


def _event(sequence: int, **overrides: object) -> PaperAuditEvent:
    """Return a valid audit event with overridable fields."""
    fields: dict[str, object] = {
        "event_id": f"e-{sequence}",
        "paper_run_id": "paper-1",
        "event_type": AuditEventType.ORDER,
        "actor": AuditActor.SYSTEM,
        "detail": "order created",
        "recorded_at": _at(2026, 1, 2, 9),
        "sequence": sequence,
    }
    fields.update(overrides)
    return PaperAuditEvent(**fields)  # type: ignore[arg-type]


class AuditEventTests(unittest.TestCase):
    """The audit event record (SP 4.11)."""

    def test_event_types_cover_all_loop_stages(self) -> None:
        self.assertEqual(
            [event_type.value for event_type in AuditEventType],
            ["SIGNAL", "APPROVAL", "ORDER", "FILL", "CIRCUIT_BREAKER", "MANUAL"],
        )

    def test_actors(self) -> None:
        self.assertEqual([actor.value for actor in AuditActor], ["SYSTEM", "APPROVER", "MANUAL"])

    def test_valid_event_and_readable(self) -> None:
        event = _event(0)
        self.assertEqual(event.sequence, 0)
        self.assertIn("ORDER e-0", event.readable())

    def test_invalid_event(self) -> None:
        with self.assertRaises(PaperAuditError):
            _event(0, event_id="")
        with self.assertRaises(PaperAuditError):
            _event(0, paper_run_id="")
        with self.assertRaises(PaperAuditError):
            _event(0, detail="")
        with self.assertRaises(PaperAuditError):
            PaperAuditEvent(
                event_id="e-0",
                paper_run_id="paper-1",
                event_type=AuditEventType.ORDER,
                actor=AuditActor.SYSTEM,
                detail="order created",
                recorded_at=_at(2026, 1, 2, 9),
                sequence=-1,
            )
        with self.assertRaises(ValueError):
            _event(0, recorded_at=datetime(2026, 1, 2, 9))


class PaperAuditLogTests(unittest.TestCase):
    """The immutable audit log (SP 4.11)."""

    def test_append_assigns_monotonic_sequence(self) -> None:
        log = new_paper_audit_log("paper-1", recorded_at=_at(2026, 1, 1, 8))
        log = log.append(
            event_id="e-1",
            event_type=AuditEventType.ORDER,
            actor=AuditActor.SYSTEM,
            detail="order created",
            recorded_at=_at(2026, 1, 2, 9),
        )
        log = log.append(
            event_id="e-2",
            event_type=AuditEventType.FILL,
            actor=AuditActor.SYSTEM,
            detail="order filled",
            recorded_at=_at(2026, 1, 2, 10),
        )
        self.assertEqual([event.sequence for event in log.events], [0, 1, 2])
        self.assertEqual([event.paper_run_id for event in log.events], ["paper-1"] * 3)

    def test_immutable_append(self) -> None:
        original = new_paper_audit_log("paper-1", recorded_at=_at(2026, 1, 1, 8))
        updated = log_audit_event(
            original,
            event_id="e-1",
            event_type=AuditEventType.ORDER,
            actor=AuditActor.SYSTEM,
            detail="order created",
            recorded_at=_at(2026, 1, 2, 9),
        )
        self.assertEqual(len(original.events), 1)
        self.assertEqual(len(updated.events), 2)

    def test_events_for_filters_by_type(self) -> None:
        log = new_paper_audit_log("paper-1", recorded_at=_at(2026, 1, 1, 8))
        log = log.append(
            event_id="e-1",
            event_type=AuditEventType.ORDER,
            actor=AuditActor.SYSTEM,
            detail="order created",
            recorded_at=_at(2026, 1, 2, 9),
        )
        log = log.append(
            event_id="e-2",
            event_type=AuditEventType.CIRCUIT_BREAKER,
            actor=AuditActor.SYSTEM,
            detail="breaker tripped",
            recorded_at=_at(2026, 1, 3, 9),
        )
        self.assertEqual(
            [event.event_type for event in log.events_for(AuditEventType.ORDER)],
            [AuditEventType.ORDER],
        )
        self.assertEqual(len(log.events_for(AuditEventType.CIRCUIT_BREAKER)), 1)

    def test_readable_renders_trail(self) -> None:
        log = new_paper_audit_log("paper-1", recorded_at=_at(2026, 1, 1, 8))
        rendered = log.readable()
        self.assertIn("paper audit log: 1 events", rendered)
        self.assertIn("MANUAL", rendered)

    def test_out_of_order_sequences_rejected(self) -> None:
        with self.assertRaises(PaperAuditError):
            PaperAuditLog(events=(_event(2), _event(1)))

    def test_duplicate_sequences_rejected(self) -> None:
        with self.assertRaises(PaperAuditError):
            PaperAuditLog(events=(_event(1), _event(1)))


class AuditFingerprintTests(unittest.TestCase):
    """The deterministic, replayable fingerprint (SP 4.11)."""

    def _log(self) -> PaperAuditLog:
        log = new_paper_audit_log("paper-1", recorded_at=_at(2026, 1, 1, 8))
        return log.append(
            event_id="e-1",
            event_type=AuditEventType.ORDER,
            actor=AuditActor.SYSTEM,
            detail="order created",
            recorded_at=_at(2026, 1, 2, 9),
        )

    def test_fingerprint_stable_for_equal_logs(self) -> None:
        self.assertEqual(audit_fingerprint(self._log()), audit_fingerprint(self._log()))

    def test_json_is_key_sorted(self) -> None:
        payload = audit_json(self._log())
        self.assertIn('"event_type":"ORDER"', payload)
        self.assertIn('"sequence":0', payload)

    def test_fingerprint_changes_with_event_content(self) -> None:
        base = audit_fingerprint(self._log())
        changed = self._log()
        changed = PaperAuditLog(events=(_event(0), _event(1, detail="order changed")))
        self.assertNotEqual(base, audit_fingerprint(changed))

    def test_fingerprint_changes_with_type_and_actor(self) -> None:
        base = audit_fingerprint(self._log())
        typed = PaperAuditLog(events=(_event(0), _event(1, event_type=AuditEventType.FILL)))
        self.assertNotEqual(base, audit_fingerprint(typed))
        acted = PaperAuditLog(events=(_event(0), _event(1, actor=AuditActor.APPROVER)))
        self.assertNotEqual(base, audit_fingerprint(acted))

    def test_fingerprint_changes_with_timestamp(self) -> None:
        base = audit_fingerprint(self._log())
        reordered = PaperAuditLog(events=(_event(0), _event(1, recorded_at=_at(2026, 1, 3, 9))))
        self.assertNotEqual(base, audit_fingerprint(reordered))
