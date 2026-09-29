# I Added a Memory Off Switch to Prove Hindsight Mattered

The most persuasive control in our onboarding assistant is a toggle labelled "Memory on". Flip it off and the assistant confidently tells a new engineer to email IT for a shared database password, a process that was retired years ago. Flip it on and the same question gets the correct steps, with the name of the mentor who wrote them and the date.

I built the screens for Ramp, an onboarding assistant that remembers what mentors have taught it. The memory itself lives in [Hindsight, an open-source agent memory system](https://github.com/vectorize-io/hindsight). My job was to make that memory visible and useful to three very different people: a new joiner, a busy mentor, and a manager. This post is about the UI decisions that made people trust it.

## Three people, one app

Ramp is a single Streamlit app with three views chosen by who you are:

- **Joiner**: a chat, a welcome-back note, answers from mentors as they arrive, and a progress panel across ten onboarding topics.
- **Mentor**: a queue of questions Ramp wasn't sure about, each with a text box.
- **Manager**: where joiners get stuck, which handbook pages need fixing, per-joiner progress, and a written summary.

The routing is as boring as it should be:

```python
if user["type"] == "joiner":
    joiner_view(user)
elif user["type"] == "mentor":
    mentor_view(user)
else:
    manager_view(user)
```

Keeping all three in one app was the right call. The whole value of Ramp is a loop between these people: a joiner asks, a mentor answers, the next joiner benefits, the manager sees the pattern. Having all three views in one place made that loop easy to build and easy to explain.

## Why the off switch exists

When you show someone an assistant with memory, the answers look like any other chatbot's. Good answers don't show you where they came from. People nod politely and assume it's a wrapper around a language model and a wiki.

So the joiner sidebar has this:

```python
use_memory = st.toggle("Memory on", value=True,
                       help="Turn off to see how a plain handbook bot answers, with no memory.")
```

With memory off, Ramp uses a different system prompt, skips Hindsight recall entirely, doesn't mark old pages as outdated, and doesn't log or learn anything. The screen says so plainly:

```python
if not use_memory:
    st.warning("Memory is off. This is how a plain handbook bot behaves: no team knowledge, "
               "no memory of you, nothing learned.")
```

This turned out to be useful well beyond showing Ramp to people. It's a side-by-side test we can run on any question. When we add a new handbook page or a mentor corrects something, asking the same question with memory off and on shows exactly what Hindsight is contributing. When the two answers are identical, memory isn't adding anything for that topic, and that's worth knowing too.

## Showing where every answer came from

The second decision: every answer shows its sources, and you can open the actual memories used.

```python
if meta.get("sources"):
    st.markdown(f'<p class="src">Sources: {" | ".join(meta["sources"])}</p>', unsafe_allow_html=True)
if meta.get("memories_used"):
    with st.expander(f"Memories used ({len(meta['memories_used'])})"):
        for m in meta["memories_used"]:
            st.markdown(f"- {m}")
```

Sources come back from the agent as readable labels, like "Team memory: mentor answer, 2026-06-03" or "Handbook: VPN access (updated 2024-03-02, outdated)". The expander shows the recalled memory text itself, for example the full sentence about which mentor answered which question and what they said.

Most joiners never open the expander. That's fine. The fact that it's there changes how they read the answer. And when an answer is wrong, the expander tells us immediately whether the problem is recall (wrong memories came back) or generation (right memories, bad answer). That alone made debugging far faster.

## "I'm not sure" has to look different

When Ramp isn't confident, usually because the only source is a handbook page over a year old and no mentor has covered the topic, it forwards the question to the joiner's mentor. The UI makes this impossible to miss:

```python
if meta.get("forwarded_to"):
    st.warning(f"I'm not sure this is still correct, so I've asked your mentor "
               f"{meta['forwarded_to']}. You'll see the answer here.")
```

A yellow box, the mentor's name, and a promise about where the answer will appear. A hedge buried in the answer text is easy to skim past. A separate, differently coloured element gets read. It also sets the right expectation: you're not stuck, a named person has your question.

When the mentor replies, the answer appears at the top of the joiner's screen in its own card with a "Got it" button. Clicking it marks the topic done and refreshes the welcome-back note.

## Feedback buttons that write to memory

Under every confident answer there are two buttons: **This worked** and **Still stuck**.

```python
if c1.button("This worked", key=f"ok-{jid}-{i}"):
    agent.record_feedback(jid, meta["topic"], worked=True, question=meta.get("question", ""))
    msg["feedback"] = "worked"
    st.rerun()
if c2.button("Still stuck", key=f"stuck-{jid}-{i}"):
    item = agent.forward_to_mentor(jid, meta.get("question", ""), meta["topic"], msg["content"])
    agent.record_feedback(jid, meta["topic"], worked=False, question=meta.get("question", ""))
    msg["feedback"] = "stuck"
    msg["meta"]["forwarded_to"] = store.person(item["mentor_id"])["name"]
    st.rerun()
```

These aren't vanity thumbs. "This worked" moves the topic to done in the progress panel and writes a line into the joiner's Hindsight bank, so tomorrow's welcome-back note knows. "Still stuck" does something more useful: it forwards the question to the mentor even though Ramp was confident. That's the escape hatch for when memory itself is wrong or out of date. The joiner doesn't need to know any of this. They just press the honest button.

## The mentor screen is designed around one sentence

The subtitle on the mentor view is:

> Answer once. Every future joiner gets your answer automatically.

That sentence does real work. Mentors write different answers when they know it's going to every future joiner rather than one person in a DM. They write complete steps instead of "just ping infra". The text box placeholder repeats it: "Write the answer you'd want every new joiner to get."

When they click **Send answer**, the spinner says "Saving to team memory..." and the toast says "Answer sent and saved to team memory". I was careful with that wording. The mentor should understand that this answer is being kept, not just delivered.

The empty state matters too. When there's nothing waiting, the screen says: "No questions waiting. New joiners are getting answers from team memory." For a mentor, an empty queue is the product working.

## A manager metric that means something

The manager view opens with four numbers: questions asked, answered from team memory, sent to mentors, and waiting now. The second one carries a tooltip:

```python
c2.metric("Answered from team memory", stats["from_memory"],
          help="Each one is a question no mentor had to answer again.")
```

That's the number a manager actually cares about, expressed in the terms they care about: mentor time saved. Below it are tables for where joiners get stuck and which handbook pages to update (with owner and reason), and a button that asks Hindsight's `reflect` to summarise what joiners struggle with.

## Streamlit things I'd warn you about

Streamlit reruns the whole script on every click. Two consequences:

- **Cache expensive things in session state.** The welcome-back note involves a Hindsight recall and a model call. It's generated once per joiner and stored in `st.session_state.welcome`, then deliberately cleared when something changes it, like clicking "Got it" on a mentor answer.
- **Threads move under you.** Streamlit doesn't guarantee the same thread between reruns, which broke the async Hindsight client until our memory layer moved every call onto one dedicated worker thread. If you're using any async SDK from Streamlit, plan for this on day one.

## Lessons learned

**1. Build a before-and-after control into the product.** A memory toggle turned an invisible feature into something anyone can check in ten seconds, and it's now part of how we test changes.

**2. Show sources on every answer, and let people open the raw memories.** Trust goes up, and debugging gets much easier because you can tell recall problems from generation problems.

**3. Uncertainty needs its own visual element.** Put "I'm not sure" in a different box with a named human attached. Hedging inside the answer text gets skipped.

**4. Make feedback buttons do real work.** "Still stuck" is a manual override on the agent's confidence. Joiners use it because it gets them a human.

**5. Tell mentors their answer is permanent.** The copy on the mentor screen improved the answers that went into memory, which improved every answer after that.

If you're building something similar, the [Hindsight docs on retain, recall and reflect](https://hindsight.vectorize.io/) are worth reading before you design the screens, because the three operations map neatly to three kinds of UI: writing, answering, and summarising. And if you're still deciding whether your assistant needs memory at all, Vectorize's explainer on [agent memory](https://vectorize.io/what-is-agent-memory) is a good place to start.
