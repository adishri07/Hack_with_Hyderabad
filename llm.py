"""One small wrapper around the chat model, so the rest of the code doesn't care
whether we use Azure OpenAI, Groq or OpenAI."""
import json
import re

import config


class LLMError(Exception):
    """Raised when the model can't be reached or returns something unusable."""


class LLM:
    def __init__(self):
        self.provider = config.LLM_PROVIDER
        self.client = None
        self.model = None

        if self.provider == "azure":
            from openai import AzureOpenAI

            self.client = AzureOpenAI(
                api_key=config.AZURE_OPENAI_API_KEY,
                azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                api_version=config.AZURE_OPENAI_API_VERSION,
            )
            self.model = config.AZURE_OPENAI_DEPLOYMENT
        elif self.provider == "groq":
            from openai import OpenAI

            self.client = OpenAI(api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
            self.model = config.GROQ_MODEL
        elif self.provider == "openai":
            from openai import OpenAI

            self.client = OpenAI(api_key=config.OPENAI_API_KEY)
            self.model = config.OPENAI_MODEL
        else:
            self.provider = "mock"

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def label(self) -> str:
        return "Mock (no AI key)" if self.is_mock else f"{self.provider.title()}: {self.model}"

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        if self.is_mock:
            raise LLMError("Mock provider has no model. Callers should use their fallback.")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # network, auth, rate limits
            raise LLMError(f"Model call failed: {exc}") from exc
        text = response.choices[0].message.content or ""
        # Some models (for example qwen3) include their reasoning in <think> tags
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for JSON and parse it, tolerating code fences or extra text around it."""
        text = self.chat(system, user)
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise LLMError("Model did not return JSON.")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON: {exc}") from exc


llm = LLM()
