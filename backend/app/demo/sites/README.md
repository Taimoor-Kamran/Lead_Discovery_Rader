# Demo websites

One directory per host, read by `FixtureFetchBackend` (`app/core/fetch_backends.py`) and
`FixturePageSpeedClient` (`app/modules/audit_web/psi.py`). They are what makes
`make load-demo-data` and the whole test suite audit real-looking websites **without a
single request leaving the machine and without any API key**.

Every host here is either reserved under `.invalid` (RFC 2606 — it can never resolve) or a
demo builder subdomain that exists only in `austin_plumbers.json`. The backends are
installed only where `Settings.fixtures_allowed` is true (`local`, `development`, `ci`), so
nothing here can ever shadow a real site in staging or production.

Nothing in these files is copied from a real website. Every business, phone number and
address is invented, matching the fictional listings in `austin_plumbers.json`.

## Files in a host directory

| File | What it is |
|---|---|
| `index.html` | The homepage. **Every** path under the host reads this one file — the audit is homepage-only, so a fixture never pretends to be a whole site |
| `robots.txt` | Served at `/robots.txt`. Missing → a 404, which the audit reads as "no rules" |
| `psi.json` | A PageSpeed Insights response. Missing → the audit records `checks.psi_error` |
| `_meta.json` | Optional. Status codes, headers, redirects and simulated failures |

## `_meta.json`

```json
{
  "note": "why this fixture exists",
  "failure": "tls",
  "paths": {
    "http:/":        {"status": 301, "headers": {"location": "https://host/"}},
    "/robots.txt":   {"status": 503, "body": "Service Unavailable"}
  }
}
```

- `failure` applies to **every** request to the host: `tls` (the certificate does not
  verify), `timeout`, or `connect`.
- `paths` keys are matched as `"<scheme>:<path>"` first and `"<path>"` second, so a
  redirect can apply to `http` only.
- An entry may set `status`, `headers`, an inline `body`, or its own `failure`.

## Adding a case

1. Create the directory and its files.
2. Add the domain to `expected_audits.json` under `by_domain`, with the status and the
   sorted finding codes it must produce, and a `case` line saying what it is for.
3. Run `pytest tests/integration/test_demo_audits.py`. It asserts the whole map, so a
   missing or extra finding shows up as a diff.

A host with no directory at all is the unreachable case: `badphoneplumbing.invalid` is
left out on purpose.
