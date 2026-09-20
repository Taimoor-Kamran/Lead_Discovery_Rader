# Demo AI answers

One file per demo business, keyed by its domain, read by `FakeLLMClient`
(`app/modules/ai/fake_client.py`). They are what lets `make load-demo-data` and the test
suite run the whole classification step — schema retry, guardrails, escalation, reuse —
**without an API key and without a single request leaving the machine**.

Every answer here is a *scripted model output*, written by hand to exercise one path. None
of it is a real model's answer and none of it is a fact about a real business: every
business, quote and domain is fictional and lives only in `austin_plumbers.json` and
`app/demo/sites/`.

## File shape

```json
{
  "note": "why this case exists",
  "triage":     [ {"raw": "not json at all"}, {"output": { ...an AIOutput... }} ],
  "escalation": [ {"output": { ... }} ]
}
```

- `triage` and `escalation` are answered **in order**, and the last entry repeats once the
  list is exhausted. That is how "schema drift, then valid on the retry" is scripted.
- An entry gives either `output` (an object, serialised for the caller) or `raw` (returned
  as is). `tokens_in` / `tokens_out` are optional.
- A business with no file — or a file with no entry for the tier asked — gets the minimal
  "unknown" answer: no summary, no opportunities, no intent.

## The cases

| File | Case |
|---|---|
| `bartoncreekplumbing.invalid` | A clean, good answer that agrees with the rules and raises their confidence a little |
| `zilkerpipeworks.invalid` | Schema drift on the first call, a valid answer on the retry |
| `oakhillplumbing.invalid` | An **invented quote** (dropped, recorded) and an AI-only `ads_social` suggestion (capped at 0.6) |
| `wixwaterworks.wixsite.com` | An **invented email address** in a rationale (the rationale is blanked) |
| `riversideplumbing.invalid` | The **prompt-injection** homepage: the fake model obeys it and says `buying_intent = explicit`; the guardrails force `none_detected` |
| `godaddygutters.godaddysites.com` | An unclear triage answer (confidence 0.5) that **escalates** to the stronger model |

`expected_opportunities.json` records what every demo business must end up with, and
`tests/integration/test_demo_opportunities.py` asserts it.
