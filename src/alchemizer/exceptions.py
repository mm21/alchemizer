"""
Exception definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .entity.entity import BaseEntity

__all__ = [
    "ReadOnlyError",
    "ValidationError",
    "EntityValidationError",
    "assert_valid",
]


class ReadOnlyError(Exception):
    """
    Raised when user attempts to write a field which is read-only.
    """

    def __init__(self, field: str, entity: BaseEntity):
        super().__init__(f"Attempt to set read-only field {field} of {entity}")


class ValidationError(Exception):
    """
    Raised upon flush when changes in unit of work are invalid or incompatible.
    """

    errors: list[str]
    """
    Description of each error found, one per invalid entity.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        errors_str = "\n".join(errors)
        super().__init__(f"Errors found during validation: {errors_str}")


class EntityValidationError(Exception):
    """
    Raised by an entity's `_flush_check()` if it's not in a valid state to be flushed.

    Not seen by the user: errors for all entities being flushed are aggregated and
    raised as a single {obj}`ValidationError`.
    """


def assert_valid(cond: bool, *args: Any):
    """
    Helper to raise {obj}`EntityValidationError` if the condition is not `True`.
    """
    if cond is not True:
        raise EntityValidationError(*args)
