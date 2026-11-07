"""Small JWT boundary for cookie sessions and MFA challenges.

PyJWT replaces python-jose (GHSA-3qf3-8w2g-rqmx) and its python-ecdsa
dependency. Claims, signing key and configured algorithm remain unchanged.
Only the nine signing algorithms previously supported by our JOSE backend
are accepted; decoding always uses the caller's single configured algorithm.
"""
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import PyJWTError as JWTError

ALGORITHMS = frozenset({"HS256", "HS384", "HS512", "RS256", "RS384", "RS512",
                        "ES256", "ES384", "ES512"})


def encode(payload, key, *, algorithm):
    if algorithm not in ALGORITHMS:
        raise jwt.InvalidAlgorithmError("Unsupported session algorithm")
    return jwt.encode(payload, key, algorithm=algorithm)


def decode(token, key, *, algorithms):
    if len(algorithms) != 1 or algorithms[0] not in ALGORITHMS:
        raise jwt.InvalidAlgorithmError("One configured session algorithm is required")
    try:
        # SECRET_KEY historically serves both signing and verification. PyJWT's
        # RSA verifier needs the public half rather than an RSA private object.
        # Select the parser from trusted configuration, NEVER from the JWT header.
        prepared = jwt.get_algorithm_by_name(algorithms[0]).prepare_key(key)
        if isinstance(prepared, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
            prepared = prepared.public_key()
        return jwt.decode(token, prepared, algorithms=algorithms)
    except (TypeError, ValueError) as exc:
        # Malformed signed NumericDate claims and unusable keys fail closed through
        # the same JWT error boundary as a bad signature, not an HTTP 500.
        raise JWTError("Invalid session token or key") from exc
