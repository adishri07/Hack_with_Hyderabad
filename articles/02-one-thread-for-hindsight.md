# Why I Pinned Every Hindsight Call to One Thread

The first question a new joiner asked our onboarding assistant worked perfectly. The second one crashed with `RuntimeError: Event loop is closed`. Nothing had changed between the two requests except which thread Streamlit decided to run them on.

I own the memory layer of Ramp, our onboarding assistant. Ramp answers new joiners' questions, sends anything it isn't sure about to a mentor, and stores the mentor's answer so nobody has to answer it again. All of that remembering runs on [Hindsight, the open-source memory engine for agents](https://github.com/vectorize-io/hindsight). This post covers how I wrapped Hindsight so the rest of the codebase never has to think about it, and the threading problem that shaped the whole design.

## What the memory layer has to do

Ramp keeps two kinds of memory:

- **Team memory**: one shared bank of verified answers from mentors. Every joiner's question is checked against it.
- **Personal memory**: one bank per joiner, holding what they asked, what they finished, and where they got stuck. It powers the "welcome back" note and stops Ramp repeating steps someone already did.

The rest of the app talks to memory through five functions in `memory.py`: `retain_team`, `recall_team`, `retain_personal`, `recall_personal` and `reflect_team`. None of them expose a Hindsight type. Recall results come back as a small dataclass:

```python
@dataclass
class Memory:
    text: str
    context: str | None = None
    tags: list[str] = field(default_factory=list)
    when: str | None = None
```

That boundary was deliberate. The agent code builds prompts out of `text`, `context` and `when`, and it shouldn't need to know that Hindsight calls the date `occurred_start` on some results and `mentioned_at` on others. Keeping the mapping in one place meant that when I changed recall settings, nothing outside `memory.py` moved.

## The bug

Ramp's UI is Streamlit. Streamlit reruns your script on every interaction, and those reruns don't always happen on the same thread.

The Hindsight Python client is async underneath. It keeps an internal connection that belongs to the event loop it was first used on. So the sequence went like this:

1. First request, thread A. The client creates its connection on thread A's event loop. Works.
2. Second request, thread B. The client reuses the connection, which is tied to a loop that is no longer running. `Event loop is closed`.

This is painful because it's intermittent. Sometimes two requests land on the same thread and everything looks fine. You can click around for a minute, decide it's fixed, and then watch it fall over when someone else tries it.

The options I considered:

- **Create a new client for every call.** It works, but you pay for connection setup on every recall, and a single joiner question makes at least two.
- **Run `asyncio.run()` per call with a fresh loop.** Same problem as above, plus you now own loop lifecycle in every function.
- **Give the client one permanent home.** One thread, one event loop, for the life of the process. Everything that touches Hindsight goes through that thread.

I went with the third.

## The fix: one worker thread, one loop

```python
class HindsightMemory:
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
```

Three things are going on here.

The client is **created on the worker thread**, not on whatever thread happened to import the module. That's why `_make_client` is submitted to the pool rather than called directly. The event loop it sets up belongs to that worker and is never closed.

Every Hindsight call, whether `retain`, `recall`, `reflect` or `create_bank`, goes through `_call`, which **submits the call to the same single worker** and waits for the result. From the caller's side it's a normal blocking function. Streamlit can run on any thread it likes.

There's a **timeout on every call**. If something upstream hangs, the request fails in a bounded time instead of holding a Streamlit session forever.

Yes, this serialises all memory traffic in the process through one thread. For our workload that's fine: each joiner question makes two recalls and one background retain, and the language model call next to them takes far longer. If we ever need more throughput, the next step is several worker threads each with their own client and loop, not going back to sharing a client across threads.

## Banks that create themselves

Hindsight organises memories into banks, and each bank can have a mission, a short description of what it's for. I didn't want a separate setup step for every new joiner, so banks are created the first time they're used:

```python
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
```

The `_ready_banks` set means we try to create each bank at most once per process. Swallowing the create error looks lazy, but it's intentional: after the first run the bank almost always exists, and if something is really wrong (a bad key, the service down), the very next `retain` or `recall` will raise a clear error. I'd rather have one failure point than two.

Bank names are namespaced with a prefix from config: `<prefix>-team` and `<prefix>-joiner-<id>`. Changing the prefix gives you a completely separate memory space, which is how we keep staging and production apart without any extra code.

## Sync or async retain

Hindsight processes a retained memory before it becomes searchable, and the client lets you choose whether to wait for that. Ramp uses both, depending on who reads the memory next.

```python
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
```

- When a **joiner asks a question**, we write a note to their personal bank with `wait=False`. They're looking at the chat and shouldn't wait on a memory write they won't need for minutes.
- When a **mentor answers**, we write to the team bank with `wait=True`. The whole point of that write is that the next joiner, possibly seconds later, gets the answer. If it isn't searchable yet, that joiner is sent to the mentor again, and we've failed at the one thing Ramp exists to do.

The `timestamp` parameter matters more than it looks. When we import past onboarding history, each memory gets the date it actually happened, not the date we imported it. Recall results carry that date back, and Ramp shows it in its sources ("Team memory: mentor answer, 2026-06-03"). Joiners trust a dated answer more, and the model can prefer a newer mentor answer over an older one.

## Recall settings

```python
def recall(self, bank_id, query, limit=6) -> list[Memory]:
    self._ensure_bank(bank_id)
    response = self._call(self.client.recall, bank_id=bank_id, query=query, max_tokens=2000, budget="mid")
    return [
        Memory(text=r.text, context=r.context, tags=r.tags or [],
               when=r.occurred_start or r.mentioned_at)
        for r in response.results[:limit]
    ]
```

`max_tokens=2000` caps how much memory lands in the prompt. `budget="mid"` is Hindsight's middle setting for how hard recall works to find matches. We ask for up to six team memories and four personal ones per question. More than that and the prompt fills with loosely related answers that make the model hedge. The [Hindsight documentation](https://hindsight.vectorize.io/) explains the budget levels if you want to tune this for your own data.

## A setup check that tests the real thing

The last piece is small but has saved us plenty of time. `check_setup.py` doesn't just check that an API key is present. It writes a memory and reads it back:

```python
bank = f"{config.BANK_PREFIX}-setup-check"
memory.backend.retain(bank, "The staging database is requested in the infra-access channel.")
results = memory.backend.recall(bank, "How do I get database access?")
print("  Recall returned:", results[0].text if results else "nothing (try again in a few seconds)")
```

Note that the query and the stored text share almost no words. "Get database access" versus "staging database is requested in the infra-access channel". If this comes back, recall by meaning is working end to end, not just string matching. Anyone setting up a new environment runs it first.

## Lessons learned

**1. Give async clients a permanent home thread.** If your framework owns threading (Streamlit, some WSGI servers, task runners), don't share an async client across its threads. One worker, one loop, created on that worker. It's twenty lines and it removes a whole class of intermittent failures.

**2. Put a boundary type between your app and your memory SDK.** Our `Memory` dataclass has four fields. The rest of the code doesn't import anything from Hindsight, which made the threading fix invisible to everyone else.

**3. Choose sync or async writes by who reads next.** Block when a different user needs the fact right away. Don't block when it's background context for later.

**4. Always pass the real timestamp.** Memories without accurate dates can't be ranked by freshness, and in a domain where processes change, freshness is most of what matters.

**5. Test recall with a query that doesn't share words with the memory.** It's the quickest proof you're getting semantic recall and not keyword search in disguise.

Once the memory layer was dull and reliable, the interesting work could happen above it: deciding when Ramp should say "I'm not sure", and making a mentor's single answer reach every joiner after them. If you want the broader context on why agents need this kind of layer at all, Vectorize has a clear piece on [what agent memory is](https://vectorize.io/what-is-agent-memory).
