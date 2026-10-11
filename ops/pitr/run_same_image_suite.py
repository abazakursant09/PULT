"""Run reviewed workflow shell matrices on ONE existing local image, on hosted CI only.

No build, pull, registry, upload or publication. Local image ID is NOT a registry digest.
The source workflow remains the owner of each test script and synthetic environment.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
PREFIX = ["Checkout", "Set up Python", "Set up Docker Buildx"]
SETUP = "TLS MinIO + network + S3 config"
SELECTED = {
    "synthetic": [SETUP, "Run synthetic PITR (base + WAL + T1/T2/T3 LSN restore)",
                  "PITR B1 negative matrix (fail-closed)", "Teardown"],
    "extended": [SETUP, "Extended fault matrix (probe + long-outage + concurrent + restart + corruption)",
                 "Teardown"],
}
SKIPPED = {
    "synthetic": ["Offline PITR contract guard", "Build PITR image (exact-pinned) + prove closure"],
    "extended": ["Offline guards (extended + B1 contract + base digest)", "Build PITR image (exact-pinned)"],
}


def plan(doc, suite, image_id):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("invalid local image ID")
    job = doc["jobs"]["pitr-" + suite]
    steps = job["steps"]
    expected = PREFIX + SKIPPED[suite] + ["Build checksum-verified MinIO test image"] + SELECTED[suite]
    if [s["name"] for s in steps] != expected:
        raise ValueError("workflow shape changed; review adapter")
    scripts = []
    for step in steps[-len(SELECTED[suite]):]:
        allowed = {"name", "run", "if"} if step["name"] == "Teardown" else {"name", "run"}
        if set(step) != allowed or ("if" in step and step["if"] != "always()"):
            raise ValueError("unsupported step semantics")
        script = step["run"]
        if not isinstance(script, str) or "${{" in script:
            raise ValueError("unsupported script expression")
        scripts.append((step["name"], script.replace("pult-pitr:ci", image_id)))
    env = job["env"]
    if set(env) != {"MINIO_IMAGE", "S3_KEY", "S3_SECRET", "CIPHER_PASS", "PGPW", "BUCKET", "STANZA"}:
        raise ValueError("unexpected suite environment")
    if any(not isinstance(v, str) or "${{" in v for v in env.values()):
        raise ValueError("unsupported environment expression")
    return scripts, env.copy()


def verify_image(image_id):
    result = subprocess.run(["docker", "image", "inspect", image_id, "--format",
                             "{{.Id}} {{.Os}}/{{.Architecture}}"],
                            check=True, capture_output=True, text=True)
    if result.stdout.strip() != image_id + " linux/amd64":
        raise ValueError("image identity/platform mismatch")


def execute(scripts, env, run):
    """Always run the original teardown, while preserving matrix failures."""
    try:
        for name, script in scripts[:-1]:
            print("Running: " + name, flush=True)
            run(script, env)
    finally:
        run(scripts[-1][1], env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SELECTED)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args(argv)
    if (sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("dedicated GitHub-hosted Linux runner required")
    doc = yaml.safe_load((ROOT / f".github/workflows/pitr_{args.suite}.yml").read_text())
    scripts, extra = plan(doc, args.suite, args.image_id)
    verify_image(args.image_id)
    env = os.environ.copy()
    env.update(extra)

    def run(script, environment):
        subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                       cwd=ROOT, env=environment, check=True, timeout=2700)

    execute(scripts, env, run)
    verify_image(args.image_id)
    print(f"SOURCE_SUITE_PASS suite={args.suite} local_image_id={args.image_id}")


if __name__ == "__main__":
    main()
