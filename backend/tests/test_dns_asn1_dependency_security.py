"""Bounded, socket-free regressions for DNS/ASN.1 dependency advisories.

DNS: GHSA-45c3-73f7-pv4m. ASN.1: GHSA-2qm8-x737-gmw5,
GHSA-fm9c-5rcf-2q5p, GHSA-mm57-4v3p-rchx, GHSA-9m75-vc3g-4x39.
Small fixtures only; no timing thresholds or unbounded DoS reproducers.
"""
from importlib.metadata import version
from pathlib import Path
import re
import socket
import sys

import dns.exception
import dns.name
import pytest
from packaging.version import Version
from pyasn1.codec.ber import decoder as ber
from pyasn1.codec.cer import decoder as cer
from pyasn1.codec.der import decoder as der
from pyasn1.type import univ

BACKEND = Path(__file__).resolve().parents[1]
FIXED = {"dnspython": "2.9.0", "pyasn1": "0.6.5"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Dependency regression must not access the network")
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.mark.parametrize("package,minimum", FIXED.items())
def test_installed_dependency_is_fixed(package, minimum):
    assert Version(version(package)) >= Version(minimum)


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-ci.lock"])
@pytest.mark.parametrize("package,minimum", FIXED.items())
def test_both_locks_pin_fixed_dependencies(filename, package, minimum):
    pins = re.findall(rf"^{package}==([^\s\\]+)",
                      (BACKEND / filename).read_text(encoding="utf-8"), re.M)
    assert len(pins) == 1
    assert Version(pins[0]) >= Version(minimum)


def pointer_chain(hops):
    # Every pointer points strictly backwards; the resolved name is just root.
    wire = bytearray(b"\x00")
    previous = 0
    for _ in range(hops):
        offset = len(wire)
        wire.extend((0xC000 | previous).to_bytes(2, "big"))
        previous = offset
    return bytes(wire), previous


@pytest.mark.parametrize("hops", [1, 8, 16])
def test_normal_dns_compression_remains_compatible(hops):
    wire, offset = pointer_chain(hops)
    name, consumed = dns.name.from_wire(wire, offset)
    assert name == dns.name.root
    assert consumed == 2


def test_excessive_dns_pointer_chain_is_rejected():
    wire, offset = pointer_chain(32)
    with pytest.raises(dns.exception.FormError, match="pointer chain was too long"):
        dns.name.from_wire(wire, offset)


def tlv(tag, body):
    size = len(body)
    length = bytes([size]) if size < 128 else bytes([0x82]) + size.to_bytes(2, "big")
    return bytes([tag]) + length + body


@pytest.mark.parametrize("codec", [ber, cer, der])
def test_constructed_bit_strings_do_not_repeatedly_shift_growing_value(monkeypatch, codec):
    calls = []
    original = univ.BitString.fromOctetString

    def observe(*args, **kwargs):
        calls.append(kwargs.get("prepend"))
        return original(*args, **kwargs)

    monkeypatch.setattr(univ.BitString, "fromOctetString", staticmethod(observe))
    # BER-valid; CER/DER permissively accept this with schema. Not conforming DER.
    count = 64
    value, remainder = codec.decode(tlv(0x23, b"\x03\x02\x00\xa5" * count),
                                    asn1Spec=univ.BitString())
    assert remainder == b"" and len(value) == count * 8
    assert value.asOctets() == b"\xa5" * count
    assert len(calls) == 1, "Constructed BIT STRING must convert the assembled value once"
    assert calls[0] is None, "Growing integers must not be passed as prepend"


@pytest.mark.parametrize("codec", [ber, cer, der])
def test_constructed_octets_preserve_nested_values(codec):
    # The fixed decoder assembles decoded octets, not nested BER headers.
    nested = tlv(0x24, tlv(0x04, b"ab") + tlv(0x24, tlv(0x04, b"cd")))
    value, remainder = codec.decode(nested, asn1Spec=univ.OctetString())
    assert value.asOctets() == b"abcd" and remainder == b""


@pytest.mark.parametrize("codec", [ber, cer, der])
def test_binary_real_preserves_exact_mantissa(codec):
    # 256 octets: finite, deterministic; no float()/str() of the large value.
    mantissa = b"\x01" + b"\xa5" * 255
    value, remainder = codec.decode(tlv(0x09, b"\x80\x00" + mantissa))
    assert remainder == b""
    assert value[0] == int.from_bytes(mantissa, "big")
    assert value[1:] == (2, 0)


@pytest.mark.parametrize("codec", [ber, cer, der])
def test_schemaless_container_type_tracking_is_bounded(monkeypatch, codec):
    sizes = []

    class TrackedSet(set):
        def add(self, item):
            super().add(item)
            sizes.append(len(self))

    monkeypatch.setattr(ber, "set", TrackedSet, raising=False)
    tag_ids = [32 + index * sys.hash_info.modulus for index in range(32)]
    body = bytearray()
    for tag_id in tag_ids:
        groups = [tag_id & 0x7f]
        remaining = tag_id >> 7
        while remaining:
            groups.append((remaining & 0x7f) | 0x80)
            remaining >>= 7
        body.extend(b"\xbf" + bytes(reversed(groups)) + b"\x03\x04\x01A")
    value, remainder = codec.decode(tlv(0x30, bytes(body)))
    assert remainder == b"" and len(value) == len(tag_ids)
    assert all(value[i].asOctets() == b"A" and value[i].tagSet[-1].tagId == tag_id
               for i, tag_id in enumerate(tag_ids))
    assert sizes and max(sizes) <= 2, "Classification needs at most two distinct types"


def test_email_validator_remains_compatible_without_dns():
    from email_validator import validate_email
    result = validate_email("Seller+demo@EXAMPLE.COM", check_deliverability=False)
    assert result.normalized == "Seller+demo@example.com"


def test_hmac_jwt_remains_compatible_and_algorithm_pinned():
    from services import session_jwt as jwt
    from services.session_jwt import JWTError
    key = "synthetic-offline-test-key-not-a-production-secret"
    token = jwt.encode({"sub": "synthetic-user", "ver": 1}, key, algorithm="HS256")
    assert jwt.decode(token, key, algorithms=["HS256"])["sub"] == "synthetic-user"
    with pytest.raises(JWTError):
        jwt.decode(token, key, algorithms=["HS512"])
