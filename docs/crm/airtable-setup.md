# Airtable setup for Lead Discovery Radar

Radar writes one row per approved business into an Airtable table you own. Nothing is
written until a reviewer has approved at least one opportunity for that business and its
undo window (30 minutes) has closed. Radar keeps the business facts up to date; the four
columns that belong to your sales team — **Assigned rep**, **Status**, **Follow-up date**,
**Notes** — are written once when the row is created and never touched again.

You need about ten minutes and an Airtable account that can create bases and tokens.

## 1. Create a base

1. In Airtable, click **Create** → **Start from scratch**. Name it, for example,
   *Lead Discovery Radar*.
2. Open the base and copy its id from the URL: it is the part that starts with `app`,
   e.g. `https://airtable.com/appXXXXXXXXXXXXXX/...` → `appXXXXXXXXXXXXXX`. This is
   `AIRTABLE_BASE_ID`.

## 2. Create the Leads table

Two ways. Either works; the second saves typing.

### Option A — by hand

Rename the default table to **Leads** and create these fields with **exactly** these names
and types (the primary field is the first one). If you prefer other names, keep the types
and see "Renaming columns" below.

| Field | Type | Notes |
|---|---|---|
| Radar Business ID | Single line text | primary field; Radar's upsert key |
| Business name | Single line text | |
| Legal name | Single line text | |
| Industry | Single line text | plain label, e.g. Plumbing |
| City | Single line text | |
| State | Single line text | |
| Postal code | Single line text | |
| Address | Single line text | |
| Website | URL | |
| Public phone | Single line text (or Phone) | formatted `(512) 555-0102` |
| Services | Single line text | e.g. `Website redesign; Online booking` |
| Lead score | Number, 0 decimals | highest approved score, 0–100 |
| Why this is a lead | Long text | the audit rules' reasons |
| Top findings | Long text | plain-language findings from the latest website audit |
| Source | Single line text | e.g. Google Places |
| Source URL | URL | the listing |
| Radar link | URL | opens the lead in Radar |
| Date discovered | Date (ISO) | |
| Date approved | Date (ISO) | |
| Approved by | Single line text (or Email) | reviewer's email |
| Do not contact | Checkbox | set by Radar when a business asks not to be contacted |
| Assigned rep | Single line text (or Single select) | **yours** — set on create, never overwritten |
| Status | Single select: `New`, `Withdrawn`, plus your own | **yours** — `New` on create |
| Follow-up date | Date | **yours** — empty on create |
| Notes | Long text | **yours** — the reviewer's approval note on create |

### Option B — let Radar create it

With a token that also has `schema.bases:write` (step 3), run:

```bash
make crm-bootstrap-airtable
```

It creates **Leads** with every field above and refuses to run if a table with that name
already exists. You can remove the `schema.bases:write` scope from the token afterwards.

## 3. Create a personal access token

1. Go to <https://airtable.com/create/tokens> → **Create new token**.
2. Name it *Lead Discovery Radar*.
3. Scopes — add exactly these:
   - `data.records:read`
   - `data.records:write`
   - `schema.bases:read` (lets `make crm-check` verify the columns and lets Radar build
     links to records)
   - `schema.bases:write` **only** if you want Option B above; remove it afterwards.
4. Access — add **only** the base from step 1.
5. Click **Create token** and copy it. It starts with `pat` and is shown **once**.

## 4. What to send the person running Radar

Send these three values over a channel you trust (a password manager share, not email):

```
AIRTABLE_TOKEN=pat...           # the token from step 3
AIRTABLE_BASE_ID=app...         # from step 1
AIRTABLE_TABLE=Leads            # the table name (or its tbl... id)
```

They go into Radar's `.env`, then:

```bash
make crm-check                  # prints OK / MISSING per field; fix anything missing
# set CRM_DESTINATION=airtable in .env, then restart the stack
```

From then on every approved lead lands in the table within about a minute of its undo
window closing (and immediately with **Send now** on the CRM page).

## Renaming columns

Radar maps its fields to your column names through
`backend/app/modules/crm/crm_field_map.airtable.json`. To use different column names, copy
that file, change the right-hand values, and point `AIRTABLE_FIELD_MAP` at your copy. Keep
the left-hand keys as they are. `make crm-check` tells you if a mapped column is missing
or has an incompatible type.

## Limits Radar respects

Airtable allows 5 requests per second per base and answers 429 with a 30-second wait
when exceeded. Radar stays at 4 per second (`AIRTABLE_RPS`), honours the wait, retries a
failed send three times with growing pauses, and then parks the lead as **Held** on the
CRM page with the error, where a CRM manager can retry it. Radar never deletes a row.
