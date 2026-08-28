from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
DEAD_FRONTEND_ROUTES = (
    "/ad-strategy",
    "/auto-promotions",
    "/community",
    "/dashboard/action-engine",
    "/dashboard/finance",
    "/dashboard/leaks",
    "/dashboard/opportunities",
    "/dashboard/products",
    "/dashboard/risks",
    "/dashboard/seo",
    "/dashboard/seo-cards",
    "/dashboard/sklad",
    "/dashboard/zakazy",
)


def test_backend_emits_no_links_to_unavailable_frontend_routes():
    runtime_files = [*BACKEND.rglob("*.py"), *BACKEND.rglob("*.json")]
    runtime_files = [
        path
        for path in runtime_files
        if "tests" not in path.parts and "__pycache__" not in path.parts
    ]

    violations = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        for route in DEAD_FRONTEND_ROUTES:
            if route in source:
                violations.append(f"{path.relative_to(BACKEND)}: {route}")

    assert not violations, "Backend emits links to unavailable frontend routes:\n" + "\n".join(violations)
