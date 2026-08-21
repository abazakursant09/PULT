"""LEGAL-PRELAUNCH-C1 — offline guard for blocker #14 (tracked seller-upload CSVs).

Proves, WITHOUT ever reading file contents:
  * no *.csv (any case) is tracked under backend/uploads/imports/;
  * .gitignore carries a recursive, case-covering ignore for those artifacts, and git actually
    treats a nested .csv AND .CSV as ignored at multiple depths;
  * the runtime upload path and orphan TTL are unchanged (this unit does not touch behavior);
  * the legal docs report the current-tree cleanup honestly and do NOT claim a git-history purge.

Uses only git plumbing (ls-files / check-ignore) and text of .gitignore / config / docs — never
the CSV bytes. No network, no shell beyond git metadata.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
GITIGNORE = REPO / ".gitignore"
CSV_IMPORT = BACKEND / "routers" / "csv_import.py"
LEGAL = REPO / "docs" / "legal"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_no_tracked_csv_under_uploads_imports():
    out = _git("ls-files", "backend/uploads/imports").stdout.splitlines()
    csvs = [p for p in out if p.lower().endswith(".csv")]
    assert csvs == [], f"tracked CSV artifacts must be 0, found {len(csvs)}"


def test_gitignore_has_recursive_csv_rule():
    body = _r(GITIGNORE)
    assert "backend/uploads/imports/**/*.csv" in body, "recursive CSV ignore rule missing"
    # No un-ignoring negation may re-include an upload artifact — a negation is inert only while a
    # parent-dir exclude masks it; if that blanket is ever removed the negation becomes a live hole.
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("!") and "uploads/imports" in s:
            raise AssertionError(f"un-ignoring negation for upload artifacts is forbidden: {s!r}")


def test_git_actually_ignores_nested_csv_any_case():
    # git check-ignore: rc 0 == path is ignored. Test both extensions at two nesting depths;
    # a single-level rule, a removed rule, or an un-ignoring negation makes one of these rc 1.
    for rel in (
        "backend/uploads/imports/u1/a.csv",
        "backend/uploads/imports/u1/deeper/b.csv",
        "backend/uploads/imports/u1/a.CSV",
        "backend/uploads/imports/u1/deeper/b.CSV",
    ):
        rc = _git("check-ignore", rel).returncode
        assert rc == 0, f"git does not ignore {rel} (check-ignore rc={rc})"


def test_runtime_upload_path_and_ttl_unchanged():
    src = _r(CSV_IMPORT)
    assert '"uploads" / "imports"' in src, "runtime upload path must be unchanged"
    assert "_ORPHAN_TTL_SECONDS = 3600" in src, "orphan TTL must be unchanged by this unit"


def _table_row(name: str, num: int) -> list[str] | None:
    """Parsed cells of the markdown table row in a docs/legal file whose first cell is `num`."""
    prefix = f"| {num} |"
    for line in _r(LEGAL / name).splitlines():
        if line.strip().startswith(prefix):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return None


def test_blocker_24_history_exposure_pinned():
    # Structural: the #24 row itself must exist and stay an open, unfinished history-exposure
    # blocker — a generic "OPEN" elsewhere (e.g. the #14 row) must not stand in for it.
    row = _table_row("launch-legal-checklist.md", 24)
    assert row is not None, "blocker #24 row missing from the checklist table"
    assert len(row) >= 4, f"#24 row malformed: {row}"
    joined = " ".join(row).lower()
    status = row[3].upper()
    # (2) meaning: git-history exposure / assessment / rewrite
    assert ("history" in joined or "истори" in joined) and (
        "rewrite" in joined or "blob" in joined or "экспозиц" in joined
    ), f"#24 must be the git-history exposure blocker, got: {row[1]!r}"
    # (3) explicitly not purged / needs a separate coordinated decision
    assert ("not performed" in joined) or ("separate" in joined) or ("отдельн" in joined), (
        "#24 must state history purge is NOT PERFORMED / requires a separate decision"
    )
    # (4) status stays open
    assert status in {"OPEN", "BLOCKED", "UNKNOWN", "PARTIAL"}, f"#24 status must stay open, got {row[3]!r}"
    for bad in ("DONE", "PASS", "READY", "VERIFIED", "CLOSED"):
        assert bad not in status, f"#24 must not be marked {bad} without a real decision"
    # (5) #14 stays PARTIAL and cannot substitute for #24
    r14 = _table_row("launch-legal-checklist.md", 14)
    assert r14 is not None and "PARTIAL" in r14[3].upper(), "#14 must stay PARTIAL, distinct from #24"


def test_docs_do_not_claim_history_purge():
    chk = _r(LEGAL / "launch-legal-checklist.md")
    ev = _r(LEGAL / "source-evidence.md")
    # honest: current-tree cleanup only; history stays an open, separate decision
    assert "NOT PERFORMED" in ev, "source-evidence must state history purge NOT PERFORMED"
    assert "history purge" in ev.lower(), "source-evidence must name the history-purge item"
    assert "OPEN" in chk, "checklist must keep the historical-exposure follow-up OPEN"
    # must NOT overclaim a full git-history deletion anywhere in the legal package
    for name in ("launch-legal-checklist.md", "source-evidence.md", "README.md"):
        body = _r(LEGAL / name)
        for bad in ("полностью удалены из Git", "history purged", "история очищена"):
            assert bad not in body, f"{name} must not claim a full git-history purge ({bad!r})"
