"""REST client handling, including NewModeStream base class."""

from __future__ import annotations

from functools import cached_property
from typing import Any
from urllib.parse import parse_qs, urlparse

import backoff
import requests
from hotglue_singer_sdk.authenticators import APIAuthenticatorBase
from hotglue_singer_sdk.streams import RESTStream
from typing_extensions import override

JSON_API_MEDIA_TYPE = "application/vnd.api+json"
DEFAULT_BASE_URL = "https://base.newmode.net"
PAGE_SIZE = 50


class NewModeStream(RESTStream):
    """New/Mode JSON:API stream class."""

    # JSON:API returns resources under `data`.
    records_jsonpath = "$.data[*]"
    # Pagination is offset based; the token is computed from `links.next`.
    next_page_token_jsonpath = None
    page_size = PAGE_SIZE

    # The contact collection routinely takes ~55s and the upstream gateway cuts
    # requests off at 60s, so 504s are common and expected rather than fatal.
    extra_retry_statuses: list[int] = [429, 504]

    @override
    @property
    def url_base(self) -> str:
        """Return the JSON:API root, configurable via the ``api_base_url`` setting."""
        base_url = (self.config.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")
        return f"{base_url}/jsonapi"

    @override
    @cached_property
    def authenticator(self) -> APIAuthenticatorBase:
        """Return a new authenticator object.

        Returns:
            An authenticator instance.
        """
        authenticator_cls, auth_endpoint = self._tap.access_token_support(self._tap)
        return authenticator_cls(self, auth_endpoint=auth_endpoint)

    @override
    @property
    def http_headers(self) -> dict:
        """Return the http headers needed.

        New/Mode rejects `application/json` on the JSON:API routes with a 415.

        Returns:
            A dictionary of HTTP headers.
        """
        return {"Accept": JSON_API_MEDIA_TYPE}

    @override
    def backoff_wait_generator(self) -> Any:
        """Cap the exponential backoff so a slow collection is not waited out for minutes."""
        return backoff.expo(factor=3, max_value=60)

    @override
    def backoff_max_tries(self) -> int:
        """Retry more than the SDK default, since 504s here are frequent."""
        return 8

    @override
    def get_next_page_token(
        self,
        response: requests.Response,
        previous_token: Any | None,
    ) -> Any | None:
        """Return the next `page[offset]`, or None when the last page was read.

        JSON:API advertises a `links.next` href only while more records remain, so
        its absence ends pagination.

        Args:
            response: A raw `requests.Response`_ object.
            previous_token: Previous pagination offset.

        Returns:
            The offset to request next, or None.

        .. _requests.Response:
            https://requests.readthedocs.io/en/latest/api/#requests.Response
        """
        next_link = (response.json().get("links") or {}).get("next") or {}
        next_href = next_link.get("href")
        if not next_href:
            return None

        # Prefer the offset the API itself advertises; fall back to advancing a page.
        offsets = parse_qs(urlparse(next_href).query).get("page[offset]")
        if offsets and offsets[0].isdigit():
            return int(offsets[0])
        return (previous_token or 0) + self.page_size

    @override
    def get_url_params(
        self,
        context: dict | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: The stream context.
            next_page_token: The offset to request.

        Returns:
            A dictionary of URL query parameters.
        """
        params: dict = {"page[limit]": self.page_size}
        if next_page_token:
            params["page[offset]"] = next_page_token
        return params

    @override
    def response_error_message(self, response: requests.Response) -> str:
        """Summarize the JSON:API `errors` array when the API returns one.

        Gateway timeouts come back as HTML, so the body is not assumed to be JSON.

        Args:
            response: A `requests.Response`_ object.

        Returns:
            The error message.

        .. _requests.Response:
            https://requests.readthedocs.io/en/latest/api/#requests.Response
        """
        try:
            errors = response.json().get("errors")
        except ValueError:
            errors = None
        if not errors:
            return super().response_error_message(response)

        details = []
        for error in errors:
            if not isinstance(error, dict):
                continue
            pointer = (error.get("source") or {}).get("pointer")
            detail = error.get("detail") or error.get("title") or ""
            details.append(f"{pointer}: {detail}" if pointer else detail)
        summary = "; ".join(detail for detail in details if detail)
        return f"{response.status_code} from {urlparse(response.url).path}: {summary}"
