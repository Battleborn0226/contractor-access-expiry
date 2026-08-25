"""Tests for webhook signature verification and JWT construction.

Signature verification is the only thing standing between a public endpoint
and forged installation events, so its failure modes are worth pinning: an
empty secret must never validate, and the digest must be taken over the exact
bytes received rather than a re-serialisation of the parsed JSON.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import jwt
import pytest

from app.github_client import build_app_jwt
from app.main import verify_signature


SECRET = "a-test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestSignatureVerification:
    def test_a_correct_signature_passes(self):
        body = b'{"action":"created"}'
        assert verify_signature(body, sign(body), SECRET) is True

    def test_a_tampered_body_fails(self):
        body = b'{"action":"created"}'
        signature = sign(body)
        assert verify_signature(b'{"action":"deleted"}', signature, SECRET) is False

    def test_a_wrong_secret_fails(self):
        body = b'{"action":"created"}'
        assert verify_signature(body, sign(body, "other"), SECRET) is False

    def test_an_empty_secret_never_validates(self):
        """A misconfigured deployment must reject, not accept everything."""

        body = b'{"action":"created"}'
        assert verify_signature(body, sign(body, ""), "") is False

    def test_a_missing_signature_fails(self):
        assert verify_signature(b"{}", None, SECRET) is False

    def test_an_unprefixed_signature_fails(self):
        body = b"{}"
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(body, digest, SECRET) is False

    def test_reserialised_json_does_not_match(self):
        """Why the raw body is used: whitespace changes the digest."""

        original = b'{"action": "created",  "id": 1}'
        signature = sign(original)
        reserialised = json.dumps(json.loads(original)).encode()

        assert reserialised != original
        assert verify_signature(reserialised, signature, SECRET) is False


class TestAppJwt:
    def test_the_jwt_backdates_iat_and_expires_within_ten_minutes(self):
        """GitHub rejects future-dated iat, and caps exp at 10 minutes."""

        now = int(time.time())
        token = build_app_jwt("12345", _rsa_key(), now=now)
        claims = jwt.decode(token, options={"verify_signature": False})

        assert claims["iss"] == "12345"
        assert claims["iat"] == now - 60
        assert claims["exp"] - claims["iat"] <= 600


def _rsa_key() -> str:
    """A real RSA key, generated in-process, for signing test tokens."""

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class TestPrivacyPage:
    """The privacy page makes a promise the storage layer has to keep."""

    def test_scans_persist_no_collaborator_identities(self):
        """The page says names are never stored. Pin that to the schema."""

        from app.storage import SCHEMA

        scans = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS scans") :]
        scans = scans[: scans.index(");")]

        for forbidden in ("login", "collaborator", "name", "email", "repo_full"):
            assert forbidden not in scans.lower(), (
                f"scans table gained a {forbidden!r} column -- the privacy "
                f"page promises collaborator identities are never stored"
            )
