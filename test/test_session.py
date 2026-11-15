"""
Tests session lifecycle and default session registration.
"""

from framework import Folder, Session, Store
from pytest import raises

from alchemizer import BaseSession, normalize_entities


def test_default(store: Store):
    # no default registered
    assert Session._get_default() is None
    with raises(AssertionError, match="No session provided"):
        Folder(folder_id="f1")

    session = Session(store, default=True)
    assert Session._get_default() is session
    assert Folder(folder_id="f1").session is session

    # only one default at a time
    with raises(AssertionError, match="already registered"):
        Session(store, default=True)

    # non-default session doesn't interfere
    session2 = Session(store)
    session2.deregister_default()
    assert Session._get_default() is session
    assert Folder(folder_id="f1", session=session2).session is session2

    session.deregister_default()
    assert Session._get_default() is None


def test_default_scope(store: Store):
    """
    Default sessions of different frameworks, and subclasses within a framework.
    """

    class OtherSession(BaseSession):
        pass

    class SubSession(Session):
        pass

    session = SubSession(store, default=True)
    other = OtherSession(default=True)

    # subclass shares its framework's default
    assert Session._get_default() is session
    assert SubSession._get_default() is session
    assert OtherSession._get_default() is other

    session.deregister_default()
    assert Session._get_default() is None
    assert OtherSession._get_default() is other

    other.deregister_default()


def test_context(store: Store):
    with Session(store, default=True) as session:
        folder = Folder("New")
        assert not session.closed

    # flushed, deregistered, and closed
    assert store.log == [("create", "folder1")]
    assert folder.folder_id == "folder1"
    assert Session._get_default() is None
    assert session.closed


def test_context_error(store: Store):
    session = Session(store)

    with raises(RuntimeError):
        with session:
            Folder("New", session=session)
            raise RuntimeError

    # not flushed
    assert store.log == []
    assert session.dirty_count == 1
    assert not session.closed


def test_normalize_entities(session: Session):
    folder1 = Folder(folder_id="f1", session=session)
    folder2 = Folder(folder_id="f2", session=session)

    assert normalize_entities(folder1) == [folder1]
    assert normalize_entities((folder1, folder2)) == [folder1, folder2]


def test_repr(store: Store):
    session = Session(store, default=True)
    assert repr(session) == "Session(entities=0, dirty=0, default=True)"

    folder = Folder(folder_id="f1")
    folder.name = "Renamed"
    Folder("New")
    session.deregister_default()

    # entities being created aren't in identity map until flushed
    assert (
        repr(session) == str(session) == "Session(entities=1, dirty=2, default=False)"
    )
