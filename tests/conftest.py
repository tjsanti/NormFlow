import pytest

import normflow.mapping_service


@pytest.fixture(autouse=True)
def _reset_model():
    normflow.mapping_service._MODEL = None
