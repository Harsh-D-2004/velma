class TokenBudget:
    """Per-job cap. `used` is ctx.tokens, so it survives a resume."""

    def __init__(self, limit: int):
        self.limit = limit

    def over(self, used: int) -> bool:
        return used >= self.limit

    def warn(self, used: int) -> bool:
        return used >= 0.85 * self.limit
