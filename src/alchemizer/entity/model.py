"""
Implements an entity's model, which tracks its fields, and driver, which interfaces with
the backing store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Any, Callable, Generator, Literal, overload

from ..session import BaseSession
from ..state import State

if TYPE_CHECKING:
    from .entity import BaseEntity
    from .extension import StatefulExtension

__all__ = [
    "BaseDriver",
    "BaseEntityModel",
    "ModelContainer",
]


class BaseDriver[SessionT: BaseSession, BackingT](ABC):
    """
    Implements interface to backing store for an entity.

    Type parameters:

    - `SessionT`: The framework's session class
    - `BackingT`: Type of object received from backing store for this entity
    """

    entity: BaseEntity[SessionT, Any, BackingT]
    session: SessionT

    def __init__(self, entity: BaseEntity[SessionT, Any, BackingT]):
        self.entity = entity
        self.session = entity.session

    @abstractmethod
    def fetch(self) -> BackingT | None:
        """
        Retrieve model from backing store, or `None` if it doesn't exist.
        """
        ...

    @abstractmethod
    def flush_create(
        self, sorter: TopologicalSorter[BaseEntity]
    ) -> BackingT | Generator[BackingT, None, None]:
        """
        Create entity in backing store and return its resulting model.

        Can alternatively be implemented as a generator which yields the resulting
        model, in which case it will be resumed after the entity is set up with the new
        model and marked as clean.

        If other entities get flushed as a side effect, they should be marked as clean
        and passed to `sorter.done()`.
        """
        ...

    @abstractmethod
    def flush_update(self, sorter: TopologicalSorter[BaseEntity]) -> BackingT:
        """
        Update entity in backing store and return its resulting model.
        """
        ...

    @abstractmethod
    def flush_delete(self, sorter: TopologicalSorter[BaseEntity]) -> None:
        """
        Delete entity from backing store.
        """
        ...


class BaseEntityModel[BackingT, DriverT: BaseDriver](ABC):
    """
    Abstraction of data model which is stored as a record in backing store,
    encapsulating both locally modified data and data as received from backing store.

    Type parameters:

    - `BackingT`: Type of object received from backing store for this entity
    - `DriverT`: Subclass of {obj}`BaseDriver` used to interface with backing store
    """

    # entity owning this object
    entity: BaseEntity

    # driver to interface with backing store
    driver: DriverT

    # cached data fetched from backing store, or None if not fetched
    backing_data: dict[str, Any] | None = None

    # locally modified or created data not yet committed to backing store,
    # or None if not fetched
    working_data: dict[str, Any] | None = None

    # whether model setup was completed, populating model from backing store
    setup_done: bool = False

    # whether entity exists in backing store, or None if unknown
    _exists: bool | None = None

    # list of stateful extensions registered by subclass
    _extensions: list[StatefulExtension[BackingT]]

    def __init__(self, entity: BaseEntity):
        self.entity = entity
        self.driver = self.driver_cls(entity)
        self._extensions = []

    def __str__(self):
        fields = []

        def get_field_str(value: Any) -> str:
            return (
                f"'{value.replace(" '", "\\' ")}'"
                if isinstance(value, str)
                else str(value)
            )

        for field in self.update_fields:
            if self.backing_data is None:
                if self.entity._is_create:
                    backing = ""
                else:
                    backing = "?"
            else:
                backing = f"{get_field_str(self.backing_data[field])}"

            if self.working_data is None or not self.is_changed:
                working = ""
            else:
                if (
                    self.backing_data is not None
                    and self.backing_data[field] == self.working_data[field]
                ):
                    working = ""
                else:
                    if self.entity._is_create:
                        arrow = ""
                    else:
                        arrow = "->"

                    working = f"{arrow}{get_field_str(self.working_data[field])}"

            fields.append(f"{field}={backing}{working}")

        fields_str = ", ".join(fields)

        return f"{{{fields_str}}}"

    @property
    @abstractmethod
    def driver_cls(self) -> type[DriverT]:
        """
        Class to interface with backing store.
        """
        ...

    @property
    @abstractmethod
    def entity_id_field(self) -> str:
        """
        Field used to store entity id.
        """
        ...

    @property
    @abstractmethod
    def update_fields(self) -> list[str]:
        """
        Fields allowed for user update.
        """
        ...

    @property
    @abstractmethod
    def default_fields(self) -> dict[str, Any]:
        """
        Default values of fields allowed for user update, used upon create.
        """
        ...

    @property
    def exists(self) -> bool:
        """
        Whether the entity for certain exists in backing store.
        """
        return bool(self._exists)

    @property
    def nexists(self) -> bool:
        """
        Whether the entity for certain does not exist in backing store (setup is
        additionally done).
        """
        return self._exists is False and self.setup_done

    @property
    def fields_changed(self) -> bool:
        """
        Whether any fields differ from those in backing store.
        """
        if self.working_data is None or self.backing_data is None:
            return False
        else:
            backing = {k: self.backing_data[k] for k in self.update_fields}
            return backing != self.working_data

    @property
    def extension_changed(self) -> bool:
        """
        Whether any extensions have state needing to be flushed.
        """
        return any(ext._is_changed for ext in self._extensions)

    @property
    def is_changed(self) -> bool:
        """
        Whether there are any changes needing to be flushed.
        """
        return self.entity._is_create or self.fields_changed or self.extension_changed

    def to_backing_data(self, model: BackingT) -> dict[str, Any]:
        """
        Convert model received from backing store to a mapping of field names to values.

        By default handles mappings and iterables of key/value pairs, e.g. pydantic
        models; override to handle other types.
        """
        return dict(model)  # type: ignore[call-overload]

    def check_newer(self, model: BackingT) -> bool:
        """
        Check whether the provided model, passed upon instantiating an entity which may
        already be set up, should replace the current backing data.

        By default only accepts the model if there is no backing data yet; override to
        e.g. compare modification timestamps.
        """
        _ = model
        return self.backing_data is None

    def is_field_changed(self, field: str) -> bool:
        """
        Check whether the provided field differs from that in backing store.
        """
        if self.working_data is None or self.backing_data is None:
            return False

        return self.backing_data[field] != self.working_data[field]

    def get_changed_fields(self) -> dict[str, Any]:
        """
        Get a dict of all fields which are changed.
        """
        assert self.backing_data
        assert self.working_data
        fields = dict()

        for field in self.update_fields:
            if self.backing_data[field] != self.working_data[field]:
                fields[field] = self.working_data[field]

        return fields

    @overload
    def get_field[T](
        self, field: str, check_type: type[T], *, allow_none: Literal[False] = False
    ) -> T: ...

    @overload
    def get_field[T](
        self, field: str, check_type: type[T], *, allow_none: Literal[True]
    ) -> T | None: ...

    def get_field[T](
        self, field: str, check_type: type[T], *, allow_none: bool = False
    ) -> T | None:
        """
        Get field from model, with working data taking precedence over backing data.
        """

        def get_value(data: dict[str, Any]) -> T | None:
            assert field in data
            val = data[field]
            if val is not None or not allow_none:
                assert isinstance(val, check_type)
            return val

        # perform model setup if not done
        self.setup_check()

        # attempt to get value from working data
        if self.working_data and field in self.working_data:
            return get_value(self.working_data)

        if not self.entity._is_create:
            # backing data should be populated at this point
            assert self.backing_data

        # attempt to get value from backing data
        if self.backing_data:
            return get_value(self.backing_data)

        # return None in case data is not available yet (e.g. accessing
        # field assigned by backing store when not created yet)
        assert allow_none
        return None

    def set_field(self, field: str, value: Any):
        """
        Set field in working data.
        """
        # ensure field is writeable
        assert field in self.update_fields

        # perform model setup if not done
        self.setup_check()

        # check if working data is initialized
        if self.working_data is None:
            # backing data should exist: working data populated for create
            assert self.backing_data is not None

            # populate working data with copy of backing data, filtered by
            # fields used for update
            self.working_data = {k: self.backing_data[k] for k in self.update_fields}

        # handle deleted state
        if self.entity._state is State.DELETE:
            raise Exception(
                f"Attempt to set field={field} on entity={self.entity} marked for delete"
            )

        # set field in working data
        self.working_data[field] = value

        # set as dirty/clean if needed
        self.entity._check_state()

    def setup_check(self):
        """
        Setup model if not already done, prior to get/set access.
        """
        if self.setup_done is False:
            self.setup()

    def setup_check_init(self, model: BackingT | None, create: bool | None = None):
        """
        Setup model if necessary and backing model provided during init (entity already
        retrieved from backing store).
        """
        if create or (model is not None and self.check_newer(model)):
            self.setup(model=model, create=create)

    def setup(self, model: BackingT | None = None, create: bool | None = None):
        """
        Populate state from backing store.

        May occur any number of times per entity.
        """
        if create is True:
            # if creating, just set flag: no need to query backing store
            self._exists = False

            # there can't be a backing model if it doesn't exist
            assert model is None
        else:
            # attempt to fetch from backing store if not provided
            if model is None:
                model = self.driver.fetch()

            if model is None:
                self.backing_data = None

                # create is False means expected to exist
                assert create is None
            else:
                self.backing_data = self.to_backing_data(model)

            # set exists flag
            self._exists = self.backing_data is not None

        # populate working fields
        if self._exists:
            # reset fields if any; will create on demand if needed
            self.working_data = None
        else:
            # populate new working fields
            self.working_data = {
                f: self._get_default_field(f) for f in self.update_fields
            }

            # move to create state
            self.entity._set_dirty(State.CREATE)

        if model is not None:
            # invoke setup callback for entity
            self.entity._setup(model)

        # set setup_done before extensions are setup; they may refer to state
        # from model
        self.setup_done = True

        # invoke setup callback for extensions
        for ext in self._extensions:
            ext._setup(model)

    def teardown(self):
        """
        Discard all state, including that of extensions.
        """
        self.backing_data = None
        self.working_data = None
        self.setup_done = False

        for ext in self._extensions:
            ext._teardown()

    def flush(
        self, sorter: TopologicalSorter[BaseEntity]
    ) -> tuple[BackingT | None, Generator | None]:
        """
        Flush model according to entity's state.
        """
        if self.entity._state in [State.CREATE, State.UPDATE]:
            # if creating or updating, invoke flush prep
            self.entity._flush_prep()

        # get flush method based on state
        func: Callable[[TopologicalSorter[BaseEntity]], Any] = {
            State.CREATE: self.driver.flush_create,
            State.UPDATE: self.driver.flush_update,
            State.DELETE: self.driver.flush_delete,
        }[self.entity._state]

        # invoke flush method
        result = func(sorter)

        new_model: BackingT | None
        gen: Generator | None = None
        if isinstance(result, Generator):
            # generator: yields model, then performs extra processing
            gen = result
            new_model = next(result)
        else:
            # not generator: just returns model
            new_model = result

        # ensure we got the updated model
        if self.entity._state in [State.CREATE, State.UPDATE]:
            assert new_model is not None
        else:
            assert new_model is None

        if self.entity._state is State.CREATE:
            # set entity id if needed
            if self.entity._entity_id is None:
                assert new_model is not None
                entity_id = self.to_backing_data(new_model)[self.entity_id_field]
                self.entity._set_entity_id(entity_id)

        return (new_model, gen)

    def register_extension(self, extension: StatefulExtension[BackingT]):
        """
        Register extension to receive model updates and handle teardown.

        Only stateful extensions are registered.
        """
        self._extensions.append(extension)

    def flush_extensions(self) -> BackingT | None:
        """
        Flush extensions and return the latest model, if applicable.
        """
        new_model: BackingT | None = None

        for ext in self._extensions:
            if ext._is_changed:
                new_model = ext._flush() or new_model

        return new_model

    def _get_default_field(self, field: str) -> Any:
        """
        Get default field for initializing working data.
        """
        assert field in self.default_fields
        return self.default_fields[field]


class ModelContainer:
    """
    Indicates that subclasses contain a model instance.
    """

    _model: BaseEntityModel

    def __init__(self, model: BaseEntityModel):
        self._model = model
