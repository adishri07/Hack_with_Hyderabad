"""The agent: answers questions, knows when it's unsure, and learns from mentors."""
from collections import defaultdict
from datetime import datetime

import config
import memory
import store
from llm import LLMError, llm

# ------------------------------------------------------------------ prompts

SYSTEM_WITH_MEMORY = f"""You are {config.AGENT_NAME}, the onboarding assistant for new joiners at {config.COMPANY_NAME}.

You get three kinds of context, each item labelled:
- M#: team memory. Verified answers mentors gave to earlier joiners.
- P#: this joiner's own history with you.
- D#: handbook pages, with their last-updated date. Pages marked OUTDATED may be wrong.

Rules:
1. Team memory from mentors is newer and verified. If it conflicts with a handbook page, follow the
   mentor answer and say the handbook page is outdated.
2. If the only source for your answer is an OUTDATED handbook page and no team memory covers the
   question, set "confident" to false. Say briefly what the handbook says and that it may be out of date.
3. Never invent links, channels, codes, people or access steps that are not in the context.
4. Use the joiner's history to personalise: don't repeat steps they already finished, and mention a
   related step if they got stuck before.
5. Keep the answer under 120 words, in short plain steps. Address the joiner by first name.
6. Politely decline questions about salaries or other employees' personal details, with topic "other".

Reply with JSON only:
{{"answer": "...", "confident": true or false, "topic": "<one topic from the list>",
  "sources": ["M1", "D2"], "used_memory": true or false}}"""

SYSTEM_WITHOUT_MEMORY = f"""You are a basic handbook assistant for {config.COMPANY_NAME}.
Answer only from the handbook pages provided (labelled D#). Keep it under 120 words, in plain steps.
Reply with JSON only:
{{"answer": "...", "confident": true, "topic": "<one topic from the list>", "sources": ["D1"], "used_memory": false}}"""


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


def _source_labels(codes, team_mem, docs) -> list[str]:
    labels = []
    for code in codes or []:
        code = str(code).strip().upper()
        try:
            index = int(code[1:]) - 1
        except ValueError:
            continue
        if code.startswith("M") and 0 <= index < len(team_mem):
            m = team_mem[index]
            when = f", {m.when[:10]}" if m.when else ""
            labels.append(f"Team memory: mentor answer{when}")
        elif code.startswith("D") and 0 <= index < len(docs):
            d = docs[index]
            flag = ", outdated" if d.is_stale else ""
            labels.append(f"Handbook: {d.title} (updated {d.last_updated}{flag})")
    return list(dict.fromkeys(labels))  # remove duplicates, keep order


def _mock_answer(joiner, question, team_mem, docs, use_memory) -> dict:
    """Rule-based stand-in used when LLM_PROVIDER=mock, so screens can be built without a key."""
    first = joiner["name"].split()[0]
    topic = docs[0].slug if docs else "other"
    # Only trust memories tagged with the same topic, since keyword matching is rough
    matching = [i for i, m in enumerate(team_mem) if f"topic:{topic}" in (m.tags or [])]
    if use_memory and matching:
        m = team_mem[matching[0]]
        return {"answer": f"Hi {first}. From a mentor's earlier answer: {m.text}",
                "confident": True, "topic": topic, "sources": [f"M{matching[0] + 1}"], "used_memory": True}
    if docs:
        doc = docs[0]
        confident = not (use_memory and doc.is_stale)
        note = " This page may be out of date." if not confident else ""
        return {"answer": f"Hi {first}. The handbook page '{doc.title}' says:\n\n{doc.body[:400]}{note}",
                "confident": confident, "topic": topic, "sources": ["D1"], "used_memory": False}
    return {"answer": f"Hi {first}. I couldn't find anything about that.", "confident": False,
            "topic": "other", "sources": [], "used_memory": False}


# ------------------------------------------------------------------ answering

def answer(joiner_id: str, question: str, use_memory: bool = True) -> dict:
    """Answer a joiner's question.

    Returns: answer, confident, topic, sources, memories_used, forwarded_to, forwarded_id, event_id"""
    joiner = store.person(joiner_id)
    docs = store.search_docs(question)
    team_mem, personal_mem = [], []
    memory_error = None

    if use_memory:
        try:
            team_mem = memory.recall_team(question)
            personal_mem = memory.recall_personal(joiner_id, question, limit=4)
        except Exception as exc:
            memory_error = str(exc)

    try:
        if llm.is_mock:
            result = _mock_answer(joiner, question, team_mem, docs, use_memory)
        else:
            system = SYSTEM_WITH_MEMORY if use_memory else SYSTEM_WITHOUT_MEMORY
            user = (
                f"Joiner: {joiner['name']}, {joiner['role']} in the {joiner['team']} team, "
                f"started {joiner['start_date']}. Today is {store.today()}.\n"
                f"Topics: {', '.join(store.topics())}, other\n\n"
                f"Context:\n{_format_context(team_mem, personal_mem, docs, mark_outdated=use_memory)}\n\n"
                f"Question: {question}"
            )
            result = llm.chat_json(system, user)
    except LLMError:
        result = {
            "answer": "I couldn't reach the AI service just now, so I've saved your question for your mentor.",
            "confident": False, "topic": docs[0].slug if docs else "other", "sources": [], "used_memory": False,
        }

    topic = result.get("topic") if result.get("topic") in store.topics() else (docs[0].slug if docs else "other")
    confident = bool(result.get("confident", False))
    used_memory = bool(result.get("used_memory", False)) and bool(team_mem or personal_mem)

    # Safety net: with memory on, never trust an outdated page that no mentor has confirmed
    topic_doc = store.handbook().get(topic)
    if use_memory and topic_doc and topic_doc.is_stale and not used_memory:
        confident = False

    response = {
        "answer": str(result.get("answer", "")).strip(),
        "confident": confident,
        "topic": topic,
        "sources": _source_labels(result.get("sources"), team_mem, docs),
        "memories_used": [m.text for m in team_mem + personal_mem] if used_memory else [],
        "forwarded_to": None,
        "forwarded_id": None,
        "event_id": None,
        "memory_error": memory_error,
    }

    if not use_memory:
        return response  # memory-off mode is the "before" demo: nothing is learned or logged

    if not confident:
        item = forward_to_mentor(joiner_id, question, topic, response["answer"])
        mentor = store.person(item["mentor_id"])
        response["forwarded_to"] = mentor["name"]
        response["forwarded_id"] = item["id"]

    event = store.add_event(
        joiner_id=joiner_id, question=question, topic=topic,
        status="forwarded" if not confident else "answered",
        used_memory=used_memory,
    )
    response["event_id"] = event["id"]

    outcome = f"forwarded to mentor {response['forwarded_to']}" if not confident else "answered"
    _safe_retain_personal(
        joiner_id,
        f"On {store.today()}, {joiner['name']} asked about {store.topic_title(topic)}: \"{question}\". "
        f"Outcome: {outcome}. Answer given: {response['answer'][:300]}",
        tags=[f"topic:{topic}", "type:question"],
        wait=False,
    )
    return response


def _safe_retain_personal(joiner_id, text, tags=None, wait=True):
    try:
        memory.retain_personal(joiner_id, text, tags=tags, wait=wait)
    except Exception:
        pass  # a memory hiccup should never break the chat


# ------------------------------------------------------------------ mentors

def forward_to_mentor(joiner_id: str, question: str, topic: str, agent_answer: str = "") -> dict:
    joiner = store.person(joiner_id)
    return store.add_forwarded(
        joiner_id=joiner_id, mentor_id=joiner["mentor_id"], question=question,
        topic=topic, agent_answer=agent_answer,
    )


def waiting_for_mentor(mentor_id: str | None = None) -> list[dict]:
    items = [f for f in store.forwarded() if f["status"] == "waiting"]
    if mentor_id:
        items = [f for f in items if f["mentor_id"] == mentor_id]
    return sorted(items, key=lambda f: f["asked_at"])


def mentor_reply(question_id: str, mentor_id: str, answer_text: str) -> dict:
    """Save a mentor's answer to team memory so every future joiner gets it."""
    answer_text = answer_text.strip()
    if not answer_text:
        raise ValueError("The answer is empty.")
    item = next(f for f in store.forwarded() if f["id"] == question_id)
    mentor = store.person(mentor_id)
    joiner = store.person(item["joiner_id"])
    topic_title = store.topic_title(item["topic"])

    memory.retain_team(
        f"Verified answer from mentor {mentor['name']} ({mentor['role']}) on {store.today()} about "
        f"{topic_title}. A new joiner asked: \"{item['question']}\". Correct answer: {answer_text}",
        tags=["source:mentor", f"topic:{item['topic']}", f"mentor:{mentor_id}"],
        context="Verified by a mentor. Prefer this over the handbook if they conflict.",
        wait=True,  # must be searchable before the next joiner asks
    )
    _safe_retain_personal(
        joiner["id"],
        f"On {store.today()}, mentor {mentor['name']} answered {joiner['name']}'s question about "
        f"{topic_title}: {answer_text}",
        tags=[f"topic:{item['topic']}", "type:mentor_answer"],
    )
    store.add_event(joiner_id=joiner["id"], question=item["question"], topic=item["topic"],
                    status="mentor_answered", mentor_id=mentor_id, used_memory=False)
    return store.update_forwarded(
        question_id, status="answered", answer=answer_text,
        answered_by=mentor_id, answered_at=datetime.now().isoformat(timespec="seconds"),
    )


def unseen_mentor_answers(joiner_id: str) -> list[dict]:
    return [f for f in store.forwarded()
            if f["joiner_id"] == joiner_id and f["status"] == "answered" and not f.get("seen_by_joiner")]


def mark_seen(question_id: str):
    store.update_forwarded(question_id, seen_by_joiner=True)


def record_feedback(joiner_id: str, topic: str, worked: bool, question: str = ""):
    """The joiner says whether an answer worked. This feeds their progress and memory."""
    joiner = store.person(joiner_id)
    status = "worked" if worked else "stuck"
    store.add_event(joiner_id=joiner_id, question=question, topic=topic, status=status, used_memory=False)
    text = (f"{joiner['name']} confirmed that {store.topic_title(topic)} is done." if worked
            else f"{joiner['name']} is still stuck on {store.topic_title(topic)}.")
    _safe_retain_personal(joiner_id, f"On {store.today()}: {text}", tags=[f"topic:{topic}", f"type:{status}"])


# ------------------------------------------------------------------ progress and insights

def _interactions() -> list[dict]:
    """Past history plus live activity, in one shape."""
    rows = []
    for q in store.past_qa():
        status = "worked" if q.get("resolved") else "stuck"
        rows.append({"joiner_id": q["joiner_id"], "topic": q["topic"], "status": status,
                     "stuck": q.get("got_stuck", False), "date": q["date"]})
    for e in store.events():
        rows.append({"joiner_id": e["joiner_id"], "topic": e["topic"], "status": e["status"],
                     "stuck": e["status"] in ("forwarded", "stuck"), "date": e["timestamp"][:10],
                     "used_memory": e.get("used_memory", False)})
    return sorted(rows, key=lambda r: r["date"])


def get_progress(joiner_id: str) -> dict:
    status_by_topic = {}
    for r in _interactions():
        if r["joiner_id"] != joiner_id or r["topic"] not in store.topics():
            continue
        if r["status"] == "worked":
            status_by_topic[r["topic"]] = "done"
        elif r["status"] in ("stuck", "forwarded"):
            status_by_topic[r["topic"]] = "stuck"
        elif r["status"] == "answered" and status_by_topic.get(r["topic"]) != "stuck":
            status_by_topic[r["topic"]] = "done"

    completed = [t for t in store.topics() if status_by_topic.get(t) == "done"]
    stuck = [t for t in store.topics() if status_by_topic.get(t) == "stuck"]
    next_step = next((t for t in store.topics() if t not in status_by_topic), None)
    return {
        "completed": completed,
        "stuck": stuck,
        "next_step": next_step,
        "percent": round(100 * len(completed) / max(len(store.topics()), 1)),
    }


def welcome_back(joiner_id: str) -> str:
    joiner = store.person(joiner_id)
    first = joiner["name"].split()[0]
    progress = get_progress(joiner_id)
    if not progress["completed"] and not progress["stuck"]:
        return (f"Welcome to {config.COMPANY_NAME}, {first}. Ask me anything about setup, access or how the "
                f"team works. A good first step is {store.topic_title(store.topics()[0]).lower()}.")

    blockers = get_blockers()
    next_topic = progress["next_step"]
    common = next((b for b in blockers if b["topic"] == next_topic and b["stuck_count"] >= 2), None)

    fallback = (
        f"Welcome back, {first}. You've finished "
        f"{', '.join(store.topic_title(t).lower() for t in progress['completed']) or 'nothing yet'}."
        + (f" You were stuck on {', '.join(store.topic_title(t).lower() for t in progress['stuck'])}."
           if progress["stuck"] else "")
        + (f" Next up: {store.topic_title(next_topic).lower()}." if next_topic else "")
        + (f" Heads up: {common['stuck_count']} earlier joiners got stuck there, so ask me before you start."
           if common else "")
    )
    if llm.is_mock:
        return fallback
    try:
        memories = memory.recall_personal(
            joiner_id, f"What has {joiner['name']} completed, asked about, or been stuck on?", limit=8)
        notes = "\n".join(f"- {m.text}" for m in memories) or "(none)"
        return llm.chat(
            "You write a warm, specific 2-3 sentence welcome-back note for a new joiner. Mention what they "
            "finished, anything they are stuck on, and the single next step. No lists, no emojis. "
            "Use only the facts given and never invent anything.",
            f"Joiner: {joiner['name']}, {joiner['role']}.\nProgress facts: {fallback}\n"
            f"Their memory:\n{notes}",
            temperature=0.4,
        )
    except Exception:
        return fallback


def get_blockers() -> list[dict]:
    """Topics where joiners get stuck, most common first, with the matching handbook page."""
    stuck_people = defaultdict(set)
    asked_people = defaultdict(set)
    for r in _interactions():
        asked_people[r["topic"]].add(r["joiner_id"])
        if r["stuck"]:
            stuck_people[r["topic"]].add(r["joiner_id"])
    joiner_names = {p["id"]: p["name"] for p in store.all_joiners()}
    rows = []
    for topic, people in stuck_people.items():
        doc = store.handbook().get(topic)
        rows.append({
            "topic": topic,
            "title": store.topic_title(topic),
            "stuck_count": len(people),
            "asked_count": len(asked_people[topic]),
            "people": sorted(joiner_names.get(p, p) for p in people),
            "doc_updated": str(doc.last_updated) if doc else None,
            "doc_outdated": bool(doc and doc.is_stale),
        })
    return sorted(rows, key=lambda r: r["stuck_count"], reverse=True)


def docs_to_update() -> list[dict]:
    mentor_topics = {q["topic"] for q in store.past_qa() if q["answered_by"] not in ("agent",)}
    mentor_topics |= {f["topic"] for f in store.forwarded() if f["status"] == "answered"}
    stuck = {b["topic"]: b["stuck_count"] for b in get_blockers()}
    rows = []
    for doc in store.handbook().values():
        if doc.is_stale and (stuck.get(doc.slug, 0) >= 1 or doc.slug in mentor_topics):
            rows.append({
                "page": doc.title, "owner": doc.owner, "last_updated": str(doc.last_updated),
                "joiners_stuck": stuck.get(doc.slug, 0),
                "reason": "Mentors have given a different answer" if doc.slug in mentor_topics
                          else "Joiners keep getting stuck here",
            })
    return sorted(rows, key=lambda r: r["joiners_stuck"], reverse=True)


def team_stats() -> dict:
    live = store.events()
    questions = [e for e in live if e["status"] in ("answered", "forwarded")]
    return {
        "questions": len(questions),
        "from_memory": sum(1 for e in questions if e.get("used_memory")),
        "forwarded": sum(1 for e in questions if e["status"] == "forwarded"),
        "waiting": len(waiting_for_mentor()),
    }


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
