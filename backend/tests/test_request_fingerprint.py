"""SECURITY-2D-1B-A — golden + property tests for the canonical request fingerprint (pure helper).

The digests below are FIXED goldens: if canonicalization ever changes, these break loudly (the algorithm
is pinned by schema_version "fp1"). key = operation identity, fingerprint = contents — this module only
fingerprints contents and never creates an operation id.
"""
import copy
import re
from decimal import Decimal
import unicodedata

import pytest

from services.marketplace.request_fingerprint import request_fingerprint as fp, canonical_json

_FMT = re.compile(r"^fp1:[0-9a-f]{64}$")

_BASE = dict(user_id="u1", connection_id="c1", marketplace="wildberries", action_type="set_price",
             mode="manual_l3", target={"offer_id": "o1"}, params={"price": Decimal("1000.00")},
             reverted_from=None)

# ── golden digests (exact) ────────────────────────────────────────────────────
G_BASE       = "fp1:88275109d5f67fbf5cea2b7f6408b7185b5c71e5ae1ef94bc2e92d61802db7bb"
G_DIFF_USER  = "fp1:cea7ff1e8ee45c49029c3794a809141f991bb7043c541e9b1648711205e783cd"
G_DIFF_CONN  = "fp1:2c4c5b910264c27ce5e46b528574c26fa0927d3088cb0e15cdaa62a2f7b1e918"
G_DIFF_MP    = "fp1:66026cd60c32730edf2ec3794d66bfda5eb4cfada73ab1e1643e02bda5248796"
G_DIFF_ACT   = "fp1:786dda344a1f6d9cf864b1399a11c57f046a44b1003a8b23534f487b9ebbf453"
G_DIFF_MODE  = "fp1:db67cf6089074dcec095bb09afbb7cb0ae840be896d1beb838c7932368f97778"
G_DIFF_TGT   = "fp1:464a2a2a3eea75431bcec5a75d9ef4772c5c18ae13f477d9391df75a9775fab9"
G_DIFF_PARAM = "fp1:10fb8d8e1a2406907ff4d9655dbcff3364cc5666e72d5d5a07df8c4d0081d15d"
G_DIFF_REV   = "fp1:16715c7347008fed76fbab5d3113618d95c318db1bf735ec45419166f21d537e"
G_SCALE_1DP  = "fp1:fc30f15eb53407eacfac6e275cd8586bb55c56e90cc45a535634f70937a94c65"
G_NFC        = "fp1:39a96e565c33931b6f83d349304beefec16298335e90f6dde526dddc1c5c463d"
G_NONE_NOTE  = "fp1:83fbe5bdd4e1ff88819121e8a1b679e981e98d7ceb20b6b0f53287a8ec7f0f88"
G_BOOL_TRUE  = "fp1:38fccabd7ce286e3bf41ec1bf3d734ef1382c7af52a501828bfe7fbaafc75951"
G_INT_ONE    = "fp1:97bc2bde1e7dd70fbc418c112aa656ca675c34804159712d73bbc5fa772e53fc"
G_LIST_AB    = "fp1:ac3b4a62168a3f385387680ec43c79336f4a22a0dad98a71a713fe3472d4f8fc"
G_LIST_BA    = "fp1:7291d154e80b46ad0038e71ac553605a89d15fd445533609dedb1557469c06d3"


def test_stable_exact_golden():
    assert fp(**_BASE) == G_BASE


def test_format_and_length():
    v = fp(**_BASE)
    assert _FMT.match(v) and len(v) == 68 <= 72   # fits String(72)


def test_same_object_same_fingerprint():
    assert fp(**_BASE) == fp(**copy.deepcopy(_BASE))


@pytest.mark.parametrize("field,val,golden", [
    ("user_id", "u2", G_DIFF_USER),
    ("connection_id", "c2", G_DIFF_CONN),
    ("marketplace", "ozon", G_DIFF_MP),
    ("action_type", "reduce_discount", G_DIFF_ACT),
    ("mode", "automated_l4", G_DIFF_MODE),
    ("target", {"offer_id": "o2"}, G_DIFF_TGT),
    ("params", {"price": Decimal("999.00")}, G_DIFF_PARAM),
    ("reverted_from", "log-9", G_DIFF_REV),
])
def test_each_field_changes_the_fingerprint(field, val, golden):
    v = fp(**{**_BASE, field: val})
    assert v == golden and v != G_BASE


def test_decimal_scale_matters():
    # 1000.0 vs 1000.00 → different string → different fingerprint (no zero-stripping)
    assert fp(**{**_BASE, "params": {"price": Decimal("1000.0")}}) == G_SCALE_1DP != G_BASE


def test_unicode_nfc_equivalence():
    composed = fp(**{**_BASE, "target": {"offer_id": "café"}})
    decomposed = fp(**{**_BASE, "target": {"offer_id": unicodedata.normalize("NFD", "café")}})
    assert composed == decomposed == G_NFC


def test_none_differs_from_missing():
    with_none = fp(**{**_BASE, "params": {"price": Decimal("1000.00"), "note": None}})
    without = fp(**{**_BASE, "params": {"price": Decimal("1000.00")}})
    assert with_none == G_NONE_NOTE and without == G_BASE and with_none != without


def test_bool_differs_from_int():
    assert fp(**{**_BASE, "params": {"flag": True}}) == G_BOOL_TRUE
    assert fp(**{**_BASE, "params": {"flag": 1}}) == G_INT_ONE
    assert G_BOOL_TRUE != G_INT_ONE


def test_list_order_is_preserved_and_caller_sorting_is_stable():
    assert fp(**{**_BASE, "params": {"ids": ["a", "b"]}}) == G_LIST_AB
    assert fp(**{**_BASE, "params": {"ids": ["b", "a"]}}) == G_LIST_BA != G_LIST_AB
    # a caller that sorts an order-insensitive list gets the canonical result
    assert fp(**{**_BASE, "params": {"ids": sorted(["b", "a"])}}) == G_LIST_AB


def test_sorted_keys_in_canonical_json():
    j = canonical_json({"schema_version": "fp1", "b": 1, "a": 2, "m": {"z": 1, "y": 2}}).decode()
    assert j == '{"a":2,"b":1,"m":{"y":2,"z":1},"schema_version":"fp1"}'   # sorted, no whitespace


# ── fail-closed rejections ────────────────────────────────────────────────────

def test_float_rejected_top_level():
    with pytest.raises(TypeError):
        fp(**{**_BASE, "params": {"price": 1000.0}})


def test_float_rejected_when_nested():
    with pytest.raises(TypeError):
        fp(**{**_BASE, "params": {"nested": {"deep": [1, 2, 3.14]}}})


def test_decimal_nan_and_infinity_rejected():
    for bad in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError):
            fp(**{**_BASE, "params": {"price": bad}})


def test_non_str_dict_key_rejected():
    with pytest.raises(TypeError):
        fp(**{**_BASE, "params": {1: "x"}})


def test_unsupported_type_rejected():
    with pytest.raises(TypeError):
        fp(**{**_BASE, "params": {"when": object()}})


def test_input_object_not_mutated():
    target = {"offer_id": "o1"}
    params = {"price": Decimal("1000.00"), "ids": ["b", "a"]}
    before_t = copy.deepcopy(target)
    before_p = copy.deepcopy(params)
    fp(**{**_BASE, "target": target, "params": params})
    assert target == before_t and params == before_p   # helper never mutates its inputs
