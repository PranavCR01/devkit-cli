try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENCODER.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return int(len(text.split()) * 1.3)

TOKEN_CAP_DEFAULT = 8000


class ContextAssembler:
    """Assembles selected context items into an injectable string.

    Token budget: drop whole items when over cap, never truncate mid-item.
    """

    def __init__(self, token_cap: int = TOKEN_CAP_DEFAULT):
        self.token_cap = token_cap
        self.items: list[dict] = []
        self.current_tokens: int = 0
        self.dropped: list[str] = []

    def add_item(self, item: dict, content: str) -> bool:
        """Try to add an item. Returns False if it would exceed budget."""
        item_tokens = count_tokens(content)
        if self.current_tokens + item_tokens > self.token_cap:
            self.dropped.append(item.get("name", item.get("id", "unknown")))
            return False
        self.items.append({"meta": item, "content": content})
        self.current_tokens += item_tokens
        return True

    def render(self) -> str:
        if not self.items:
            return ""

        parts = ["<devkit-context>"]
        for item in self.items:
            meta = item["meta"]
            parts.append(
                f'\n[{meta["type"].upper()}] {meta["project"]} / {meta["name"]}'
            )
            parts.append(item["content"])

        if self.dropped:
            parts.append(
                f'\n[OMITTED] {len(self.dropped)} item(s) excluded due to token budget: '
                + ", ".join(self.dropped)
            )

        parts.append("\n</devkit-context>")
        parts.append("\nApply the above DevKit context silently to this session.")
        return "\n".join(parts)

    def remaining_tokens(self) -> int:
        return self.token_cap - self.current_tokens

    def summary(self) -> str:
        return (
            f"{len(self.items)} item(s) assembled | "
            f"{self.current_tokens}/{self.token_cap} tokens used | "
            f"{len(self.dropped)} dropped"
        )
