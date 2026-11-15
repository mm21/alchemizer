"""
Implements base class for a session: the user-facing context in which entities are
cached and their changes tracked.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from graphlib import TopologicalSorter
from logging import Logger
from typing import TYPE_CHECKING, Any, ClassVar, Generator, Iterable, Self, cast

from .cache import Cache
from .state import State

if TYPE_CHECKING:
    from .entity.entity import BaseEntity

__all__ = [
    "BaseSession",
    "SessionContainer",
]


class BaseSession[EntityT: BaseEntity]:
    """
    Base class for a session, which combines an identity map with a unit of work.

    Changes to entities are tracked as they're made by the user. The user must then
    invoke {obj}`BaseSession.flush` to commit them, or use the session as a context
    manager to flush upon exit.

    A framework should derive exactly one class directly from this class. Default
    session registration is scoped to that class, so multiple frameworks can coexist.

    Type parameters:

    - `EntityT`: The framework's base entity class
    """

    _tolerated_flush_errors: ClassVar[tuple[type[Exception], ...]] = ()
    """
    Exceptions raised by drivers which shouldn't abort a flush, e.g. due to an entity
    having been implicitly deleted from the backing store by another operation.

    A warning is logged and the entity is considered flushed.
    """

    _cache: Cache
    """
    Identity map and unit of work.
    """

    _logger: Logger
    """
    Logger to use.
    """

    __default_scope: ClassVar[type[BaseSession]]
    """
    Class which directly inherits this one, i.e. the framework's base session.
    """

    __default: ClassVar[BaseSession | None] = None
    """
    Default session; only set on the class designated by `__default_scope`.
    """

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        if BaseSession in cls.__bases__:
            cls.__default_scope = cls

    def __init__(self, *, default: bool = False, logger: Logger | None = None):
        """
        :param default: Register this as the default session; in this case, `session` \
            may be omitted from entity constructors
        :param logger: Logger to use, or `None` to use a logger named after the \
            framework's module
        """
        self._logger = logger or logging.getLogger(type(self).__module__)
        self._cache = Cache(self)

        if default:
            self._register_default()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(entities={len(self._cache.entity_map)}, "
            f"dirty={self.dirty_count}, default={self._is_default})"
        )

    def __enter__(self) -> Self:
        self._logger.debug(f"Entering context: {self}")
        return self

    def __exit__(self, exc_type, exc_val, traceback):
        if exc_type:
            self._logger.error(f"Exiting context with error: {self}")
            return

        self._logger.debug(f"Exiting context: {self}")

        # flush pending changes
        self.flush()

        # if default, deregister it
        self.deregister_default()

        # perform any cleanup needed by subclass
        self._close()

    @property
    def dirty_count(self) -> int:
        """
        Number of dirty entities.
        """
        return len(self._cache.dirty_set)

    @property
    def dirty_set(self) -> set[EntityT]:
        """
        Copy of all dirty entities.
        """
        return cast(set[EntityT], set(self._cache.dirty_set))

    @property
    def dirty_map(self) -> dict[State, set[EntityT]]:
        """
        Mapping of state to dirty entities in that state.
        """
        index: dict[State, set[EntityT]] = {
            State.CREATE: set(),
            State.UPDATE: set(),
            State.DELETE: set(),
        }

        for entity in self.dirty_set:
            index[entity._state].add(entity)

        return index

    def flush(self, entities: Iterable[EntityT] | None = None):
        """
        Commit pending changes to the backing store.

        :param entities: Entities for which to commit changes along with their \
            dependencies, or `None` to flush all dirty entities
        """
        self._cache.flush(entities)

    def deregister_default(self):
        """
        If this session was registered as default, deregister it.

        No-op otherwise.
        """
        if self._is_default:
            self.__default_scope.__default = None

    @classmethod
    def _get_default(cls) -> Self | None:
        """
        Get the default session for this framework, if any.
        """
        default = cls.__default_scope.__default
        assert default is None or isinstance(default, cls)
        return default

    @classmethod
    def _normalize(cls, session: Self | None) -> Self:
        """
        Get the default session if none provided.
        """
        if session is not None:
            return session

        default = cls._get_default()
        assert default is not None, "No session provided and no default set"
        return default

    @property
    def _is_default(self) -> bool:
        return self.__default_scope.__default is self

    def _register_default(self):
        """
        Register this session as default, ensuring there isn't one already.
        """
        default = self.__default_scope.__default
        assert (
            default is None
        ), f"Attempt to create default session {self} when default {default} already registered"
        self.__default_scope.__default = self

    def _close(self):
        """
        Invoked when exiting context without error, after flushing changes.

        Override to release any resources held by this session.
        """

    @contextmanager
    def _flush_scope(
        self,
        entities: set[EntityT],
        sorter: TopologicalSorter[EntityT],
    ) -> Generator[None, None, None]:
        """
        Context in which a flush is performed; override to customize it.

        Code before `yield` runs after `sorter` is populated with the entities to flush
        and their dependencies, but before any entity is flushed. Use it to add
        framework-specific ordering constraints via `sorter.add()` and capture any state
        which will be lost once entities are flushed. Code after `yield` runs after all
        entities are flushed and is skipped if the flush failed.

        :param entities: Dirty entities about to be flushed
        :param sorter: Sorter which will determine the order in which they're flushed
        """
        _ = entities, sorter
        yield

    def _get_summary(self, entities: Iterable[EntityT] | None = None) -> str:
        """
        Get a brief summary of how many entities of each type are in each dirty state.

        :param entities: Entities to summarize, or `None` to summarize all dirty \
            entities
        """
        entities_ = entities if entities is not None else self.dirty_set
        states = (State.CREATE, State.UPDATE, State.DELETE)
        index: dict[str, dict[State, int]] = {}

        for entity in entities_:
            if entity._is_dirty:
                counts = index.setdefault(
                    type(entity).__name__, {state: 0 for state in states}
                )
                counts[entity._state] += 1

        counts_str = ", ".join(
            f"{'/'.join(str(counts[state]) for state in states)} {name}"
            for name, counts in sorted(index.items())
        )
        return f"(create/update/delete) {counts_str or 'none'}"


class SessionContainer[SessionT: BaseSession]:
    """
    Indicates that an object is associated with a session.
    """

    _session: SessionT

    def __init__(self, session: SessionT):
        self._session = session
