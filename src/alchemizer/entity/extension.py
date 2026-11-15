"""
Implements extensions, which add state to an entity beyond its model's fields.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .model import ModelContainer

if TYPE_CHECKING:
    from .entity import BaseEntity

__all__ = [
    "Extension",
    "StatefulExtension",
]


class Extension(ABC, ModelContainer):
    """
    Enables an entity to be extended with additional state besides the entity's model,
    e.g. a collection of related entities.
    """

    _entity: BaseEntity

    def __init__(self, entity: BaseEntity):
        super().__init__(entity._model)
        self._entity = entity

    @abstractmethod
    def _setattr(self, obj: Any):
        """
        Invoked by the entity to set this extension's data, e.g. upon assignment of the
        attribute by which the entity exposes this extension.
        """
        ...


class StatefulExtension[BackingT](Extension):
    """
    Extension which has state derived from the entity's model.

    This state is populated during model setup and cleared during teardown.
    """

    def __init__(self, entity: BaseEntity):
        super().__init__(entity)
        entity._model.register_extension(self)

    @property
    def _is_changed(self) -> bool:
        """
        Whether there is state needing to be flushed.

        Stateful extensions may or may not have state needing to be flushed; defaults to
        not requiring flush.
        """
        return False

    @abstractmethod
    def _setup(self, model: BackingT | None):
        """
        Invoked after model is initially set up, or if it's refreshed.

        :param model: Model received from backing store, or `None` if entity is being \
            created
        """
        ...

    @abstractmethod
    def _teardown(self):
        """
        Reset current state.
        """
        ...

    def _flush(self) -> BackingT | None:
        """
        Commit changes to backing store, returning the latest model if applicable.

        Only invoked if `_is_changed` is `True`.
        """
        return None
