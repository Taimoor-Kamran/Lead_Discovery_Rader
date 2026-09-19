"""The fake provider: scripted answers from `app/demo/ai/<key>.json`. Never leaves the machine.

It exists so the demo and the whole test suite can exercise every path the real provider
would take — a clean answer, schema drift and a retry, an invented quote, an invented
email, a prompt injection that the model obeyed, an unclear case that escalates — without
a key and without a network. A fixture file looks like:

    {
      "note": "why this case exists",
      "triage":     [ {"raw": "not json at all"}, {"output": { ...an AIOutput... }} ],
      "escalation": [ {"output": { ... }} ]
    }

Each tier is a list, answered in order and repeating the last entry once exhausted: that
is how "schema drift, then valid on retry" is scripted. An entry gives either `output`
(an object, serialised for the caller) or `raw` (text returned as is). `tokens_in` and
`tokens_out` are optional; without them a rough count from the prompt length is used.
A key with no fixture — or a fixture with no entry for the tier — gets the minimal valid
"unknown" answer, which says nothing and proposes nothing.
"""

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.modules.ai.client import LLMRequest, LLMResult
from app.modules.ai.schema import AIOutput

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "demo" / "ai"
FAKE_TRIAGE_MODEL = "fake-triage"
FAKE_ESCALATION_MODEL = "fake-escalation"
CHARS_PER_TOKEN = 4


class FakeLLMClient:
    provider = "fake"

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(root) if root is not None else FIXTURE_ROOT
        self._served: Counter[tuple[str, str]] = Counter()
        self.requests: list[LLMRequest] = []

    def reset(self) -> None:
        self._served.clear()
        self.requests.clear()

    def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        started = time.perf_counter()
        entry = self._entry(request)
        if entry is None:
            text = AIOutput.unknown().model_dump_json()
        elif "raw" in entry:
            text = str(entry["raw"])
        else:
            text = json.dumps(entry.get("output"), ensure_ascii=False)
        return LLMResult(
            text=text,
            model=request.model,
            tokens_in=int(entry.get("tokens_in", _rough(request.system + request.user)))
            if entry
            else _rough(request.system + request.user),
            tokens_out=int(entry.get("tokens_out", _rough(text))) if entry else _rough(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def fixture_path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    def _entry(self, request: LLMRequest) -> dict[str, Any] | None:
        key = request.fixture_key
        if not key:
            return None
        path = self.fixture_path(key)
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as handle:
            fixture = json.load(handle)
        answers = fixture.get(request.tier)
        if not isinstance(answers, list) or not answers:
            return None
        index = self._served[(key, request.tier)]
        self._served[(key, request.tier)] += 1
        entry = answers[min(index, len(answers) - 1)]
        return dict(entry) if isinstance(entry, dict) else None


def _rough(text: str) -> int:
    return max(len(text) // CHARS_PER_TOKEN, 1)
