"""NewMode tap class."""

from __future__ import annotations

from typing import Any

from hotglue_singer_sdk import Stream, Tap
from hotglue_singer_sdk import typing as th  # JSON schema typing helpers
from hotglue_singer_sdk.authenticators import OAuthAuthenticator
from typing_extensions import override

from tap_newmode.auth import NewModeAuthenticator
from tap_newmode.streams import (
    ContactsStream,
)

STREAM_TYPES = [
    ContactsStream,
]


class TapNewMode(Tap):
    """Singer tap for NewMode."""

    name = "tap-newmode"

    # TODO: Update this section with the actual config values you expect:
    config_jsonschema = th.PropertiesList(
        th.Property(
            "start_date",
            th.DateTimeType,
            description="The earliest record date to sync",
            default="2000-01-01T00:00:00Z",
        ),
        th.Property(
            "api_url",
            th.StringType,
            description="Base URL for the NewMode API",
            default="https://base.newmode.net",
        ),
        th.Property(
            "client_id",
            th.StringType,
            required=True,
            description="OAuth client ID for the NewMode OAuth app",
        ),
        th.Property(
            "client_secret",
            th.StringType,
            required=True,
            description="OAuth client secret for the NewMode OAuth app",
        ),
        th.Property(
            "refresh_token",
            th.StringType,
            description="OAuth refresh token for the NewMode OAuth app",
        ),
    ).to_dict()

    @override
    def discover_streams(self) -> list[Stream]:
        """Return a list of discovered streams."""
        return [stream_class(tap=self) for stream_class in STREAM_TYPES]

    @classmethod
    def access_token_support(
        cls,
        connector: Any = None,
    ) -> tuple[type[OAuthAuthenticator], str]:
        """Return the authenticator class and OAuth token endpoint.

        Returns:
            A tuple with the authenticator class and the OAuth token endpoint URL.
        """
        # TODO: replace with the real OAuth token endpoint for your vendor.
        return NewModeAuthenticator, "https://base.newmode.net/oauth/token"


if __name__ == "__main__":
    TapNewMode.cli()
