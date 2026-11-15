import os
import sys
from typing import Generator

from pytest import fixture

# enable import of modules in test folder
sys.path.insert(0, os.path.dirname(__file__))

from framework import Session, Store


@fixture
def store() -> Store:
    store = Store()
    store.create("folder", "f1", name="Folder 1", tags=["a"])
    store.create("document", "d1", title="Document 1", folder_id="f1", revision=1)
    store.log.clear()
    return store


@fixture
def session(store: Store) -> Generator[Session, None, None]:
    session = Session(store)
    yield session
    session.deregister_default()
