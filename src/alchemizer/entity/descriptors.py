"""
Implements descriptors for common patterns of entity attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, cast, overload

from ..exceptions import ReadOnlyError

if TYPE_CHECKING:
    from .entity import BaseEntity

__all__ = [
    "EntityIdDescriptor",
    "WriteThroughDescriptor",
    "WriteOnceDescriptor",
]


class EntityIdDescriptor:
    """
    Accessor for read-only entity id.

    :raises ReadOnlyError: Upon write attempt
    """

    @overload
    def __get__(self, obj: None, objtype: type) -> Self: ...
    @overload
    def __get__(self, obj: BaseEntity, objtype: type) -> str: ...
    def __get__(self, obj: BaseEntity | None, objtype: type) -> Self | str:
        _ = objtype
        if obj is None:
            return self
        return cast(str, obj._entity_id)

    def __set__(self, obj: BaseEntity, val: Any):
        _ = val
        raise ReadOnlyError("_entity_id", obj)


class WriteThroughDescriptor[T]:
    """
    Accessor for an attribute which immediately populates the underlying model field
    when updated.

    Enables a field to be exposed as a richer object, e.g. another entity rather than
    its id.
    """

    # attribute which holds value for reading
    _attr: str

    # attribute of value which holds data to write to model field
    _obj_attr: str

    # name of model field to be set
    _field: str

    def __init__(self, attr: str, obj_attr: str, field: str):
        """
        :param attr: Name of attribute in which to store the assigned value
        :param obj_attr: Name of the assigned value's attribute to write to model field
        :param field: Name of model field to be set
        """
        self._attr = attr
        self._obj_attr = obj_attr
        self._field = field

    @overload
    def __get__(self, obj: None, objtype: type) -> Self: ...
    @overload
    def __get__(self, obj: BaseEntity, objtype: type) -> T: ...
    def __get__(self, obj: BaseEntity | None, objtype: type) -> Self | T:
        _ = objtype
        if obj is None:
            return self
        obj._model.setup_check()
        return getattr(obj, self._attr)

    def __set__(self, obj: BaseEntity, value: T):
        assert value is not None

        # ensure model is set up first so setup doesn't overwrite the new value
        obj._model.setup_check()

        setattr(obj, self._attr, value)
        obj._model.set_field(self._field, getattr(value, self._obj_attr))


class WriteOnceDescriptor[T]:
    """
    Accessor for an attribute which is only allowed a single value.

    Subsequent assignments are a no-op if they set the same value.

    :raises ValueError: Upon write attempt with `None` or a different value than \
        currently set
    """

    # attribute which holds value
    _attr: str

    # name of method to invoke after setting value
    _validator: str | None

    def __init__(self, attr: str, *, validator: str | None = None):
        """
        :param attr: Name of attribute in which to store the assigned value
        :param validator: Name of entity method to invoke after setting value
        """
        self._attr = attr
        self._validator = validator

    @overload
    def __get__(self, obj: None, objtype: type) -> Self: ...
    @overload
    def __get__(self, obj: BaseEntity, objtype: type) -> T: ...
    def __get__(self, obj: BaseEntity | None, objtype: type) -> Self | T:
        _ = objtype
        if obj is None:
            return self
        obj._model.setup_check()
        return getattr(obj, self._attr)

    def __set__(self, obj: BaseEntity, value: T):
        if value is None:
            raise ValueError(f"Cannot set {self._attr} with value None on {obj}")

        cur_value = getattr(obj, self._attr)

        if cur_value is None:
            setattr(obj, self._attr, value)
        elif value != cur_value:
            raise ValueError(f"New value {value} must equal current value {cur_value}")

        if validator := self._validator:
            getattr(obj, validator)()
