"""Portable joint-PG contracts; NOT native/systemd/PG or Docker proof.

Only optimized CLI refusal spawns Python, with -I -S -B and no resource path.
All verdict tests execute fresh source modules then forbid native entry points.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
HERE = REPO / "ops/backup/tests/operator_pg_lifetime"
CASES = ("authorized", "close_then_raise", "blocked_close_hang",
         "controller_death", "wrong_peer", "restored")
FLAGS = {"identity_enrollment_admitted": False, "journal_dispatch_admitted": False,
         "purge_or_restore_admitted": False}
SUCCESS = "OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION"
UNKNOWN = "UNKNOWN_REQUIRES_REVIEW"
VERSIONS = {"asyncpg": "0.31.0", "greenlet": "3.5.5", "sqlalchemy": "2.0.51",
            "typing-extensions": "4.16.0"}
WHEELS = {
    "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl":
        "aad7a33913fb8bcb5454313377cc330fbb19a0cd5faa7272407d8a0c4257b671",
    "greenlet-3.5.5-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl":
        "147b25a42e5ca5be3d42356e8f608b37af715a1c196e9bf9d1627f3341adfe1d",
    "sqlalchemy-2.0.51-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl":
        "1d21ce524ab86c23046e992a5b81cb54c21079c6df6e78b8fc77d77cac70a6b9",
    "typing_extensions-4.16.0-py3-none-any.whl":
        "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
STEPS = ("Guard joint synthetic PG operator lifetime contracts",
         "Download exact joint PG proof wheels",
         "Joint synthetic PG operator lifetime no runtime admission")


def fresh(file):
    module = ModuleType("_portable_joint_contract")
    module.__file__ = str(file)
    exec(compile(file.read_bytes(), str(file), "exec", dont_inherit=True), module.__dict__)
    return module


def lock_blocks(file):
    text = file.read_text().replace("\r\n", "\n")
    pattern = r"(?m)^([A-Za-z0-9_.-]+)==([^\n]+)(?:\n[ \t]+[^\n]+)*"
    return {match[1].lower().replace("_", "-"): match[0].rstrip()
            for match in re.finditer(pattern, text)}


def good_results():
    results = []
    for case in CASES:
        receipt = None if case == "controller_death" else dict(
            status=SUCCESS if case == "authorized" else UNKNOWN,
            cleanup_confirmed=True, cgroup_empty=True, pipe_eof_confirmed=True,
            child_reaped=True, observation_sha256="a" * 64 if case == "authorized" else None,
            **FLAGS)
        results.append(dict(case=case, outcome="PASS", owned_resources_cleanup_confirmed=True,
            reader_backends_absent_before_pg_teardown=True, application_rows_unchanged=True,
            production_pg_source_admission_proved=False,
            legacy_supervision_receipt_test_payload_only=receipt, **FLAGS))
    return results


class JointPGContracts(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((HERE / "source-manifest.json").read_bytes())
        self.gate = fresh(HERE / "gate.py")
        forbidden = mock.Mock(side_effect=AssertionError("portable test cannot access native resources"))
        self.gate.os = SimpleNamespace()
        self.gate.Path = forbidden
        self.gate.subprocess = SimpleNamespace(run=forbidden, Popen=forbidden)
        for name in ("capture", "sources", "preflight", "run_case", "wheel_bodies", "install_wheels"):
            setattr(self.gate, name, forbidden)

    def denied(self, mutate):
        results = good_results()
        mutate(results)
        self.assertIs(self.gate.valid_results(results), False)

    def test_manifest_is_test_composition_not_runtime_approval(self):
        self.assertEqual(self.manifest["format"], "pult-operator-pg-joint-test-sources-v1")
        self.assertEqual(self.manifest["status"], "TEST_COMPOSITION_ONLY_NO_PG_RUNTIME_ADMISSION")
        self.assertEqual(self.manifest["admitted_operations"], [])
        self.assertEqual(self.manifest["flags"], FLAGS)
        self.assertTrue(all(value is False for value in self.manifest["flags"].values()))
        self.assertNotIn("operator_approval", self.manifest)

    def test_manifest_has_exact_six_cases_in_order(self):
        self.assertEqual(tuple(self.manifest["cases"]), CASES)
        self.assertEqual(self.gate.CASES, CASES)
        self.assertEqual(len(set(CASES)), 6)

    def test_manifest_has_exact_fourteen_paths_and_current_lf_hashes(self):
        profile = fresh(REPO / "ops/backup/operator_host/profile.py")
        expected = dict(profile.PATHS,
            baseline_helper="ops/backup/tests/operator_offline_host_proof.py",
            policy="backend/services/account_closure_policy.py",
            reviewer="backend/services/erasure_operator_review.py")
        self.assertEqual(set(self.manifest["sources"]), set(expected))
        self.assertEqual(len(expected), 14)
        for role, relative in expected.items():
            with self.subTest(role=role):
                item = self.manifest["sources"][role]
                self.assertEqual(set(item), {"path", "sha256"})
                self.assertEqual(item["path"], relative)
                body = (REPO / relative).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(item["sha256"], hashlib.sha256(body).hexdigest())

    def test_accepted_offline_runtime_mode_and_catalogue_are_unchanged(self):
        profile = fresh(REPO / "ops/backup/operator_host/profile.py")
        bootstrap = fresh(REPO / "ops/backup/operator_host/bootstrap.py")
        self.assertEqual(profile.MODE, "SYNTHETIC_REVIEW_ENDPOINT_ONLY")
        self.assertEqual(bootstrap.MODE, profile.MODE)
        self.assertEqual(profile.PINS, bootstrap.PINS)
        catalogue = json.loads((REPO / "ops/backup/operator_host/source-profile.json").read_bytes())
        self.assertEqual(catalogue["status"], "DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED")
        self.assertIsNone(catalogue["operator_approval"])
        self.assertIs(catalogue["lifecycle"]["live_authority"], False)
        for role in profile.PATHS:
            self.assertEqual(self.manifest["sources"][role], catalogue["sources"][role])

    def test_manifest_pins_cached_pg_image_and_isolated_runtime(self):
        self.assertEqual(self.manifest["postgres_image"],
            "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777")
        self.assertEqual(self.manifest["runtime"], dict(
            executable="/usr/bin/python3.12", flags=["-I", "-S", "-B"], optimization=0))

    def test_four_wheel_hashes_and_versions_are_not_repinned(self):
        self.assertEqual(self.manifest["wheels"], WHEELS)
        self.assertEqual(self.manifest["versions"], VERSIONS)
        blocks = lock_blocks(HERE / "requirements.lock")
        self.assertEqual(set(blocks), set(VERSIONS))
        for package, version in VERSIONS.items():
            self.assertRegex(blocks[package], "^" + re.escape(package + "==" + version) + r"(?:[ \t]|$)")
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", (HERE / "requirements.lock").read_text()))
        self.assertTrue(set(WHEELS.values()) <= hashes)

    def test_all_four_requirement_blocks_are_exact_backend_ci_copies(self):
        proof = lock_blocks(HERE / "requirements.lock")
        accepted = lock_blocks(REPO / "backend/requirements-ci.lock")
        self.assertEqual(set(proof), set(VERSIONS))
        for name in VERSIONS:
            with self.subTest(package=name):
                self.assertEqual(proof[name], accepted[name])

    def test_valid_results_accepts_complete_nonadmitting_six(self):
        self.assertIs(self.gate.valid_results(good_results()), True)

    def test_valid_results_rejects_non_list(self):
        for value in (None, {}, tuple(good_results()), "PASS"):
            with self.subTest(value=type(value).__name__):
                self.assertIs(self.gate.valid_results(value), False)

    def test_valid_results_rejects_partial_inventory(self):
        for count in range(6):
            self.assertIs(self.gate.valid_results(good_results()[:count]), False)

    def test_valid_results_rejects_extra_case(self):
        self.denied(lambda rows: rows.append(copy.deepcopy(rows[0])))

    def test_valid_results_rejects_reordered_cases(self):
        self.denied(lambda rows: rows.reverse())

    def test_valid_results_rejects_duplicate_case(self):
        self.denied(lambda rows: rows[1].update(case="authorized"))

    def test_valid_results_rejects_fail(self):
        for index in range(6):
            with self.subTest(index=index):
                self.denied(lambda rows: rows[index].update(outcome="FAIL"))

    def test_valid_results_rejects_skipped_outcome(self):
        for outcome in ("SKIP", "SKIPPED", "NOT_RUN"):
            with self.subTest(outcome=outcome):
                self.denied(lambda rows: rows[0].update(outcome=outcome))

    def test_valid_results_rejects_non_mapping_case(self):
        self.denied(lambda rows: rows.__setitem__(2, None))

    def test_valid_results_rejects_admission_flags(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.denied(lambda rows: rows[0].update({flag: True}))

    def test_valid_results_rejects_false_like_non_boolean_flags(self):
        for value in (0, None, "false"):
            with self.subTest(value=value):
                self.denied(lambda rows: rows[0].update(identity_enrollment_admitted=value))

    def test_valid_results_rejects_cleanup_uncertainty(self):
        for value in (False, None, 1):
            with self.subTest(value=value):
                self.denied(lambda rows: rows[0].update(owned_resources_cleanup_confirmed=value))

    def test_valid_results_rejects_reader_backend_leak(self):
        self.denied(lambda rows: rows[0].update(reader_backends_absent_before_pg_teardown=False))

    def test_valid_results_rejects_changed_application_rows(self):
        self.denied(lambda rows: rows[0].update(application_rows_unchanged=False))

    def test_valid_results_rejects_production_source_admission_lie(self):
        for value in (True, None, 0):
            with self.subTest(value=value):
                self.denied(lambda rows: rows[0].update(production_pg_source_admission_proved=value))

    def test_valid_results_rejects_missing_required_case_field(self):
        for field in ("case", "outcome", "owned_resources_cleanup_confirmed",
                      "reader_backends_absent_before_pg_teardown", "application_rows_unchanged",
                      "production_pg_source_admission_proved", *FLAGS):
            with self.subTest(field=field):
                self.denied(lambda rows: rows[0].pop(field))

    def test_valid_results_rejects_admitting_parent_receipt(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.denied(lambda rows: rows[0]["legacy_supervision_receipt_test_payload_only"].update({flag: True}))

    def test_valid_results_rejects_positive_status_on_negative_review(self):
        for index in (1, 2, 4, 5):
            with self.subTest(index=index):
                self.denied(lambda rows: rows[index]["legacy_supervision_receipt_test_payload_only"].update(status=SUCCESS))

    def test_valid_results_rejects_observation_on_unknown(self):
        self.denied(lambda rows: rows[1]["legacy_supervision_receipt_test_payload_only"].update(observation_sha256="a" * 64))

    def test_valid_results_rejects_parent_receipt_after_controller_death(self):
        self.denied(lambda rows: rows[3].update(legacy_supervision_receipt_test_payload_only={**FLAGS, "status": UNKNOWN}))

    def test_valid_results_rejects_positive_receipt_cleanup_lies(self):
        for index in (0, 1, 2, 4, 5):
            for field in ("cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped"):
                with self.subTest(index=index, field=field):
                    self.denied(lambda rows: rows[index]["legacy_supervision_receipt_test_payload_only"].update({field: False}))

    def test_valid_results_requires_receipt_field_even_for_dead_controller(self):
        self.denied(lambda rows: rows[3].pop("legacy_supervision_receipt_test_payload_only"))

    def test_valid_results_rejects_bad_or_missing_observation_digest(self):
        for value in (None, True, "A" * 64, "a" * 63, "g" * 64):
            with self.subTest(value=value):
                self.denied(lambda rows: rows[0]["legacy_supervision_receipt_test_payload_only"].update(observation_sha256=value))
        self.denied(lambda rows: rows[1]["legacy_supervision_receipt_test_payload_only"].pop("observation_sha256"))

    def test_optimized_cli_refuses_before_resources_on_windows_or_linux(self):
        env = {key: value for key, value in os.environ.items()
               if key.upper() in ("PATH", "SYSTEMROOT", "WINDIR")}
        for flag in ("-O", "-OO"):
            with self.subTest(flag=flag):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", flag, str(HERE / "gate.py")],
                    capture_output=True, timeout=15, check=False, env=env)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(json.loads(result.stdout), dict(
                    outcome="NOT_RUN", reason="optimized_joint_pg_gate_refused"))

    def test_three_ci_steps_are_mandatory_exact_and_after_teardown(self):
        text = (REPO / ".github/workflows/backup_restore_synthetic.yml").read_text()
        matches = list(re.finditer(r"(?m)^([ \t]*)- name: (.+)$", text))
        blocks = {}
        positions = {}
        for match in matches:
            name = match[2].strip()
            if name not in STEPS and name != "Teardown":
                continue
            self.assertNotIn(name, blocks)
            following = re.search(r"(?m)^" + re.escape(match[1]) + r"- ", text[match.end():])
            end = len(text) if following is None else match.end() + following.start()
            blocks[name], positions[name] = text[match.start():end], match.start()
        self.assertTrue(all(name in blocks for name in (*STEPS, "Teardown")))
        self.assertEqual(sorted(STEPS, key=positions.get), list(STEPS))
        for name in STEPS:
            with self.subTest(step=name):
                self.assertGreater(positions[name], positions["Teardown"])
                self.assertNotRegex(blocks[name], r"(?m)^\s*(if|continue-on-error):")
                self.assertNotRegex(blocks[name], r"\|\|\s*true\b|\bset\s+\+e\b")
        self.assertIn("python -B -m pytest backend/tests/test_operator_pg_lifetime_ci.py -q", blocks[STEPS[0]])
        download = blocks[STEPS[1]]
        for required in ("--require-hashes", "--only-binary", "operator_pg_lifetime/requirements.lock", "operator-pg-wheels"):
            self.assertIn(required, download)
        native = re.sub(r"\\\r?\n\s*", " ", blocks[STEPS[2]])
        self.assertIn('sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B '
            'ops/backup/tests/operator_pg_lifetime/gate.py --wheels-dir "$RUNNER_TEMP/operator-pg-wheels"', native)


if __name__ == "__main__":
    unittest.main(verbosity=2)
