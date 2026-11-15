"""
Toy framework built on alchemizer, backed by an in-memory store.

Models a tree of folders containing documents: a document depends on its folder, and
deleting a folder implicitly deletes its documents from the store.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import Any, Generator, Sequence

from alchemizer import (
    BaseDriver,
    BaseEntity,
    BaseEntityModel,
    BaseSession,
    EntityIdDescriptor,
    StatefulExtension,
    WriteOnceDescriptor,
    WriteThroughDescriptor,
    assert_valid,
)

type Record = dict[str, Any]


class MissingError(Exception):
    """
    Raised by store upon attempt to modify a record which doesn't exist.
    """


@dataclass
class Store:
    """
    In-memory backing store which logs its operations.
    """

    records: dict[str, Record] = field(default_factory=dict)
    log: list[tuple[str, str]] = field(default_factory=list)
    fetch_count: int = 0
    _next_id: int = 0

    def get(self, record_id: str) -> Record | None:
        self.fetch_count += 1
        record = self.records.get(record_id)
        return None if record is None else record.copy()

    def create(self, kind: str, record_id: str | None = None, **fields: Any) -> Record:
        if record_id is None:
            self._next_id += 1
            record_id = f"{kind}{self._next_id}"
        self.records[record_id] = {"id": record_id, "kind": kind, **fields}
        self.log.append(("create", record_id))
        return self.records[record_id].copy()

    def update(self, record_id: str, **fields: Any) -> Record:
        if record_id not in self.records:
            raise MissingError(record_id)
        self.records[record_id].update(fields)
        self.log.append(("update", record_id))
        return self.records[record_id].copy()

    def delete(self, record_id: str):
        if record_id not in self.records:
            raise MissingError(record_id)
        del self.records[record_id]
        self.log.append(("delete", record_id))

        # cascade to documents in a deleted folder
        for record in list(self.records.values()):
            if record.get("folder_id") == record_id:
                del self.records[record["id"]]


class Session(BaseSession[BaseEntity]):
    _tolerated_flush_errors = (MissingError,)

    store: Store
    closed: bool = False
    flush_scopes: list[tuple[int, str]]

    def __init__(self, store: Store, **kwargs: Any):
        super().__init__(**kwargs)
        self.store = store
        self.flush_scopes = []

    def _close(self):
        self.closed = True

    @contextmanager
    def _flush_scope(
        self, entities: set[BaseEntity], sorter: TopologicalSorter[BaseEntity]
    ) -> Generator[None, None, None]:
        # flush all creates before any deletes
        creates = {e for e in entities if e._is_create}
        for entity in entities:
            if entity._is_delete and not entity._is_abandoned:
                sorter.add(entity, *creates)

        count = len(entities)
        self.flush_scopes.append((count, "begin"))
        yield
        self.flush_scopes.append((count, "end"))


class Driver(BaseDriver[Session, Record]):
    kind: str

    def fetch(self) -> Record | None:
        assert self.entity._entity_id
        return self.session.store.get(self.entity._entity_id)

    def flush_create(
        self, sorter: TopologicalSorter[BaseEntity]
    ) -> Record | Generator[Record, None, None]:
        return self._create(sorter)

    def _create(self, sorter: TopologicalSorter[BaseEntity]) -> Record:
        _ = sorter
        assert self.entity._model.working_data is not None
        return self.session.store.create(
            self.kind, self.entity._entity_id, **self.entity._model.working_data
        )

    def flush_update(self, sorter: TopologicalSorter[BaseEntity]) -> Record:
        _ = sorter
        assert self.entity._entity_id
        return self.session.store.update(
            self.entity._entity_id, **self.entity._model.get_changed_fields()
        )

    def flush_delete(self, sorter: TopologicalSorter[BaseEntity]) -> None:
        _ = sorter
        assert self.entity._entity_id
        self.session.store.delete(self.entity._entity_id)


class FolderDriver(Driver):
    kind = "folder"


class DocumentDriver(Driver):
    kind = "document"

    def flush_create(
        self, sorter: TopologicalSorter[BaseEntity]
    ) -> Generator[Record, None, None]:
        yield self._create(sorter)

        # entity is now set up with the new record
        assert isinstance(self.entity, Document)
        self.entity.post_create_state = (
            self.entity.state.name,
            self.entity.document_id,
        )


class FolderModel(BaseEntityModel[Record, FolderDriver]):
    @property
    def driver_cls(self) -> type[FolderDriver]:
        return FolderDriver

    @property
    def entity_id_field(self) -> str:
        return "id"

    @property
    def update_fields(self) -> list[str]:
        return ["name"]

    @property
    def default_fields(self) -> dict[str, Any]:
        return {"name": "new folder"}


class DocumentModel(BaseEntityModel[Record, DocumentDriver]):
    @property
    def driver_cls(self) -> type[DocumentDriver]:
        return DocumentDriver

    @property
    def entity_id_field(self) -> str:
        return "id"

    @property
    def update_fields(self) -> list[str]:
        return ["title", "folder_id"]

    @property
    def default_fields(self) -> dict[str, Any]:
        return {"title": "new document", "folder_id": ""}

    def check_newer(self, model: Record) -> bool:
        return self.backing_data is None or model.get(
            "revision", 0
        ) > self.backing_data.get("revision", 0)


class Entity[ModelT: BaseEntityModel](BaseEntity[Session, ModelT, Record]):
    """
    Base entity for this framework.
    """

    @classmethod
    def _get_session_cls(cls) -> type[Session]:
        return Session


class Tags(StatefulExtension[Record]):
    """
    Tags of a folder, stored separately from its fields.
    """

    _backing: list[str] | None = None
    _working: list[str] | None = None
    setup_count: int = 0

    @property
    def value(self) -> list[str]:
        self._model.setup_check()
        tags = self._working if self._working is not None else self._backing
        return list(tags or [])

    @property
    def _is_changed(self) -> bool:
        return self._working is not None and self._working != (self._backing or [])

    def _setattr(self, obj: Any):
        self._model.setup_check()
        self._working = list(obj)
        self._entity._check_state()

    def _setup(self, model: Record | None):
        self.setup_count += 1
        self._backing = None if model is None else list(model.get("tags", []))

    def _teardown(self):
        self._backing = None
        self._working = None

    def _flush(self) -> Record | None:
        assert self._entity._entity_id and self._working is not None
        session = self._entity.session
        assert isinstance(session, Session)
        return session.store.update(self._entity._entity_id, tags=self._working)


class Folder(Entity[FolderModel]):
    folder_id = EntityIdDescriptor()

    _tags: Tags

    def __new__(
        cls,
        name: str | None = None,
        *,
        folder_id: str | None = None,
        session: Session | None = None,
        **kwargs: Any,
    ):
        _ = name
        return super().__new__(cls, entity_id=folder_id, session=session, **kwargs)

    def __init__(
        self,
        name: str | None = None,
        *,
        folder_id: str | None = None,
        session: Session | None = None,
        **kwargs: Any,
    ):
        super().__init__(entity_id=folder_id, session=session, **kwargs)
        self._set_attrs(name=name)

    @property
    def name(self) -> str:
        return self._model.get_field("name", str)

    @name.setter
    def name(self, val: str):
        self._model.set_field("name", val)

    @property
    def tags(self) -> list[str]:
        return self._tags.value

    @tags.setter
    def tags(self, val: list[str]):
        self._tags._setattr(val)

    @property
    def documents(self) -> list[Document]:
        """
        Cached documents in this folder.
        """
        return [
            e
            for e in self._session._cache.entity_map.values()
            if isinstance(e, Document) and e._folder_obj is self
        ] + [
            e
            for e in self._session._cache.dirty_set
            if isinstance(e, Document) and e._folder_obj is self and e._is_create
        ]

    @property
    def _model_cls(self) -> type[FolderModel]:
        return FolderModel

    @property
    def _associated_entities(self) -> Sequence[BaseEntity]:
        return self.documents

    def _init(self):
        self._tags = Tags(self)

    def _flush_check(self):
        assert_valid(len(self.name) > 0, "Folder has no name")


class Document(Entity[DocumentModel]):
    document_id = EntityIdDescriptor()
    folder = WriteThroughDescriptor[Folder]("_folder_obj", "_entity_id", "folder_id")
    owner = WriteOnceDescriptor["str | None"]("_owner_obj", validator="_check_owner")

    post_create_state: tuple[str, str] | None = None
    owner_checks: int = 0

    _folder_obj: Folder | None = None
    _owner_obj: str | None = None

    def __new__(cls, *args: Any, document_id: str | None = None, **kwargs: Any):
        _ = args
        return super().__new__(
            cls,
            entity_id=document_id,
            session=kwargs.get("session"),
            backing_model=kwargs.get("backing_model"),
        )

    def __init__(
        self,
        title: str | None = None,
        folder: Folder | None = None,
        *,
        document_id: str | None = None,
        session: Session | None = None,
        backing_model: Record | None = None,
    ):
        super().__init__(
            entity_id=document_id, session=session, backing_model=backing_model
        )
        self._set_attrs(title=title, folder=folder)

    @property
    def title(self) -> str:
        return self._model.get_field("title", str)

    @title.setter
    def title(self, val: str):
        self._model.set_field("title", val)

    @property
    def revision(self) -> int | None:
        return self._model.get_field("revision", int, allow_none=True)

    @property
    def _model_cls(self) -> type[DocumentModel]:
        return DocumentModel

    @property
    def _str_short(self) -> str:
        return f"Document({self._entity_id})"

    @property
    def _dependencies(self) -> set[BaseEntity]:
        return {self._folder_obj} if self._folder_obj else set()

    def _setup(self, model: Record):
        if folder_id := model["folder_id"]:
            self._folder_obj = Folder(folder_id=folder_id, session=self._session)

    def _flush_check(self):
        assert_valid(self._folder_obj is not None, "Document has no folder")

    def _flush_prep(self):
        # folder id may not have been known when folder was assigned
        assert self._folder_obj
        if not self._is_delete and self._folder_obj._entity_id:
            self._model.set_field("folder_id", self._folder_obj._entity_id)

    def _check_owner(self):
        self.owner_checks += 1


class Report(Document):
    """
    Specialized document, to exercise instantiating a cached entity as a subclass.
    """

    init_count: int = 0

    def _init(self):
        self.init_count += 1
