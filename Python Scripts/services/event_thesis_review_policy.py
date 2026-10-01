"""Event-triggered thesis reviews are off or shadow; there is deliberately no live mode yet.

Exit authority from an event review is a separate rollout decision. Until then the arbiter's
action is recorded as would-be only, whatever THESIS_VALIDATOR_MODE says.
"""
from dataclasses import asdict, dataclass
import os


POLICY_VERSION = 'event-thesis-review-v1'
MODES = {'off', 'shadow'}


@dataclass(frozen=True)
class EventThesisReviewPolicy:
    mode: str = 'shadow'
    # A newer revision inside this window replaces the older one, so bursts are reviewed once.
    debounce_seconds: int = 900
    daily_reviews_per_position: int = 3
    # Reviews run inside the position-management cycle; one item bounds its added latency.
    items_per_cycle: int = 1
    policy_version: str = POLICY_VERSION

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('EVENT_THESIS_REVIEW_MODE must be off or shadow')
        for value in (self.debounce_seconds, self.daily_reviews_per_position, self.items_per_cycle):
            if type(value) is not int or value < 0:
                raise ValueError('Event thesis review limits must be non-negative integers')
        if self.items_per_cycle < 1:
            raise ValueError('Event thesis reviews need at least one item per cycle')

    def manifest(self):
        return asdict(self)

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('EVENT_THESIS_REVIEW_MODE', 'shadow').strip().lower(),
                   debounce_seconds=int(os.getenv('EVENT_THESIS_REVIEW_DEBOUNCE_SECONDS', '900')),
                   daily_reviews_per_position=int(os.getenv('EVENT_THESIS_REVIEW_DAILY_LIMIT', '3')))
