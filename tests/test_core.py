"""Tests standard tap features using the built-in SDK tests library."""

import datetime

import pytest
from hotglue_singer_sdk.testing import get_standard_tap_tests

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
