from dataclasses import dataclass
from typing import Any, Protocol

import jwt
import requests
from jwt import PyJWKClient

from virector.config import Settings


class AuthenticationError(RuntimeError):
    """Raised when a bearer token cannot be trusted."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None = None


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AuthenticatedUser:
        """Verify a token and return its trusted user identity."""


class SupabaseTokenVerifier:
    """Verify Supabase access tokens using JWKS or the Auth user endpoint."""

    _ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")

    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url:
            raise ValueError("VIRECTOR_SUPABASE_URL is required for authentication.")
        self.base_url = settings.supabase_url.rstrip("/")
        self.issuer = f"{self.base_url}/auth/v1"
        self.audience = settings.supabase_jwt_audience
        self.publishable_key = settings.supabase_publishable_key
        self.jwks_client = PyJWKClient(
            f"{self.issuer}/.well-known/jwks.json",
            cache_jwk_set=True,
            lifespan=600,
        )

    @staticmethod
    def _user_from_claims(claims: dict[str, Any]) -> AuthenticatedUser:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("Access token is missing a valid subject.")
        email = claims.get("email")
        return AuthenticatedUser(
            user_id=subject,
            email=email if isinstance(email, str) else None,
        )

    def _verify_asymmetric(self, token: str, algorithm: str) -> AuthenticatedUser:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
        return self._user_from_claims(claims)

    def _verify_legacy_hs256(self, token: str) -> AuthenticatedUser:
        if not self.publishable_key:
            raise AuthenticationError(
                "This Supabase project uses legacy HS256 tokens, so "
                "VIRECTOR_SUPABASE_PUBLISHABLE_KEY must be configured."
            )
        response = requests.get(
            f"{self.issuer}/user",
            headers={
                "apikey": self.publishable_key,
                "Authorization": f"Bearer {token}",
            },
            timeout=10,
        )
        if response.status_code != 200:
            raise AuthenticationError("Supabase rejected the access token.")
        payload = response.json()
        user_id = payload.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise AuthenticationError("Supabase returned an invalid user identity.")
        email = payload.get("email")
        return AuthenticatedUser(
            user_id=user_id,
            email=email if isinstance(email, str) else None,
        )

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm in self._ASYMMETRIC_ALGORITHMS:
                return self._verify_asymmetric(token, algorithm)
            if algorithm == "HS256":
                return self._verify_legacy_hs256(token)
            raise AuthenticationError("Access token uses an unsupported algorithm.")
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, requests.RequestException, ValueError, TypeError) as exc:
            raise AuthenticationError("Access token verification failed.") from exc


def create_token_verifier(settings: Settings) -> TokenVerifier | None:
    if not settings.supabase_url:
        return None
    return SupabaseTokenVerifier(settings)
