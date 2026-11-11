"""Config-driven state machine for SolvA treatment stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


def _matches(patterns: list[str], value: Any) -> bool:
    text = "" if value is None else str(value).strip()
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


@dataclass(slots=True)
class StageDecision:
    stage: str
    sequence: int
    is_after_marker: bool
    is_average: bool


class StageClassifier:
    """State machine that changes stage only from the test-count column."""

    def __init__(self, config: Mapping[str, Any]):
        self.after_patterns = list(config["stage_rules"]["after_markers"])
        self.average_patterns = list(config["stage_rules"]["average_markers"])
        self.reset()

    def reset(self) -> None:
        self.sequence = 0
        self.stage = "before_solva"

    def classify(self, test_label: Any) -> StageDecision:
        is_average = _matches(self.average_patterns, test_label)
        is_after = _matches(self.after_patterns, test_label)
        if is_after:
            self.sequence += 1
            self.stage = f"after_solva_{self.sequence}"
        return StageDecision(self.stage, self.sequence, is_after, is_average)


def parse_replicate_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    match = re.match(r"^\s*(\d+)", "" if value is None else str(value))
    return int(match.group(1)) if match else None

