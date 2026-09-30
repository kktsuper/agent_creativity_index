import os
import shutil
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="acr-test-")
os.environ.update({
    "ACR_DATABASE_URL": f"sqlite:///{TMP}/test.sqlite3",
    "ACR_DATA_DIR": TMP,
    "ACR_LLM_MODE": "mock",
    "ACR_ANCHOR_BACKEND": "local",
    "ACR_PRIORART_SOURCES": "acr",
    "ACR_ADMIN_PASSWORD": "pw",
    "ACR_EMBEDDED_WORKER": "0",
    "ACR_SECRET_KEY": "test-secret",
    "ACR_REGISTRATION_TOKEN": "",   # open registration, whatever a developer's local .env says
})


@pytest.fixture(scope="session")
def app():
    from acr.config import reset_settings_cache
    reset_settings_cache()
    from acr.main import create_app
    from acr.db import init_db
    init_db()
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TMP, ignore_errors=True)


PAPER_MD = """# Sparse witnesses for lower bounds

## Introduction
{intro}

## Method
We construct explicit witnesses by a greedy search over structured families, then verify each candidate with
an exact checker. The construction is parameterized by a seed graph and a rotation rule. We describe the search
space, the pruning rule that keeps the search tractable, and the verification procedure that certifies every
witness independently of the search.

## Results
The search finds witnesses matching the best known bounds for small parameters and a new witness for one
open case. All witnesses are attached as data and verified by the attached checker. We report running times,
the number of candidates examined, and the fraction pruned at each depth.

## Discussion
The approach is limited by the size of the structured families; we discuss extensions to other families and
the failure modes we observed, including cases where the pruning rule discards the only valid witnesses.

## References
- Prior work on explicit constructions (2019). - Classical bounds survey (2015). - ACR-2025-000001.
"""


def make_paper(title="Sparse witnesses for Ramsey lower bounds", n_words=600):
    intro = ("This paper studies explicit constructions for lower bounds in extremal combinatorics. " * 12)
    body = PAPER_MD.format(intro=intro)
    while len(body.split()) < n_words:
        body += "\nAdditional discussion of the method and its limitations follows here in detail. "
    return body
