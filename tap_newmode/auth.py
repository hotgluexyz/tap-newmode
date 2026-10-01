"""New/Mode Authentication."""

from __future__ import annotations

from hotglue_singer_sdk.authenticators import OAuthAuthenticator, SingletonMeta
from typing_extensions import override


# The SingletonMeta metaclass makes all streams reuse the same authenticator instance,
# so a token is fetched once per run rather than once per stream.
class NewModeAuthenticator(OAuthAuthenticator, metaclass=SingletonMeta):
    """OAuth 2.0 client-credentials authenticator for the New/Mode Impact API."""

    @override
    @property
    def oauth_request_body(self) -> dict:
        """Return the client-credentials grant body for the New/Mode token endpoint.

        New/Mode issues short-lived tokens (300s) and returns no ``refresh_token``,
        so every refresh re-runs the client-credentials grant.

        Returns:
            A dict with the request body.
        """
        return {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
