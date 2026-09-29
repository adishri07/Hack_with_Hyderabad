"""Settings loaded from the .env file. Every other module imports from here."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
HANDBOOK_DIR = DATA_DIR / "handbook"

COMPANY_NAME = os.getenv("COMPANY_NAME", "Nimbus Cloud")
AGENT_NAME = os.getenv("AGENT_NAME", "Ramp")

# LLM: "azure", "groq", "openai" or "mock" (no key needed, for building screens)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").strip().lower()
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Memory: Hindsight Cloud when a key is present, otherwise a local file (development only)
HINDSIGHT_API_KEY = os.getenv("HINDSIGHT_API_KEY", "")
HINDSIGHT_BASE_URL = os.getenv("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io")
BANK_PREFIX = os.getenv("BANK_PREFIX", "nimbus")

# A handbook page older than this many days is treated as possibly outdated
STALE_AFTER_DAYS = int(os.getenv("STALE_AFTER_DAYS", "365"))
