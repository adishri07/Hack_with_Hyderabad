"""Load past onboarding history into memory. Run once before the demo:

    python seed_memory.py

To start completely fresh on Hindsight, change BANK_PREFIX in .env (for example
nimbus2) and run this again."""
from datetime import datetime

import memory
import store


def main():
    history = store.past_qa()
    current = {p["id"] for p in store.current_joiners()}
    team_count = personal_count = 0

    print(f"Memory backend: {memory.backend_name()}")
    if not memory.uses_hindsight():
        memory.backend.clear()
        print("Using local memory. Add HINDSIGHT_API_KEY to .env to seed Hindsight instead.")

    for q in history:
        when = datetime.fromisoformat(q["date"] + "T10:00:00")
        joiner = store.person(q["joiner_id"])
        topic_title = store.topic_title(q["topic"])

        if q["answered_by"] != "agent":
            mentor = store.person(q["answered_by"])
            memory.retain_team(
                f"Verified answer from mentor {mentor['name']} ({mentor['role']}) on {q['date']} about "
                f"{topic_title}. A new joiner asked: \"{q['question']}\". Correct answer: {q['answer']}",
                tags=["source:mentor", f"topic:{q['topic']}", f"mentor:{mentor['id']}"],
                context="Verified by a mentor. Prefer this over the handbook if they conflict.",
                when=when,
            )
            team_count += 1

        if q["joiner_id"] in current:
            outcome = "It worked." if q["resolved"] else "Still stuck on it."
            memory.retain_personal(
                q["joiner_id"],
                f"On {q['date']}, {joiner['name']} asked about {topic_title}: \"{q['question']}\". "
                f"Answer: {q['answer']} {outcome}",
                tags=[f"topic:{q['topic']}", "type:question"],
                when=when,
            )
            personal_count += 1
        print(".", end="", flush=True)

    store.reset_runtime_files()
    print(f"\nDone. Team memories: {team_count}. Personal memories: {personal_count}.")


if __name__ == "__main__":
    main()
