"""Bearer authentication for the private Error API."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from starlette.authentication import AuthCredentials, AuthenticationBackend, BaseUser
from starlette.requests import HTTPConnection

from .config import BEARER_TOKEN_PATTERN, ApiSettings, CredentialScope

_FINGERPRINT_HEX_LENGTH = 12
_LOGGER = logging.getLogger("error_api.auth")


@dataclass(frozen=True, slots=True)
class Principal(BaseUser):
    label: str
    scope: CredentialScope

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.label


class ApiKeyAuthenticationBackend(AuthenticationBackend):
    """Match configured keys in constant time and expose one request principal."""

    def __init__(self, settings: ApiSettings) -> None:
        self._log_raw_credentials = settings.log_raw_credentials
        self._credentials = tuple(
            (
                hashlib.sha256(
                    credential.secret.get_secret_value().encode("ascii")
                ).digest(),
                Principal(credential.label, credential.scope),
            )
            for credential in settings.credentials
        )

    async def authenticate(
        self, connection: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        authorization = connection.headers.get("Authorization")
        supplied = ""
        well_formed = False
        if authorization is not None and authorization.startswith("Bearer "):
            supplied = authorization[7:]
            well_formed = BEARER_TOKEN_PATTERN.fullmatch(supplied) is not None

        supplied_digest = hashlib.sha256(supplied.encode()).digest()
        matched: Principal | None = None
        for secret_digest, principal in self._credentials:
            if hmac.compare_digest(supplied_digest, secret_digest):
                matched = principal
        fingerprint = supplied_digest.hex()[:_FINGERPRINT_HEX_LENGTH]
        if not well_formed or matched is None:
            if authorization is not None:
                reason = "malformed" if not well_formed else "unknown"
                if self._log_raw_credentials:
                    _LOGGER.warning(
                        "Error API credential rejected reason=%s credential=%r",
                        reason,
                        supplied,
                    )
                else:
                    _LOGGER.warning(
                        "Error API credential rejected reason=%s fingerprint=%s",
                        reason,
                        fingerprint,
                    )
            return None
        if self._log_raw_credentials:
            _LOGGER.info(
                "Error API credential accepted label=%s scope=%s credential=%r",
                matched.label,
                matched.scope,
                supplied,
            )
        else:
            _LOGGER.info(
                "Error API credential accepted label=%s scope=%s fingerprint=%s",
                matched.label,
                matched.scope,
                fingerprint,
            )
        return AuthCredentials(("authenticated", matched.scope)), matched
