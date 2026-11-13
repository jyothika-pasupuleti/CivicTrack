import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.database import Base
from app.main import choose_department, create_app, haversine_km
from app.models import Category, Department, Issue, User
from app.security import hash_password
from app.time_utils import UTCDateTime, utc_now


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test-only-secret-key-with-at-least-32-bytes",
        upload_dir=str(tmp_path / "uploads"),
    )
    application = create_app(settings)
    engine = application.state.session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    with application.state.session_factory() as db:
        db.add_all(
            [
                Category(slug="roads", name="Roads and potholes", base_severity=3),
                Category(slug="water", name="Water and drainage", base_severity=3),
                User(
                    email="admin@example.test",
                    full_name="Local Admin",
                    password_hash=hash_password("a-long-admin-password"),
                    role="admin",
                ),
            ]
        )
        db.commit()
    with TestClient(application) as test_client:
        yield test_client


def register_citizen(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Asha Citizen",
            "email": "asha@example.test",
            "password": "a-long-citizen-password",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_registration_complaint_tracking_and_admin_access(client: TestClient):
    landing = client.get("/")
    assert landing.status_code == 200
    assert "Better places." in landing.text
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    photo = io.BytesIO()
    Image.new("RGB", (4, 4), color="green").save(photo, format="PNG")
    photo.seek(0)

    created = client.post(
        "/api/issues",
        data={
            "title": "Pothole near the bus stop",
            "description": "There is a large pothole beside the bus stop entrance.",
            "category_id": str(category_id),
            "location_address": "Oak Street bus stop",
        },
        files={"photo": ("pothole.png", photo, "image/png")},
    )
    assert created.status_code == 201, created.text
    issue = created.json()
    assert issue["complaint_code"].startswith("CT-")
    assert issue["status"] == "submitted"
    assert issue["image_url"]
    created_at = datetime.fromisoformat(issue["created_at"])
    assert created_at.utcoffset() == timedelta(0)
    with client.app.state.session_factory() as db:
        stored_issue = (
            db.query(Issue).filter_by(complaint_code=issue["complaint_code"]).one()
        )
        history = stored_issue.events[0]
        assert stored_issue.created_at.utcoffset() == timedelta(0)
        assert history.created_at.utcoffset() == timedelta(0)
        assert isinstance(Issue.__table__.c.created_at.type, UTCDateTime)

    listed = client.get("/api/issues")
    assert listed.status_code == 200
    assert [item["complaint_code"] for item in listed.json()] == [
        issue["complaint_code"]
    ]
    image = client.get(issue["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert (
        client.patch(
            f"/api/issues/{issue['complaint_code']}",
            json={"status": "resolved"},
        ).status_code
        == 403
    )


def test_manual_address_is_saved_for_citizen_and_admin_without_coordinates(
    client: TestClient,
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    with client.app.state.session_factory() as db:
        category = db.get(Category, category_id)
        db.add_all(
            [
                Department(
                    name="North Roads Office",
                    latitude=37.8,
                    longitude=-122.4,
                    categories=[category],
                ),
                Department(
                    name="South Roads Office",
                    latitude=37.7,
                    longitude=-122.5,
                    categories=[category],
                ),
            ]
        )
        db.commit()

    created = client.post(
        "/api/issues",
        data={
            "title": "Damaged sidewalk by library",
            "description": "The sidewalk is damaged near the public library entrance.",
            "category_id": str(category_id),
            "location_address": "  12 MG Road, near City Library, Central Ward  ",
        },
    )
    assert created.status_code == 201, created.text
    issue = created.json()
    assert issue["location_address"] == "12 MG Road, near City Library, Central Ward"
    assert issue["latitude"] is None
    assert issue["longitude"] is None
    assert issue["department_id"] is None

    citizen_history = client.get("/api/issues").json()
    assert citizen_history[0]["location_address"] == issue["location_address"]

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    admin_dashboard = client.get("/api/issues").json()
    assert admin_dashboard[0]["location_address"] == issue["location_address"]


def test_address_and_coordinates_are_saved_and_visible_to_citizen_and_admin(
    client: TestClient,
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    created = client.post(
        "/api/issues",
        data={
            "title": "Broken streetlight by the park",
            "description": "The streetlight beside the neighborhood park is out.",
            "category_id": str(category_id),
            "location_address": "  5 Park Road  ",
            "latitude": "37.81",
            "longitude": "-122.41",
        },
    )

    assert created.status_code == 201, created.text
    expected_location = {
        "location_address": "5 Park Road",
        "latitude": 37.81,
        "longitude": -122.41,
    }
    assert {key: created.json()[key] for key in expected_location} == expected_location
    citizen_issue = client.get("/api/issues").json()[0]
    assert {key: citizen_issue[key] for key in expected_location} == expected_location

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    admin_issue = client.get("/api/issues").json()[0]
    assert {key: admin_issue[key] for key in expected_location} == expected_location


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(37.81, -122.41), (37.82, -122.42)],
    ids=["gps-coordinates", "map-pin-coordinates"],
)
def test_coordinate_based_location_is_preserved(
    client: TestClient, latitude: float, longitude: float
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    created = client.post(
        "/api/issues",
        data={
            "title": "Blocked drain near the market",
            "description": "The drain is blocked outside the neighborhood market.",
            "category_id": str(category_id),
            "latitude": str(latitude),
            "longitude": str(longitude),
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["latitude"] == latitude
    assert created.json()["longitude"] == longitude


def test_invalid_or_partial_location_is_rejected(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    base_data = {
        "title": "Blocked drain near market",
        "description": "The drain is blocked outside the neighborhood market.",
        "category_id": str(category_id),
    }

    too_long = client.post(
        "/api/issues",
        data={**base_data, "location_address": "A" * 501},
    )
    partial_coordinates = client.post(
        "/api/issues",
        data={**base_data, "latitude": "37.81"},
    )
    partial_coordinates_without_latitude = client.post(
        "/api/issues",
        data={**base_data, "longitude": "-122.41"},
    )
    invalid_latitude = client.post(
        "/api/issues",
        data={**base_data, "latitude": "91", "longitude": "0"},
    )
    invalid_longitude = client.post(
        "/api/issues",
        data={**base_data, "latitude": "0", "longitude": "181"},
    )

    assert too_long.status_code == 422
    assert partial_coordinates.status_code == 422
    assert partial_coordinates_without_latitude.status_code == 422
    assert invalid_latitude.status_code == 422
    assert invalid_longitude.status_code == 422


def test_new_complaint_requires_an_address_or_coordinates(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    base_data = {
        "title": "Blocked drain near market",
        "description": "The drain is blocked outside the neighborhood market.",
        "category_id": str(category_id),
    }

    missing_location = client.post("/api/issues", data=base_data)
    blank_address = client.post(
        "/api/issues", data={**base_data, "location_address": "   "}
    )

    assert missing_location.status_code == 422
    assert blank_address.status_code == 422
    assert missing_location.json()["detail"] == {
        "field": "location",
        "message": "Please provide an address or select a location on the map.",
    }


@pytest.mark.parametrize("description", ["", "   "], ids=["empty", "whitespace"])
def test_complaint_description_is_validated_independently(
    client: TestClient, description: str
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    response = client.post(
        "/api/issues",
        data={
            "title": "Blocked drain near market",
            "description": description,
            "category_id": str(category_id),
            "location_address": "Neighborhood market",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "field": "description",
        "message": "Please enter a complaint description.",
    }


def test_missing_location_error_is_independent_of_valid_description(
    client: TestClient,
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    response = client.post(
        "/api/issues",
        data={
            "title": "Blocked drain near market",
            "description": "The drain is blocked outside the neighborhood market.",
            "category_id": str(category_id),
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "location"
    assert response.json()["detail"]["message"] == (
        "Please provide an address or select a location on the map."
    )


def test_existing_complaint_without_location_remains_readable(client: TestClient):
    citizen = register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    with client.app.state.session_factory() as db:
        db.add(
            Issue(
                complaint_code="CT-2020-LEGACY",
                reporter_id=citizen["id"],
                category_id=category_id,
                title="Legacy complaint without location",
                description="This historical record has no saved location.",
                location_address=None,
                latitude=None,
                longitude=None,
            )
        )
        db.commit()

    legacy_issue = client.get("/api/issues").json()[0]
    assert legacy_issue["complaint_code"] == "CT-2020-LEGACY"
    assert legacy_issue["location_address"] is None
    assert legacy_issue["latitude"] is None
    assert legacy_issue["longitude"] is None


def test_admin_can_assign_and_update_complaint(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    created = client.post(
        "/api/issues",
        data={
            "title": "Blocked drainage channel",
            "description": "Water cannot flow through the drain near the crossing.",
            "category_id": str(category_id),
            "location_address": "Drainage channel by the crossing",
        },
    )
    assert created.status_code == 201, created.text

    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    assert login.status_code == 200
    department = client.post("/api/departments", json={"name": "Public Works"})
    assert department.status_code == 201, department.text

    updated = client.patch(
        f"/api/issues/{created.json()['complaint_code']}",
        json={
            "status": "in_progress",
            "assigned_department_id": department.json()["id"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "in_progress"
    assert updated.json()["department_name"] == "Public Works"
    unassigned = client.patch(
        f"/api/issues/{created.json()['complaint_code']}",
        json={"assigned_department_id": None},
    )
    assert unassigned.status_code == 200
    assert unassigned.json()["department_id"] is None
    assert len(client.get("/api/issues").json()) == 1


def test_admin_can_correct_department_name_without_changing_assignments(
    client: TestClient,
):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    department = client.post(
        "/api/departments",
        json={
            "name": "  Department name Categories to handle 37.4850 78.4250  ",
            "latitude": 37.485,
            "longitude": 78.425,
            "category_ids": [category_id],
        },
    )
    assert department.status_code == 201, department.text
    department_id = department.json()["id"]
    assert department.json()["name"] == (
        "Department name Categories to handle 37.4850 78.4250"
    )

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": "asha@example.test", "password": "a-long-citizen-password"},
    )
    issue = client.post(
        "/api/issues",
        data={
            "title": "Blocked drain by the market",
            "description": "The drain is blocked outside the neighborhood market.",
            "category_id": str(category_id),
            "location_address": "Neighborhood market",
        },
    )
    assert issue.status_code == 201, issue.text
    assert issue.json()["department_id"] == department_id

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    renamed = client.patch(
        f"/api/departments/{department_id}",
        json={"name": "Central Public Works"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {"id": department_id, "name": "Central Public Works"}

    department_data = client.get("/api/departments").json()[0]
    assert department_data["name"] == "Central Public Works"
    assert department_data["latitude"] == 37.485
    assert department_data["longitude"] == 78.425
    assert department_data["category_ids"] == [category_id]

    updated_issue = client.get("/api/issues").json()[0]
    assert updated_issue["department_id"] == department_id
    assert updated_issue["department_name"] == "Central Public Works"
    analytics = client.get("/api/admin/analytics").json()
    assert {"name": "Central Public Works", "count": 1} in analytics["by_department"]
    assert not any(
        "Categories" in row["name"] or "37.485" in row["name"]
        for row in analytics["by_department"]
    )


def test_registration_validates_email_and_password(client: TestClient):
    invalid_email = client.post(
        "/api/auth/register",
        json={
            "full_name": "Valid Name",
            "email": "not-an-email",
            "password": "long-password",
        },
    )
    assert invalid_email.status_code == 422
    short_password = client.post(
        "/api/auth/register",
        json={
            "full_name": "Valid Name",
            "email": "valid@example.test",
            "password": "short",
        },
    )
    assert short_password.status_code == 422


def test_admin_and_citizen_login_flows_have_separate_permissions(client: TestClient):
    citizen = register_citizen(client)
    citizen_me = client.get("/api/auth/me")
    assert citizen_me.status_code == 200
    assert citizen_me.json()["role"] == "citizen"
    assert client.get("/api/admin/users").status_code == 403

    client.post("/api/auth/logout")
    admin_login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-long-admin-password"},
    )
    assert admin_login.status_code == 200
    assert admin_login.json()["role"] == "admin"
    admin_me = client.get("/api/auth/me")
    assert admin_me.status_code == 200
    assert admin_me.json()["role"] == "admin"
    admin_users = client.get("/api/admin/users")
    assert admin_users.status_code == 200
    assert {
        "id",
        "full_name",
        "email",
        "role",
        "active",
        "created_at",
    }.issubset(admin_users.json()[0])
    assert "password_hash" not in admin_users.json()[0]

    client.post("/api/auth/logout")
    citizen_login = client.post(
        "/api/auth/login",
        json={
            "email": "asha@example.test",
            "password": "a-long-citizen-password",
        },
    )
    assert citizen_login.status_code == 200
    assert citizen_login.json()["id"] == citizen["id"]
    assert citizen_login.json()["role"] == "citizen"
    assert client.get("/api/admin/users").status_code == 403


def test_legacy_naive_utc_values_round_trip_as_aware_utc(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    response = client.post(
        "/api/issues",
        data={
            "title": "Streetlight near the station",
            "description": "The streetlight beside the station is not turning on.",
            "category_id": str(category_id),
            "location_address": "Station entrance",
        },
    )
    complaint_code = response.json()["complaint_code"]
    legacy_timestamp = datetime(2020, 1, 2, 3, 4, 5)
    with client.app.state.session_factory() as db:
        issue = db.query(Issue).filter_by(complaint_code=complaint_code).one()
        issue.created_at = legacy_timestamp
        db.commit()
    with client.app.state.session_factory() as db:
        issue = db.query(Issue).filter_by(complaint_code=complaint_code).one()
        assert issue.created_at == legacy_timestamp.replace(tzinfo=UTC)


def test_submission_notifications_and_nearest_category_department(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]

    client.post(
        "/api/auth/logout",
    )
    assert (
        client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.test",
                "password": "a-long-admin-password",
            },
        ).status_code
        == 200
    )
    farther = client.post(
        "/api/departments",
        json={
            "name": "Far Roads Office",
            "latitude": 38.0,
            "longitude": -122.0,
            "category_ids": [category_id],
        },
    )
    nearer = client.post(
        "/api/departments",
        json={
            "name": "Near Roads Office",
            "latitude": 37.8,
            "longitude": -122.4,
            "category_ids": [category_id],
        },
    )
    assert farther.status_code == nearer.status_code == 201

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "asha@example.test",
            "password": "a-long-citizen-password",
        },
    )
    issue = client.post(
        "/api/issues",
        data={
            "title": "Damaged sidewalk near school",
            "description": "The sidewalk is damaged on the way to the local school.",
            "category_id": str(category_id),
            "latitude": "37.81",
            "longitude": "-122.41",
        },
    )
    assert issue.status_code == 201, issue.text
    assert issue.json()["department_id"] == nearer.json()["id"]

    notifications = client.get("/api/notifications")
    assert notifications.status_code == 200
    assert notifications.json()["unread_count"] == 2
    messages = [item["message"] for item in notifications.json()["items"]]
    assert any("was submitted" in message for message in messages)
    assert any("was assigned" in message for message in messages)
    notification_id = notifications.json()["items"][0]["id"]
    assert client.patch(
        f"/api/notifications/{notification_id}/read", json={"read": True}
    ).json()["read"]
    assert client.get("/api/notifications").json()["unread_count"] == 1


def test_department_service_radius_prevents_out_of_range_assignment():
    department = Department(
        id=1,
        name="Nearby only",
        latitude=37.8,
        longitude=-122.4,
        service_radius_km=1,
    )
    assert choose_department([department], 40.0, -122.4) is None


def test_status_change_notifications_are_private(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    issue = client.post(
        "/api/issues",
        data={
            "title": "Overflowing public bin",
            "description": "The public bin beside the library is overflowing.",
            "category_id": str(category_id),
            "location_address": "Public library",
        },
    ).json()
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.test",
            "password": "a-long-admin-password",
        },
    )
    response = client.patch(
        f"/api/issues/{issue['complaint_code']}", json={"status": "resolved"}
    )
    assert response.status_code == 200
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "asha@example.test",
            "password": "a-long-citizen-password",
        },
    )
    notifications = client.get("/api/notifications").json()
    assert notifications["unread_count"] == 2
    assert any("was resolved" in item["message"] for item in notifications["items"])

    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.195, rel=0.001)
    client.post("/api/auth/logout")
    assert client.get("/api/notifications").status_code == 401


def test_sla_policy_priority_overdue_and_analytics_use_database(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    first_issue = client.post(
        "/api/issues",
        data={
            "title": "Damaged crossing signal",
            "description": "The crossing signal is not working near the library.",
            "category_id": str(category_id),
            "location_address": "Library crossing",
        },
    ).json()
    second_issue = client.post(
        "/api/issues",
        data={
            "title": "Overflowing neighborhood bin",
            "description": "The neighborhood bin has been full for several days.",
            "category_id": str(category_id),
            "location_address": "Neighborhood park entrance",
        },
    ).json()

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.test",
            "password": "a-long-admin-password",
        },
    )
    assert client.get("/api/admin/analytics").status_code == 200
    policy = client.put(
        "/api/admin/sla-policies",
        json={
            "category_id": category_id,
            "severity_min": 3,
            "response_minutes": 60,
            "resolution_minutes": 120,
        },
    )
    assert policy.status_code == 200, policy.text
    prioritized = client.patch(
        f"/api/issues/{first_issue['complaint_code']}",
        json={"severity": 3},
    )
    assert prioritized.status_code == 200
    response_due = datetime.fromisoformat(prioritized.json()["sla_response_due_at"])
    resolution_due = datetime.fromisoformat(prioritized.json()["sla_resolution_due_at"])
    assert response_due.utcoffset() == timedelta(0)
    assert resolution_due.utcoffset() == timedelta(0)
    assert resolution_due - response_due == timedelta(minutes=60)
    assert abs((response_due - utc_now()) - timedelta(minutes=60)) < timedelta(
        seconds=2
    )

    with client.app.state.session_factory() as db:
        issue = (
            db.query(Issue)
            .filter_by(complaint_code=first_issue["complaint_code"])
            .one()
        )
        issue.sla_response_due_at = datetime.now(UTC) - timedelta(minutes=2)
        issue.sla_resolution_due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    issue_list = client.get("/api/issues").json()
    overdue_issue = next(
        item
        for item in issue_list
        if item["complaint_code"] == first_issue["complaint_code"]
    )
    assert overdue_issue["overdue"] is True
    assert overdue_issue["response_overdue"] is True

    assert (
        client.patch(
            f"/api/issues/{second_issue['complaint_code']}",
            json={"status": "rejected"},
        ).status_code
        == 200
    )
    analytics = client.get("/api/admin/analytics")
    assert analytics.status_code == 200
    data = analytics.json()
    assert data["total"] == 2
    assert data["pending"] == 1
    assert data["rejected"] == 1
    assert data["overdue"] == 1
    assert data["by_category"] == [
        {"name": "Roads and potholes", "count": 2},
        {"name": "Water and drainage", "count": 0},
    ]
    assert data["by_department"] == [{"name": "Unassigned", "count": 2}]

    assert (
        client.patch(
            f"/api/issues/{first_issue['complaint_code']}",
            json={"status": "resolved"},
        ).status_code
        == 200
    )
    resolved_analytics = client.get("/api/admin/analytics").json()
    assert resolved_analytics["resolved"] == 1
    assert resolved_analytics["rejected"] == 1
    assert resolved_analytics["pending"] == 0
    assert resolved_analytics["overdue"] == 0
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "asha@example.test",
            "password": "a-long-citizen-password",
        },
    )
    messages = [
        item["message"] for item in client.get("/api/notifications").json()["items"]
    ]
    assert any("was resolved" in message for message in messages)
    assert any("was rejected" in message for message in messages)


def test_keyword_suggestions_are_explainable_and_not_claimed_as_ml(client: TestClient):
    register_citizen(client)
    response = client.post(
        "/api/category-suggestions",
        json={
            "title": "Pothole on the road",
            "description": "A pothole and cracked road near the crossing.",
        },
    )
    assert response.status_code == 200
    suggestion = response.json()
    assert suggestion["method"] == "transparent_keyword_rules"
    assert "not a trained ML prediction" in suggestion["message"]
    assert suggestion["suggestions"][0]["category"] == "Roads and potholes"
    assert suggestion["suggestions"][0]["matched_terms"] == [
        "crossing",
        "pothole",
        "road",
    ]

    no_match = client.post(
        "/api/category-suggestions",
        json={
            "title": "Something unusual",
            "description": "There are no known civic issue terms in this report.",
        },
    )
    assert no_match.status_code == 200
    assert no_match.json()["suggestions"] == []


def test_admin_verified_category_labels_are_saved(client: TestClient):
    register_citizen(client)
    category_id = client.get("/api/categories").json()[0]["id"]
    issue = client.post(
        "/api/issues",
        data={
            "title": "Pothole by the pedestrian crossing",
            "description": "The road has a pothole beside the pedestrian crossing.",
            "category_id": str(category_id),
            "location_address": "Pedestrian crossing",
        },
    ).json()
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.test",
            "password": "a-long-admin-password",
        },
    )
    categories = client.get("/api/categories").json()
    water_category = next(
        item for item in categories if item["name"] == "Water and drainage"
    )
    verified = client.patch(
        f"/api/issues/{issue['complaint_code']}",
        json={"category_id": water_category["id"], "note": "Reviewed by staff"},
    )
    assert verified.status_code == 200
    assert verified.json()["category"] == "Water and drainage"
    assert verified.json()["category_source"] == "admin_verified"

    labels = client.get("/api/admin/verified-labels")
    assert labels.status_code == 200
    assert len(labels.json()) == 1
    assert labels.json()[0]["title"] == "Pothole by the pedestrian crossing"
    assert labels.json()[0]["category"] == "Water and drainage"

    client.post("/api/auth/logout")
    assert client.get("/api/admin/verified-labels").status_code == 401
