"""SECURITY-2D-1B-B — operation key validation (pure). Identity, never content."""
import pytest

from services.marketplace import operation_key as ok

_GOOD = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"   # canonical lowercase UUIDv4


def test_client_key_wraps_valid_uuid():
    assert ok.client_key(_GOOD) == "v1:client:" + _GOOD
    assert ok.is_valid_v1_key(ok.client_key(_GOOD))


def test_namespaced_builders_and_length():
    assert ok.decision_key("d1") == "v1:decision:d1"
    assert ok.review_key("r1") == "v1:review:r1"
    assert ok.revert_key("L1") == "v1:revert:L1"
    for k in (ok.client_key(_GOOD), ok.decision_key(_GOOD), ok.review_key(_GOOD),
              ok.revert_key(_GOOD)):
        assert ok.is_valid_v1_key(k) and len(k) <= ok.MAX_KEY_LEN


@pytest.mark.parametrize("bad", [
    None,                                            # missing → REQUIRED
    "",                                              # empty
    "   ",                                           # whitespace
    " 3f2504e0-4f89-41d3-9a0c-0305e82c3301",         # leading space
    "3F2504E0-4F89-41D3-9A0C-0305E82C3301",          # uppercase (non-canonical)
    "3f2504e0-4f89-11d3-9a0c-0305e82c3301",          # version 1, not 4
    "3f2504e0-4f89-41d3-0a0c-0305e82c3301",          # variant 0, invalid
    "3f2504e04f8941d39a0c0305e82c3301",              # no hyphens / wrong length
    "not-a-uuid",                                    # malformed
    "3f2504e0-4f89-41d3-9a0c-0305e82c3301-extra",    # overlong
])
def test_bad_client_uuid_rejected(bad):
    with pytest.raises(ok.OperationKeyError):
        ok.client_key(bad)


def test_required_vs_invalid_codes():
    with pytest.raises(ok.OperationKeyError) as e1:
        ok.canonical_client_uuid(None)
    assert e1.value.code == "OPERATION_KEY_REQUIRED"
    with pytest.raises(ok.OperationKeyError) as e2:
        ok.canonical_client_uuid("nope")
    assert e2.value.code == "OPERATION_KEY_INVALID"


def test_bool_is_not_a_key():
    with pytest.raises(ok.OperationKeyError):
        ok.canonical_client_uuid(True)   # bool must never be accepted as a uuid


_U = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"   # a canonical uuid tail


@pytest.mark.parametrize("key,ok_flag", [
    ("v1:client:" + _U, True),
    ("v1:decision:" + _U, True),                     # every namespace needs a real UUID tail
    ("v1:review:" + _U, True),
    ("v1:revert:" + _U, True),
    ("v1:intent:" + _U, True),                        # reserved namespace, still UUID-shaped
    ("v1:decision:abc", False),                      # non-UUID tail rejected
    ("v1:review:abc", False),
    ("v1:revert:abc", False),
    ("v1:intent:abc", False),
    ("v1:client:x", False),
    ("v1:review:" + _U.upper(), False),              # uppercase tail is non-canonical
    ("review:abc", False),                           # legacy format
    ("price:p:100", False),                          # legacy content key
    ("v1:client:", False),                           # empty tail
    ("v1:client:has space", False),                  # whitespace in tail
    ("v2:client:" + _U, False),                       # wrong version prefix
    (None, False),
    ("v1:" + "x" * 200, False),                       # overlong / no valid namespace
])
def test_is_valid_v1_key(key, ok_flag):
    assert ok.is_valid_v1_key(key) is ok_flag


def test_server_key_builders_reject_non_uuid_downstream():
    # the builders accept any id string, but a non-UUID tail is caught by is_valid_v1_key before the
    # executor would ever act on it (a malformed server-derived key can never reach the provider)
    assert not ok.is_valid_v1_key(ok.decision_key("not-a-uuid"))
    assert not ok.is_valid_v1_key(ok.review_key("123"))
    assert ok.is_valid_v1_key(ok.decision_key(_U))


def test_forbid_body_key():
    ok.forbid_body_key(None)                          # None is fine
    with pytest.raises(ok.OperationKeyError) as e:
        ok.forbid_body_key(_GOOD)
    assert e.value.code == "BODY_OPERATION_KEY_FORBIDDEN"
