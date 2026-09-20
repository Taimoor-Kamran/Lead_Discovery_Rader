# Pilot: validating the radar with real data

The blueprint asks for one real-data validation before anyone trusts the scores (slide 53).
This is that run, sized so it costs a few dollars and one reviewer's afternoon, followed by
two weeks of ordinary selling by hand. Do it in production mode (`docs/operations.md`) with
the client's own Google Places key and OpenAI key in `.env.prod`. Nothing in this guide sends
a message to anyone: outreach stays with the reps, by hand.

## Before you start

- `make prod-up` running with `AI_PROVIDER=openai`, the model names and prices filled in,
  and `CRM_DESTINATION` set to `csv` or `airtable`.
- Budget alerts set in Google Cloud Billing and a monthly spend limit in the OpenAI dashboard.
- The client's legal/compliance review of the outreach itself is done (blueprint slide 43).
- One reviewer and at least one sales rep with accounts.

## The run

1. Searches → pick **one industry** and **one city** you actually sell to, max results **60**
   (the Places ceiling for one query). Read the cost estimate: it should be 3 Places calls,
   at most 60 PageSpeed calls and at most 60 AI calls. Note the numbers.
2. *Save and run*. Follow the four stages; when classification is done, open the review queue
   for that city.
3. The reviewer reviews **at least 20 businesses** in one sitting, with a stopwatch on the
   whole sitting. For each: approve (assigning a rep), reject with a reason, not a fit, needs
   enrichment, duplicate, or do not contact. Reject when the evidence is wrong — that is the
   number we most need.
4. Duplicates page: decide every pending pair. Separately note any two businesses you *know*
   are the same and the system kept apart.
5. Health page: read AI spend for the day and the Places / PageSpeed calls. Fill in the table.
6. For 14 days the reps contact the approved leads by hand (their own phone and email) and
   record every reply, however short, in the CRM's Notes.

## Results (fill in)

| Measure | Value | Source |
|---|---|---|
| Industry / city / date | | |
| Results discovered / businesses after resolution | | pipeline view (discovery `stored_new`, resolution `created` + `linked_existing`) |
| Businesses reviewed | | review queue |
| Approved / rejected / not a fit / needs enrichment / duplicate / DNC | | review queue counts |
| **Approval rate** (approved ÷ reviewed) | | |
| **False-positive findings**: opportunities rejected with *Evidence is wrong* | | reject reasons |
| Duplicate pairs sent to review; pairs the reviewer merged | | Duplicates page, Health → Duplicates |
| **Duplicates missed** (same business kept apart, found by eye) | | reviewer's notes |
| Audits: done / robots blocked / unreachable | | Health → Audit outcomes |
| **Time per review** (sitting ÷ businesses) | | stopwatch |
| Places calls / PageSpeed calls / AI calls | | Health → sources, AI |
| **API + AI cost** for the run (USD) | | Health → AI spend; Google Cloud billing for Places/PSI |
| Leads contacted in 14 days / replies / meetings booked | | reps' CRM notes |

## Go / no-go questions

Answer each with the numbers above, in writing, before tagging v1.0.0.

1. Did the reviewer approve at least a third of what the queue showed, and reject fewer than
   one in five for *wrong evidence*? If evidence is wrong more often than that, the rules or
   the AI guardrails need work before more cities.
2. Were duplicates handled — no more than a couple of pairs missed by eye, and no false merges
   found in the CRM?
3. Was a review under three minutes per business? If not, what on the page slowed it down?
4. Did the run cost what the estimate said, and is that acceptable per approved lead?
5. Did any approved lead reply within 14 days? Zero replies is a signal about the outreach or
   the list, not necessarily the radar — but write down which.
6. Did anything in the audit look like it should not have been fetched (robots, a site owner
   complaint)? If yes, stop and fix before scaling.

A "no" on 1, 2 or 6 is a no-go: fix and re-run the pilot. A "no" on 3–5 is a judgement call to
record in the release checklist.
