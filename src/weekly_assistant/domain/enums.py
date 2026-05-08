from enum import Enum


class Lifecycle(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class MovementType(str, Enum):
    REAL_RESULT = "real_result"
    NO_MOVEMENT = "no_movement"
    UNCLEAR = "unclear"


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    FINAL = "final"


class YesNo(str, Enum):
    YES = "yes"
    NO = "no"
