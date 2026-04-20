import anthropic
import config


class ClaudeClient:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._model = config.MODEL

    def respond(
        self,
        system: str,
        history: list[dict],
        user_msg: str,
        max_tokens: int = 300,
    ) -> str:
        history.append({"role": "user", "content": user_msg})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=history,
        )

        assistant_text = next(
            (block.text for block in response.content if block.type == "text"),
            "",
        )
        history.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    def quick(self, system: str, prompt: str, max_tokens: int = 200) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(
            (block.text for block in response.content if block.type == "text"),
            "",
        )
