# Alchemizer

A meta-framework to facilitate development of frameworks using identity map and unit of work patterns

[![Python versions](https://img.shields.io/pypi/pyversions/alchemizer.svg)](https://pypi.org/project/alchemizer)
[![PyPI](https://img.shields.io/pypi/v/alchemizer?color=%2334D058&label=pypi%20package)](https://pypi.org/project/alchemizer)
[![Tests](./badges/tests.svg?dummy=8484744)]()
[![Coverage](./badges/cov.svg?dummy=8484744)]()
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

- [Alchemizer](#alchemizer)
  - [Motivation](#motivation)
  - [Getting started](#getting-started)
  - [Concepts](#concepts)
    - [Entity state](#entity-state)
    - [Flush](#flush)
  - [Building a framework](#building-a-framework)
    - [Session](#session)
    - [Driver](#driver)
    - [Model](#model)
    - [Entity](#entity)
    - [Using it](#using-it)
  - [Going further](#going-further)
    - [Dependencies](#dependencies)
    - [Validation](#validation)
    - [Customizing flush](#customizing-flush)
    - [Extensions](#extensions)
    - [Descriptors](#descriptors)
    - [Default session](#default-session)
    - [Declarative subclasses](#declarative-subclasses)
  - [Conventions](#conventions)

The name is a nod to [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) and [RuneScape](https://runescape.wiki/w/Alchemiser).

## Motivation

Suppose you're wrapping a remote API, a database, or a tree of files, and you want your users to work with plain Python objects: get an object, set some attributes, and have the right calls made on their behalf. Doing this well takes the same machinery every time:

- **Identity map**: asking for the same record twice should give the same object, so that changes made in one place are seen everywhere.
- **Lazy loading**: a record should be fetched when it's first needed, and not again.
- **Change tracking**: you need to know which objects are new, modified, or deleted, and which fields changed. Setting a field back to its original value shouldn't result in a write.
- **Unit of work**: pending changes should be validated together, then written in an order which respects the dependencies between them.

Alchemizer provides this machinery as a set of base classes. You supply what's specific to your domain: what a record's fields are and how to fetch, create, update, and delete it. The result is a framework of your own, in the style of an ORM, for whatever backing store you have.

It was extracted from [TriliumAlchemy](https://github.com/mm21/trilium-alchemy), an SDK for [Trilium Notes](https://github.com/TriliumNext/Trilium), which is built on it.

## Getting started

Install from PyPI:

```bash
pip install alchemizer
```

Alchemizer requires Python 3.12+ and has no dependencies.

## Concepts

| Class | Role |
| --- | --- |
| `BaseSession` | Context in which entities are cached and their changes tracked. Owns the connection to your backing store. Your users create one and call `flush()` on it. |
| `BaseEntity` | An object identified by an id and mapped to a record in the backing store. These are the objects your users work with. |
| `BaseEntityModel` | Tracks an entity's fields: the data as last received from the backing store, and the working copy with the user's changes. One per entity. |
| `BaseDriver` | Fetches, creates, updates, and deletes an entity's record in the backing store. One per entity. |
| `Extension` | Optional state attached to an entity beyond its fields, e.g. a collection of related entities. |

### Entity state

Every entity has a `state`, maintained automatically as the user makes changes:

| State | Meaning | Entered when |
| --- | --- | --- |
| `State.CLEAN` | No pending changes | Loaded, flushed, or changes reverted |
| `State.CREATE` | Pending create | Instantiated without an id, or with an id not found in the backing store |
| `State.UPDATE` | Pending update | A field differs from the backing store |
| `State.DELETE` | Pending delete | `delete()` invoked |

Entities which aren't clean are *dirty*, and are tracked by the session until flushed.

### Flush

`session.flush()` writes all dirty entities; `session.flush(entities)` or `entity.flush()` writes specific entities along with the dirty entities they depend on. A flush:

1. Collects the entities to flush and, recursively, their dirty dependencies.
2. Validates each one. If any are invalid, raises a single `ValidationError` listing every problem; nothing is written.
3. Sorts the entities so that each one follows its dependencies.
4. Invokes each entity's driver to create, update, or delete it, then updates the entity with the record returned by the backing store and marks it clean.

Using a session as a context manager flushes it upon exit, unless an exception was raised.

## Building a framework

Here's a complete, minimal framework for users stored in a `dict`. A real one would talk to an API or database instead, but the structure is the same.

### Session

The session holds whatever your drivers need to reach the backing store. Its type parameter is your base entity class.

```python
from graphlib import TopologicalSorter
from typing import Any

from alchemizer import BaseDriver, BaseEntity, BaseEntityModel, BaseSession

type Record = dict[str, Any]


class Session(BaseSession["User"]):
    def __init__(self, db: dict[str, Record], **kwargs: Any):
        super().__init__(**kwargs)
        self.db = db
```

### Driver

The driver performs the actual reads and writes for one entity. Its type parameters are your session class and the type of object your backing store gives you for a record, which can be anything: here a `dict`, in TriliumAlchemy a pydantic model.

Each `flush_*` method receives the sorter used to order the flush, which most drivers can ignore; see [Customizing flush](#customizing-flush).

```python
class UserDriver(BaseDriver[Session, Record]):
    @property
    def user_id(self) -> str:
        assert self.entity._entity_id
        return self.entity._entity_id

    def fetch(self) -> Record | None:
        return self.session.db.get(self.user_id)

    def flush_create(self, sorter: TopologicalSorter) -> Record:
        assert self.entity._model.working_data is not None
        user_id = f"user{len(self.session.db) + 1}"
        record = {"id": user_id, **self.entity._model.working_data}
        self.session.db[user_id] = record
        return record

    def flush_update(self, sorter: TopologicalSorter) -> Record:
        record = self.session.db[self.user_id]
        record.update(self.entity._model.get_changed_fields())
        return record

    def flush_delete(self, sorter: TopologicalSorter) -> None:
        del self.session.db[self.user_id]
```

`flush_create()` and `flush_update()` return the record as it now exists in the backing store, which becomes the entity's new backing data. If the backing store assigned the id, as it does here, the entity picks it up from the returned record.

### Model

The model declares which driver to use, which field holds the id, and which fields the user may change along with their defaults for new entities.

```python
class UserModel(BaseEntityModel[Record, UserDriver]):
    @property
    def driver_cls(self) -> type[UserDriver]:
        return UserDriver

    @property
    def entity_id_field(self) -> str:
        return "id"

    @property
    def update_fields(self) -> list[str]:
        return ["name", "email"]

    @property
    def default_fields(self) -> dict[str, Any]:
        return {"name": "", "email": ""}
```

Records are converted to a `dict` of fields using `dict(record)`, which handles mappings and pydantic models. For anything else, override `to_backing_data()`.

### Entity

The entity is what your users see, so its public interface is up to you. Expose fields by reading and writing them through the model, which handles lazy loading and state tracking.

```python
class User(BaseEntity[Session, UserModel, Record]):
    def __new__(cls, user_id: str | None = None, session: Session | None = None):
        return super().__new__(cls, entity_id=user_id, session=session)

    def __init__(self, user_id: str | None = None, session: Session | None = None):
        super().__init__(entity_id=user_id, session=session)

    @property
    def user_id(self) -> str | None:
        return self._entity_id

    @property
    def name(self) -> str:
        return self._model.get_field("name", str)

    @name.setter
    def name(self, value: str):
        self._model.set_field("name", value)

    @property
    def email(self) -> str:
        return self._model.get_field("email", str)

    @email.setter
    def email(self, value: str):
        self._model.set_field("email", value)

    @property
    def _model_cls(self) -> type[UserModel]:
        return UserModel

    @classmethod
    def _get_session_cls(cls) -> type[Session]:
        return Session
```

The identity map lookup happens in `__new__()`, so if you customize the constructor's signature, override both `__new__()` and `__init__()` and pass the id and session along as shown.

With more than one kind of entity, define `_get_session_cls()` once in a base class of your own and derive your entities from it.

### Using it

```python
from alchemizer import State

db = {"user1": {"id": "user1", "name": "Ada", "email": "ada@example.com"}}

with Session(db, default=True) as session:
    ada = User("user1")

    # identity map: one object per id per session
    assert User("user1") is ada

    # loaded upon first access
    assert ada.name == "Ada"

    # changes are tracked, but not yet written
    ada.email = "ada@example.org"
    assert ada.state is State.UPDATE
    assert db["user1"]["email"] == "ada@example.com"

    # reverting a change makes the entity clean again
    ada.email = "ada@example.com"
    assert ada.state is State.CLEAN

    ada.email = "ada@example.org"

    # no id: pending create
    grace = User()
    grace.name = "Grace"
    assert grace.state is State.CREATE
    assert session.dirty_count == 2

# changes are flushed upon exiting the context
assert db["user1"]["email"] == "ada@example.org"
assert db["user2"] == {"id": "user2", "name": "Grace", "email": ""}
assert grace.user_id == "user2"
```

Entities also provide `delete()` to mark for deletion, `invalidate()` to discard cached data and pending changes, `refresh()` to reload from the backing store, and `str_summary` to describe pending changes. Sessions provide `dirty_set`, `dirty_map`, and `dirty_count` to inspect what's pending.

## Going further

These are the hooks you can override as your framework grows. All are optional.

### Dependencies

If an entity can't be written before another one exists, e.g. because it stores the other's id, return the other entity from `_dependencies`. Dirty dependencies are flushed first, even when the user flushes only the dependent entity.

```python
class Post(Entity[PostModel]):
    @property
    def _dependencies(self) -> set[BaseEntity]:
        return {self.author}

    def _flush_prep(self):
        # author's id is known by now, even if author was just created
        self._model.set_field("author_id", self.author.user_id)
```

`_flush_prep()` is invoked right before an entity is created or updated, making it the place to fill in fields which depend on other entities having been flushed.

Similarly, `_associated_entities` returns the entities which don't exist independently of this one. They're invalidated along with it, and dropped from the identity map when it's deleted.

If a dependency is deleted, or was created and then deleted before ever being flushed, the dependent entity is skipped with a warning rather than written.

### Validation

Override `_flush_check()` to reject changes before anything is written. Use `assert_valid()` to raise an `EntityValidationError`; these are collected across all entities being flushed and raised to the user as a single `ValidationError`, with the messages in its `errors` attribute.

```python
from alchemizer import assert_valid

class User(Entity[UserModel]):
    def _flush_check(self):
        assert_valid("@" in self.email, f"Invalid email: {self.email}")
```

### Customizing flush

Override the session's `_flush_scope()` context manager to run code around a flush. Before yielding, you can add ordering constraints beyond those implied by `_dependencies` and capture anything you'll need afterward; after yielding, all entities have been flushed.

```python
from contextlib import contextmanager

class Session(BaseSession[Entity]):
    @contextmanager
    def _flush_scope(self, entities, sorter):
        # flush all creates before any deletes
        creates = {e for e in entities if e._is_create}
        for entity in entities:
            if entity._is_delete:
                sorter.add(entity, *creates)

        yield

        self.connection.commit()
```

Other session hooks:

- `_tolerated_flush_errors`: exception types which a driver may raise without aborting the flush. Useful when your backing store deletes records implicitly, e.g. a cascading delete, so that a later attempt to delete them fails harmlessly. A warning is logged and the entity is considered flushed.
- `_close()`: invoked when exiting the session's context without an error, after flushing.
- `_get_summary()`: describes how many entities are in each state, for logging.

If writing one entity writes another as a side effect, its driver should mark the other as clean and pass it to `sorter.done()`. A `flush_create()` which needs to do further work once its entity has been updated with the new record can be written as a generator: yield the record, then continue.

### Extensions

A `StatefulExtension` holds state which is loaded and discarded along with an entity's model but isn't one of its fields: related entities, or data stored via a separate API. Create it in the entity's `_init()` hook. It's notified via `_setup()` when the entity is loaded and `_teardown()` when it's invalidated. If it reports `_is_changed`, the entity is considered dirty and the extension's `_flush()` is invoked after the entity's fields are written.

```python
from alchemizer import StatefulExtension

class Avatar(StatefulExtension[Record]):
    _image: bytes | None = None
    _changed: bool = False

    @property
    def _is_changed(self) -> bool:
        return self._changed

    def _setattr(self, obj: bytes):
        self._model.setup_check()
        self._image, self._changed = obj, True
        self._entity._check_state()

    def _setup(self, model: Record | None):
        self._image, self._changed = None, False

    def _teardown(self):
        self._image, self._changed = None, False

    def _flush(self) -> Record | None:
        self._entity.session.upload_avatar(self._entity.user_id, self._image)
        return None
```

### Descriptors

For common patterns of attribute access:

- `EntityIdDescriptor`: exposes the entity id as a read-only attribute, raising `ReadOnlyError` upon assignment.
- `WriteThroughDescriptor`: exposes a field as a richer object. Assigning the object stores it and writes one of its attributes to the model field, e.g. assigning a `User` to `post.author` sets the `author_id` field.
- `WriteOnceDescriptor`: allows an attribute to be set only once, e.g. the owner of an entity which can't be moved.

```python
class Post(Entity[PostModel]):
    post_id = EntityIdDescriptor()
    author = WriteThroughDescriptor[User]("_author", "user_id", "author_id")
```

### Default session

A session created with `default=True` is used by entities instantiated without a `session`, as in the example above. There can be one default session at a time; it's deregistered upon exiting its context or by invoking `deregister_default()`.

The default is tracked per framework, i.e. per class deriving directly from `BaseSession`, so frameworks built on Alchemizer don't interfere with each other.

### Declarative subclasses

Instantiating an entity which is already cached using a subclass of its current class converts the cached object to the subclass and initializes it again. This lets you offer subclasses which describe specific records, such as a `Root` class which always maps to the record with id `root`, and have them be interchangeable with the same record obtained generically.

## Conventions

Your entity and session classes are used by two audiences: you (the framework author) and your users. Alchemizer keeps these apart by naming:

- Public members, such as `flush()`, `delete()`, `state`, and `dirty_set`, are for your users and become part of your framework's interface.
- Members prefixed with `_`, such as `_model`, `_dependencies`, and `_flush_check()`, are for you to use and override. They stay out of your users' way, as well as out of their autocompletion and your generated API documentation.

Models, drivers, and extensions aren't user-facing, so their interfaces are public.
