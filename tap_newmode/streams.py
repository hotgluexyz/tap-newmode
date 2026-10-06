"""Stream type classes for tap-newmode."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from hotglue_singer_sdk import typing as th  # JSON Schema typing helpers
from typing_extensions import override

from tap_newmode.client import NewModeStream

# Attributes New/Mode returned as null for every contact in the reference account, so
# their types could not be confirmed against live data. The JSON:API schema, OpenAPI
# and Drupal `field_config` routes are all unavailable on this install, so these are
# typed permissively to keep a sync from failing on an unexpected value.
# "null" is omitted here: the SDK appends it when building each Property.
_UNVERIFIED = th.CustomType({"type": ["string", "number", "boolean", "object", "array"]})
# Donation figures; Drupal serializes decimals as either a float or a decimal string.
_UNVERIFIED_DECIMAL = th.CustomType({"type": ["number", "string"]})

# Drupal emits this sentinel in place of an id when a referenced entity cannot be
# resolved for the current credentials.
_MISSING_REFERENCE = "missing"


class ContactsStream(NewModeStream):
    """Stream for New/Mode contacts."""

    name = "contacts"
    path = "/contact/contact"
    primary_keys: ClassVar[list[str]] = ["id"]
    replication_key = "changed"

    schema = th.PropertiesList(
        # Resource identity
        th.Property("id", th.StringType, description="Contact uuid."),
        th.Property("type", th.StringType, description="JSON:API resource type."),
        th.Property(
            "drupal_internal__id",
            th.IntegerType,
            description="Numeric contact id used by the v2.1 REST API.",
        ),
        th.Property(
            "drupal_internal__revision_id",
            th.IntegerType,
            description="Numeric id of the current contact revision.",
        ),
        th.Property(
            "org_id",
            th.IntegerType,
            description="Numeric id of the organization the contact belongs to.",
        ),
        # Name
        th.Property("honorific", th.StringType, description="Honorific, e.g. 'Ms'."),
        th.Property("first_name", th.StringType, description="Given name."),
        th.Property("last_name", th.StringType, description="Family name."),
        th.Property("name_suffix", th.StringType, description="Name suffix, e.g. 'Jr'."),
        # Contact details
        th.Property("email", th.StringType, description="Email address."),
        th.Property("mobile_phone", th.StringType, description="Mobile phone number."),
        th.Property("alternate_phone", th.StringType, description="Alternate phone number."),
        th.Property("twitter_handle", th.StringType, description="Twitter handle."),
        # Address
        th.Property("street_address", th.StringType, description="Street address."),
        th.Property("city", th.StringType, description="City."),
        th.Property("region", th.StringType, description="State, province or region code."),
        th.Property("country", th.StringType, description="Country code."),
        th.Property("postal_code", th.StringType, description="Postal or zip code."),
        th.Property("latitude", th.NumberType, description="Geocoded latitude."),
        th.Property("longitude", th.NumberType, description="Geocoded longitude."),
        # Consent
        th.Property("opt_in", th.BooleanType, description="Opted in to the organization."),
        th.Property(
            "nm_product_opt_in",
            th.BooleanType,
            description="Opted in to New/Mode product messaging.",
        ),
        th.Property(
            "nm_marketing_opt_in",
            th.BooleanType,
            description="Opted in to New/Mode marketing messaging.",
        ),
        th.Property("subscriber", _UNVERIFIED, description="Subscriber reference."),
        # Timestamps (New/Mode returns epoch seconds as strings; converted to RFC 3339)
        th.Property("created", th.DateTimeType, description="When the contact was created."),
        th.Property("changed", th.DateTimeType, description="When the contact last changed."),
        # Sync and engagement
        th.Property("prefill_hash", th.StringType, description="Hash used to prefill forms."),
        th.Property("sync_status", _UNVERIFIED, description="Sync status."),
        th.Property("latest_sync_status", _UNVERIFIED, description="Most recent sync status."),
        th.Property(
            "supporter_engagement_score",
            th.IntegerType,
            description="Supporter engagement score.",
        ),
        # Donations
        th.Property(
            "donation_recurring_org",
            _UNVERIFIED_DECIMAL,
            description="Recurring donations to the organization.",
        ),
        th.Property(
            "donation_recurring_nm",
            _UNVERIFIED_DECIMAL,
            description="Recurring donations to New/Mode.",
        ),
        th.Property(
            "donation_recurring_org_total",
            _UNVERIFIED_DECIMAL,
            description="Total recurring donations to the organization.",
        ),
        th.Property(
            "donation_recurring_nm_total",
            _UNVERIFIED_DECIMAL,
            description="Total recurring donations to New/Mode.",
        ),
        th.Property(
            "donation_onetime_org",
            _UNVERIFIED_DECIMAL,
            description="One-time donations to the organization.",
        ),
        th.Property(
            "donation_onetime_nm",
            _UNVERIFIED_DECIMAL,
            description="One-time donations to New/Mode.",
        ),
        # Relationships
        th.Property(
            "groups",
            th.ArrayType(th.StringType),
            description="Uuids of the contact groups the contact belongs to.",
        ),
        th.Property(
            "entitygroupfield",
            th.ArrayType(th.StringType),
            description="Uuids of the group content entries for the contact.",
        ),
    ).to_dict()

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        """Parse one of New/Mode's epoch-second timestamps, or None if unparseable."""
        if value in (None, ""):
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _relationship_ids(relationship: Any) -> list[str]:
        """Return the resolvable uuids referenced by a JSON:API relationship."""
        if not isinstance(relationship, dict):
            return []
        data = relationship.get("data")
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        return [
            item["id"]
            for item in data
            if isinstance(item, dict) and item.get("id") and item["id"] != _MISSING_REFERENCE
        ]

    def _is_before_bookmark(self, changed: datetime, context: dict | None) -> bool:
        """Return True when a record predates the bookmark and should be skipped."""
        starting = self.get_starting_timestamp(context)
        if starting is None:
            return False
        if starting.tzinfo is None:
            starting = starting.replace(tzinfo=timezone.utc)

        return changed < starting

    @override
    def post_process(
        self,
        row: dict,
        context: dict | None = None,
    ) -> dict | None:
        """Flatten the JSON:API envelope into a flat contact record.

        Attributes are lifted to the top level so field names match target-newmode,
        and relationships are reduced to lists of uuids.

        Args:
            row: A JSON:API resource object.
            context: The stream context.

        Returns:
            The flattened record.
        """
        record: dict[str, Any] = {
            "id": row.get("id"),
            "type": row.get("type"),
            **(row.get("attributes") or {}),
        }

        created = self._parse_timestamp(record.get("created"))
        # `changed` is the replication key, so every emitted record needs a usable value:
        # the SDK indexes it when advancing state and raises on a missing or null one.
        # Drupal initializes `changed` to `created`, so that is the fallback.
        changed = self._parse_timestamp(record.get("changed"))
        if changed is None:
            if created is None:
                self.logger.warning(
                    "Skipping contact %s: neither `changed` nor `created` is a usable timestamp.",
                    record.get("id"),
                )
                return None
            # A fallback `created` says nothing about when the contact last changed, so
            # it is never compared to the bookmark; filtering on it could drop an updated
            # contact on every run. The record is re-emitted each sync instead.
            changed = created
        elif self._is_before_bookmark(changed, context):
            return None

        record["created"] = created.isoformat() if created else None
        record["changed"] = changed.isoformat()

        relationships = row.get("relationships") or {}
        for relationship_name in ("groups", "entitygroupfield"):
            record[relationship_name] = self._relationship_ids(
                relationships.get(relationship_name),
            )

        return record
