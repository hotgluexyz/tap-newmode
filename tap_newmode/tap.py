"""New/Mode tap class."""

from __future__ import annotations

from typing import Any

from hotglue_singer_sdk import Stream, Tap
from hotglue_singer_sdk import typing as th  # JSON schema typing helpers
from hotglue_singer_sdk.authenticators import OAuthAuthenticator
from typing_extensions import override

from tap_newmode.auth import NewModeAuthenticator
from tap_newmode.client import DEFAULT_BASE_URL, resolve_base_url
from tap_newmode.streams import (
    ContactsStream,
)

STREAM_TYPES = [
    ContactsStream,
]


class TapNewMode(Tap):
    """Singer tap for New/Mode."""

    name = "tap-newmode"

    # Config keys mirror target-newmode so one connector config drives both.
    config_jsonschema = th.PropertiesList(
        th.Property(
            "client_id",
            th.StringType,
            required=True,
            description="New/Mode OAuth client id.",
        ),
        th.Property(
            "client_secret",
            th.StringType,
            required=True,
            description="New/Mode OAuth client secret.",
        ),
        th.Property(
            "api_base_url",
            th.StringType,
            description=f"New/Mode API base URL. Defaults to {DEFAULT_BASE_URL}.",
        ),
        th.Property(
            "start_date",
            th.DateTimeType,
            description="The earliest record date to sync.",
            default="2000-01-01T00:00:00Z",
        ),
        th.Property(
            "_refresh_token_via_hg_api",
            th.BooleanType,
            default=False,
            description=(
                "Fetch access tokens from the Hotglue access token endpoint instead of "
                "running the client-credentials grant against New/Mode directly. Requires "
                "the TENANT, API_KEY, FLOW, ENV_ID and TAP environment variables."
            ),
        ),
        th.Property(
            "access_token",
            th.StringType,
            description="Current access token. Populated by the tap after authenticating.",
        ),
        th.Property(
            "expires_in",
            th.IntegerType,
            description="Epoch seconds when the access token expires. Managed by the tap.",
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
        """Return the authenticator class and the New/Mode OAuth token endpoint.

        Args:
            connector: The tap instance, used to read ``api_base_url`` when set.

        Returns:
            A tuple with the authenticator class and the OAuth token endpoint URL.
        """
        base_url = resolve_base_url(connector.config) if connector is not None else DEFAULT_BASE_URL
        return NewModeAuthenticator, f"{base_url}/oauth/token"


if __name__ == "__main__":
    TapNewMode.cli()
