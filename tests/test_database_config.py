from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app import config, main
from app.config import Settings
from app.database import Base
from app.models import Category, User
from app.security import hash_password


def test_settings_load_project_env_independent_of_current_directory(
    tmp_path: Path, monkeypatch
):
    env_file = tmp_path / "civictrack.env"
    env_file.write_text(
        "CIVICTRACK_DATABASE_URL=sqlite:///config-test.db\n"
        "CIVICTRACK_SECRET_KEY=test-only-secret-key-with-at-least-32-bytes\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.delenv("CIVICTRACK_DATABASE_URL", raising=False)
    monkeypatch.delenv("CIVICTRACK_SECRET_KEY", raising=False)

    settings = config.load_settings()
    parsed_url = make_url(settings.database_url)

    assert parsed_url.drivername == "sqlite"
    assert parsed_url.database == "config-test.db"
    assert settings.secret_key == "test-only-secret-key-with-at-least-32-bytes"


def test_application_loads_settings_and_creates_engine_at_startup(monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test-only-secret-key-with-at-least-32-bytes",
    )
    load_count = 0

    def load_test_settings() -> Settings:
        nonlocal load_count
        load_count += 1
        return settings

    monkeypatch.setattr(main, "load_settings", load_test_settings)
    application = main.create_app()

    assert application.state.session_factory is None
    with TestClient(application) as client:
        assert load_count == 1
        assert application.state.settings is settings
        engine_url = application.state.session_factory.kw["bind"].url
        assert engine_url.drivername == "sqlite"
        Base.metadata.create_all(application.state.session_factory.kw["bind"])
        with application.state.session_factory() as db:
            db.add(Category(slug="roads", name="Roads"))
            db.add(
                User(
                    email="admin@example.test",
                    full_name="Test Admin",
                    password_hash=hash_password("test-password-long"),
                    role="admin",
                )
            )
            db.commit()
        response = client.get("/api/categories")
        assert response.status_code == 200
        assert response.json() == [{"id": 1, "name": "Roads"}]
        login = client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.test",
                "password": "test-password-long",
            },
        )
        assert login.status_code == 200
