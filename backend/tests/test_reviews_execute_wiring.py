"""
Static guarantees that the reviews router no longer fakes publication and routes
real publishing through the executor (ME-2). No app/auth boot required.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "routers" / "reviews.py").read_text(encoding="utf-8")


def test_patch_does_not_fake_publish():
    # PATCH must reject manual 'published' and never set status='published' itself
    assert "'published' нельзя выставить вручную" in SRC
    # the only place status becomes 'published' is the real publish endpoint
    assert SRC.count('review.status = "published"') == 1


def test_publish_endpoint_calls_executor():
    start = SRC.index("async def publish_review_response(")
    body = SRC[start:]
    assert "executor.execute(" in body
    assert 'action_type="publish_review_response"' in body
    assert "external_review_id" in body            # refuses non-synced reviews
    assert "if not res.ok" in body                 # only marks published on real success


def test_publish_sets_real_provenance():
    start = SRC.index("async def publish_review_response(")
    body = SRC[start:]
    assert "review.published_at" in body
    assert "review.execution_log_id = res.log_id" in body


def test_no_marketplace_client_called_directly_for_writes():
    # writes must go through the executor; the only direct wb_client use is the
    # read-only sync (list_unanswered_feedbacks), never a publish.
    assert "wb_client.publish_feedback_answer" not in SRC
