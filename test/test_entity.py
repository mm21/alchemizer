"""
Tests identity map, state tracking, and field access.
"""

from framework import Document, Folder, Report, Session, Store
from pytest import raises

from alchemizer import EntityIdDescriptor, ReadOnlyError, State


def test_identity(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)

    # same id yields same object, without accessing store
    assert Folder(folder_id="f1", session=session) is folder
    assert store.fetch_count == 0
    assert folder.state is State.CLEAN

    # fetched upon first access only
    assert folder.name == "Folder 1"
    assert folder.name == "Folder 1"
    assert store.fetch_count == 1

    # a different session has its own identity map
    session2 = Session(store)
    folder2 = Folder(folder_id="f1", session=session2)
    assert folder2 is not folder
    assert folder2.session is session2

    # can't change session of an entity
    with raises(AssertionError):
        Folder.__init__(folder, folder_id="f1", session=session2)


def test_related_identity(session: Session):
    document = Document(document_id="d1", session=session)
    assert document.title == "Document 1"

    # related entity instantiated upon setup resolves to same object
    assert document.folder is Folder(folder_id="f1", session=session)
    assert document.folder.documents == [document]


def test_subclass(session: Session):
    document = Document(document_id="d1", session=session)
    assert type(document) is Document

    # instantiating as subclass converts the cached object
    report = Report(document_id="d1", session=session)
    assert report is document
    assert type(document) is Report
    assert report.init_count == 1

    # instantiating as superclass or same class returns it as-is
    assert Document(document_id="d1", session=session) is report
    assert Report(document_id="d1", session=session) is report
    assert type(report) is Report
    assert report.init_count == 1

    # unrelated class with same id is not allowed
    with raises(AssertionError):
        Folder(folder_id="d1", session=session)


def test_update_state(session: Session):
    folder = Folder(folder_id="f1", session=session)

    folder.name = "Folder 1"
    assert folder.state is State.CLEAN

    folder.name = "Renamed"
    assert folder.state is State.UPDATE
    assert folder.name == "Renamed"
    assert session.dirty_set == {folder}
    assert session.dirty_map[State.UPDATE] == {folder}
    assert folder._model.is_field_changed("name")
    assert folder._model.get_changed_fields() == {"name": "Renamed"}

    # reverting change restores clean state
    folder.name = "Folder 1"
    assert folder.state is State.CLEAN
    assert session.dirty_count == 0


def test_create_state(session: Session):
    folder = Folder("New", session=session)
    assert folder.state is State.CREATE
    assert folder.folder_id is None
    assert folder.name == "New"

    # default value
    assert Folder(session=session).name == "new folder"
    assert session.dirty_count == 2

    # field assigned by store isn't available yet
    document = Document("Doc", folder, session=session)
    assert document.revision is None
    assert folder.documents == [document]


def test_delete_state(session: Session):
    folder = Folder(folder_id="f1", session=session)
    folder.name = "Renamed"

    folder.delete()
    assert folder.state is State.DELETE
    assert session.dirty_map[State.DELETE] == {folder}

    with raises(Exception, match="marked for delete"):
        folder.name = "Renamed again"


def test_invalidate(session: Session, store: Store):
    document = Document(document_id="d1", session=session)
    folder = document.folder
    document.title = "Changed"
    folder.name = "Changed"
    assert session.dirty_count == 2

    # invalidating discards changes of entity and associated entities
    folder.invalidate()
    assert session.dirty_count == 0
    assert not folder._model.setup_done
    assert not document._model.setup_done

    # refetched upon next access
    store.records["f1"]["name"] = "Changed externally"
    assert folder.name == "Changed externally"
    assert document.title == "Document 1"


def test_refresh(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)
    folder.name = "Changed"

    store.records["f1"]["name"] = "Changed externally"
    count = store.fetch_count

    folder.refresh()
    assert store.fetch_count == count + 1
    assert folder.state is State.CLEAN
    assert folder.name == "Changed externally"

    # refresh from model which was already retrieved
    folder._refresh_model({"id": "f1", "name": "Provided", "tags": []})
    assert store.fetch_count == count + 1
    assert folder.name == "Provided"


def test_backing_model(session: Session, store: Store):
    record = store.records["d1"].copy()

    # no fetch needed if model provided
    document = Document(document_id="d1", session=session, backing_model=record)
    assert document.title == "Document 1"
    assert store.fetch_count == 0

    # stale model is ignored
    stale = {**record, "title": "Stale", "revision": 0}
    assert Document(document_id="d1", session=session, backing_model=stale) is document
    assert document.title == "Document 1"

    # newer model is picked up
    newer = {**record, "title": "Newer", "revision": 2}
    Document(document_id="d1", session=session, backing_model=newer)
    assert document.title == "Newer"
    assert document.revision == 2


def test_nonexistent(session: Session):
    folder = Folder(folder_id="missing", session=session)

    # looked up from store, doesn't exist: becomes a create with provided id
    assert folder.name == "new folder"
    assert folder.state is State.CREATE

    session.flush()
    assert session.store.records["missing"]["name"] == "new folder"


def test_descriptors(session: Session):
    folder = Folder(folder_id="f1", session=session)
    document = Document(document_id="d1", session=session)

    # entity id
    assert isinstance(Folder.folder_id, EntityIdDescriptor)
    assert folder.folder_id == "f1"
    with raises(ReadOnlyError, match="read-only field"):
        folder.folder_id = "f2"  # type: ignore[assignment]

    # write through
    folder2 = Folder(folder_id="f2", session=session)
    document.folder = folder2
    assert document.folder is folder2
    assert document._model.get_field("folder_id", str) == "f2"
    assert document.state is State.UPDATE

    # write once
    assert document.owner is None
    with raises(ValueError):
        document.owner = None
    document.owner = "me"
    document.owner = "me"
    assert document.owner == "me"
    assert document.owner_checks == 2
    with raises(ValueError):
        document.owner = "you"


def test_str(session: Session):
    folder = Folder(folder_id="f1", session=session)
    document = Document(document_id="d1", session=session)

    assert str(folder) == repr(folder) == "Folder(entity_id='f1')"
    assert str(document) == "Document(d1)"

    # not yet fetched
    assert folder.str_summary.split("\n    ") == [
        "Folder(entity_id='f1')",
        "CLEAN",
        "{name=?}",
    ]

    folder.name = "It's renamed"
    assert folder.str_summary.split("\n    ") == [
        "Folder(entity_id='f1')",
        "UPDATE",
        "{name='Folder 1'->'It's renamed'}",
    ]

    new = Folder("New", session=session)
    assert str(new._model) == "{name='New'}"
    assert "dirty_set" in str(session._cache)
