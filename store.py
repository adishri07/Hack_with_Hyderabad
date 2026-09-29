"""Plain JSON files for the people, handbook, and the live activity log.

Hindsight holds what the agent *remembers*. These files hold the structured records
the screens need (who is who, which questions are waiting for a mentor, and so on)."""
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

import config

PEOPLE_FILE = config.DATA_DIR / "people.json"
PAST_QA_FILE = config.DATA_DIR / "past_qa.json"
EVENTS_FILE = config.DATA_DIR / "events.json"          # created at runtime
FORWARDED_FILE = config.DATA_DIR / "forwarded.json"    # created at runtime

# The order a new joiner should work through the handbook
ONBOARDING_ORDER = [
    "laptop-setup", "vpn", "tools", "team-meetings", "org-chart",
    "timesheets", "leave-policy", "staging-database", "code-review", "deployment",
]

_lock = threading.Lock()

STOPWORDS = set(
    "a an the and or of to in on for with is are was were be do does did how what when where "
    "who which i my me we our you your it this that can should get have has not no from at by "
    "as if then there their they them any about into need needs".split()
)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\-]+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def _read(path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def today() -> str:
    return date.today().isoformat()


# ---------- people ----------

@lru_cache(maxsize=1)
def people() -> list[dict]:
    return _read(PEOPLE_FILE, [])


def person(person_id: str) -> dict:
    for p in people():
        if p["id"] == person_id:
            return p
    raise KeyError(f"Unknown person: {person_id}")


def current_joiners() -> list[dict]:
    return [p for p in people() if p["type"] == "joiner" and p.get("current")]


def all_joiners() -> list[dict]:
    return [p for p in people() if p["type"] == "joiner"]


def mentors() -> list[dict]:
    return [p for p in people() if p["type"] == "mentor"]


# ---------- handbook ----------

@dataclass
class Doc:
    slug: str
    title: str
    last_updated: date
    owner: str
    body: str

    @property
    def age_days(self) -> int:
        return (date.today() - self.last_updated).days

    @property
    def is_stale(self) -> bool:
        return self.age_days > config.STALE_AFTER_DAYS


@lru_cache(maxsize=1)
def handbook() -> dict[str, Doc]:
    docs = {}
    for path in sorted(config.HANDBOOK_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        updated = re.search(r"^Last updated:\s*(\d{4}-\d{2}-\d{2})", text, flags=re.MULTILINE)
        owner = re.search(r"^Owner:\s*(.+)$", text, flags=re.MULTILINE)
        body = re.sub(r"^(#\s+.+|Last updated:.*|Owner:.*)$", "", text, flags=re.MULTILINE).strip()
        docs[path.stem] = Doc(
            slug=path.stem,
            title=title.group(1).strip() if title else path.stem,
            last_updated=date.fromisoformat(updated.group(1)) if updated else date.today(),
            owner=owner.group(1).strip() if owner else "Unknown",
            body=body,
        )
    return docs


def topics() -> list[str]:
    return [t for t in ONBOARDING_ORDER if t in handbook()] + [
        t for t in handbook() if t not in ONBOARDING_ORDER
    ]


def topic_title(topic: str) -> str:
    doc = handbook().get(topic)
    return doc.title if doc else topic.replace("-", " ").capitalize()


def search_docs(question: str, limit: int = 3) -> list[Doc]:
    """Simple keyword search. Ten short pages don't need a vector database."""
    q = words(question)
    scored = []
    for doc in handbook().values():
        title_words = words(doc.title + " " + doc.slug.replace("-", " "))
        score = 3 * len(q & title_words) + len(q & words(doc.body))
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [doc for _, doc in scored[:limit]]


# ---------- history and live activity ----------

def past_qa() -> list[dict]:
    return _read(PAST_QA_FILE, [])


def events() -> list[dict]:
    return _read(EVENTS_FILE, [])


def add_event(**fields) -> dict:
    event = {"id": uuid.uuid4().hex[:8], "timestamp": datetime.now().isoformat(timespec="seconds"), **fields}
    with _lock:
        data = events()
        data.append(event)
        _write(EVENTS_FILE, data)
    return event


def forwarded() -> list[dict]:
    return _read(FORWARDED_FILE, [])


def add_forwarded(**fields) -> dict:
    item = {
        "id": "f" + uuid.uuid4().hex[:6],
        "asked_at": datetime.now().isoformat(timespec="seconds"),
        "status": "waiting",
        "seen_by_joiner": False,
        **fields,
    }
    with _lock:
        data = forwarded()
        data.append(item)
        _write(FORWARDED_FILE, data)
    return item


def update_forwarded(item_id: str, **changes) -> dict:
    with _lock:
        data = forwarded()
        for item in data:
            if item["id"] == item_id:
                item.update(changes)
                _write(FORWARDED_FILE, data)
                return item
    raise KeyError(f"Unknown forwarded question: {item_id}")


def reset_runtime_files():
    """Clears live activity for a fresh demo. Hindsight memory is not touched."""
    for path in (EVENTS_FILE, FORWARDED_FILE):
        if path.exists():
            path.unlink()
