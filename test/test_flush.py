"""
Tests unit of work: validation, dependency ordering, and flush behavior.
"""

import logging

from framework import Document, Folder, Session, Store
from pytest import LogCaptureFixture, raises

from alchemizer import State, ValidationError


def test_update(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)
    folder.name = "Renamed"

    session.flush()
    assert store.log == [("update", "f1")]
    assert store.records["f1"]["name"] == "Renamed"
    assert folder.state is State.CLEAN
    assert folder.name == "Renamed"

    # nothing left to flush
    session.flush()
    assert len(store.log) == 1


def test_create_order(session: Session, store: Store):
    # document depends on its folder, regardless of creation order
    document = Document("Doc", session=session)
    folder = Folder("New", session=session)
    document.folder = folder

    session.flush()
    assert store.log == [("create", "folder1"), ("create", "document2")]
    assert folder.folder_id == "folder1"
    assert document.document_id == "document2"
    assert store.records["document2"]["folder_id"] == "folder1"
    assert session.dirty_count == 0

    # generator-based driver resumed after entity was set up
    assert document.post_create_state == ("CLEAN", "document2")

    # entities added to identity map once id is known
    assert Folder(folder_id="folder1", session=session) is folder
    assert Document(document_id="document2", session=session) is document


def test_flush_entity(session: Session, store: Store):
    folder = Folder("New", session=session)
    document = Document("Doc", folder, session=session)
    other = Folder("Other", session=session)

    # flushes entity and its dependencies only
    document.flush()
    assert store.log == [("create", "folder1"), ("create", "document2")]
    assert session.dirty_set == {other}

    # dependencies not flushed if clean
    document.title = "Renamed"
    document.flush()
    assert store.log[2:] == [("update", "document2")]

    session.flush([other])
    assert store.log[3:] == [("create", "folder3")]
    assert session.dirty_count == 0


def test_validation(session: Session, store: Store):
    folder = Folder("", session=session)
    document = Document("Doc", session=session)
    valid = Folder("Valid", session=session)

    # errors are aggregated and nothing is flushed
    with raises(ValidationError) as exc_info:
        session.flush()

    errors = exc_info.value.errors
    assert len(errors) == 2
    assert any("Folder has no name" in e for e in errors)
    assert any("Document has no folder" in e for e in errors)
    assert "Folder has no name" in str(exc_info.value)
    assert store.log == []
    assert session.dirty_count == 3

    # dependencies are validated too
    document.folder = folder
    with raises(ValidationError) as exc_info:
        document.flush()
    assert len(exc_info.value.errors) == 1

    folder.name = "Named"
    session.flush()
    assert len(store.log) == 3
    assert valid.folder_id


def test_delete(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)
    assert folder.name

    folder.delete()
    session.flush()
    assert store.log == [("delete", "f1")]
    assert folder.state is State.CLEAN

    # removed from identity map
    assert Folder(folder_id="f1", session=session) is not folder


def test_delete_abandoned(session: Session, store: Store):
    # deleting an entity which was never created is a no-op
    folder = Folder("New", session=session)
    folder.delete()
    assert folder.state is State.DELETE

    session.flush()
    assert store.log == []
    assert session.dirty_count == 0


def test_orphan(session: Session, store: Store, caplog: LogCaptureFixture):
    # entity whose dependency was abandoned isn't flushed
    folder = Folder("New", session=session)
    document = Document("Doc", folder, session=session)
    folder.delete()

    with caplog.at_level(logging.WARNING):
        session.flush()

    assert store.log == []
    assert document.state is State.CLEAN
    assert "Orphaned entity not being flushed" in caplog.text


def test_tolerated_error(session: Session, store: Store, caplog: LogCaptureFixture):
    document = Document(document_id="d1", session=session)
    folder = document.folder
    document.title = "Renamed"

    # record implicitly deleted from store by another operation
    del store.records["d1"]

    with caplog.at_level(logging.WARNING):
        session.flush()

    assert "Flush failed" in caplog.text
    assert document.state is State.CLEAN
    assert folder.state is State.CLEAN


def test_untolerated_error(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)
    folder.name = "Renamed"

    def update(record_id: str, **fields):
        raise RuntimeError("Store unavailable")

    store.update = update  # type: ignore[method-assign]

    with raises(RuntimeError):
        session.flush()

    # flush aborted: entity remains dirty and scope didn't complete
    assert folder.state is State.UPDATE
    assert session.flush_scopes == [(1, "begin")]


def test_flush_scope(session: Session, store: Store):
    # no scope entered if nothing to flush
    session.flush()
    assert session.flush_scopes == []

    old = Folder(folder_id="f1", session=session)
    old.delete()
    new = Folder("New", session=session)
    document = Document("Doc", new, session=session)

    # scope adds constraint that deletes follow creates
    session.flush()
    assert session.flush_scopes == [(3, "begin"), (3, "end")]
    assert store.log == [
        ("create", "folder1"),
        ("create", "document2"),
        ("delete", "f1"),
    ]
    assert document.folder is new


def test_extension(session: Session, store: Store):
    folder = Folder(folder_id="f1", session=session)
    assert folder.tags == ["a"]
    assert folder._tags.setup_count == 1

    # extension state alone makes the entity dirty
    folder.tags = ["a", "b"]
    assert folder.state is State.UPDATE
    assert folder._model.extension_changed
    assert not folder._model.fields_changed

    folder.tags = ["a"]
    assert folder.state is State.CLEAN

    folder.tags = ["b"]
    session.flush()
    assert store.log == [("update", "f1")]
    assert store.records["f1"]["tags"] == ["b"]
    assert folder.tags == ["b"]
    assert folder.state is State.CLEAN

    # extension torn down along with model
    folder.tags = ["c"]
    folder.invalidate()
    assert folder.tags == ["b"]

    # extension flushed after fields upon create
    new = Folder("New", session=session)
    new.tags = ["x"]
    session.flush()
    assert store.log[1:] == [("create", "folder1"), ("update", "folder1")]
    assert store.records["folder1"] == {
        "id": "folder1",
        "kind": "folder",
        "name": "New",
        "tags": ["x"],
    }


def test_summary(session: Session):
    assert session._get_summary() == "(create/update/delete) none"

    folder = Folder(folder_id="f1", session=session)
    folder.name = "Renamed"
    Folder("New", session=session)
    document = Document(document_id="d1", session=session)
    document.delete()

    assert (
        session._get_summary() == "(create/update/delete) 0/0/1 Document, 1/1/0 Folder"
    )
    assert session._get_summary([folder]) == "(create/update/delete) 0/1/0 Folder"
