# Teaching an Onboarding Bot to Distrust Stale Docs With Hindsight

Three of our ten onboarding handbook pages were wrong. The VPN page described a client we shut down in January. The timesheet page listed a project code that no longer existed and the wrong deadline. The staging database page told people to email IT for a shared password. Every new joiner read those pages, followed them, failed, and asked a senior engineer.

The pages weren't wrong because nobody cared. They were wrong because nobody reads the onboarding handbook except new joiners, and new joiners don't know it's wrong. They assume they made a mistake.

I worked on the data side of Ramp, our onboarding assistant: the handbook pages, the people and mentor records, and the history of questions earlier joiners asked. This post is about how we taught Ramp to treat an old page with suspicion, and how [Hindsight's agent memory](https://github.com/vectorize-io/hindsight) gives it something better to trust.

## Every page carries its own date

Each handbook page is a Markdown file with two header lines:

```markdown
# Staging database access
Last updated: 2024-06-10
Owner: Platform team
```

The loader pulls those out with regex and builds a small object:

```python
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
```

`STALE_AFTER_DAYS` defaults to 365. That's a blunt rule, and I know it. Plenty of year-old pages are fine, and some week-old pages are wrong. But it's a rule a model can follow, a manager can understand, and a page owner can't argue with. "Your page hasn't been touched in two years" is a fact. "Your page might be wrong" is an opinion.

The `Owner` field matters as much as the date. When Ramp flags a page for update, the manager view shows who owns it. A stale-page report without owners is just a list of complaints.

In our handbook, the three wrong pages were also the three oldest: VPN (March 2024), timesheets (January 2024) and staging database (June 2024). The other seven had all been touched in 2026. That's not a coincidence. Pages that people keep correct get edited. Pages that rot get left alone.

## Old pages go into the prompt labelled as old

When Ramp answers a question, it gets three kinds of context, each labelled so the model can cite it: `M#` for mentor answers from Hindsight team memory, `P#` for the joiner's own history, and `D#` for handbook pages. Stale pages are marked in the label itself:

```python
for i, d in enumerate(docs, 1):
    flag = " OUTDATED" if (mark_outdated and d.is_stale) else ""
    parts.append(f"D{i}: [{d.title}, last updated {d.last_updated}{flag}]\n{d.body}")
```

The system prompt then tells the model what to do with that label: if the only source for an answer is an `OUTDATED` page and no team memory covers the question, it must not be confident. It should say what the handbook says, say it may be out of date, and let Ramp forward the question to a mentor.

We back that up in code, because a prompt rule is a request, not a guarantee. After the model answers, if the topic's handbook page is stale and no memory was used, the answer is marked not confident regardless of what the model said.

## Why the handbook search is just keywords

This surprises people. The handbook search is plain keyword overlap, not embeddings:

```python
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
```

Title words count three times as much as body words. That's the whole ranking. For a handbook of a few dozen short, well-titled pages, it's accurate, fast, and easy to reason about when something goes wrong.

The fuzzy part of the problem isn't finding the handbook page. It's finding what a mentor said last month in reply to a question worded completely differently. "Workday rejects my code" and "what do I book my hours to?" are the same question. That's where semantic recall earns its keep, and that's the job we gave to Hindsight. Using the right tool for each half kept the system simple.

## Seeding memory with real history

Ramp shouldn't start empty. Before any joiner used it, we had months of onboarding questions and mentor answers sitting in chat threads. We imported them as structured records, each with a date, the joiner, the topic, the question, the answer, who answered it, and whether the joiner got stuck.

In the history we imported, VPN was the single most-asked topic by a wide margin, and most of the answers came from the same one or two senior engineers. That told us two things before Ramp answered a single live question: which page to fix first, and which mentor was quietly carrying onboarding.

The seeding script turns that history into Hindsight memories:

```python
for q in history:
    when = datetime.fromisoformat(q["date"] + "T10:00:00")
    ...
    if q["answered_by"] != "agent":
        mentor = store.person(q["answered_by"])
        memory.retain_team(
            f"Verified answer from mentor {mentor['name']} ({mentor['role']}) on {q['date']} about "
            f"{topic_title}. A new joiner asked: \"{q['question']}\". Correct answer: {q['answer']}",
            tags=["source:mentor", f"topic:{q['topic']}", f"mentor:{mentor['id']}"],
            context="Verified by a mentor. Prefer this over the handbook if they conflict.",
            when=when,
        )
```

Some decisions in there worth calling out:

**Only mentor answers go into team memory.** If an earlier answer came from the assistant itself, we don't re-store it as team knowledge. Otherwise the system would slowly start trusting its own guesses, which is the exact problem we're trying to avoid.

**Every memory gets its real date via `when`.** Hindsight stores the timestamp we give it, not the import time. So when Ramp cites a mentor answer, it shows the date the mentor actually gave it. When two mentor answers conflict, the newer one can win.

**The memory text is a whole story.** Mentor name and role, date, topic, the original question, and the answer. That makes it citable, and it gives recall more to match against than a bare instruction would.

**Tags carry structure.** `source:mentor`, `topic:vpn`, `mentor:rahul`. They let us audit what a particular mentor has taught the system, or everything we know about one topic.

**Personal memories only for current joiners.** People who finished onboarding months ago don't need a personal bank. Current joiners get their past questions and outcomes loaded, so their first welcome-back note already knows they finished laptop setup and got stuck on VPN.

## What this looks like with real questions

A new engineer asks: *"The Nimbus-Legacy VPN profile says the server is not reachable. How do I connect?"*

The keyword search finds the VPN page, last updated March 2024, marked `OUTDATED`. Hindsight recall finds a mentor answer from June: the legacy client was shut down, install GlobalProtect from Company Portal, use the zero-trust profile, sign in with SSO. The model follows the mentor answer, says the handbook page is out of date, and cites both. Nobody gets pinged.

A different engineer asks about staging database access. The page is two years old, and nobody has asked a mentor about it yet, so there's no team memory. Ramp says the handbook describes an email process that may be out of date and forwards the question to the engineer's mentor. Once that mentor answers, it's in team memory, and the next person gets it straight away.

Meanwhile, the manager's "Handbook pages to update" table lists all three stale pages with their owners and the reason, either "Mentors have given a different answer" or "Joiners keep getting stuck here".

## Lessons learned

**1. Put a date and an owner on every page, and make the software read them.** A `Last updated` line that nothing checks is decoration. One that feeds the prompt and a report changes behaviour.

**2. A blunt freshness rule beats a clever one.** "Older than a year" is easy to explain, easy to enforce, and caught every wrong page we had.

**3. Don't use embeddings where keywords work.** Our handbook search is fifteen lines. Save semantic search for the part of the problem that is actually fuzzy, which for us was mentor memory.

**4. Never let the system learn from itself.** Only human-verified answers go into shared memory. Assistant answers can be logged, but they shouldn't become knowledge.

**5. Import history with its real timestamps.** Dates are what let a newer answer beat an older one, and what make a cited source believable.

If you want to go further with this pattern, the [Hindsight documentation](https://hindsight.vectorize.io/) covers tags, timestamps and bank missions in detail, and Vectorize's overview of [how agent memory differs from retrieval](https://vectorize.io/what-is-agent-memory) is a good explanation of why the handbook and the mentor memory needed different tools.
