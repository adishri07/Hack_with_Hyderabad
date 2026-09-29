"""Agent memory, powered by Hindsight.

Two kinds of memory:
  * Team memory (one shared bank): verified answers from mentors. Every joiner benefits.
  * Personal memory (one bank per joiner): what that person asked, finished and got stuck on.

If HINDSIGHT_API_KEY is empty, a simple local file is used instead so people can build
screens without a key. The final demo and submission must run on Hindsight."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import config
from store import words

TEAM_BANK_SUFFIX = "team"


@dataclass
class Memory:
    text: str
    context: str | None = None
    tags: list[str] = field(default_factory=list)
    when: str | None = None


def team_bank() -> str:
    return f"{config.BANK_PREFIX}-{TEAM_BANK_SUFFIX}"


def joiner_bank(joiner_id: str) -> str:
    return f"{config.BANK_PREFIX}-joiner-{joiner_id}"


# ---------------------------------------------------------------- Hindsight

class HindsightMemory:
    """All Hindsight calls run on one dedicated background thread with one event loop.

    Streamlit reruns the app on different threads. The Hindsight client keeps an
    internal connection tied to the event loop it was first used on, so calling it from
    a new thread fails with "Event loop is closed". Running every call on the same
    worker thread avoids that."""

    name = "Hindsight Cloud"

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight")
        self.client = self._pool.submit(self._make_client).result()
        self._ready_banks: set[str] = set()

    @staticmethod
    def _make_client():
        from hindsight_client import Hindsight

        asyncio.set_event_loop(asyncio.new_event_loop())  # one loop, kept for the thread's lifetime
        return Hindsight(base_url=config.HINDSIGHT_BASE_URL, api_key=config.HINDSIGHT_API_KEY)

    def _call(self, fn, *args, **kwargs):
        return self._pool.submit(fn, *args, **kwargs).result(timeout=180)

    def _ensure_bank(self, bank_id: str):
        if bank_id in self._ready_banks:
            return
        if bank_id == team_bank():
            mission = (
                f"Onboarding knowledge for {config.COMPANY_NAME}: verified answers from mentors about "
                "tools, access, processes and team norms that new joiners ask about."
            )
        else:
            mission = "One new joiner's onboarding journey: what they asked, finished, and got stuck on."
        try:
            self._call(self.client.create_bank, bank_id=bank_id, name=bank_id, mission=mission)
        except Exception:
            pass  # the bank usually exists already; retain will surface real errors
        self._ready_banks.add(bank_id)

    def retain(self, bank_id, text, context=None, tags=None, when=None, wait=True):
        self._ensure_bank(bank_id)
        self._call(
            self.client.retain,
            bank_id=bank_id,
            content=text,
            context=context,
            tags=tags,
            timestamp=when,
            retain_async=not wait,
        )

    def recall(self, bank_id, query, limit=6) -> list[Memory]:
        self._ensure_bank(bank_id)
        response = self._call(self.client.recall, bank_id=bank_id, query=query, max_tokens=2000, budget="mid")
        return [
            Memory(
                text=r.text,
                context=r.context,
                tags=r.tags or [],
                when=r.occurred_start or r.mentioned_at,
            )
            for r in response.results[:limit]
        ]

    def reflect(self, bank_id, query) -> str | None:
        self._ensure_bank(bank_id)
        return self._call(self.client.reflect, bank_id=bank_id, query=query).text


# ---------------------------------------------------------------- Local fallback

class LocalMemory:
    """Keyword-matching stand-in for development. Not used in the final demo."""

    name = "Local file (development only)"
    path = config.DATA_DIR / "local_memory.json"

    def __init__(self):
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def retain(self, bank_id, text, context=None, tags=None, when=None, wait=True):
        with self._lock:
            data = self._load()
            data.setdefault(bank_id, []).append({
                "text": text,
                "context": context,
                "tags": tags or [],
                "when": (when or datetime.now()).isoformat(timespec="seconds"),
            })
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def recall(self, bank_id, query, limit=6) -> list[Memory]:
        q = words(query)
        scored = []
        for item in self._load().get(bank_id, []):
            score = len(q & words(item["text"] + " " + " ".join(item["tags"])))
            if score:
                scored.append((score, item["when"], item))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [Memory(i["text"], i["context"], i["tags"], i["when"]) for _, _, i in scored[:limit]]

    def reflect(self, bank_id, query) -> str | None:
        return None

    def clear(self):
        if self.path.exists():
            self.path.unlink()


def _make_backend():
    if config.HINDSIGHT_API_KEY:
        return HindsightMemory()
    return LocalMemory()


backend = _make_backend()


def backend_name() -> str:
    return backend.name


def uses_hindsight() -> bool:
    return isinstance(backend, HindsightMemory)


# ---------------------------------------------------------------- Public functions

def retain_team(text: str, tags: list[str] | None = None, context: str | None = None,
                when: datetime | None = None, wait: bool = True):
    backend.retain(team_bank(), text, context=context, tags=tags, when=when, wait=wait)


def recall_team(question: str, limit: int = 6) -> list[Memory]:
    return backend.recall(team_bank(), question, limit)


def retain_personal(joiner_id: str, text: str, tags: list[str] | None = None,
                    context: str | None = None, when: datetime | None = None, wait: bool = True):
    backend.retain(joiner_bank(joiner_id), text, context=context, tags=tags, when=when, wait=wait)


def recall_personal(joiner_id: str, question: str, limit: int = 6) -> list[Memory]:
    return backend.recall(joiner_bank(joiner_id), question, limit)


def reflect_team(question: str) -> str | None:
    return backend.reflect(team_bank(), question)
