import hashlib
import os

import pytest


@pytest.fixture
def key():
    return os.urandom(32)


@pytest.fixture
def other_key():
    return os.urandom(32)


@pytest.fixture(scope="session")
def fixed_key():
    return hashlib.sha256(b"watermark test key").digest()
