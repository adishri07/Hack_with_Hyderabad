# One Hindsight Bank per Team, One per New Hire

When we started designing memory for our onboarding assistant, the obvious approach was one big memory store with everything in it, filtered by tags. We didn't do that. Ramp uses one shared memory bank for the team and a separate bank for every new joiner, and that split turned out to be the most important architectural decision in the project.

I worked on the architecture of Ramp, an onboarding assistant that answers new joiners' questions, sends anything it's unsure about to a mentor, and remembers the mentor's answer for everyone after. Memory is handled by [Hindsight, an open-source memory layer for AI agents](https://github.com/vectorize-io/hindsight). This post explains how the pieces fit together and why we drew the lines where we did.

## The shape of the system

Ramp is small. Here's the whole thing:

- `app.py`: a Streamlit app with three views, for joiners, mentors and managers.
- `agent.py`: answering, the confidence check, forwarding to mentors, progress tracking and manager insights.
- `memory.py`: everything that talks to Hindsight.
- `store.py`: structured records such as people, handbook pages, forwarded questions and events.
- `llm.py`: a thin wrapper so we can run on Azure OpenAI, Groq or OpenAI without the rest of the code caring.

The request path for a joiner's question:

1. The app calls `agent.answer(joiner_id, question)`.
2. The agent recalls from the team bank and the joiner's personal bank in Hindsight, and searches handbook pages in `store.py`.
3. It builds a labelled prompt and asks the model for a JSON answer with a confidence flag and sources.
4. If the answer isn't confident, it creates a forwarded question for the joiner's mentor.
5. It logs an event and writes a note to the joiner's personal bank.

When a mentor replies, `agent.mentor_reply` writes the answer to the team bank and the joiner's personal bank, and marks the forwarded question answered.

## First split: what we remember versus what we track

Before the two banks, there's a more basic split: `store.py` versus `memory.py`. The top of `store.py` says it directly:

> Hindsight holds what the agent *remembers*. These files hold the structured records the screens need (who is who, which questions are waiting for a mentor, and so on).

A forwarded question has an ID, a status (`waiting` or `answered`), a mentor, and a timestamp. The mentor screen needs "all waiting questions for Rahul, oldest first". That's a filter and a sort, and it has to be exact. It would be silly to ask a semantic memory system for it.

A mentor's answer is different. The next joiner won't ask with the same words. "Workday rejects NIM-GEN-001" and "what code do I use for my timesheet?" need to find the same memory. That's recall by meaning, and it's what Hindsight is for.

Whenever we were unsure where something belonged, we asked: will it be looked up by ID or status, or found by meaning? The answer always made it obvious.

## Second split: team memory and personal memory

Bank names are generated from a prefix:

```python
def team_bank() -> str:
    return f"{config.BANK_PREFIX}-{TEAM_BANK_SUFFIX}"


def joiner_bank(joiner_id: str) -> str:
    return f"{config.BANK_PREFIX}-joiner-{joiner_id}"
```

The **team bank** holds only verified answers from mentors. Every question from every joiner recalls from it.

Each **joiner bank** holds one person's journey: what they asked, what the outcome was, what their mentor told them, and whether they said "This worked" or "Still stuck". Only that joiner's questions and their welcome-back note read from it.

Why not one bank with tags?

**Noise.** In a shared store, Priya's question about VPN errors and Arjun's note that he finished VPN setup would both come back when a third joiner asks about VPN. Neither is useful to the third joiner, and both take space in the prompt that should go to the mentor's verified answer. Separate banks mean team recall only returns team knowledge.

**Trust.** Team memory has one rule: a mentor verified it. Personal memory is a log, including wrong turns and answers that didn't work. Mixing them makes it much harder to tell the model "prefer team memory over the handbook", because some of what it's recalling isn't team memory.

**Privacy.** What a new joiner struggled with is their business and their mentor's. A per-person bank gives us a clean boundary. Deleting someone's onboarding history is deleting a bank, not hunting for tagged records.

**Different missions.** Hindsight lets each bank have a mission describing what it holds. The team bank's mission is about verified onboarding knowledge. A joiner bank's mission is about one person's journey. Each bank is told what it is for, instead of one bank being told it's for everything.

## Putting both into one prompt

The agent merges both kinds of memory with the handbook into one labelled context block:

```python
def _format_context(team_mem, personal_mem, docs, mark_outdated=True) -> str:
    parts = []
    for i, m in enumerate(team_mem, 1):
        parts.append(f"M{i}: {m.text}" + (f" (context: {m.context})" if m.context else ""))
    for i, m in enumerate(personal_mem, 1):
        parts.append(f"P{i}: {m.text}")
    for i, d in enumerate(docs, 1):
        flag = " OUTDATED" if (mark_outdated and d.is_stale) else ""
        parts.append(f"D{i}: [{d.title}, last updated {d.last_updated}{flag}]\n{d.body}")
    return "\n\n".join(parts) if parts else "(no context found)"
```

The prefixes do two jobs. They tell the model how much to trust each item: `M` is verified, `P` is personal context, `D` is a document that may be outdated. And they give the model short codes to cite. The model returns `"sources": ["M1", "D2"]`, and we turn those back into labels a person can read:

```python
if code.startswith("M") and 0 <= index < len(team_mem):
    m = team_mem[index]
    when = f", {m.when[:10]}" if m.when else ""
    labels.append(f"Team memory: mentor answer{when}")
elif code.startswith("D") and 0 <= index < len(docs):
    d = docs[index]
    flag = ", outdated" if d.is_stale else ""
    labels.append(f"Handbook: {d.title} (updated {d.last_updated}{flag})")
```

Personal memories are used but never shown as a source. They shape the answer ("you already finished VPN setup, so skip step one") without being presented as authority.

## What personal memory buys us

The clearest use of the joiner bank is the welcome-back note. When a joiner opens Ramp, the agent recalls from their bank:

```python
memories = memory.recall_personal(
    joiner_id, f"What has {joiner['name']} completed, asked about, or been stuck on?", limit=8)
```

It combines those memories with progress facts computed from the event log and asks the model for a two or three sentence note, using only the facts given. A typical result: Priya finished laptop setup and VPN, is waiting on her mentor about staging database access, and her next step is tools. If two or more earlier joiners got stuck on her next topic, the note warns her to ask before starting.

Without personal memory, every session starts from zero, and every joiner has to re-explain where they are. With it, Ramp behaves like a mentor who remembers yesterday's conversation.

## Failure handling follows the same lines

Because the two kinds of memory have different jobs, they fail differently.

Writing to a joiner's bank is best-effort. The agent wraps it so a memory hiccup never breaks the chat, and uses Hindsight's async retain so the joiner isn't kept waiting.

Writing a mentor's answer to the team bank is not best-effort. It waits until the memory is searchable, because the next joiner depends on it. If it fails, the mentor sees an error and can retry, rather than the answer silently vanishing.

If recall fails when answering, the agent carries on with the handbook alone and reports that memory was unavailable. The joiner gets a less informed answer instead of an error.

## Lessons learned

**1. Separate "found by ID" from "found by meaning".** Queues and statuses go in ordinary storage. Knowledge goes in memory. Every time we were tempted to blur that, it cost us later.

**2. Split memory by trust, not just by topic.** Verified team knowledge and personal history have different reliability. Keeping them in different banks keeps the trust rules simple.

**3. One bank per person is a clean privacy boundary.** It makes deletion and access decisions straightforward.

**4. Label context by source and let the model cite short codes.** It keeps prompts readable and gives you sources you can show users.

**5. Decide failure behaviour per kind of memory.** Background personal notes can be best-effort. Shared knowledge that other people depend on should not be.

If you're designing memory for your own agent, the [Hindsight documentation on memory banks](https://hindsight.vectorize.io/) is worth reading before you commit to a layout, and Vectorize's piece on [what agent memory is](https://vectorize.io/what-is-agent-memory) is a good primer on why this is a different problem from document retrieval.
