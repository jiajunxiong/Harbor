"""Frozen-dataset profiling and lifecycle events (MVP 5 / SP 5.26, SP 5.30).

**This suite writes.** It creates validation runs and freezes them, which is how
the delivery was verified against real data — the two bugs found in this work
(an empty placeholder fingerprint rejected by the manifest model, and a
"no offenders" gap message that flagged a fully covered item) were both only
visible by running it. Point ``HARBOR_TEST_DATABASE_URL`` at a test database.

The writes are append-only and additive: no truncation, no migration, and a run
that already exists is never rewritten. The assertions are therefore about
*relationships* rather than fixed counts: a profile must reproduce its own
fingerprint, a component must lie inside the window it belongs to, and a warning
must correspond to a gate item that did not pass.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from harbor.core.validation_config_loader import load_validation_config
from harbor.core.validation_domain import ValidationStatus
from harbor.services.validation import (
    ValidationCommandResult,
    run_validation_command,
    run_validation_from_config,
)
from harbor.services.validation_dataset import build_profile
from harbor.storage.validation_repositories import ValidationRepository

TEST_DATABASE_URL = os.environ.get("HARBOR_TEST_DATABASE_URL")

_SKIP_REASON = "Set HARBOR_TEST_DATABASE_URL to run the validation profiling tests."

_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "configs"
    / "validation"
    / "hk_validation.yaml"
)


def _engine() -> Engine:
    assert TEST_DATABASE_URL is not None
    return create_engine(TEST_DATABASE_URL)


@unittest.skipUnless(TEST_DATABASE_URL, _SKIP_REASON)
class ValidationFreezeTests(unittest.TestCase):
    """Exercise the dataset freeze against a real database."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _engine()
        with cls.engine.begin() as connection:
            created: ValidationCommandResult = run_validation_from_config(_CONFIG, connection)
            # Frozen once for the whole class: ``freeze`` is a one-way transition,
            # so a per-test freeze would fail from the second test on.
            cls.frozen: ValidationCommandResult = run_validation_command(
                connection, created.run_id, command="freeze"
            )
        cls.run_id = created.run_id

    def setUp(self) -> None:
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        self.repository = ValidationRepository(self.connection)

    def _freeze(self) -> ValidationCommandResult:
        with self.engine.begin() as connection:
            return run_validation_command(connection, self.run_id, command="freeze")

    def _manifest_row(self) -> dict[str, object]:
        row = self.connection.execute(self.repository.get_manifest(self.run_id)).first()
        assert row is not None
        return dict(row._mapping)

    def _event_rows(self) -> list[dict[str, object]]:
        rows = self.connection.execute(self.repository.list_events(self.run_id)).all()
        return [dict(row._mapping) for row in rows]

    def test_creating_a_run_records_its_creation_event(self) -> None:
        events = self._event_rows()

        self.assertTrue(events)
        self.assertEqual(events[0]["from_status"], None)
        self.assertEqual(events[0]["to_status"], ValidationStatus.DRAFT.value)

    def test_freezing_records_the_dataset_and_the_freeze_time(self) -> None:
        self.assertEqual(self.frozen.status, ValidationStatus.DATA_FROZEN)
        manifest = self._manifest_row()
        events = self._event_rows()

        self.assertEqual(manifest["markets"], ["HK"])
        self.assertEqual(len(str(manifest["fingerprint"])), 64)
        freeze_events = [row for row in events if row["to_status"] == "DATA_FROZEN"]
        self.assertEqual(len(freeze_events), 1)
        # This row is the run's freeze time: the master row keeps only the last
        # updated_at, so without it the moment the dataset was frozen is lost.
        self.assertIsNotNone(freeze_events[0]["recorded_at"])

    def test_every_recorded_component_lies_inside_the_frozen_window(self) -> None:
        manifest = self._manifest_row()
        components = manifest["components"]
        assert isinstance(components, list)

        self.assertTrue(components)
        for component in components:
            start = component["start"]
            end = component["end"]
            self.assertIsNotNone(start, msg=component["component"])
            self.assertIsNotNone(end, msg=component["component"])
            self.assertGreaterEqual(str(start), str(manifest["start_date"]))
            self.assertLessEqual(str(end), str(manifest["end_date"]))
            self.assertLessEqual(str(start), str(end))

    def test_the_fingerprint_is_reproducible_from_the_same_data(self) -> None:
        recorded = str(self._manifest_row()["fingerprint"])

        # Same config, same data, same fingerprint: this is what lets a dashboard
        # claim a run's data identity instead of showing a timestamp.
        rebuilt = build_profile(
            self.connection,
            config=load_validation_config(_CONFIG),
            config_hash=str(self._manifest_row()["config_hash"]),
        )

        self.assertEqual(rebuilt.fingerprint, recorded)

    def test_warnings_correspond_to_gate_items_that_did_not_pass(self) -> None:
        rows = self.connection.execute(self.repository.list_warnings(self.run_id)).all()
        warnings = [dict(row._mapping) for row in rows]

        # A coverage item that passes must not produce a warning: that is how a
        # complete stock pool ended up carrying a "no quotes" warning of its own.
        profile = build_profile(
            self.connection,
            config=load_validation_config(_CONFIG),
            config_hash=str(self._manifest_row()["config_hash"]),
        )
        failing = {
            (result.market.value, result.item.value)
            for gate in profile.gates
            for result in gate.results
            if result.severity is not None
        }
        recorded = {
            (str(row["context"]["market"]), str(row["context"]["item"])) for row in warnings
        }

        self.assertEqual(recorded, failing)
        for row in warnings:
            self.assertIn(row["severity"], {"warning", "error"})
            self.assertTrue(str(row["message"]).strip())

    def test_a_second_freeze_is_refused_and_changes_nothing(self) -> None:
        before = self._manifest_row()
        events_before = len(self._event_rows())

        with self.assertRaises(ValueError):
            self._freeze()

        self.assertEqual(self._manifest_row(), before)
        self.assertEqual(len(self._event_rows()), events_before)

    def test_the_frozen_run_reads_back_through_the_repository(self) -> None:
        row = self.connection.execute(self.repository.get_run(self.run_id)).first()
        assert row is not None

        self.assertEqual(str(row.status), ValidationStatus.DATA_FROZEN.value)
