# Using Hindsight Reflect to Find Which Wiki Pages Are Lying

Every engineering manager I know has a vague sense that the onboarding docs are bad. Very few can tell you which page is worst, who owns it, and how many new hires it tripped up last quarter. We built a screen that answers those three questions, and the most useful part of it is one call to Hindsight's `reflect`.

Ramp is our onboarding assistant. New joiners ask it questions. When it isn't confident, usually because the only source is an old handbook page, it forwards the question to a mentor. The mentor's answer is stored in [Hindsight agent memory](https://github.com/vectorize-io/hindsight) so every joiner after them gets it. That loop produces a steady stream of evidence about where onboarding breaks. This post is about turning that evidence into something a manager can act on.

## The evidence Ramp already collects

Without any extra work, Ramp records three things:

1. **Events.** Every question gets logged with the joiner, the topic, and what happened: answered, forwarded to a mentor, marked "worked", or marked "stuck".
2. **Forwarded questions.** Everything Ramp sent to a mentor, and the mentor's answer.
3. **Team memory in Hindsight.** Every verified mentor answer, as a dated sentence with the question, the answer, and tags for topic and mentor.

Plus the imported history of earlier joiners' questions from before Ramp existed.

The first two are ordinary records. The third is where the meaning lives. The manager view uses all of them, and the interesting design question was which parts to compute and which parts to hand to Hindsight.

## The deterministic part: where people get stuck

Some questions should have exact answers. "How many joiners got stuck on VPN?" is a count. We don't want a language model estimating it.

`get_blockers()` merges past history and live events into one shape and counts distinct people per topic:

```python
def get_blockers() -> list[dict]:
    """Topics where joiners get stuck, most common first, with the matching handbook page."""
    stuck_people = defaultdict(set)
    asked_people = defaultdict(set)
    for r in _interactions():
        asked_people[r["topic"]].add(r["joiner_id"])
        if r["stuck"]:
            stuck_people[r["topic"]].add(r["joiner_id"])
    ...
```

We count people, not questions. One frustrated joiner asking about VPN five times is one person stuck, not five. The table shows the topic, how many joiners got stuck, how many asked at all, who they were, and whether the handbook page for that topic is outdated.

Then `docs_to_update()` turns that into a to-do list for page owners:

```python
for doc in store.handbook().values():
    if doc.is_stale and (stuck.get(doc.slug, 0) >= 1 or doc.slug in mentor_topics):
        rows.append({
            "page": doc.title, "owner": doc.owner, "last_updated": str(doc.last_updated),
            "joiners_stuck": stuck.get(doc.slug, 0),
            "reason": "Mentors have given a different answer" if doc.slug in mentor_topics
                      else "Joiners keep getting stuck here",
        })
```

A page lands on this list only if it's more than a year old **and** there's evidence it's hurting someone: a joiner got stuck on it, or a mentor has answered that topic. An old page that nobody struggles with doesn't make the list. It might be old and still correct, and we'd rather not bury owners in noise.

The `reason` column is the most important field on the screen. "Mentors have given a different answer" is not a guess. It means a senior engineer has written down something that contradicts the page, and that answer is sitting in team memory. A page owner can't argue with that.

## The part that needs judgement: reflect

Counts tell you where people get stuck. They don't tell you *why*, or what to fix first, or that three topics are really one underlying problem. That needs someone to read through what mentors have been saying.

Hindsight has three core operations. `retain` stores a memory. `recall` finds memories relevant to a query. `reflect` reasons over what's in a bank and returns an answer. Recall gives you raw material. Reflect gives you a conclusion. The [Hindsight docs](https://hindsight.vectorize.io/) cover the difference in detail.

The manager's "Summarise what joiners struggle with" button calls reflect on the team bank:

```python
def team_insight() -> str:
    """Ask Hindsight to reflect over everything the team has taught the agent."""
    question = ("Which onboarding topics do new joiners struggle with most, which handbook pages seem "
                "outdated, and what should the manager fix first? Answer in 4 short bullet points.")
    try:
        text = memory.reflect_team(question)
        if text:
            return text
    except Exception:
        pass
    rows = get_blockers()[:4]
    return "\n".join(f"- {r['title']}: {r['stuck_count']} joiner{'s' if r['stuck_count'] != 1 else ''} got stuck"
                     + (" (handbook page is outdated)" if r["doc_outdated"] else "") for r in rows)
```

A few things I like about this.

**It's one call.** The alternative was to recall a few dozen memories, stuff them into a prompt, and write a second summarisation prompt with its own rules. Reflect does that job against the whole bank, and the team bank's mission already tells Hindsight what the memories are about.

**It reasons over what mentors actually said.** Because every team memory includes the original question, the mentor's answer and the date, reflect can notice things our counts can't. For example, that the VPN answers and the laptop setup answers both point to the same Company Portal install, so fixing one page means updating the other. Or that timesheet answers mention both a retired code and a changed deadline, so the page is wrong in two places.

**The prompt asks for a decision, not a report.** "What should the manager fix first?" and "4 short bullet points". Managers don't need a summary of the bank. They need a starting point for Monday.

**It has a plain fallback.** If reflect is unavailable, the manager still gets the top four blockers from the deterministic counts, with an outdated-page flag. The button always returns something useful.

## One number for mentor time saved

The top of the manager view has four metrics. The one that matters is "Answered from team memory":

```python
return {
    "questions": len(questions),
    "from_memory": sum(1 for e in questions if e.get("used_memory")),
    "forwarded": sum(1 for e in questions if e["status"] == "forwarded"),
    "waiting": len(waiting_for_mentor()),
}
```

`used_memory` is only true when the model said it used memory **and** memory actually came back for that question. We don't take the model's word for it alone. Each one of those questions is a question no mentor had to answer again. It's not a perfect measure of time saved, but it's honest, and it goes up as the system learns.

## A walk through the manager screen

A manager, Anita, opens Ramp. The metrics show how many questions came in, how many were answered from team memory, how many went to mentors, and how many are waiting right now.

Below that, "Where joiners get stuck" puts VPN at the top, with several earlier joiners stuck and the page flagged as outdated. Timesheets and staging database follow.

"Handbook pages to update" lists the VPN, timesheet and staging database pages, each with its owning team, the last-updated date from 2024, and a reason. VPN and timesheets say "Mentors have given a different answer".

"Current joiners" shows each person's progress, what they're stuck on, and their next step.

Anita clicks "Summarise what joiners struggle with". Hindsight reflects over the team bank and returns four bullets: which topics cause the most trouble, which pages disagree with what mentors are telling people, and which page to fix first. She forwards the VPN row to the network team with the mentor's answer attached as the replacement text.

The fix for a bad page is usually already written. A mentor wrote it in reply to a confused joiner. Ramp's job is to notice, and to put it in front of the person who owns the page.

## Lessons learned

**1. Compute what can be counted, and reflect on what needs judgement.** Counts of stuck joiners are exact and should come from code. "Why, and what first?" is where reflect adds value.

**2. Count people, not questions.** One person asking five times is one person stuck. Counting questions overstates problems for the most persistent users.

**3. Only flag pages with evidence of harm.** Old plus someone stuck, or old plus a mentor contradicting it. That keeps the list short enough that owners actually act on it.

**4. Ask reflect for a decision.** "What should I fix first, in four bullets" is far more useful to a manager than "summarise everything".

**5. Always have a fallback.** Any feature built on a remote call should degrade to something deterministic and still useful.

Every onboarding question is a small signal that some piece of documentation failed someone. Ramp collects those signals as a side effect of answering questions, and Hindsight turns them into a to-do list. If you want to understand why storing these as memories rather than log lines makes that possible, Vectorize's explainer on [agent memory for AI systems](https://vectorize.io/what-is-agent-memory) is a good read.
