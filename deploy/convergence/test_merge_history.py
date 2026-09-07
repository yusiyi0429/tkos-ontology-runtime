from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import merge_history as merge
import rehearse


class PreservationManifestTests(unittest.TestCase):
    def entry(self, mode="aw_superset"):
        memory = {"shared": "a" * 64}
        aw = {**memory, "extra": "b" * 64}
        entry = {"schema": "public", "table": "runs", "mode": mode,
                 "expected_sets": {"memory_only": 0, "aw_only": 1, "shared": 1},
                 "memory_fingerprint": merge.fingerprint(memory), "aw_fingerprint": merge.fingerprint(aw),
                 "union_fingerprint": merge.fingerprint({**memory, **aw})}
        return entry, memory, aw

    def test_manifest_has_exact_53_table_classification(self):
        manifest = json.loads(merge.MANIFEST.read_text())
        merge.validate_manifest(manifest, rehearse.approved_legacy_inventory())
        self.assertEqual(len([t for t in manifest["tables"] if t["mode"] == "equal"]), 42)

    def test_shared_primary_key_conflict_is_rejected_before_subset_merge(self):
        entry, memory, aw = self.entry()
        aw["shared"] = "c" * 64
        with self.assertRaisesRegex(merge.MergeRejected, "SHARED_PRIMARY_KEY_CONTENT_CONFLICT"):
            merge.validate_records(entry, memory, aw)

    def test_unreviewed_extra_row_and_changed_union_fail_manifest(self):
        entry, memory, aw = self.entry()
        merge.validate_records(entry, memory, aw)
        aw["unreviewed"] = "d" * 64
        with self.assertRaisesRegex(merge.MergeRejected, "SOURCE_DOES_NOT_MATCH_REVIEWED_MANIFEST"):
            merge.validate_records(entry, memory, aw)
        entry, memory, aw = self.entry()
        entry["union_fingerprint"]["sha256"] = "wrong"
        with self.assertRaisesRegex(merge.MergeRejected, "UNION_DOES_NOT_MATCH"):
            merge.validate_records(entry, memory, aw)

    def test_equal_or_memory_superset_cannot_consume_aw_only_rows(self):
        for mode in ("equal", "memory_superset"):
            with self.subTest(mode=mode), self.assertRaisesRegex(merge.MergeRejected, "SUBSET_DIRECTION_REJECTED"):
                merge.validate_records(*self.entry(mode))

    def test_unknown_table_wrong_class_or_wrong_pk_cannot_expand_allowlist(self):
        baseline = json.loads(merge.MANIFEST.read_text())
        for mutation in ("unknown", "mode", "pk"):
            manifest = deepcopy(baseline)
            entry = next(t for t in manifest["tables"] if t["schema"] == "public" and t["table"] == "runs")
            if mutation == "unknown": entry["table"] = "unreviewed_table"
            if mutation == "mode": entry["mode"] = "equal"
            if mutation == "pk": entry["primary_key"] = ["conversation_id"]
            with self.subTest(mutation=mutation), self.assertRaises(merge.MergeRejected):
                merge.validate_manifest(manifest, rehearse.approved_legacy_inventory())

    def test_remote_or_existing_application_database_is_rejected(self):
        for url in ("postgresql://localhost/existing", "postgresql://production/tkos_convergence_x"):
            with self.assertRaises(merge.MergeRejected): merge.require_local_database(url)


if __name__ == "__main__":
    unittest.main()
