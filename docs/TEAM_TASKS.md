# Who owns what

| Person | Role | Files |
| --- | --- | --- |
| 1 | Agent logic (lead) | `agent.py`, `llm.py`, `.env` setup |
| 2 | Hindsight memory | `memory.py`, `seed_memory.py`, `check_setup.py` |
| 3 | Screens | `app.py` |
| 4 | Data | `data/handbook/`, `data/people.json`, `data/past_qa.json` |
| 5 | Pitch and slides | `docs/` slides, architecture diagram |
| 6 | Video, README, submission | `README.md`, `docs/DEMO_SCRIPT.md`, YouTube video |

The code already runs end to end. Each person's job is to understand their file, test it, improve it, and be able to explain it to the judges.

## First steps for everyone
1. Clone the repo, create a virtual environment, `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`. Leave it on `LLM_PROVIDER=mock` if you don't have keys yet.
3. `python seed_memory.py`, then `streamlit run app.py`, and click through the demo script once.

## Rules
- Never commit `.env` or any key.
- Change only your own files. If you need a change in someone else's file, ask them.
- Push small commits with clear messages.
