# PageSpeed Insights fixtures

`runpagespeed_mobile_three_categories.json` is **constructed, not recorded**: no
`PAGESPEED_API_KEY` existed when v0.11.0 was built, and the rules keep live calls out of
the test suite. It follows the documented v5 `runPagespeed` response shape
(`lighthouseResult.categories.<id>.score` as 0–1, `audits.<id>.numericValue`) for a request
with `category=performance&category=accessibility&category=best-practices`, trimmed to the
fields the audit reads. Replace it with a recorded response once a key is available.

`runpagespeed_accessibility_unscored.json` is the same shape with the accessibility
category present but `"score": null`, which Lighthouse reports when a category could not
be computed.
