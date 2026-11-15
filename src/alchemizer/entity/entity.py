"""
Implements base class for an entity, a framework-specific object which is managed by the
identity map.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Set
from graphlib import TopologicalSorter
from typing import Any, Generator, Self, Sequence, cast

from ..session import BaseSession, SessionContainer
from ..state import State
from .model import BaseEntityModel, ModelContainer

__all__ = [
    "BaseEntity",
    "normalize_entities",
]


class BaseEntity[SessionT: BaseSession, ModelT: BaseEntityModel, BackingT](
    ABC, SessionContainer[SessionT], ModelContainer
):
    """
    Base class for entities: objects uniquely identified by an id and mapped to records
    in a backing store.

    At most one object exists per entity id per session: instantiating an entity with
    the id of one which is already cached returns the cached object.

    Public members are intended for the framework's users, while those prefixed with `_`
    are intended for the framework itself.

    Type parameters:

    - `SessionT`: The framework's session class
    - `ModelT`: Subclass of {obj}`BaseEntityModel` managing this entity's fields
    - `BackingT`: Type of object received from backing store for this entity
    """

    # unique id
    _entity_id: str | None = None

    # init state
    _init_done: bool = False

    # current state
    _state: State

    def __new__(
        cls,
        # entity id, or None to create new one
        entity_id: str | None = None,
        # session, or None to use default session
        session: SessionT | None = None,
        # backing model, if already loaded
        backing_model: BackingT | None = None,
        # whether entity is being created (otherwise inferred from whether
        # entity_id provided)
        create: bool | None = None,
    ) -> Self:
        _ = backing_model, create
        session = cls._get_session_cls()._normalize(session)

        # check if this object is already cached
        if entity_id is not None and entity_id in session._cache.entity_map:
            entity = session._cache.entity_map[entity_id]

            # sanity check: cached object can be subclass or superclass
            # - enables an entity to be equivalently instantiated using a more specific
            # subclass, e.g. one which declaratively defines its fields
            assert isinstance(entity, cls) or issubclass(cls, type(entity))

            if cls is not type(entity) and issubclass(cls, type(entity)):
                # initializing a subclassed entity which is already cached. reset
                # init state and reassign class to trigger initializing it as
                # expected.
                entity._init_done = False
                entity.__class__ = cls

            # return cached object
            return cast(Self, entity)

        # proceed with creation of new object
        return super().__new__(cls)

    def __init__(
        self,
        *,
        entity_id: str | None = None,
        session: SessionT | None = None,
        backing_model: BackingT | None = None,
        create: bool | None = None,
    ):
        session = self._get_session_cls()._normalize(session)

        # skip init if already done, but update model if needed
        if self._init_done:
            assert self._entity_id == entity_id
            assert (
                self._session is session
            ), f"Attempt to associate new session {session} with entity having existing session {self._session}"

            self._model.setup_check_init(backing_model)
            return

        # set early since subclass may create other entities which in turn
        # instantiate this one
        self._init_done = True

        self._state = State.CLEAN
        SessionContainer.__init__(self, session)
        ModelContainer.__init__(self, self._model_cls(self))

        if entity_id is None:
            # set create flag if no entity_id provided
            create = True
        else:
            # set entity_id and add to cache
            self._set_entity_id(entity_id)

        # invoke subclassed init hook (model extensions, etc)
        self._init()

        # setup model if needed
        self._model.setup_check_init(backing_model, create)

    def __str__(self):
        return self.str_short

    def __repr__(self):
        return str(self)

    @property
    def state(self) -> State:
        """
        Current state.
        """
        return self._state

    @property
    def session(self) -> SessionT:
        """
        Session to which this entity belongs.
        """
        return self._session

    @property
    def str_short(self) -> str:
        """
        Get a short description of this entity.
        """
        return self._str_short

    @property
    def str_summary(self) -> str:
        """
        Get a summary of this entity, including its current state and model values.
        """
        indent = f"\n{' '*4}"
        return indent.join(
            [self.str_short]
            + self._str_summary_extra_pre
            + [self._str_state, str(self._model)]
            + self._str_summary_extra_post
        )

    def flush(self):
        """
        Commit changes to backing store for this entity and its dependencies.
        """
        self._session._cache.flush({self})

    def invalidate(self):
        """
        Discard cached contents and user-provided data for this entity.

        Upon next access, data will be fetched from backing store.
        """
        for entity in [self] + list(self._associated_entities):
            entity._model.teardown()
            if entity._is_dirty:
                entity._set_clean()

    def delete(self):
        """
        Mark this entity for pending delete.
        """
        self._delete()

    def refresh(self):
        """
        Update value from backing store, discarding any local changes.
        """
        self.invalidate()
        self._model.setup()

    @property
    @abstractmethod
    def _model_cls(self) -> type[ModelT]:
        """
        Type used to create `_model`.
        """
        ...

    @classmethod
    @abstractmethod
    def _get_session_cls(cls) -> type[SessionT]:
        """
        Get the framework's session class, used to look up the default session.
        """
        ...

    @property
    def _str_short(self) -> str:
        """
        Implementation of `str_short`.
        """
        return f"{type(self).__name__}(entity_id={self._entity_id!r})"

    @property
    def _str_state(self) -> str:
        """
        Get description of current state for use in summary.
        """
        return self._state.name

    @property
    def _str_summary_extra_pre(self) -> list[str]:
        """
        Get extra lines to be inserted at beginning of summary.
        """
        return []

    @property
    def _str_summary_extra_post(self) -> list[str]:
        """
        Get extra lines to be inserted at end of summary.
        """
        return []

    @property
    def _dependencies(self) -> Set[BaseEntity]:
        """
        Get entities this entity depends on.

        Upon flush, dirty dependencies are flushed along with, and prior to, this
        entity.
        """
        return set()

    @property
    def _associated_entities(self) -> Sequence[BaseEntity]:
        """
        Get entities to be refreshed/cleaned up along with this one, e.g. those which
        don't exist independently of it.
        """
        return ()

    @property
    def _is_clean(self) -> bool:
        return self._state is State.CLEAN

    @property
    def _is_dirty(self) -> bool:
        return self._state is not State.CLEAN

    @property
    def _is_create(self) -> bool:
        return self._state is State.CREATE

    @property
    def _is_update(self) -> bool:
        return self._state is State.UPDATE

    @property
    def _is_delete(self) -> bool:
        return self._state is State.DELETE

    @property
    def _is_abandoned(self) -> bool:
        """
        Whether this entity never existed in backing store and never will, e.g. it was
        created and then deleted before being flushed.
        """
        return self._state in {State.CLEAN, State.DELETE} and self._model.nexists

    @property
    def _is_orphan(self) -> bool:
        """
        Whether a dependency of this entity was abandoned or is being deleted.
        """
        return any(dep._is_abandoned or dep._is_delete for dep in self._dependencies)

    def _init(self):
        """
        Register model extensions or perform any other init needed before model setup.
        """

    def _setup(self, model: BackingT):
        """
        Populate any state based on model retrieved from backing store.
        """
        _ = model

    def _flush_check(self):
        """
        Check if this entity is in a valid state to be committed to backing store.

        Upon invalid state, should raise {obj}`EntityValidationError` with useful
        description of problem; see {obj}`assert_valid`.
        """

    def _flush_prep(self):
        """
        Propagate any values to fields if needed before flush.
        """

    def _delete(self):
        """
        Implementation of `delete()`; subclass can extend to e.g. delete associated
        entities.
        """
        self._model.setup_check()
        self._set_dirty(State.DELETE)

    def _set_attrs(self, **kwargs: Any):
        """
        Set attributes on self, skipping those with a value of `None`.
        """
        for attr, val in kwargs.items():
            if val is not None:
                setattr(self, attr, val)

    def _set_entity_id(self, entity_id: str):
        """
        Set entity id and add to cache.
        """
        assert entity_id is not None

        if self._entity_id is None:
            self._entity_id = entity_id
            self._session._cache.add(self)
        else:
            assert self._entity_id == entity_id

    def _refresh_model(self, model: BackingT):
        """
        Discard current model and update with new one.
        """
        self.invalidate()
        self._model.setup(model=model, create=False)

    def _flush(self, sorter: TopologicalSorter[BaseEntity]):
        """
        Commit changes to backing store for this entity.
        """
        logger = self._session._logger
        logger.debug(f"Flushing: {self.str_summary}")

        new_model: BackingT | None = None
        gen: Generator | None = None

        assert self._is_dirty

        if self._is_abandoned:
            # bail out if deleting an entity which never got created
            pass
        elif self._is_orphan:
            # bail out if a dependency was abandoned
            logger.warning(
                f"Orphaned entity not being flushed since a dependency was abandoned: {self.str_summary}"
            )
        else:
            try:
                # flush model if it has pending changes
                if (
                    self._state in {State.CREATE, State.DELETE}
                    or self._model.fields_changed
                ):
                    new_model, gen = self._model.flush(sorter)
            except self._session._tolerated_flush_errors as e:
                logger.warning(
                    f"Flush failed, likely implicitly deleted by another operation: {self.str_summary} ({type(e).__name__})"
                )
            else:
                if self._state is not State.DELETE:
                    # get latest model from extensions if applicable
                    new_model = self._model.flush_extensions() or new_model

        # mark as clean
        self._set_clean()

        # perform setup if we got a new model
        if new_model is not None:
            self._model.setup(new_model)

        # continue flush sequence, if needed by driver
        if gen is not None:
            try:
                next(gen)
            except StopIteration:
                pass

    def _check_state(self):
        """
        Invoked upon field update by user.
        """
        # handle dirty state based on entity state
        if self._state is State.CLEAN:
            # compare model fields valid for update and set dirty if different
            if self._model.is_changed:
                self._set_dirty(State.UPDATE)

        elif self._state is State.CREATE:
            # do nothing; should already be in dirty set
            assert self in self._session._cache.dirty_set

        elif self._state is State.UPDATE:
            # compare models and set clean if same
            if self._model.is_changed is False:
                self._set_clean()

        else:
            raise Exception(f"Entity in invalid state: {self._state}")

    def _set_clean(self):
        """
        Set as clean from dirty state.
        """
        assert self._is_dirty
        assert self in self._session._cache.dirty_set

        self._state = State.CLEAN
        self._session._cache.dirty_set.remove(self)

    def _set_dirty(self, state: State):
        """
        Set as dirty in requested state.
        """
        assert state is not State.CLEAN

        # check if not already in requested state
        if self._state is not state:
            if self._is_clean:
                # clean -> dirty transition: add to dirty set
                assert (
                    self not in self._session._cache.dirty_set
                ), f"Already in dirty_set with state {self.state}: {self}"
                self._session._cache.dirty_set.add(self)
            else:
                # dirty -> dirty transition: make sure already in dirty set
                assert self in self._session._cache.dirty_set

                if self._is_create:
                    # only create -> delete allowed
                    assert state is State.DELETE

                elif self._is_update:
                    # update -> create not allowed
                    assert state is not State.CREATE

            self._state = state


def normalize_entities[T: BaseEntity](entities: T | Iterable[T]) -> list[T]:
    """
    Take an entity or iterable of entities and return a list.
    """
    return [entities] if isinstance(entities, BaseEntity) else list(entities)
