"""Offline session interoperability and algorithm-confusion regressions."""
import base64
from functools import lru_cache
import hashlib
import hmac
import json
from pathlib import Path
import re
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
import pytest

from services import session_jwt as jwt

SECRET = "synthetic-session-fixture-" * 4


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def unb64(raw):
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


@lru_cache
def private_key(algorithm):
    if algorithm.startswith("HS"):
        return SECRET
    if algorithm.startswith("RS"):
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ec.generate_private_key({"ES256": ec.SECP256R1, "ES384": ec.SECP384R1,
                                    "ES512": ec.SECP521R1}[algorithm]())


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def signed(payload, algorithm, key):
    """Independent RFC 7515 producer; does not call either JWT library."""
    message = b".".join((b64(json.dumps({"alg": algorithm, "typ": "JWT"}).encode()),
                          b64(json.dumps(payload).encode())))
    digest = {"256": hashes.SHA256, "384": hashes.SHA384, "512": hashes.SHA512}[algorithm[-3:]]()
    if algorithm.startswith("HS"):
        signature = hmac.new(key.encode() if isinstance(key, str) else key,
                             message, getattr(hashlib, "sha" + algorithm[-3:])).digest()
    elif algorithm.startswith("RS"):
        signature = key.sign(message, padding.PKCS1v15(), digest)
    else:
        r, s = decode_dss_signature(key.sign(message, ec.ECDSA(digest)))
        width = (key.key_size + 7) // 8
        signature = r.to_bytes(width, "big") + s.to_bytes(width, "big")
    return b".".join((message, b64(signature))).decode()


@pytest.mark.parametrize("algorithm", sorted(jwt.ALGORITHMS))
@pytest.mark.parametrize("pending", [False, True])
def test_tokens_interoperate_with_independent_signer_and_verifier(algorithm, pending):
    key = private_key(algorithm)
    configured = key if isinstance(key, str) else pem(key)
    claims = {"sub": "synthetic-user", "ver": 7, "exp": int(time.time()) + 300}
    if pending:
        claims["mfa_pending"] = True
    # Existing PEM private-key configuration must verify its own public half.
    assert jwt.decode(signed(claims, algorithm, key), configured, algorithms=[algorithm]) == claims
    new = jwt.encode(claims, configured, algorithm=algorithm)
    header, body, signature = new.encode().split(b".")
    assert json.loads(unb64(header))["alg"] == algorithm
    assert json.loads(unb64(body)) == claims
    message, signature = b".".join((header, body)), unb64(signature)
    digest = {"256": hashes.SHA256, "384": hashes.SHA384, "512": hashes.SHA512}[algorithm[-3:]]()
    if algorithm.startswith("HS"):
        assert hmac.compare_digest(signature, hmac.new(key.encode(), message,
                                   getattr(hashlib, "sha" + algorithm[-3:])).digest())
    elif algorithm.startswith("RS"):
        key.public_key().verify(signature, message, padding.PKCS1v15(), digest)
    else:
        width = len(signature) // 2
        der = encode_dss_signature(int.from_bytes(signature[:width], "big"),
                                   int.from_bytes(signature[width:], "big"))
        key.public_key().verify(der, message, ec.ECDSA(digest))


@pytest.mark.parametrize("encoding", [serialization.Encoding.PEM, serialization.Encoding.DER])
def test_public_key_cannot_be_used_as_hmac_secret(encoding):
    public = private_key("RS256").public_key().public_bytes(
        encoding, serialization.PublicFormat.SubjectPublicKeyInfo)
    forged = signed({"sub": "synthetic-user", "ver": 0}, "HS256", public)
    with pytest.raises(jwt.JWTError):
        jwt.decode(forged, public, algorithms=["HS256"])
    with pytest.raises(jwt.JWTError):
        jwt.encode({"sub": "synthetic-user"}, public, algorithm="HS256")
    with pytest.raises(jwt.JWTError):
        jwt.decode(forged, public, algorithms=["RS256"])


@pytest.mark.parametrize("algorithms", [[], ["none"], ["HS256", "RS256"], ["HS384"]])
def test_decode_never_uses_token_header_as_algorithm_policy(algorithms):
    token = signed({"sub": "synthetic-user", "ver": 0}, "HS256", SECRET)
    with pytest.raises(jwt.JWTError):
        jwt.decode(token, SECRET, algorithms=algorithms)


@pytest.mark.parametrize("claims", [
    {"exp": 1}, {"nbf": 9999999999}, {"exp": None}, {"exp": []},
    {"nbf": {}}, {"iat": None}, {"sub": 1}, {"jti": []}, {"aud": "another-service"},
])
def test_invalid_signed_claims_fail_through_jwt_error_boundary(claims):
    with pytest.raises(jwt.JWTError):
        jwt.decode(signed(claims, "HS256", SECRET), SECRET, algorithms=["HS256"])


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-ci.lock"])
def test_unpatched_jose_and_ecdsa_dependencies_are_not_shipped(filename):
    pins = dict(re.findall(r"^([a-zA-Z0-9_-]+)==([^\s\\]+)",
                (Path(__file__).resolve().parents[1] / filename).read_text(), re.M))
    assert pins["pyjwt"] == "2.15.1"
    assert not ({"python-jose", "ecdsa", "rsa"} & pins.keys())
