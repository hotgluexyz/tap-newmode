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


def test_get_next_page_token_reads_offset_from_next_link(contacts_stream):
    """The next offset is taken from the `links.next` href the API advertises."""

    class _Response:
        @staticmethod
        def json():
            return {
                "links": {
                    "next": {
                        "href": "https://base.newmode.net/jsonapi/contact/contact"
                        "?page%5Boffset%5D=5&page%5Blimit%5D=5",
                    },
                },
            }

    assert contacts_stream.get_next_page_token(_Response(), None) == 5


def test_get_next_page_token_ends_without_next_link(contacts_stream):
    """Pagination stops once the API stops advertising a next page."""

    class _Response:
        @staticmethod
        def json():
            return {"links": {"self": {"href": "https://base.newmode.net/x"}}}

    assert contacts_stream.get_next_page_token(_Response(), 5) is None
