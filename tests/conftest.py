from datetime import datetime

import pytest

from kya.risk import IST
from kya.world import World

AFTERNOON = int(datetime(2026, 9, 23, 14, 0, tzinfo=IST).timestamp())


class Clock:
    def __init__(self, t: int = AFTERNOON):
        self.t = t

    def __call__(self) -> int:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def world(clock):
    return World(clock=clock)


def outcome(out: dict) -> tuple[str, str | None]:
    return out["decision"]["outcome"], out["decision"]["layer"]
