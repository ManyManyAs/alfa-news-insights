import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def app_env(tmp_path_factory):
    """Изолированная БД + синтетические данные (data/sample), LLM в mock-режиме."""
    tmp = tmp_path_factory.mktemp("db")
    os.environ["DB_PATH"] = str(tmp / "test.db")
    os.environ["DATA_SOURCE_DIR"] = str(ROOT / "data" / "sample")
    os.environ["LLM_MODE"] = "mock"
    os.environ["ALLOW_URL_FETCH"] = "false"
    os.environ["USE_SNAPSHOT"] = "false"
    if not (ROOT / "data" / "sample" / "df_Uid_data_hack.csv").exists():
        sys.path.insert(0, str(ROOT / "backend" / "scripts"))
        import make_sample_data

        make_sample_data.main()

    from app.config import get_settings
    from app.llm.client import reset_llm
    from app.storage import db

    get_settings.cache_clear()
    db.reset_connection()
    reset_llm()
    from app.bootstrap import bootstrap

    bootstrap()
    yield
