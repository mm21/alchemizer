"""
Implements identity map and unit of work solver.
"""

from __future__ import annotations

import logging
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Iterable

from .exceptions import EntityValidationError, ValidationError

if TYPE_CHECKING:
    from .entity.entity import BaseEntity
    from .session import BaseSession

__all__ = [
    "Cache",
]


class Cache:
    """
    Combines an identity map and unit of work.

    Maintains changes made by user and synchronizes with backing store upon flush.
    """

    entity_map: dict[str, BaseEntity]
    """
    Mapping of entity id to entity object.
    """

    dirty_set: set[BaseEntity]
    """
    Set of entities which need to be synchronized with backing store.
    """

    _session: BaseSession

    def __init__(self, session: BaseSession):
        self._session = session
        self.entity_map = dict()
        self.dirty_set = set()

    def __str__(self):
        return f"Cache: entity_map={self.entity_map}, dirty_set={self.dirty_set}"

    def flush(self, entities: Iterable[BaseEntity] | None = None):
        """
        Flush provided entities and all dependencies, or all dirty entities if entities
        not provided.
        """
        logger = self._session._logger
        entities_ = entities if entities is not None else self.dirty_set

        # filter by dirty state
        dirty_set = {entity for entity in entities_ if entity._is_dirty}

        # first pass validation of entities provided by user
        self._validate(dirty_set)

        # save a reference so we can get entities newly added as dependencies
        dirty_set_old = dirty_set.copy()

        # recursively merge in all dependencies
        for entity in dirty_set_old:
            self._flush_gather(entity, dirty_set)

        # validate newly added entities
        self._validate(dirty_set - dirty_set_old)

        if not len(dirty_set):
            logger.debug("No dirty entities to flush")
            return

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"Flushing {len(dirty_set)} entities: {self._session._get_summary(dirty_set)}"
            )

        # create and populate topological sorter
        sorter: TopologicalSorter[BaseEntity] = TopologicalSorter()

        for entity in dirty_set:
            sorter.add(entity)

            for dep in entity._dependencies:
                if dep._is_dirty:
                    sorter.add(entity, dep)

        # let session customize ordering and perform any pre/post processing
        with self._session._flush_scope(dirty_set, sorter):
            sorter.prepare()

            # flush entities in order provided by sorter
            while sorter.is_active():
                for entity in sorter.get_ready():
                    if entity._is_dirty:
                        do_cleanup = entity._is_delete
                        entity._flush(sorter)

                        # remove entity and associated entities from map
                        if do_cleanup:
                            for e in (entity, *entity._associated_entities):
                                if e._entity_id in self.entity_map:
                                    del self.entity_map[e._entity_id]

                    sorter.done(entity)

    def add(self, entity: BaseEntity):
        """
        Add provided entity to cache.

        Should be invoked as soon as entity id is set.
        """
        assert entity._entity_id

        if entity._entity_id in self.entity_map:
            assert entity is self.entity_map[entity._entity_id]
        else:
            self.entity_map[entity._entity_id] = entity
            self._session._logger.debug(
                f"Added to cache: entity_id={entity._entity_id}, type={type(entity)}"
            )

    def _validate(self, entity_set: set[BaseEntity]):
        """
        Check all provided entities and if errors encountered, raise an exception with a
        list of errors.
        """
        errors: list[str] = []
        for entity in entity_set:
            try:
                entity._flush_check()
            except EntityValidationError as e:
                errors.append(f"{entity} {type(entity)}: {e}")

        if len(errors) > 0:
            raise ValidationError(errors)

    def _flush_gather(self, entity: BaseEntity, dirty_set: set[BaseEntity]):
        """
        Recursively add entity's dependencies to set if they're dirty.
        """
        for dep in entity._dependencies:
            if dep not in dirty_set and dep._is_dirty:
                dirty_set.add(dep)
                self._flush_gather(dep, dirty_set)
