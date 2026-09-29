"""Streamlit app: joiner chat, mentor answers, manager view.

Run with:  streamlit run app.py"""
import streamlit as st

import agent
import config
import memory
import store
from llm import llm

st.set_page_config(page_title=f"{config.AGENT_NAME}: onboarding that remembers", page_icon="🧭", layout="wide")

st.markdown("""
<style>
  .ramp-title {font-size: 1.9rem; font-weight: 800; margin-bottom: 0;}
  .ramp-sub {color: #5b6b80; margin-top: 0.1rem;}
  .src {font-size: 0.8rem; color: #5b6b80;}
</style>
""", unsafe_allow_html=True)

if "chats" not in st.session_state:
    st.session_state.chats = {}      # joiner_id -> list of messages
if "welcome" not in st.session_state:
    st.session_state.welcome = {}    # joiner_id -> welcome text


# ------------------------------------------------------------------ sidebar

users = store.current_joiners() + store.mentors() + [p for p in store.people() if p["type"] == "manager"]
labels = {p["id"]: f"{p['name']} ({p['type'] if p['type'] != 'joiner' else p['role']})" for p in users}

with st.sidebar:
    st.markdown(f"### {config.AGENT_NAME}")
    st.caption(f"Onboarding assistant for {config.COMPANY_NAME}")
    user_id = st.selectbox("Who are you?", options=list(labels), format_func=labels.get)
    user = store.person(user_id)

    use_memory = True
    if user["type"] == "joiner":
        use_memory = st.toggle("Memory on", value=True,
                               help="Turn off to see how a plain handbook bot answers, with no memory.")
        progress = agent.get_progress(user_id)
        st.divider()
        st.markdown("**My onboarding progress**")
        st.progress(progress["percent"] / 100, text=f"{progress['percent']}% done")
        if progress["completed"]:
            st.markdown("Done: " + ", ".join(store.topic_title(t) for t in progress["completed"]))
        if progress["stuck"]:
            st.markdown("Stuck on: " + ", ".join(store.topic_title(t) for t in progress["stuck"]))
        if progress["next_step"]:
            st.markdown(f"Next step: **{store.topic_title(progress['next_step'])}**")

    st.divider()
    st.caption(f"Memory: {memory.backend_name()}")
    st.caption(f"Model: {llm.label()}")
    if not memory.uses_hindsight():
        st.warning("Add HINDSIGHT_API_KEY to .env before the demo. Local memory is for development only.")
    with st.expander("Demo controls"):
        if st.button("Clear live activity", help="Clears chats, forwarded questions and events. "
                                                   "Hindsight memory is not changed."):
            store.reset_runtime_files()
            st.session_state.chats = {}
            st.session_state.welcome = {}
            st.rerun()


# ------------------------------------------------------------------ joiner view

def joiner_view(joiner: dict):
    jid = joiner["id"]
    first = joiner["name"].split()[0]
    st.markdown(f'<p class="ramp-title">Hi {first}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="ramp-sub">{joiner["role"]}, {joiner["team"]} team. '
                f'Mentor: {store.person(joiner["mentor_id"])["name"]}</p>', unsafe_allow_html=True)

    if not use_memory:
        st.warning("Memory is off. This is how a plain handbook bot behaves: no team knowledge, "
                   "no memory of you, nothing learned.")
    else:
        if jid not in st.session_state.welcome:
            with st.spinner("Checking where you left off..."):
                st.session_state.welcome[jid] = agent.welcome_back(jid)
        st.info(st.session_state.welcome[jid])

        for item in agent.unseen_mentor_answers(jid):
            mentor = store.person(item["answered_by"])
            with st.container(border=True):
                st.markdown(f"**{mentor['name']} answered your question** about "
                            f"{store.topic_title(item['topic']).lower()}")
                st.markdown(f"> {item['question']}")
                st.write(item["answer"])
                if st.button("Got it", key=f"seen-{item['id']}"):
                    agent.mark_seen(item["id"])
                    agent.record_feedback(jid, item["topic"], worked=True, question=item["question"])
                    st.session_state.welcome.pop(jid, None)
                    st.rerun()

    chat = st.session_state.chats.setdefault(jid, [])
    for i, msg in enumerate(chat):
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            if msg["role"] != "assistant":
                continue
            meta = msg.get("meta", {})
            if meta.get("forwarded_to"):
                st.warning(f"I'm not sure this is still correct, so I've asked your mentor "
                           f"{meta['forwarded_to']}. You'll see the answer here.")
            if meta.get("sources"):
                st.markdown(f'<p class="src">Sources: {" | ".join(meta["sources"])}</p>', unsafe_allow_html=True)
            if meta.get("memories_used"):
                with st.expander(f"Memories used ({len(meta['memories_used'])})"):
                    for m in meta["memories_used"]:
                        st.markdown(f"- {m}")
            if meta.get("memory_error"):
                st.caption(f"Memory was unavailable for this answer: {meta['memory_error'][:120]}")
            if meta.get("event_id") and not meta.get("forwarded_to") and not msg.get("feedback"):
                c1, c2, _ = st.columns([1, 1, 4])
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

    if not chat:
        st.caption("Try: \"How do I connect to the VPN?\" or \"How do I get access to the staging database?\"")

    question = st.chat_input(f"Ask {config.AGENT_NAME} anything about getting started")
    if question:
        chat.append({"role": "user", "content": question})
        with st.spinner("Checking the team's memory..." if use_memory else "Searching the handbook..."):
            result = agent.answer(jid, question, use_memory=use_memory)
        result["question"] = question
        chat.append({"role": "assistant", "content": result["answer"], "meta": result})
        st.rerun()


# ------------------------------------------------------------------ mentor view

def mentor_view(mentor: dict):
    st.markdown(f'<p class="ramp-title">Questions for {mentor["name"].split()[0]}</p>', unsafe_allow_html=True)
    st.markdown('<p class="ramp-sub">Answer once. Every future joiner gets your answer automatically.</p>',
                unsafe_allow_html=True)

    show_all = st.toggle("Show questions for all mentors", value=False)
    items = agent.waiting_for_mentor(None if show_all else mentor["id"])
    if not items:
        st.success("No questions waiting. New joiners are getting answers from team memory.")

    for item in items:
        joiner = store.person(item["joiner_id"])
        with st.container(border=True):
            st.markdown(f"**{joiner['name']}** ({joiner['role']}) asked about "
                        f"**{store.topic_title(item['topic']).lower()}** on {item['asked_at'][:10]}")
            st.markdown(f"> {item['question']}")
            if item.get("agent_answer"):
                with st.expander("What the agent said"):
                    st.write(item["agent_answer"])
            reply = st.text_area("Your answer", key=f"reply-{item['id']}",
                                 placeholder="Write the answer you'd want every new joiner to get.")
            if st.button("Send answer", key=f"send-{item['id']}", type="primary"):
                if not reply.strip():
                    st.error("Write an answer before sending.")
                else:
                    with st.spinner("Saving to team memory..."):
                        agent.mentor_reply(item["id"], mentor["id"], reply)
                    st.toast("Answer sent and saved to team memory")
                    st.rerun()

    answered = [f for f in store.forwarded() if f["status"] == "answered" and f.get("answered_by") == mentor["id"]]
    if answered:
        st.markdown("#### Answered earlier")
        for f in reversed(answered):
            st.markdown(f"- **{store.topic_title(f['topic'])}**: {f['answer']}")


# ------------------------------------------------------------------ manager view

def manager_view(manager: dict):
    st.markdown('<p class="ramp-title">Onboarding overview</p>', unsafe_allow_html=True)
    st.markdown('<p class="ramp-sub">Where new joiners get stuck, and what to fix.</p>', unsafe_allow_html=True)

    stats = agent.team_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions asked", stats["questions"])
    c2.metric("Answered from team memory", stats["from_memory"],
              help="Each one is a question no mentor had to answer again.")
    c3.metric("Sent to mentors", stats["forwarded"])
    c4.metric("Waiting now", stats["waiting"])

    st.markdown("#### Where joiners get stuck")
    blockers = agent.get_blockers()
    if blockers:
        st.dataframe(
            [{"Topic": b["title"], "Joiners stuck": b["stuck_count"], "Joiners who asked": b["asked_count"],
              "Who": ", ".join(b["people"]), "Handbook outdated": "Yes" if b["doc_outdated"] else "No"}
             for b in blockers],
            hide_index=True,
        )
    else:
        st.caption("No blockers recorded yet.")

    st.markdown("#### Handbook pages to update")
    docs = agent.docs_to_update()
    if docs:
        st.dataframe(
            [{"Page": d["page"], "Owner": d["owner"], "Last updated": d["last_updated"],
              "Joiners stuck": d["joiners_stuck"], "Why": d["reason"]} for d in docs],
            hide_index=True,
        )
    else:
        st.caption("All handbook pages look current.")

    st.markdown("#### Current joiners")
    rows = []
    for j in store.current_joiners():
        p = agent.get_progress(j["id"])
        rows.append({"Name": j["name"], "Role": j["role"], "Started": j["start_date"],
                     "Progress": f"{p['percent']}%",
                     "Stuck on": ", ".join(store.topic_title(t) for t in p["stuck"]) or "Nothing",
                     "Next step": store.topic_title(p["next_step"]) if p["next_step"] else "All done"})
    st.dataframe(rows, hide_index=True)

    st.markdown("#### Ask the team memory")
    if st.button("Summarise what joiners struggle with"):
        with st.spinner("Reflecting on everything mentors have taught the agent..."):
            st.markdown(agent.team_insight())


# ------------------------------------------------------------------ route

if user["type"] == "joiner":
    joiner_view(user)
elif user["type"] == "mentor":
    mentor_view(user)
else:
    manager_view(user)
