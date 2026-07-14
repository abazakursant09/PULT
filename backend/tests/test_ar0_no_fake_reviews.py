"""AR0 — fake review generation is gone for good.

The removed path (POST /reviews/{pid}/generate → tasks/generate_review_responses) deleted a
product's real review rows and inserted fabricated reviews/answers, violating the "No fake data"
doctrine. These are static guarantees over the repository source (no app/auth boot required):
the endpoint, its task, its import, and the fabricated-review arrays must never come back.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REVIEWS_SRC = (BACKEND / "routers" / "reviews.py").read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    # Drop full-line and trailing # comments so the explanatory note that names the old symbols
    # does not trip the guards below.
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def test_generate_endpoint_is_gone():
    code = _strip_comments(REVIEWS_SRC)
    assert "/generate" not in code, "the fake-review generate endpoint is back"
    assert "generate_reviews" not in code
    assert "BackgroundTasks" not in code, "BackgroundTasks was only for the fake generator"


def test_fake_generator_task_file_is_deleted():
    assert not (BACKEND / "tasks" / "generate_review_responses.py").exists()


def test_nothing_imports_the_fake_generator():
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts or py.name == Path(__file__).name:
            continue
        code = _strip_comments(py.read_text(encoding="utf-8"))
        assert "generate_review_responses" not in code, f"{py} still references the fake generator"


def test_fabricated_review_arrays_exist_nowhere():
    banned = ("_POSITIVE_REVIEWS", "_NEGATIVE_REVIEWS", "_PROBLEMATIC_REVIEWS")
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts or py.name == Path(__file__).name:
            continue
        code = py.read_text(encoding="utf-8")
        for name in banned:
            assert name not in code, f"{py} defines fabricated-review array {name}"


def test_no_production_path_creates_reviews_by_deleting_real_ones():
    # A blanket delete of a product's reviews followed by inserts is exactly the fabrication
    # pattern. The reviews router must not delete review rows anywhere.
    code = _strip_comments(REVIEWS_SRC)
    assert "delete(ReviewResponse)" not in code
