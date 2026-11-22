"""Mandatory source-envelope CI contract; portable checks are not native proof."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
CATALOGUE = REPO/"ops/backup/operator_pg/source-profile.json"
PROOF = REPO/"ops/backup/tests/operator_pg_admission_proof.py"
GUARD = "Guard dormant synthetic PG source-envelope contracts"
NATIVE = "Native synthetic PG source-envelope admission only"
PREDECESSOR = "Joint synthetic PG operator lifetime no runtime admission"
CASES = ("extra_dependency_directory", "authorized", "bad_approval", "source_changed",
         "wrong_mode", "extra_dependency", "changed_native_extension",
         "profile_replaced_same_bytes", "dependency_replaced_same_bytes",
         "legacy_loader_rejects_new_envelope", "shared_tmp_churn", "source_symlink",
         "source_hardlink", "source_mode", "source_owner", "source_acl",
         "source_mutated_after_load", "dependency_mutated_after_load")


def captured(relative):
    file = REPO/relative
    module = ModuleType("_portable_source_envelope_"+file.stem)
    module.__file__ = str(file)
    exec(compile(file.read_bytes(), str(file), "exec", dont_inherit=True), module.__dict__)
    return module


class AdmissionCIContracts(unittest.TestCase):
    def test_catalogue_is_exact_unapproved_draft_and_all_fifteen_sources_match(self):
        value = json.loads(CATALOGUE.read_bytes())
        bootstrap = captured("ops/backup/operator_pg/bootstrap.py")
        self.assertEqual(value["format"], "pult-operator-pg-source-catalogue-draft-v1")
        self.assertEqual(value["status"], "DRAFT_NOT_OPERATIONAL_PROFILE_OR_APPROVAL")
        self.assertNotEqual(value["format"], bootstrap.FORMAT)
        self.assertEqual(value["sources"].keys(), bootstrap.PATHS.keys())
        self.assertEqual(len(value["sources"]), 15)
        self.assertEqual(value["admitted_operations"], [])
        for flag in ("identity_enrollment_admitted", "journal_dispatch_admitted",
                     "purge_or_restore_admitted", "production_pg_source_admission_proved"):
            self.assertIs(value[flag], False)
        for name, row in value["sources"].items():
            with self.subTest(role=name):
                self.assertEqual(row["path"], bootstrap.PATHS[name])
                body = (REPO/row["path"]).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(row["sha256"], hashlib.sha256(body).hexdigest())

    def test_wheels_reuse_exact_accepted_joint_proof_catalogue(self):
        value = json.loads(CATALOGUE.read_bytes())
        accepted = json.loads((REPO/"ops/backup/tests/operator_pg_lifetime/source-manifest.json").read_bytes())
        bootstrap = captured("ops/backup/operator_pg/bootstrap.py")
        self.assertEqual(value["wheels"], bootstrap.WHEELS)
        self.assertEqual(value["wheels"], accepted["wheels"])
        self.assertEqual(len(value["wheels"]), 4)

    def workflow(self):
        return yaml.load((REPO/".github/workflows/backup_restore_synthetic.yml").read_bytes(),
                         Loader=yaml.BaseLoader)

    def test_new_native_and_guard_steps_are_mandatory_after_unchanged_joint_gate(self):
        steps = self.workflow()["jobs"]["synthetic"]["steps"]
        names = [step.get("name") for step in steps]
        self.assertEqual(names.count(GUARD), 1)
        self.assertEqual(names.count(NATIVE), 1)
        self.assertLess(names.index(PREDECESSOR), names.index(GUARD))
        self.assertLess(names.index(GUARD), names.index(NATIVE))
        for name in (GUARD, NATIVE):
            step = steps[names.index(name)]
            with self.subTest(name=name):
                self.assertNotIn("if", step)
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("|| true", step["run"])
                self.assertNotIn("set +e", step["run"])
        self.assertEqual(steps[names.index(GUARD)]["run"],
            "python -B -m pytest backend/tests/test_operator_pg_admission.py "
            "backend/tests/test_operator_pg_admission_ci.py -q")
        self.assertEqual(steps[names.index(NATIVE)]["run"],
            'sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B '
            'ops/backup/tests/operator_pg_admission_proof.py --wheels-dir "$RUNNER_TEMP/operator-pg-wheels"')

    def test_source_changes_trigger_ci_without_new_permissions_or_secrets(self):
        value = self.workflow()
        self.assertEqual(value["permissions"], {"contents": "read"})
        self.assertEqual(value["jobs"]["synthetic"]["timeout-minutes"], "25")
        for trigger in ("pull_request", "push"):
            paths = value["on"][trigger]["paths"]
            for relative in ("ops/backup/**", "backend/tests/test_operator_pg_admission.py",
                             "backend/tests/test_operator_pg_admission_ci.py",
                             "docs/operator-pg-source-admission.md"):
                with self.subTest(trigger=trigger, path=relative):
                    self.assertIn(relative, paths)

    def test_exact_eighteen_native_cases_and_frozen_setup_helpers(self):
        proof = captured("ops/backup/tests/operator_pg_admission_proof.py")
        self.assertEqual(proof.CASES, CASES)
        for role, (relative, wanted) in proof.HELPERS.items():
            with self.subTest(helper=role):
                body = (REPO/relative).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(body).hexdigest(), wanted)

    def test_actual_optimized_cli_refuses_before_resource_setup(self):
        for optimize in ("-O", "-OO"):
            with self.subTest(optimize=optimize):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", optimize, str(PROOF)],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(json.loads(result.stdout), dict(outcome="NOT_RUN",
                    reason="optimized_pg_admission_proof_refused"))

    def test_pg_observation_evidence_requires_started_and_verified_authorized_case(self):
        proof = captured("ops/backup/tests/operator_pg_admission_proof.py")
        self.assertEqual(proof.pg_evidence("authorized", True, True), dict(
            pg_fixture_started=True, no_pg_started_for_intake=False,
            reader_backends_absent_before_pg_teardown=True, application_rows_unchanged=True))
        for started, verified in ((False, False), (False, True), (True, False), (1, True)):
            with self.subTest(started=started, verified=verified), self.assertRaises(proof.Refused):
                proof.pg_evidence("authorized", started, verified)

    def test_intake_cases_cannot_claim_database_observation_or_started_fixture(self):
        proof = captured("ops/backup/tests/operator_pg_admission_proof.py")
        for case in CASES:
            if case == "authorized":
                continue
            with self.subTest(case=case):
                self.assertEqual(proof.pg_evidence(case, False, False), dict(
                    pg_fixture_started=False, no_pg_started_for_intake=True,
                    reader_backends_absent_before_pg_teardown=None, application_rows_unchanged=None))
                for started, verified in ((True, False), (False, True), (True, True)):
                    with self.subTest(started=started, verified=verified), self.assertRaises(proof.Refused):
                        proof.pg_evidence(case, started, verified)


if __name__ == "__main__":
    unittest.main(verbosity=2)
