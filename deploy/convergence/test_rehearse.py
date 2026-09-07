"""Meaningful boundaries for the local-only restore entry point."""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("convergence_rehearse", Path(__file__).with_name("rehearse.py"))
rehearse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearse)


class RehearsalBoundaryTests(unittest.TestCase):
    def source_fixture(self):
        fingerprints = [{"schema": schema, "table": table, "rows": 0, "sha256": "same"}
                        for schema, table in sorted(rehearse.approved_legacy_inventory())]
        wm = [{"table": table, "memory_only": 0, "aw_only": 0, "shared": 0, "shared_changed": 0}
              for table in rehearse.preflight.ROW_COMPARE_TABLES]
        return fingerprints, deepcopy(fingerprints), wm

    def test_complete_source_gate_accepts_equal_reviewed_inventory(self):
        result = rehearse.source_equivalence_gate(*self.source_fixture())
        self.assertTrue(result["passed"])
        self.assertEqual(result["approved_tables"], 53)
        self.assertEqual(result["non_wm_tables_equal"], 46)

    def test_aw_extra_semantic_row_cannot_pass_source_gate(self):
        memory, aw, wm = self.source_fixture()
        row = next(row for row in aw if row["schema"] == "public" and row["table"] == "semantic_entities")
        row.update(rows=1, sha256="aw-added-business-fact")
        with self.assertRaises(rehearse.AcceptanceGateFailed) as failure:
            rehearse.source_equivalence_gate(memory, aw, wm)
        self.assertEqual(failure.exception.differences, [{"table": "public.semantic_entities", "reason": "NON_WM_ROWS_OR_CONTENT_DIFFER"}])

    def test_unknown_table_fails_even_if_present_identically_in_both_sources(self):
        memory, aw, wm = self.source_fixture()
        for rows in (memory, aw):
            rows.append({"schema": "public", "table": "runtime_unreviewed_history", "rows": 1, "sha256": "same"})
        with self.assertRaises(rehearse.AcceptanceGateFailed) as failure:
            rehearse.source_equivalence_gate(memory, aw, wm)
        self.assertEqual({row["reason"] for row in failure.exception.differences}, {"UNKNOWN_TABLE"})

    def test_missing_table_fails_even_if_absent_from_both_sources(self):
        memory, aw, wm = self.source_fixture()
        memory = [row for row in memory if (row["schema"], row["table"]) != ("public", "documents")]
        aw = [row for row in aw if (row["schema"], row["table"]) != ("public", "documents")]
        with self.assertRaises(rehearse.AcceptanceGateFailed) as failure:
            rehearse.source_equivalence_gate(memory, aw, wm)
        self.assertEqual({row["reason"] for row in failure.exception.differences}, {"MISSING_APPROVED_TABLE"})

    def test_wm_only_difference_still_requires_complete_matching_subset_counts(self):
        memory, aw, wm = self.source_fixture()
        next(row for row in wm if row["table"] == "wm_objects")["aw_only"] = 1
        with self.assertRaises(rehearse.AcceptanceGateFailed):
            rehearse.source_equivalence_gate(memory, aw, wm)

    def test_unvalidated_fk_or_wrong_owner_cannot_pass(self):
        good = {"foreign_keys": 158, "unvalidated_foreign_keys": 0, "wrong_public_table_owner": 0}
        rehearse.constraints_gate(good)
        for field in ("unvalidated_foreign_keys", "wrong_public_table_owner"):
            with self.subTest(field=field), self.assertRaises(rehearse.AcceptanceGateFailed):
                rehearse.constraints_gate({**good, field: 1})
        with self.assertRaises(rehearse.AcceptanceGateFailed):
            rehearse.constraints_gate({})

    def test_default_is_plan_only_without_opening_private_state(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["rehearse.py"]), contextlib.redirect_stdout(output), patch.object(
            rehearse, "execute", side_effect=AssertionError("default must not execute")
        ):
            rehearse.main()
        self.assertEqual(json.loads(output.getvalue())["mode"], "plan_only")

    def test_restore_url_must_be_loopback_and_use_isolated_database_namespace(self):
        with self.assertRaises(RuntimeError):
            rehearse.target_url("postgresql://user@production:5432/source", "tkos_convergence_new")
        with self.assertRaises(RuntimeError):
            rehearse.target_url("postgresql://user@127.0.0.1:5432/source", "existing_database")
        self.assertTrue(rehearse.target_url("postgresql://user@127.0.0.1:5432/source", "tkos_convergence_new").endswith("/tkos_convergence_new"))

    def test_dump_is_read_only_stream_and_private_file(self):
        def stream_fake_dump(command, *, stdout, **_):
            self.assertIn("default_transaction_read_only", command[-1])
            self.assertIn("pg_dump", command[-1])
            self.assertNotIn("pg_restore", command[-1])
            stdout.write(b"synthetic dump bytes")
        with tempfile.TemporaryDirectory() as directory, patch.object(rehearse, "run_quiet", side_effect=stream_fake_dump):
            path = Path(directory) / "source.dump"
            result = rehearse.production_dump(rehearse.preflight.DB_CONTAINERS[0], "synthetic_db", path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertGreater(result["bytes"], 0)
            self.assertEqual(len(result["sha256"]), 64)
            with self.assertRaises(FileExistsError):
                rehearse.production_dump(rehearse.preflight.DB_CONTAINERS[0], "synthetic_db", path)


if __name__ == "__main__":
    unittest.main()
