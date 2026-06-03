"""
Sprint 68 Architecture Audit
============================
Static audit of business-pult repo (FastAPI backend + Next.js frontend).

Checks:
  1. Router files present in backend/routers/ vs registered in backend/main.py
  2. Model files vs exports in backend/models/__init__.py
  3. Frontend route directories missing page.tsx
  4. Orphan models (never imported by routers/services/logic)
  5. TODO/FIXME/XXX/HACK markers across the codebase
  6. Oversized files (>500 LOC)
  7. Migrations sanity (alembic head vs files present)
  8. Required env keys vs .env.example
  9. Frontend pages whose route is not referenced by any link

Output:
  - Console summary with PASS / WARN / FAIL per check
  - Full JSON: C:/business-pult/qa/sprint_68_arch_audit.json
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\business-pult")
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
QA = ROOT / "qa"
QA.mkdir(parents=True, exist_ok=True)

REPORT: dict = {"checks": {}, "stats": {}}
EXIT_FAIL = 0


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _py_files(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts and ".next" not in p.parts]


def _ts_files(base: Path) -> list[Path]:
    out: list[Path] = []
    for ext in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        for p in base.rglob(ext):
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            out.append(p)
    return out


def check_routers_registered() -> None:
    global EXIT_FAIL
    routers_dir = BACKEND / "routers"
    main_py = _read(BACKEND / "main.py")
    files = [p.stem for p in routers_dir.glob("*.py")
             if p.stem not in ("__init__",)]
    # detect `from .routers import X` or `app.include_router(X.router)` etc.
    imported = set()
    for m in re.finditer(r"from\s+(?:\.|backend\.)?routers(?:\.(\w+))?\s+import\s+([\w,\s\*]+)", main_py):
        sub, names = m.group(1), m.group(2)
        if sub:
            imported.add(sub)
        for n in names.split(","):
            n = n.strip().strip("*")
            if n:
                imported.add(n)
    for m in re.finditer(r"include_router\(\s*(\w+)\b", main_py):
        imported.add(m.group(1))

    missing = sorted(set(files) - imported)
    extra = sorted(imported - set(files) - {"router"})
    status = "PASS" if not missing else "FAIL"
    if missing:
        EXIT_FAIL += 1
    REPORT["checks"]["routers_registered"] = {
        "status": status,
        "total_router_files": len(files),
        "not_included_in_main": missing,
        "imports_unmatched": extra,
    }


def check_model_exports() -> None:
    global EXIT_FAIL
    models_dir = BACKEND / "models"
    init_py = _read(models_dir / "__init__.py")
    files = sorted(p.stem for p in models_dir.glob("*.py") if p.stem != "__init__")
    exported = set()
    for m in re.finditer(r"from\s+\.(\w+)\s+import\s+([\w,\s]+)", init_py):
        exported.add(m.group(1))
    missing = [f for f in files if f not in exported]
    status = "PASS" if not missing else "WARN"
    REPORT["checks"]["model_exports"] = {
        "status": status,
        "total_model_files": len(files),
        "not_exported": missing,
    }


def check_frontend_pages() -> None:
    global EXIT_FAIL
    app = FRONTEND / "app"
    missing_page: list[str] = []
    route_dirs: list[str] = []
    for d in app.rglob("*"):
        if not d.is_dir():
            continue
        # Next.js app router: route dir contains page.tsx OR is a group/layout-only dir
        rel = d.relative_to(app).as_posix()
        if rel.startswith("api"):
            continue
        has_page = any((d / f).exists() for f in ("page.tsx", "page.ts", "page.jsx", "page.js"))
        has_layout = any((d / f).exists() for f in ("layout.tsx", "layout.ts"))
        has_route_file = any((d / f).exists() for f in ("route.ts", "route.tsx", "route.js"))
        # only flag leaf-ish dirs that contain other code files but no page
        files_in = [f for f in d.iterdir() if f.is_file()]
        if not files_in:
            continue
        route_dirs.append(rel)
        if not (has_page or has_route_file):
            # ignore if it's just a layout/template wrapper for children
            if not has_layout:
                missing_page.append(rel)
    REPORT["checks"]["frontend_pages"] = {
        "status": "PASS" if not missing_page else "WARN",
        "total_route_dirs": len(route_dirs),
        "missing_page_tsx": missing_page,
    }


def check_orphan_models() -> None:
    models_dir = BACKEND / "models"
    model_files = [p.stem for p in models_dir.glob("*.py") if p.stem != "__init__"]
    # search references in backend (excluding models/ itself)
    refs: dict[str, int] = {m: 0 for m in model_files}
    search_dirs = [BACKEND / d for d in ("routers", "services", "logic", "tasks", "schemas")]
    pat = re.compile(r"\bfrom\s+(?:backend\.)?models(?:\.(\w+))?\s+import\b|\bmodels\.(\w+)\b")
    for sd in search_dirs:
        if not sd.exists():
            continue
        for f in _py_files(sd):
            src = _read(f)
            for m in pat.finditer(src):
                name = m.group(1) or m.group(2)
                if name in refs:
                    refs[name] += 1
    # also count main.py
    src = _read(BACKEND / "main.py")
    for m in pat.finditer(src):
        name = m.group(1) or m.group(2)
        if name in refs:
            refs[name] += 1
    orphans = sorted(k for k, v in refs.items() if v == 0)
    REPORT["checks"]["orphan_models"] = {
        "status": "PASS" if not orphans else "WARN",
        "orphans": orphans,
        "ref_counts": refs,
    }


def check_todo_markers() -> None:
    pat = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b[: ]?([^\r\n]{0,120})")
    hits: list[dict] = []
    for f in _py_files(BACKEND) + _ts_files(FRONTEND):
        src = _read(f)
        for i, line in enumerate(src.splitlines(), 1):
            m = pat.search(line)
            if m:
                hits.append({
                    "file": str(f.relative_to(ROOT)).replace("\\", "/"),
                    "line": i,
                    "kind": m.group(1),
                    "msg": m.group(2).strip()[:100],
                })
    by_kind = defaultdict(int)
    for h in hits:
        by_kind[h["kind"]] += 1
    REPORT["checks"]["todo_markers"] = {
        "status": "PASS" if not hits else "WARN",
        "total": len(hits),
        "by_kind": dict(by_kind),
        "sample": hits[:30],
    }


def check_oversized_files() -> None:
    big: list[dict] = []
    for f in _py_files(BACKEND) + _ts_files(FRONTEND):
        try:
            n = len(_read(f).splitlines())
        except Exception:
            continue
        if n > 500:
            big.append({
                "file": str(f.relative_to(ROOT)).replace("\\", "/"),
                "loc": n,
            })
    big.sort(key=lambda x: -x["loc"])
    REPORT["checks"]["oversized_files"] = {
        "status": "PASS" if not big else "WARN",
        "threshold_loc": 500,
        "count": len(big),
        "top": big[:25],
    }


def check_migrations() -> None:
    mig_dir = BACKEND / "migrations"
    if not mig_dir.exists():
        REPORT["checks"]["migrations"] = {"status": "WARN", "msg": "no migrations dir"}
        return
    versions = mig_dir / "versions"
    files = sorted(versions.glob("*.py")) if versions.exists() else []
    # parse revision / down_revision
    revs: dict[str, str | None] = {}
    for f in files:
        src = _read(f)
        r = re.search(r"^revision\s*=\s*['\"]([\w\d]+)['\"]", src, re.M)
        d = re.search(r"^down_revision\s*=\s*['\"]?([\w\d]+|None)['\"]?", src, re.M)
        if r:
            revs[r.group(1)] = d.group(1) if d else None
    # find heads = rev that nobody references as down_revision
    children = {v for v in revs.values() if v and v != "None"}
    heads = [r for r in revs if r not in children]
    REPORT["checks"]["migrations"] = {
        "status": "PASS" if len(heads) <= 1 else "FAIL",
        "files": len(files),
        "heads": heads,
        "multi_head": len(heads) > 1,
    }
    if len(heads) > 1:
        global EXIT_FAIL
        EXIT_FAIL += 1


def check_env_keys() -> None:
    ex = BACKEND / ".env.example"
    real = ROOT / ".env"
    if not ex.exists():
        REPORT["checks"]["env_keys"] = {"status": "WARN", "msg": ".env.example missing"}
        return
    def keys(p: Path) -> set[str]:
        out = set()
        for line in _read(p).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            out.add(line.split("=", 1)[0].strip())
        return out
    needed = keys(ex)
    have = keys(real) if real.exists() else set()
    missing = sorted(needed - have)
    REPORT["checks"]["env_keys"] = {
        "status": "PASS" if not missing else "WARN",
        "missing_in_dotenv": missing,
        "example_total": len(needed),
        "actual_total": len(have),
    }


def check_unlinked_routes() -> None:
    app = FRONTEND / "app"
    route_paths: list[str] = []
    for d in app.rglob("page.tsx"):
        rel = d.parent.relative_to(app).as_posix()
        if not rel or rel.startswith("api"):
            continue
        # strip dynamic segments [x] for matching
        clean = "/" + re.sub(r"\[[^\]]+\]", "*", rel)
        route_paths.append(clean)
    # search all tsx/ts for href references
    referenced: set[str] = set()
    href_pat = re.compile(r"""(?:href|router\.push|router\.replace|<Link[^>]+href)\s*=?\s*[(\[]?\s*[`'"]([^`'"]+)[`'"]""")
    for f in _ts_files(FRONTEND):
        src = _read(f)
        for m in href_pat.finditer(src):
            referenced.add(m.group(1).split("?")[0].split("#")[0])
    def is_linked(route: str) -> bool:
        for ref in referenced:
            if ref == route or ref.startswith(route.replace("/*", "/")):
                return True
            # ref may itself be dynamic
            rr = "/" + re.sub(r"\[[^\]]+\]|\${[^}]+}", "*", ref.lstrip("/"))
            if rr == route:
                return True
        return False
    orphan = sorted(r for r in route_paths if not is_linked(r))
    REPORT["checks"]["unlinked_routes"] = {
        "status": "PASS" if not orphan else "WARN",
        "total_routes": len(route_paths),
        "unlinked": orphan,
    }


def header(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def fmt(check: str) -> None:
    c = REPORT["checks"].get(check, {})
    st = c.get("status", "SKIP")
    icon = {"PASS": "[OK]", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[-]"}[st]
    print(f"{icon:8} {check}")


def main() -> int:
    header("SPRINT 68 ARCHITECTURE AUDIT")
    print(f"Root: {ROOT}")

    check_routers_registered()
    check_model_exports()
    check_frontend_pages()
    check_orphan_models()
    check_todo_markers()
    check_oversized_files()
    check_migrations()
    check_env_keys()
    check_unlinked_routes()

    header("RESULTS")
    for k in REPORT["checks"]:
        fmt(k)

    # detailed prints
    for name, c in REPORT["checks"].items():
        if c.get("status") in ("FAIL", "WARN"):
            print()
            print(f"--- {name} ({c['status']}) ---")
            for k, v in c.items():
                if k == "status":
                    continue
                if isinstance(v, list) and len(v) > 12:
                    print(f"  {k}: ({len(v)} items)")
                    for item in v[:12]:
                        print(f"    - {item}")
                    print(f"    ... +{len(v) - 12} more")
                elif isinstance(v, dict) and len(v) > 12:
                    print(f"  {k}: ({len(v)} keys)")
                else:
                    print(f"  {k}: {v}")

    out = QA / "sprint_68_arch_audit.json"
    out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    header("DONE")
    print(f"Report saved: {out}")
    print(f"FAIL checks: {EXIT_FAIL}")
    return 1 if EXIT_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
