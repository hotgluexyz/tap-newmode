"""Tests standard tap features using the built-in SDK tests library."""

import copy
import datetime

import pytest
from hotglue_singer_sdk.exceptions import ConfigValidationError
from hotglue_singer_sdk.testing import get_standard_tap_tests

from tap_newmode.client import resolve_base_url
from tap_newmode.tap import TapNewMode

SAMPLE_CONFIG = {
    "start_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
    "client_id": "placeholder",
    "client_secret": "placeholder",
}

# _test_stream_connections makes live HTTP calls; excluded by default.
# Replace SAMPLE_CONFIG placeholders with real credentials and call it directly.
_STANDARD_TESTS = [
    t
    for t in get_standard_tap_tests(TapNewMode, config=SAMPLE_CONFIG)
    if getattr(t, "__name__", "") != "_test_stream_connections"
]


@pytest.mark.parametrize("test_func", _STANDARD_TESTS)
def test_standard(test_func):
    """Run built-in SDK tap tests (CLI output and catalog discovery)."""
    test_func()


@pytest.fixture
def contacts_stream():
    """Return the contacts stream from a tap built on the sample config."""
    tap = TapNewMode(config=SAMPLE_CONFIG, parse_env_config=False)
    return next(s for s in tap.discover_streams() if s.name == "contacts")


# Marks a test case where the attribute is omitted entirely rather than null.
_ABSENT = object()

# A trimmed JSON:API resource shaped like a real New/Mode contact response.
SAMPLE_RESOURCE = {
    "type": "contact--contact",
    "id": "a0da60c0-faa7-4421-8852-7ee03c54055b",
    "links": {"self": {"href": "https://base.newmode.net/jsonapi/contact/contact/a0da"}},
    "attributes": {
        "drupal_internal__id": 169,
        "org_id": 98,
        "first_name": "Maggie",
        "last_name": "Paquette",
        "email": "maggie.paquette@newmode.net",
        "latitude": 45.3863,
        "opt_in": None,
        "created": "1682369673",
        "changed": "1682372350",
    },
    "relationships": {
        "groups": {
            "data": [
                {"type": "contact_group--contact_group", "id": "missing"},
                {
                    "type": "contact_group--contact_group",
                    "id": "1b2c3d4e-0000-0000-0000-000000000001",
                },
            ],
        },
        "entitygroupfield": {"data": []},
    },
}


def test_post_process_flattens_attributes(contacts_stream):
    """Attributes are lifted to the top level alongside the resource id and type."""
    record = contacts_stream.post_process(SAMPLE_RESOURCE)

    assert record["id"] == "a0da60c0-faa7-4421-8852-7ee03c54055b"
    assert record["type"] == "contact--contact"
    assert record["first_name"] == "Maggie"
    assert record["org_id"] == 98
    assert record["latitude"] == 45.3863
    assert "attributes" not in record
    assert "links" not in record


def test_post_process_converts_epoch_timestamps(contacts_stream):
    """Epoch-second strings become RFC 3339 so they validate as date-time."""
    record = contacts_stream.post_process(SAMPLE_RESOURCE)

    assert record["created"] == "2023-04-24T20:54:33+00:00"
    assert record["changed"] == "2023-04-24T21:39:10+00:00"


def test_post_process_reduces_relationships_to_ids(contacts_stream):
    """Relationships collapse to uuid lists, dropping Drupal's `missing` sentinel."""
    record = contacts_stream.post_process(SAMPLE_RESOURCE)

    assert record["groups"] == ["1b2c3d4e-0000-0000-0000-000000000001"]
    assert record["entitygroupfield"] == []


def test_catalog_covers_every_contact_field(contacts_stream):
    """Every attribute and relationship the API returns is present in the catalog."""
    properties = contacts_stream.schema["properties"]

    expected = (
        set(SAMPLE_RESOURCE["attributes"]) | set(SAMPLE_RESOURCE["relationships"]) | {"id", "type"}
    )
    assert not expected - set(properties)


class _FakeResponse:
    """Minimal stand-in exposing just what the stream reads off a response."""

    def __init__(
        self, body, status_code=200, url="https://base.newmode.net/jsonapi/contact/contact"
    ):
        self._body = body
        self.status_code = status_code
        self.url = url
        self.reason = "Error"
        self.text = "body text"

    def json(self):
        if isinstance(self._body, ValueError):
            raise self._body
        return self._body


_NEXT_URL = "https://base.newmode.net/jsonapi/contact/contact?page%5Boffset%5D=5&page%5Blimit%5D=5"


@pytest.mark.parametrize(
    "links",
    [
        pytest.param({"next": {"href": _NEXT_URL}}, id="link-object"),
        # JSON:API also allows a link to be a plain URI string.
        pytest.param({"next": _NEXT_URL}, id="link-string"),
        pytest.param({"next": "?page%5Boffset%5D=5&page%5Blimit%5D=5"}, id="relative-string"),
    ],
)
def test_get_next_page_token_reads_offset_from_next_link(contacts_stream, links):
    """The next offset is read from `links.next`, whether object or string form."""
    assert contacts_stream.get_next_page_token(_FakeResponse({"links": links}), None) == 5


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"links": {"self": {"href": "https://base.newmode.net/x"}}}, id="no-next"),
        pytest.param({"links": {"next": None}}, id="null-next"),
        pytest.param({"links": {"next": {}}}, id="empty-link-object"),
        pytest.param({"links": {"next": ""}}, id="empty-string"),
        pytest.param({"links": {"next": 42}}, id="non-string-link"),
        pytest.param({"links": "not-an-object"}, id="links-not-an-object"),
        pytest.param({}, id="no-links"),
        pytest.param([], id="array-body"),
        pytest.param(None, id="null-body"),
        pytest.param(ValueError("no json"), id="html-body"),
    ],
)
def test_get_next_page_token_ends_on_missing_or_malformed_link(contacts_stream, body):
    """Pagination stops rather than raising when `links.next` is absent or malformed."""
    assert contacts_stream.get_next_page_token(_FakeResponse(body), 5) is None


def test_response_error_message_summarizes_jsonapi_errors(contacts_stream):
    """A JSON:API errors array is reduced to pointer/detail pairs."""
    body = {
        "errors": [
            {"detail": "Bad value.", "source": {"pointer": "/data/attributes/email"}},
            {"title": "Conflict"},
        ],
    }
    message = contacts_stream.response_error_message(_FakeResponse(body, status_code=422))

    assert "/data/attributes/email: Bad value." in message
    assert "Conflict" in message


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="null-body"),
        pytest.param([], id="array-body"),
        pytest.param({"errors": None}, id="null-errors"),
        pytest.param({"errors": 5}, id="scalar-errors"),
        pytest.param({"errors": "boom"}, id="string-errors"),
        pytest.param({"errors": []}, id="empty-errors"),
        pytest.param({"errors": ["plain string"]}, id="non-dict-entry"),
        pytest.param({"errors": [{"source": "not-an-object", "detail": "Bad."}]}, id="bad-source"),
        pytest.param(ValueError("no json"), id="html-body"),
    ],
)
def test_response_error_message_handles_malformed_bodies(contacts_stream, body):
    """Malformed or non-JSON error bodies fall back instead of raising."""
    message = contacts_stream.response_error_message(_FakeResponse(body, status_code=504))

    assert isinstance(message, str)
    assert message


def test_response_error_message_keeps_detail_when_source_is_malformed(contacts_stream):
    """A non-object `source` is ignored but its sibling detail still reaches the message."""
    body = {"errors": [{"source": "not-an-object", "detail": "Bad value."}]}
    message = contacts_stream.response_error_message(_FakeResponse(body, status_code=422))

    assert "Bad value." in message


def test_replication_key_is_changed(contacts_stream):
    """`changed` drives incremental state."""
    assert contacts_stream.replication_key == "changed"
    assert contacts_stream.is_timestamp_replication_key


def test_post_process_skips_records_older_than_bookmark(contacts_stream, monkeypatch):
    """Records that predate the bookmark are dropped, since the API cannot filter."""
    monkeypatch.setattr(
        type(contacts_stream),
        "get_starting_timestamp",
        lambda self, context: datetime.datetime(2023, 4, 24, 21, 0, tzinfo=datetime.timezone.utc),
    )
    # SAMPLE_RESOURCE changed = 1682372350 -> 2023-04-24T21:39:10Z, after the bookmark.
    assert contacts_stream.post_process(SAMPLE_RESOURCE) is not None

    older = copy.deepcopy(SAMPLE_RESOURCE)
    older["attributes"]["changed"] = "1682369673"  # 2023-04-24T20:54:33Z, before it
    assert contacts_stream.post_process(older) is None


def test_post_process_keeps_records_when_no_bookmark(contacts_stream, monkeypatch):
    """With no bookmark yet, nothing is filtered out."""
    monkeypatch.setattr(type(contacts_stream), "get_starting_timestamp", lambda self, context: None)
    assert contacts_stream.post_process(SAMPLE_RESOURCE) is not None


def test_post_process_falls_back_to_created_when_changed_unusable(contacts_stream):
    """A missing or unparseable `changed` falls back to `created`, as Drupal initializes it."""
    for bad in (None, "", "not-a-number", _ABSENT):
        row = copy.deepcopy(SAMPLE_RESOURCE)
        if bad is _ABSENT:
            del row["attributes"]["changed"]
        else:
            row["attributes"]["changed"] = bad
        record = contacts_stream.post_process(row)
        assert record is not None, f"dropped record with changed={bad!r}"
        assert record["changed"] == record["created"] == "2023-04-24T20:54:33+00:00"


def test_post_process_skips_record_without_any_usable_timestamp(contacts_stream):
    """With neither `changed` nor `created` usable the record cannot be tracked."""
    row = copy.deepcopy(SAMPLE_RESOURCE)
    row["attributes"]["changed"] = None
    del row["attributes"]["created"]
    assert contacts_stream.post_process(row) is None


@pytest.mark.parametrize("changed", [None, _ABSENT])
def test_fallback_record_is_not_filtered_by_bookmark(contacts_stream, monkeypatch, changed):
    """A contact using the `created` fallback is emitted even when `created` is older.

    Filtering on the fallback would drop an updated contact on every run, so it never
    reaches the target.
    """
    monkeypatch.setattr(
        type(contacts_stream),
        "get_starting_timestamp",
        lambda self, context: datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    row = copy.deepcopy(SAMPLE_RESOURCE)  # created = 2023-04-24, well before the bookmark
    if changed is _ABSENT:
        del row["attributes"]["changed"]
    else:
        row["attributes"]["changed"] = changed

    record = contacts_stream.post_process(row)

    assert record is not None
    assert record["changed"] == "2023-04-24T20:54:33+00:00"


def test_fallback_record_does_not_rewind_state(contacts_stream):
    """An older fallback value leaves the bookmark at the newest real `changed`."""
    later = copy.deepcopy(SAMPLE_RESOURCE)
    later["attributes"]["changed"] = "1789000000"
    fallback = copy.deepcopy(SAMPLE_RESOURCE)
    fallback["attributes"]["changed"] = None

    for row in (later, fallback):
        contacts_stream._increment_stream_state(contacts_stream.post_process(row), context=None)

    state = contacts_stream.get_context_state(None)
    marker = state.get("progress_markers", state)
    assert marker["replication_key_value"] == "2026-09-10T00:26:40+00:00"


@pytest.mark.parametrize("changed", [None, _ABSENT])
def test_state_advances_past_record_without_changed(contacts_stream, changed):
    """Regression: a record lacking `changed` must not crash the SDK's state update.

    `increment_state` indexes the replication key and compares it to the prior value,
    raising KeyError or TypeError, and `_sync_records` does not catch either.
    """
    later = copy.deepcopy(SAMPLE_RESOURCE)
    later["attributes"]["changed"] = "1789000000"
    missing = copy.deepcopy(SAMPLE_RESOURCE)
    if changed is _ABSENT:
        del missing["attributes"]["changed"]
    else:
        missing["attributes"]["changed"] = changed

    for row in (later, missing):
        record = contacts_stream.post_process(row)
        assert record is not None
        contacts_stream._increment_stream_state(record, context=None)


def test_post_process_tolerates_naive_bookmark(contacts_stream, monkeypatch):
    """A tz-naive bookmark is treated as UTC rather than raising on comparison."""
    monkeypatch.setattr(
        type(contacts_stream),
        "get_starting_timestamp",
        lambda self, context: datetime.datetime(2030, 1, 1),  # noqa: DTZ001
    )
    assert contacts_stream.post_process(SAMPLE_RESOURCE) is None


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        pytest.param(None, "https://base.newmode.net", id="default"),
        pytest.param("https://base.newmode.net/", "https://base.newmode.net", id="https"),
    ],
)
def test_resolve_base_url_accepts_safe_urls(configured, expected):
    """HTTPS URLs are accepted, with any trailing slash removed."""
    assert resolve_base_url({"api_base_url": configured}) == expected


@pytest.mark.parametrize(
    "configured",
    [
        pytest.param("http://base.newmode.net", id="http-remote"),
        pytest.param("ftp://base.newmode.net", id="other-scheme"),
        pytest.param("base.newmode.net", id="no-scheme"),
        pytest.param("https://", id="no-host"),
    ],
)
def test_resolve_base_url_rejects_cleartext_or_malformed_urls(configured):
    """Anything that could send secrets in cleartext, or is malformed, is refused."""
    with pytest.raises(ConfigValidationError):
        resolve_base_url({"api_base_url": configured})


def test_token_endpoint_refuses_cleartext_base_url():
    """The OAuth endpoint, which receives the client secret, is never built over http."""
    tap = TapNewMode(
        config={**SAMPLE_CONFIG, "api_base_url": "http://base.newmode.net"},
        parse_env_config=False,
    )
    with pytest.raises(ConfigValidationError):
        TapNewMode.access_token_support(tap)
