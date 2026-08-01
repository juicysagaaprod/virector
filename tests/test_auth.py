from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from virector.config import Settings
from virector.services.auth import AuthenticationError, SupabaseTokenVerifier


class SigningKey:
    def __init__(self, key: object) -> None:
        self.key = key


class StaticJwksClient:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> SigningKey:
        return SigningKey(self.key)


def test_supabase_verifier_validates_asymmetric_token() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        _env_file=None,
        supabase_url="https://project.supabase.co",
    )
    verifier = SupabaseTokenVerifier(settings)
    verifier.jwks_client = StaticJwksClient(private_key.public_key())  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "email": "director@example.com",
            "iss": "https://project.supabase.co/auth/v1",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    user = verifier.verify(token)

    assert user.user_id == "11111111-1111-1111-1111-111111111111"
    assert user.email == "director@example.com"


def test_supabase_verifier_rejects_wrong_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = SupabaseTokenVerifier(
        Settings(_env_file=None, supabase_url="https://project.supabase.co")
    )
    verifier.jwks_client = StaticJwksClient(private_key.public_key())  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "iss": "https://project.supabase.co/auth/v1",
            "aud": "another-audience",
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthenticationError, match="verification failed"):
        verifier.verify(token)
