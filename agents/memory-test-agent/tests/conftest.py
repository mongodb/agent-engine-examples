"""Test configuration for memory-test-agent."""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def set_runner_mode():
    os.environ.setdefault("RUNNER_MODE", "aer")
    os.environ.setdefault("ORG_ID", "test-org")
    os.environ.setdefault("PROJECT_ID", "test-project")
    os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
