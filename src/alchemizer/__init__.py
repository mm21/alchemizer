"""
Alchemizer: A meta-framework to facilitate development of frameworks using identity map
and unit of work patterns.
"""

__submodules__ = [
    "session",
    "entity",
    "state",
    "exceptions",
    "cache",
]

# isort: off
# <AUTOGEN_INIT>
from .session import (
    BaseSession,
    SessionContainer,
)
from .entity import (
    BaseEntity,
    normalize_entities,
    BaseDriver,
    BaseEntityModel,
    ModelContainer,
    Extension,
    StatefulExtension,
    EntityIdDescriptor,
    WriteThroughDescriptor,
    WriteOnceDescriptor,
)
from .state import (
    State,
)
from .exceptions import (
    ReadOnlyError,
    ValidationError,
    EntityValidationError,
    assert_valid,
)
from .cache import (
    Cache,
)

__all__ = [
    "BaseSession",
    "SessionContainer",
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
    "State",
    "ReadOnlyError",
    "ValidationError",
    "EntityValidationError",
    "assert_valid",
    "Cache",
]
# </AUTOGEN_INIT>
