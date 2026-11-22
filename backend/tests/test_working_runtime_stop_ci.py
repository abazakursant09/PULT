"""Portable CI/receipt contracts only. NOT systemd, cgroup or kernel proof."""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import ModuleType
import subprocess
import sys
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "ops/backup/tests/working_runtime_stop_proof.py"
GUARD = "Working runtime independent stop contracts"
NATIVE = "Native working runtime construction and independent stop"
RUN = "python -B -m pytest backend/tests/test_working_runtime_stop.py backend/tests/test_working_runtime_stop_worker.py backend/tests/test_working_runtime_stop_ci.py -q"
NATIVE_RUN = "sudo -n -- unshare --net /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3.12 -I -S -B ops/backup/tests/working_runtime_stop_proof.py"
PATHS = {"ops/backup/**", "backend/tests/test_working_runtime_stop.py", "backend/tests/test_working_runtime_stop_worker.py",
         "backend/tests/test_working_runtime_stop_ci.py", "docs/working-runtime-stop.md"}


def captured():
    module = ModuleType("_portable_working_stop_receipt")
    module.__file__ = str(PROOF)
    exec(compile(PROOF.read_bytes(), str(PROOF), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def check_workflow(value):
    assert value["permissions"] == {"contents": "read"}
    assert value["on"]["push"]["branches"] == ["master"]
    job = value["jobs"]["synthetic"]
    assert not {"if", "continue-on-error"}.intersection(job)
    for event in ("push", "pull_request"):
        assert PATHS <= set(value["on"][event]["paths"])
    steps = job["steps"]
    names = [row.get("name") for row in steps]
    assert names.count(GUARD) == names.count(NATIVE) == 1
    guard = names.index(GUARD)
    assert steps[guard:guard + 2] == [{"name": GUARD, "run": RUN}, {"name": NATIVE, "run": NATIVE_RUN}]
    assert names[guard - 1] == "Joint synthetic PG operator lifetime no runtime admission"
    assert names[guard + 2] == "Guard dormant synthetic PG source-envelope contracts"
    assert names.index("Download seven hash-pinned custodian v2 proof wheels") < guard


def model(proof):
    rows = []
    for case in proof.CASES:
        rows.append(dict(case=case, outcome="PASS", root_status="CONFIRMED" if case in proof.POSITIVE else
            "REFUSED" if case in proof.EARLY else "NO_PARENT_RECEIPT" if case in proof.NO_CONTROLLER_RECEIPT else "UNKNOWN",
            held=case not in proof.EARLY | {"queued_go_death"}, state_unchanged=True, credential_unchanged=True,
            lease_released=True, cgroup_empty=True, launcher_reaped=True, fence_preserved=case not in proof.EARLY,
            kernel_restart_refused=case == "sticky_fence_restart", sentinel_untouched=case == "foreign_unit_cleanup_refused",
            lineage="ACTUAL_FORK_DESCENDANT" if case == "term_ignoring_descendant" else "NONE",
            closed_evidence=case in proof.POSITIVE | proof.PARENT_ONLY_FAULTS,
            fault_reached=case if case in proof.FAULT_REACHED else None,
            postcondition_injection=case if case in proof.POSTCONDITION_INJECTIONS else None,
            deadline_before_cleanup=case in {"freeze_actor", "freeze_controller", "term_ignoring_descendant"},
            descendant_sigkill_confirmed=case == "term_ignoring_descendant",
            base_execute_succeeded=case in proof.POSITIVE | proof.POSTCONDITION_INJECTIONS))
    return dict(format=proof.FORMAT, status="PASS", scope="ARTIFICIAL_LOCAL_CONSTRUCTION_AND_STOP_ONLY",
        tests=len(rows), skips=0, cases=rows, cleanup_confirmed=True, owned_units_empty=True, private_test_ipc_used=True,
        source_hashes_before={"fixed": "a" * 64}, source_hashes_after={"fixed": "a" * 64},
        repository_byte_hashes_before={"fixed": "b" * 64}, repository_byte_hashes_after={"fixed": "b" * 64},
        wheel_hashes_before=dict(proof.WHEEL_PINS), wheel_hashes_after=dict(proof.WHEEL_PINS),
        **dict.fromkeys(proof.FLAGS, False))


class ReceiptContracts(unittest.TestCase):
    def setUp(self):
        self.proof = captured()
        self.value = model(self.proof)

    def test_complete_pure_model_only_not_native_evidence(self):
        self.assertIs(self.proof.validate_results(self.value), True)

    def test_partial_duplicate_skip_failed_or_lying_receipt_refused(self):
        for mutation in ("missing", "duplicate", "skip", "count_bool", "fail", "cleanup", "flag", "extra", "hash", "lineage", "held", "root_status"):
            with self.subTest(mutation=mutation):
                value = deepcopy(self.value)
                if mutation == "missing": value["cases"].pop()
                elif mutation == "duplicate": value["cases"][-1] = dict(value["cases"][0])
                elif mutation == "skip": value["skips"] = 1
                elif mutation == "count_bool": value["tests"] = True
                elif mutation == "fail": value["cases"][0]["outcome"] = "FAIL"
                elif mutation == "cleanup": value["cleanup_confirmed"] = False
                elif mutation == "flag": value["launch_admitted"] = True
                elif mutation == "extra": value["unreviewed"] = True
                elif mutation == "hash": value["source_hashes_after"]["fixed"] = "c" * 64
                elif mutation == "lineage": value["cases"][0]["lineage"] = "ACTUAL_FORK_DESCENDANT"
                elif mutation == "held": value["cases"][0]["held"] = False
                else: value["cases"][0]["root_status"] = "UNKNOWN"
                with self.assertRaises(self.proof.ProofError):
                    self.proof.validate_results(value)

    def test_cleanup_fields_require_exact_true_not_one(self):
        for key in ("cgroup_empty", "launcher_reaped", "state_unchanged", "credential_unchanged", "lease_released"):
            value = deepcopy(self.value)
            value["cases"][0][key] = 1
            with self.assertRaises(self.proof.ProofError):
                self.proof.validate_results(value)

    def test_fault_reach_and_closed_worker_prerequisites_cannot_be_omitted_or_forged(self):
        for case in self.proof.FAULT_REACHED:
            for bad in (None, "unrelated_fault", True):
                value = deepcopy(self.value)
                value["cases"][self.proof.CASES.index(case)]["fault_reached"] = bad
                with self.subTest(case=case, bad=bad), self.assertRaises(self.proof.ProofError):
                    self.proof.validate_results(value)
        for case in self.proof.PARENT_ONLY_FAULTS:
            for bad in (False, 1):
                value = deepcopy(self.value)
                value["cases"][self.proof.CASES.index(case)]["closed_evidence"] = bad
                with self.subTest(case=case, closed=bad), self.assertRaises(self.proof.ProofError):
                    self.proof.validate_results(value)
        for case in self.proof.POSTCONDITION_INJECTIONS:
            for key, bad in (("base_execute_succeeded", False), ("base_execute_succeeded", 1),
                             ("postcondition_injection", None), ("postcondition_injection", "physical_os_failure")):
                value = deepcopy(self.value)
                value["cases"][self.proof.CASES.index(case)][key] = bad
                with self.subTest(case=case, key=key, bad=bad), self.assertRaises(self.proof.ProofError):
                    self.proof.validate_results(value)

    def test_deadline_and_exact_sigkill_evidence_are_mandatory(self):
        for case in ("freeze_actor", "freeze_controller", "term_ignoring_descendant"):
            value = deepcopy(self.value)
            value["cases"][self.proof.CASES.index(case)]["deadline_before_cleanup"] = False
            with self.subTest(case=case), self.assertRaises(self.proof.ProofError):
                self.proof.validate_results(value)
        value = deepcopy(self.value)
        value["cases"][self.proof.CASES.index("term_ignoring_descendant")]["descendant_sigkill_confirmed"] = False
        with self.assertRaises(self.proof.ProofError):
            self.proof.validate_results(value)
        child = dict(pid=46201, start_ticks=100)
        unit = "pult-call-gate-" + "a" * 32 + ".service"
        event = {"_PID": "1", "_BOOT_ID": "b" * 32, "UNIT": unit, "__MONOTONIC_TIMESTAMP": "2000000",
                 "MESSAGE": unit + ": Killing process 46201 (python3.12) with signal SIGKILL."}
        with patch.object(self.proof.os, "sysconf", return_value=100, create=True):
            self.assertTrue(self.proof.valid_sigkill(event, unit, child, "b" * 32, 3000000))
            for key, bad in (("_PID", "2"), ("_BOOT_ID", "c" * 32), ("UNIT", "foreign.service"),
                             ("__MONOTONIC_TIMESTAMP", "999999"), ("__MONOTONIC_TIMESTAMP", "3000001"),
                             ("MESSAGE", event["MESSAGE"].replace("46201", "46202")),
                             ("MESSAGE", event["MESSAGE"].replace("SIGKILL", "SIGTERM"))):
                self.assertFalse(self.proof.valid_sigkill(dict(event, **{key: bad}), unit, child, "b" * 32, 3000000))

    def test_seven_wheel_receipts_reject_missing_extra_drift_and_repins(self):
        wheel = next(iter(self.proof.WHEEL_PINS))
        for field in ("wheel_hashes_before", "wheel_hashes_after"):
            for bad in ("missing", "extra", "drift", "both-repinned", "non-dict"):
                value = deepcopy(self.value)
                if bad == "missing": del value[field][wheel]
                elif bad == "extra": value[field]["unreviewed.whl"] = "d" * 64
                elif bad == "drift": value[field][wheel] = "d" * 64
                elif bad == "both-repinned":
                    value["wheel_hashes_before"][wheel] = value["wheel_hashes_after"][wheel] = "d" * 64
                else: value[field] = []
                with self.subTest(field=field, bad=bad), self.assertRaises(self.proof.ProofError):
                    self.proof.validate_results(value)

    def test_outer_parent_compares_values_and_raw_bytes_not_just_input_keys(self):
        before = {"fixed": b"reviewed\r\n"}
        value = deepcopy(self.value)
        normalized, raw = self.proof.input_hashes(before)
        value.update(source_hashes_before=dict(normalized), source_hashes_after=dict(normalized),
                     repository_byte_hashes_before=dict(raw), repository_byte_hashes_after=dict(raw))
        pins = dict(self.proof.WHEEL_PINS)
        self.assertTrue(self.proof.validate_input_evidence(value, before, dict(before), pins, dict(pins)))
        for bad in ("source-claim", "raw-claim", "raw-only-change", "source-change", "extra-input", "wheel-change"):
            claimed, after, wheel_after = deepcopy(value), dict(before), dict(pins)
            if bad == "source-claim": claimed["source_hashes_before"]["fixed"] = claimed["source_hashes_after"]["fixed"] = "e" * 64
            elif bad == "raw-claim": claimed["repository_byte_hashes_before"]["fixed"] = claimed["repository_byte_hashes_after"]["fixed"] = "e" * 64
            elif bad == "raw-only-change": after["fixed"] = b"reviewed\n"
            elif bad == "source-change": after["fixed"] = b"changed\n"
            elif bad == "extra-input": after["extra"] = b"unreviewed\n"
            else: wheel_after[next(iter(pins))] = "e" * 64
            with self.subTest(bad=bad), self.assertRaises(self.proof.ProofError):
                self.proof.validate_input_evidence(claimed, before, after, pins, wheel_after)

    def test_frozen_fixture_libraries_are_exact_accepted_lf_bytes(self):
        for relative, wanted in self.proof.FIXED.values():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes().replace(b"\r\n", b"\n")).hexdigest(), wanted)
        self.assertEqual(len(self.proof.CASES), len(set(self.proof.CASES)))
        self.assertEqual(len(self.proof.CASES), 19)

    def test_new_worker_and_root_are_independently_pinned_before_native_admission(self):
        for relative, wanted in ((self.proof.STOP, self.proof.STOP_SHA), (self.proof.WORKER, self.proof.WORKER_SHA)):
            self.assertNotEqual(wanted, "0" * 64, "Main must fill the reviewed root pin before publishing/running")
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes().replace(b"\r\n", b"\n")).hexdigest(), wanted)

    def test_receipt_cannot_omit_or_add_a_parent_input_hash(self):
        self.assertIs(self.proof.validate_results(self.value, {"fixed"}), True)
        for expected in ({"fixed", "missing"}, {"different"}):
            with self.assertRaises(self.proof.ProofError):
                self.proof.validate_results(self.value, expected)

    def test_native_proof_has_no_top_level_resource_calls(self):
        tree = ast.parse(PROOF.read_bytes())
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.assertFalse(any(isinstance(child, ast.Call) for child in ast.walk(node)))

    def test_optimized_cli_refuses_before_resource_construction(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-O", str(PROOF)],
                                cwd=ROOT, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), dict(format=self.proof.FORMAT, status="NOT_RUN", tests=0, skips=0))


class WorkflowContracts(unittest.TestCase):
    def test_exact_mandatory_additive_steps_and_path_filters(self):
        value = yaml.load((ROOT / ".github/workflows/backup_restore_synthetic.yml").read_bytes(), Loader=yaml.BaseLoader)
        check_workflow(value)

    def test_skips_continuation_replacement_and_missing_filters_fail(self):
        original = yaml.load((ROOT / ".github/workflows/backup_restore_synthetic.yml").read_bytes(), Loader=yaml.BaseLoader)
        for bad in ("missing", "if", "continue", "wrong-run", "extra", "duplicate", "job-if", "job-continue", "push-path", "pr-path", "previous-step", "next-step"):
            with self.subTest(bad=bad):
                value = deepcopy(original)
                job = value["jobs"]["synthetic"]
                step = next(v for v in job["steps"] if v.get("name") == NATIVE)
                if bad == "missing": job["steps"].remove(step)
                elif bad == "if": step["if"] = "false"
                elif bad == "continue": step["continue-on-error"] = "true"
                elif bad == "wrong-run": step["run"] += " || true"
                elif bad == "extra": step["working-directory"] = "backend"
                elif bad == "duplicate": job["steps"].append(dict(step))
                elif bad == "job-if": job["if"] = "false"
                elif bad == "job-continue": job["continue-on-error"] = "true"
                elif bad == "push-path": value["on"]["push"]["paths"].remove("docs/working-runtime-stop.md")
                elif bad == "pr-path": value["on"]["pull_request"]["paths"].remove("backend/tests/test_working_runtime_stop_ci.py")
                elif bad == "previous-step": job["steps"][job["steps"].index(step) - 2]["name"] = "unreviewed predecessor"
                else: job["steps"][job["steps"].index(step) + 1]["name"] = "unreviewed successor"
                with self.assertRaises((AssertionError, ValueError)):
                    check_workflow(value)


if __name__ == "__main__":
    unittest.main()
