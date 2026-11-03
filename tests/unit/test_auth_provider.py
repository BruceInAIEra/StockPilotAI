import base64
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.services.auth_service import AuthenticationError, CognitoProvider


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings():
    return Settings(_env_file=None, cognito_user_pool_id="us-west-2_test", cognito_client_id="client",
                    cognito_domain="https://demo.auth.us-west-2.amazoncognito.com")


@pytest.mark.parametrize("bad_claim", [None, "iss", "aud", "exp", "nonce", "token_use", "email_verified", "at_hash", "signature"])
def test_oidc_verifies_signature_and_identity_claims(monkeypatch, signing_key, bad_claim):
    config = settings()
    provider = CognitoProvider(config)
    nonce = "expected-nonce"
    claims = {
        "iss": config.cognito_issuer, "aud": "client", "sub": "subject", "nonce": nonce,
        "token_use": "id", "email_verified": True, "email": "investor@example.com",
        "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=30),
        "at_hash": base64.urlsafe_b64encode(hashlib.sha256(b"access").digest()[:16]).rstrip(b"=").decode(),
    }
    if bad_claim == "exp":
        claims[bad_claim] = datetime.now(UTC) - timedelta(minutes=1)
    elif bad_claim == "email_verified":
        claims[bad_claim] = False
    elif bad_claim and bad_claim != "signature":
        claims[bad_claim] = "wrong-value"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if bad_claim == "signature" else signing_key
    id_token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(provider.jwks, "get_signing_key_from_jwt", lambda raw: SimpleNamespace(key=signing_key.public_key()))
    real_client = httpx.Client

    def token_endpoint(request):
        assert b"code_verifier=verifier" in request.content
        assert b"grant_type=authorization_code" in request.content
        return httpx.Response(200, json={"id_token": id_token, "access_token": "access", "refresh_token": "refresh"})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(token_endpoint)))
    if bad_claim:
        with pytest.raises(AuthenticationError):
            provider.exchange("code", "verifier", nonce)
    else:
        identity = provider.exchange("code", "verifier", nonce)
        assert identity.subject == "subject" and identity.email == "investor@example.com"


def test_config_rejects_insecure_origins_other_regions_and_invalid_keys():
    for override in ({"app_base_url": "http://example.com"}, {"app_base_url": "https://example.com/path"},
                     {"cognito_region": "us-east-1"}, {"cognito_user_pool_id": "us-east-1_test"},
                     {"cognito_domain": "http://example.com"}, {"session_encryption_key": "bad"}):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **override)


def test_callback_query_is_redacted_from_access_logs():
    import logging
    from app.core.logging import RedactLoginCallback
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s - %s %s %s %s",
                               ("client", "GET", "/auth/callback?code=secret&state=secret", "1.1", 303), None)
    assert RedactLoginCallback().filter(record)
    assert "secret" not in record.getMessage()
