"""Validated configuration for the private Error API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-._~+/]+=*\Z")
_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
CredentialScope = Literal["read", "triage"]


@dataclass(frozen=True, slots=True)
class ApiCredential:
    secret: SecretStr
    label: str
    scope: CredentialScope


class ApiSettings(BaseModel):
    """Fail-closed credentials and their non-secret audit labels."""

    model_config = ConfigDict(extra="forbid")

    read_key_current: SecretStr = Field(min_length=32, max_length=4096)
    read_label_current: str
    read_key_next: SecretStr | None = Field(
        default=None, min_length=32, max_length=4096
    )
    read_label_next: str | None = None
    triage_key_current: SecretStr = Field(min_length=32, max_length=4096)
    triage_label_current: str
    triage_key_next: SecretStr | None = Field(
        default=None, min_length=32, max_length=4096
    )
    triage_label_next: str | None = None
    log_raw_credentials: bool = False

    @model_validator(mode="after")
    def validate_credentials(self) -> ApiSettings:
        secrets: list[str] = []
        for credential in self.credentials:
            secret = credential.secret.get_secret_value()
            try:
                secret.encode("ascii")
            except UnicodeEncodeError as error:
                raise ValueError("API credentials must contain only ASCII") from error
            if BEARER_TOKEN_PATTERN.fullmatch(secret) is None:
                raise ValueError("API credentials must be valid bearer tokens")
            secrets.append(secret)
        if len(secrets) != len(set(secrets)):
            raise ValueError("API credentials must be distinct")
        return self

    @property
    def credentials(self) -> tuple[ApiCredential, ...]:
        pairs = (
            (self.read_key_current, self.read_label_current, "read"),
            (self.read_key_next, self.read_label_next, "read"),
            (self.triage_key_current, self.triage_label_current, "triage"),
            (self.triage_key_next, self.triage_label_next, "triage"),
        )
        credentials: list[ApiCredential] = []
        for key, label, scope in pairs:
            if (key is None) != (label is None):
                raise ValueError("each configured key requires exactly one label")
            if key is None or label is None:
                continue
            if _LABEL_PATTERN.fullmatch(label) is None:
                raise ValueError("credential labels must be non-secret identifiers")
            credentials.append(ApiCredential(key, label, scope))
        return tuple(credentials)
