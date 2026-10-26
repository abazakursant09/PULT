"""Offline regression for GHSA-6q48-6c7w-9gw2; no network or system CA store."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import ssl

import certifi
from packaging.version import Version
import pytest

BACKEND = Path(__file__).resolve().parents[1]
MINIMUM_CERTIFI = Version("2026.10.19")
COMPROMISED_ROOT_SHA256 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-ci.lock"])
def test_both_locks_exclude_certifi_before_emsign_fix(filename):
    pins = re.findall(r"^certifi==([^\s\\]+)", (BACKEND / filename).read_text(encoding="utf-8"), re.M)
    assert len(pins) == 1, f"{filename}: certifi must have one exact pin"
    assert Version(pins[0]) >= MINIMUM_CERTIFI, f"{filename}: certifi predates the emSign CA fix"


def test_installed_certifi_does_not_trust_compromised_emsign_root():
    # Deliberately start empty: loading Windows/system roots could hide a broken bundle.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    roots = context.get_ca_certs(binary_form=True)
    assert roots, "certifi bundle must still contain usable trust anchors"
    assert COMPROMISED_ROOT_SHA256 not in {hashlib.sha256(root).hexdigest() for root in roots}
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
