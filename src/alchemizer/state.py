"""
Entity state definition.
"""

from enum import Enum, auto

__all__ = [
    "State",
]


class State(Enum):
    """
    Entity state.

    Maintained automatically based on the user's updates and the entity's current state
    in the backing store. For example, state will change from `State.UPDATE` back to
    `State.CLEAN` if the user reverts changes.
    """

    CLEAN = auto()
    """
    No pending changes.
    """

    CREATE = auto()
    """
    Pending create.
    """

    UPDATE = auto()
    """
    Pending update.
    """

    DELETE = auto()
    """
    Pending delete.
    """
