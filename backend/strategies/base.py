from typing import Protocol


class Strategy(Protocol):
    def __call__(self, ticker: str, params: dict) -> dict: ...
