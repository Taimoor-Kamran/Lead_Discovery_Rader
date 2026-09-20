"""CRM export (v0.7.0): approved leads leave the system — and only approved leads.

`adapter.py` is the contract every destination implements, `service.py` the rules (the
human gate, the undo delay, dedupe, retry, suppression propagation), `worker.py` the loop
that sends what is due. Destinations: `csv_adapter.py`, `airtable_adapter.py`,
`fake_adapter.py`.
"""
