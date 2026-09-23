# PageSpeed Insights fixtures

`runpagespeed_mobile_recorded.json` is a **recorded live response**: `runPagespeed` v5 for
`https://example.com/`, `strategy=mobile`,
`category=performance&category=accessibility&category=best-practices`, Lighthouse 13.5.0,
recorded on 2026-09-23 with `scripts/psi_smoke.py --record`.

It is the response as Google sent it except for the audits `final-screenshot`,
`full-page-screenshot` and `screenshot-thumbnails` (and `lighthouseResult.fullPageScreenshot`),
whose base64 image data is most of the size and is read by nothing here. The script
refuses to write a file that contains the API key.

`example.com` is IANA's documentation domain, so no business's page content is stored.
To refresh: run the script again from the repository root with `PAGESPEED_API_KEY` set.
