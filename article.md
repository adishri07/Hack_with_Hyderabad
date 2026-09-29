# Mentors Answer Once, Hindsight Remembers It for Every Joiner

Every new engineer at our company asked the same question in their first week: "How do I get access to the staging database?" The handbook had an answer, but it was two years old and wrong. It told people to email IT for a shared password, and that process had been gone for a long time. So every joiner followed it, got nowhere, and pinged a senior engineer, who typed out the same correct answer for the fifth time that quarter.

That is the problem I built Ramp to fix. Ramp is an onboarding assistant, but the part I care about is not the chat box. It's the rule underneath: **a mentor should only have to answer a question once.** After that, the answer belongs to the team. To make that work I needed real, durable memory, and I built it on [Hindsight, an open-source agent memory system](https://github.com/vectorize-io/hindsight).

This post is about that loop: how Ramp decides it doesn't know something, how a mentor's answer becomes team memory, and the bugs I hit making it reliable.

## What Ramp does

There are three kinds of user:

- **Joiners** ask questions in a chat. They get answers with sources, a welcome-back note that picks up where they left off, and a progress panel across ten onboarding topics (laptop setup, VPN, tools, timesheets, staging database, code review, deployment, and so on).
- **Mentors** see a queue of questions Ramp wasn't confident about. They answer in plain text.
- **Managers** see where joiners get stuck, which handbook pages are out of date, and a written summary of what the team keeps struggling with.

The code is small on purpose. `agent.py` holds the answering logic, confidence check, forwarding and insights. `memory.py` wraps Hindsight. `store.py` handles structured records: people, handbook pages, and the queue of forwarded questions. `llm.py` is a thin wrapper that lets us run on Azure OpenAI, Groq or OpenAI without the rest of the code caring.

The split between `store.py` and `memory.py` was the first real design decision. Structured facts ("this question is waiting for Rahul") live in plain records, because the screens need exact lookups. Things the agent *remembers* (what a mentor said, what a joiner already finished) go into Hindsight, because they need to be recalled by meaning, not by ID.

## Two kinds of memory

Hindsight organises memory into banks. Ramp uses two kinds:

| Bank | Holds | Written when | Read when |
| --- | --- | --- | --- |
| `<prefix>-team` | Verified answers from mentors | A mentor answers a forwarded question | Every question from every joiner |
| `<prefix>-joiner-<id>` | One joiner's questions, outcomes, finished and stuck topics | Every question, mentor answer and "This worked / Still stuck" click | That joiner's questions and welcome-back note |

Each bank gets a mission when it's created, which tells Hindsight what the bank is for:

```python
if bank_id == team_bank():
    mission = (
        f"Onboarding knowledge for {config.COMPANY_NAME}: verified answers from mentors about "
        "tools, access, processes and team norms that new joiners ask about."
    )
else:
    mission = "One new joiner's onboarding journey: what they asked, finished, and got stuck on."
```

I went back and forth on whether personal history should just be rows in a table. It could be, for the progress bar. But the welcome-back note and the "don't repeat steps they already did" behaviour work much better when the model gets a handful of recalled memories like *"Priya confirmed VPN setup is done"* and *"Priya asked about timesheets and was forwarded to Rahul"*. Recall by meaning handles questions the joiner phrases in ways I never predicted. If you want the broader argument for why this matters, Vectorize has a good write-up on [what agent memory is and why stateless agents struggle](https://vectorize.io/what-is-agent-memory).

## The core loop: know when you don't know

The whole system depends on one behaviour: Ramp has to refuse to be confident when it shouldn't be. A handbook bot that confidently repeats a stale page is worse than no bot. It sends people down dead ends with the company's authority behind it.

Every question goes through the same steps:

1. Recall from the team bank and the joiner's personal bank.
2. Search the handbook.
3. Give the model all three, labelled: `M#` for mentor memories, `P#` for personal history, `D#` for handbook pages with their last-updated date. Pages older than a year are marked `OUTDATED`.
4. Ask for JSON back: the answer, a `confident` flag, a topic, and which sources it used.

The system prompt spells out the precedence:

```text
1. Team memory from mentors is newer and verified. If it conflicts with a handbook page, follow the
   mentor answer and say the handbook page is outdated.
2. If the only source for your answer is an OUTDATED handbook page and no team memory covers the
   question, set "confident" to false. Say briefly what the handbook says and that it may be out of date.
3. Never invent links, channels, codes, people or access steps that are not in the context.
```

I don't fully trust a model to follow rule 2, though. Models like to be helpful, and "helpful" often means "confident". So there's a check in code after the model responds:

```python
# Safety net: with memory on, never trust an outdated page that no mentor has confirmed
topic_doc = store.handbook().get(topic)
if use_memory and topic_doc and topic_doc.is_stale and not used_memory:
    confident = False
```

If the topic's page is stale and no memory backed the answer, the answer is not confident, whatever the model said. When `confident` is false, Ramp tells the joiner what the handbook says, flags that it may be out of date, and forwards the question to that joiner's mentor. The joiner isn't stuck waiting in silence. They know a human is on it.

That code check was a good call. Prompt rules are suggestions. Anything that decides whether a person gets sent down a wrong path should be enforced in code too.

## The mentor answers once

When a mentor answers a forwarded question, this is what runs:

```python
memory.retain_team(
    f"Verified answer from mentor {mentor['name']} ({mentor['role']}) on {store.today()} about "
    f"{topic_title}. A new joiner asked: \"{item['question']}\". Correct answer: {answer_text}",
    tags=["source:mentor", f"topic:{item['topic']}", f"mentor:{mentor_id}"],
    context="Verified by a mentor. Prefer this over the handbook if they conflict.",
    wait=True,  # must be searchable before the next joiner asks
)
```

A few details here took me more than one try.

**The memory text is a full sentence, not just the answer.** Storing only the mentor's reply is tempting. Recall still works, but the model can't tell *who* said it, *when*, or *what question* it answered. Putting the original question, the mentor's name and role, and the date in the text lets the model cite it properly ("From Rahul's answer on the 12th...") and lets Hindsight match future questions against the original wording.

**Tags carry structure.** `source:mentor`, `topic:staging-database`, `mentor:rahul`. They're cheap, and they give us a way to filter and audit. If a mentor leaves, we can find everything they taught the system.

**Context tells the model how much to trust it.** The `context` field travels with the memory into the prompt, so the "prefer this over the handbook" instruction sits right next to the fact it's about.

**`wait=True` matters.** Hindsight supports async retain, and I use it for personal memories written on every chat message, because the joiner shouldn't wait on a memory write. But the mentor's answer is different. The whole point is that the *next* joiner gets it. If a second joiner asks thirty seconds later and the retain is still being processed, the loop breaks and a mentor gets pinged again. So team writes block until the memory is searchable. Personal writes don't.

The same answer also goes into the joiner's personal bank, so their welcome-back note can say "Rahul answered your staging database question" the next time they open Ramp.

## What it looks like in practice

Here's the sequence I use when showing Ramp to people.

First, with memory turned off (Ramp has a switch that makes it behave like a plain handbook bot), Arjun asks: *"How do I get access to the staging database?"* The bot confidently tells him to email IT for the shared password. It's wrong, and nothing about the answer shows that.

With memory on, Priya asks the same question. There's no team memory about staging access yet, and the page was last updated two years ago. Ramp tells her the handbook describes an email process, says it may be out of date, and forwards the question to Rahul, her mentor.

Rahul opens his queue and writes:

> The email process is gone. Post a request in the #infra-access channel using the access template. Your team lead approves it and you're added to the stg-db-readers group. Connect with your own SSO login; there are no shared passwords.

That goes into the team bank.

Priya comes back, sees Rahul's answer, clicks "This worked", and her progress panel moves staging database to done.

Then Arjun asks again, with memory on. He gets Rahul's answer straight away, with the source listed as "Team memory: mentor answer" and the date. Rahul isn't pinged. That's the whole product in one exchange.

The manager view closes the loop the other way. `docs_to_update()` flags any stale page where mentors have given a different answer or joiners keep getting stuck, along with the page owner. And the summary button calls Hindsight's `reflect` over the team bank:

```python
question = ("Which onboarding topics do new joiners struggle with most, which handbook pages seem "
            "outdated, and what should the manager fix first? Answer in 4 short bullet points.")
text = memory.reflect_team(question)
```

`reflect` is different from recall. It reasons over everything in the bank and gives a synthesised answer. That's the right tool for "what patterns do you see?", and it saved me from writing a pile of aggregation code plus a second prompt. The [Hindsight documentation on retain, recall and reflect](https://hindsight.vectorize.io/) explains the three operations well if you're choosing between them.

## The bug that cost me an afternoon

Ramp's UI is Streamlit, and Streamlit reruns your script on different threads. The Hindsight Python client is async underneath and keeps a connection tied to the event loop it was first used on. The first question worked. The second one, on a different thread, failed with `Event loop is closed`.

I tried a few clever fixes before settling on a simple one: every Hindsight call runs on one dedicated worker thread that owns one event loop for its whole life.

```python
class HindsightMemory:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight")
        self.client = self._pool.submit(self._make_client).result()

    @staticmethod
    def _make_client():
        from hindsight_client import Hindsight
        asyncio.set_event_loop(asyncio.new_event_loop())  # one loop, kept for the thread's lifetime
        return Hindsight(base_url=config.HINDSIGHT_BASE_URL, api_key=config.HINDSIGHT_API_KEY)

    def _call(self, fn, *args, **kwargs):
        return self._pool.submit(fn, *args, **kwargs).result(timeout=180)
```

Serialising calls through one thread sounds like a bottleneck. In practice memory calls are a small part of each request next to the model call, and a single-worker pool gave me something I value more than throughput: the problem went away completely. If you're putting an async client behind a framework that owns its own threads, give the client a home thread early and save yourself the debugging.

Related: memory failures must never break the chat. Personal writes go through a wrapper that swallows errors, and recall failures are reported as a flag on the response while Ramp falls back to the handbook. A joiner who gets a slightly less personal answer is fine. A joiner who gets a stack trace is not.

## Lessons learned

**1. Make "I'm not sure" a feature, and enforce it in code.** The most useful thing Ramp does is decline to answer confidently from a stale page. The prompt asks for it and a post-check guarantees it. Anything where a wrong answer costs a person real time deserves both.

**2. Store memories as self-contained sentences.** Who said it, when, in reply to what, and the answer. It makes recall match better and makes answers citable. A bare "use #infra-access" means little six months later.

**3. Separate "what we know" from "what we track".** Queues, statuses and IDs belong in ordinary storage. Knowledge that must be found by meaning belongs in memory. Mixing them makes both worse.

**4. Pick sync or async writes by who reads next.** If the next reader is a different user who needs the fact right away, block until it's searchable. If it's background context, write async and move on.

**5. Memory decays too, so design for correction.** A mentor's answer can go out of date just like a handbook page. Ramp's answer is to have mentors answer again, and the newer, dated memory wins in recall and in the prompt. That's not a full solution, but writing dates into every memory from day one is what makes it possible.

What I like about this design is that it gets better through normal use. Nobody has to curate a knowledge base or remember to update the wiki. A mentor answers a question they would have answered anyway, once, and every joiner after that benefits. Hindsight does the remembering. The code just has to be honest about when it doesn't know.
