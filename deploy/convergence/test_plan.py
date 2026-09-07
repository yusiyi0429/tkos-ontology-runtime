"""Guard that planning cannot accidentally become an external migration."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("convergence_plan", Path(__file__).with_name("plan.py"))
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


class PlanBoundaryTests(unittest.TestCase):
    def test_default_plan_never_contacts_remote_or_marks_ready(self):
        with patch.object(planner, "remote", side_effect=AssertionError("must remain local")):
            result = planner.plan(None)
        self.assertFalse(result["applied"])
        self.assertFalse(result["ready_for_production_replacement"])
        self.assertIn("0017_dri_delivery.sql", [m["name"] for m in result["target_migrations"]])

    def test_sql_wraps_query_in_read_only_transaction_and_omits_stderr(self):
        with patch.object(planner, "remote", return_value='{"rows": 2}\n') as remote:
            self.assertEqual(planner.sql(planner.DB_CONTAINERS[0], "SELECT count(*) FROM users;"), [{"rows": 2}])
        arguments, = remote.call_args.args
        self.assertEqual(arguments[:3], ["docker", "exec", "-i"])
        statement = remote.call_args.kwargs["stdin"]
        self.assertTrue(statement.startswith("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"))
        self.assertTrue(statement.endswith("ROLLBACK;\n"))
        self.assertIn("statement_timeout='8s'", statement)
        with self.assertRaises(ValueError):
            planner.sql("unapproved-container", "SELECT 1;")

    def test_schema_or_data_equality_does_not_authorize_replacement(self):
        stats = [{"kind": "fingerprint", "table": "semantic_entities", "sha256": "same", "complete": True}]
        databases = [{"container": container, "database_slot": 0,
                      "tables": [{"schema": "public", "table": "semantic_entities"}],
                      "statistics": stats, "governed_rls": []} for container in planner.DB_CONTAINERS[:2]]
        result = planner.plan({"databases": databases, "errors": []})
        self.assertTrue(result["source_comparison"]["tables"][0]["equal"])
        self.assertFalse(result["ready_for_production_replacement"])
        self.assertIn("NO_AUTHORIZED_CUTOVER_MANIFEST", result["blockers"])
        self.assertIn("SOURCE_SCHEMA_HAS_NOT_BEEN_REHEARSED_AT_TARGET_VERSION", result["blockers"])

    def test_partial_or_ambiguous_sources_are_not_reported_equal(self):
        self.assertFalse(planner.comparison({"databases": []})["comparable"])
        self.assertNotIn("gov_credentials", planner.FINGERPRINT_TABLES)
        self.assertNotIn("users", planner.FINGERPRINT_TABLES)

    def test_plan_never_promotes_working_memory_subset_to_single_source_selection(self):
        result = planner.plan({"databases": [], "errors": []})
        self.assertIsNone(result["source_recommendation"]["candidate"])
        self.assertEqual(result["source_recommendation"]["status"], "two_source_preservation_manifest_required")


if __name__ == "__main__":
    unittest.main()
