"""
Implements base classes for an entity, a framework-specific object which is managed by
the identity map, along with its model, driver, and extensions.
"""

__submodules__ = [
    "entity",
    "model",
    "extension",
    "descriptors",
]

# isort: off
# <AUTOGEN_INIT>
from .entity import (
    BaseEntity,
    normalize_entities,
)
from .model import (
    BaseDriver,
    BaseEntityModel,
    ModelContainer,
)
from .extension import (
    Extension,
    StatefulExtension,
)
from .descriptors import (
    EntityIdDescriptor,
    WriteThroughDescriptor,
    WriteOnceDescriptor,
)

__all__ = [
    "BaseEntity",
    "normalize_entities",
    "BaseDriver",
    "BaseEntityModel",
    "ModelContainer",
    "Extension",
    "StatefulExtension",
    "EntityIdDescriptor",
    "WriteThroughDescriptor",
    "WriteOnceDescriptor",
]
# </AUTOGEN_INIT>
