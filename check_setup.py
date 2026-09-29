"""Quick check that the model and Hindsight keys work. Run:  python check_setup.py"""
import config
import memory
from llm import LLMError, llm

print(f"Model provider: {llm.label()}")
if llm.is_mock:
    print("  Mock mode. Set LLM_PROVIDER and a key in .env to use a real model.")
else:
    try:
        print("  Model says:", llm.chat("Reply in five words or fewer.", "Say hello to the team."))
    except LLMError as exc:
        print("  Model FAILED:", exc)

print(f"Memory backend: {memory.backend_name()}")
bank = f"{config.BANK_PREFIX}-setup-check"
try:
    memory.backend.retain(bank, "The staging database is requested in the infra-access channel.")
    results = memory.backend.recall(bank, "How do I get database access?")
    print("  Recall returned:", results[0].text if results else "nothing (try again in a few seconds)")
except Exception as exc:
    print("  Memory FAILED:", exc)
