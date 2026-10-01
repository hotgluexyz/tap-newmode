# tap-newmode

A [Singer](https://www.singer.io/) tap that extracts data from **New/Mode**. It is built with [hotglue-singer-sdk](https://github.com/hotgluexyz/HotglueSingerSDK) and speaks the standard Singer message protocol on stdout, so you can pair it with any compatible target.

## Features

- **REST** streams over New/Mode's Drupal [JSON:API](https://jsonapi.org/) (see `client.py` / `streams.py`).
- **OAuth2** client-credentials auth, with optional token fetching via the Hotglue access token endpoint.
- Config keys mirror [`target-newmode`](https://github.com/hotgluexyz/target-newmode), so one connector config drives both.
- Offset pagination driven by the `links.next` href the API advertises.

### Streams

| Stream | Endpoint | Primary key | Replication |
| ------ | -------- | ----------- | ----------- |
| `contacts` | `GET /jsonapi/contact/contact` | `id` (contact uuid) | Full table |

Records are flattened out of the JSON:API envelope: `attributes` are lifted to the top
level so field names match `target-newmode`, the resource `id` and `type` are kept, and
`groups` / `entitygroupfield` are reduced to lists of uuids. The `created` and `changed`
timestamps arrive as epoch-second strings and are converted to RFC 3339.

#### Why `contacts` is full table

New/Mode's contact collection is slow — a single page takes roughly 55 seconds, and the
upstream gateway cuts requests off at 60, so `504 Gateway Time-out` is common. Adding
`sort=changed` with a `filter[...]` on `changed` pushed every request past that ceiling:
13 consecutive attempts returned 504 or timed out, while unfiltered requests to the same
collection succeeded regularly. Server-side incremental filtering is therefore not usable
on this API today, and the stream syncs full table.

To compensate, `504` is treated as retriable alongside `429`, and the stream retries up to
8 times with capped exponential backoff.

#### Field types

Eight attributes were null for every contact in the reference account
(`name_suffix`, `subscriber`, `sync_status`, `latest_sync_status` and the four
`donation_*` fields), and New/Mode's install exposes no JSON:API schema, OpenAPI or
Drupal `field_config` route to confirm them against. Those are typed permissively in
`streams.py` so an unexpected value cannot fail a sync; tighten them if the vendor
documents the real types.

## Requirements

- Python **3.10+** (see `requires-python` in `pyproject.toml`).

## Installation

1. **Clone** this repository and `cd` into the project directory.
2. **Create `config.json`** in the project root with your credentials and settings (see [Configuration](#configuration) for the fields and an example).
3. **Create a virtual environment** and activate it:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, use `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

4. **Install the package** in editable mode:

```bash
pip install -e .
```

5. **Run the tap** (with the venv still activated):

```bash
tap-newmode --help
```

## Configuration

| Setting | Type | Required | Default | Description |
| ------- | ---- | -------- | ------- | ----------- |
| `client_id` | string | yes | — | New/Mode OAuth client id. |
| `client_secret` | string | yes | — | New/Mode OAuth client secret. |
| `api_base_url` | string | no | `https://base.newmode.net` | New/Mode API base URL. |
| `start_date` | string (datetime) | no | `2000-01-01T00:00:00Z` | Earliest record date to sync. |
| `_refresh_token_via_hg_api` | boolean | no | `false` | Fetch tokens from the Hotglue access token endpoint. |
| `access_token` | string | no | — | Current access token; written back by the tap. |
| `expires_in` | integer | no | — | Epoch seconds when the token expires; managed by the tap. |

`client_id`, `client_secret`, `api_base_url`, `access_token` and `expires_in` use the same
names and semantics as `target-newmode`. Target-only keys (`org_id`, `gid`,
`default_country`, `only_upsert_empty_fields`, `lookup_method`, `lookup_fields`) are
ignored by the tap, so a shared config is safe to pass to either.

Run `tap-newmode --about` (or `tap-newmode --about --format=markdown`) for the authoritative schema for your installed version.

### Example `config.json`

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "api_base_url": "https://base.newmode.net",
  "start_date": "2000-01-01T00:00:00Z"
}
```

Do not commit real credentials. Prefer environment variables or a secrets manager in production.

### Environment-based config

You can load settings from the process environment using `--config=ENV` (the SDK merges env into config). Env names follow the tap’s setting keys (see `tap-newmode --about`).

## Usage

With your virtual environment **activated** and `config.json` in place:

Discover stream catalog:

```bash
tap-newmode --config config.json --discover > catalog.json
```

Run a sync (with optional state):

```bash
tap-newmode --config config.json --catalog catalog.json --state state.json
```

Pipe to any Singer target:

```bash
tap-newmode --config config.json --catalog catalog.json | target-jsonl
```

Inspect built-in settings and stream metadata:

```bash
tap-newmode --about
```

## API / documentation

- **Base URL:** `https://base.newmode.net`
- **Token endpoint:** `POST /oauth/token` (grant type `client_credentials`; tokens last 300 seconds and no refresh token is issued)
- **Contacts:** `GET /jsonapi/contact/contact`, requiring the `application/vnd.api+json` Accept header — New/Mode answers `application/json` with a 415
- Reference: New/Mode Impact v2.1 API summary

Note that the v2.1 REST API documented under `/api/v2.1` is a separate surface from the
Drupal JSON:API routes under `/jsonapi` that this tap reads.

### Hotglue access token endpoint

Set `"_refresh_token_via_hg_api": true` and provide the `TENANT`, `API_KEY`, `FLOW`,
`ENV_ID` and `TAP` environment variables to fetch tokens from Hotglue instead of running
the client-credentials grant against New/Mode directly. It defaults to `false` so local
runs work without that environment.


## License
Apache 2.0 — see `LICENSE` and `pyproject.toml`.
