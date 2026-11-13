import io
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, load_settings
from app.database import build_session_factory, get_db
from app.dependencies import get_current_user, require_admin
from app.models import (
    Category,
    Department,
    DepartmentCategory,
    Issue,
    IssueEvent,
    IssueImage,
    Notification,
    SlaPolicy,
    User,
    VerifiedCategoryLabel,
)
from app.schemas import (
    CategorySuggestionRequest,
    DepartmentCreate,
    DepartmentRename,
    IssueLocation,
    IssueUpdate,
    LoginRequest,
    NotificationRead,
    RegisterRequest,
    SlaPolicyUpsert,
    UserResponse,
)
from app.security import create_access_token, hash_password, verify_password
from app.time_utils import as_utc, utc_isoformat, utc_now

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_STATUSES = {
    "submitted",
    "under_review",
    "in_progress",
    "resolved",
    "closed",
    "rejected",
}
STATUS_LABELS = {
    "submitted": "Submitted",
    "under_review": "Under review",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed": "Closed",
    "rejected": "Rejected",
}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, value)))


def choose_department(
    departments: list[Department], latitude: float | None, longitude: float | None
) -> Department | None:
    if not departments:
        return None
    if latitude is None or longitude is None:
        return departments[0] if len(departments) == 1 else None

    candidates: list[tuple[float, Department]] = []
    for department in departments:
        if department.latitude is None or department.longitude is None:
            continue
        distance = haversine_km(
            latitude, longitude, department.latitude, department.longitude
        )
        if (
            department.service_radius_km is None
            or distance <= department.service_radius_km
        ):
            candidates.append((distance, department))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    has_department_coordinates = any(
        department.latitude is not None and department.longitude is not None
        for department in departments
    )
    if has_department_coordinates:
        return None
    return departments[0] if len(departments) == 1 else None


def create_notification(db: Session, user_id: int, issue_id: int, message: str) -> None:
    db.add(Notification(user_id=user_id, issue_id=issue_id, message=message))


def matching_sla_policy(
    db: Session, category_id: int, severity: int
) -> SlaPolicy | None:
    return db.scalar(
        select(SlaPolicy)
        .where(
            SlaPolicy.category_id == category_id,
            SlaPolicy.severity_min <= severity,
        )
        .order_by(SlaPolicy.severity_min.desc())
        .limit(1)
    )


def apply_sla_deadlines(
    db: Session, issue: Issue, *, starts_at: datetime | None = None
) -> None:
    policy = matching_sla_policy(db, issue.category_id, issue.severity)
    if not policy:
        issue.sla_response_due_at = None
        issue.sla_resolution_due_at = None
        return
    deadline_start = as_utc(starts_at or issue.created_at or utc_now())

    issue.sla_response_due_at = deadline_start + timedelta(
        minutes=policy.response_minutes
    )
    issue.sla_resolution_due_at = deadline_start + timedelta(
        minutes=policy.resolution_minutes
    )


def issue_overdue_state(issue: Issue, now: datetime) -> dict[str, bool]:
    now = as_utc(now)
    response_due_at = (
        as_utc(issue.sla_response_due_at) if issue.sla_response_due_at else None
    )
    resolution_due_at = (
        as_utc(issue.sla_resolution_due_at) if issue.sla_resolution_due_at else None
    )
    terminal = issue.status in {"resolved", "closed", "rejected"}
    response_overdue = (
        issue.status == "submitted"
        and response_due_at is not None
        and response_due_at < now
    )
    resolution_overdue = (
        not terminal and resolution_due_at is not None and resolution_due_at < now
    )
    return {
        "response_overdue": response_overdue,
        "resolution_overdue": resolution_overdue,
        "overdue": response_overdue or resolution_overdue,
    }


def issue_payload(issue: Issue) -> dict[str, object]:
    now = utc_now()
    overdue_state = issue_overdue_state(issue, now)
    return {
        "complaint_code": issue.complaint_code,
        "title": issue.title,
        "description": issue.description,
        "category": issue.category.name,
        "category_id": issue.category_id,
        "category_source": issue.category_source,
        "status": issue.status,
        "status_label": STATUS_LABELS.get(issue.status, issue.status),
        "severity": issue.severity,
        "sla_response_due_at": utc_isoformat(issue.sla_response_due_at)
        if issue.sla_response_due_at
        else None,
        "sla_resolution_due_at": utc_isoformat(issue.sla_resolution_due_at)
        if issue.sla_resolution_due_at
        else None,
        **overdue_state,
        "created_at": utc_isoformat(issue.created_at),
        "department_id": issue.assigned_department_id,
        "department_name": issue.assigned_department.name
        if issue.assigned_department
        else None,
        "location_address": issue.location_address,
        "latitude": issue.latitude,
        "longitude": issue.longitude,
        "reporter_name": issue.reporter.full_name,
        "image_url": f"/api/issues/{issue.complaint_code}/image"
        if issue.image
        else None,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings
    session_factory = build_session_factory(settings.database_url) if settings else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal app_settings, session_factory
        if app_settings is None:
            app_settings = load_settings()
            session_factory = build_session_factory(app_settings.database_url)
            app.title = app_settings.app_name
        if not app_settings.secret_key:
            raise RuntimeError(
                "CIVICTRACK_SECRET_KEY is required. Copy .env.example to .env and set a random key."
            )
        app.state.settings = app_settings
        app.state.session_factory = session_factory
        yield
        if session_factory is not None:
            session_factory.kw["bind"].dispose()

    app = FastAPI(
        title=app_settings.app_name if app_settings else "CivicTrack",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

    @app.get("/")
    def landing_page() -> FileResponse:
        return FileResponse(ROOT / "frontend" / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/categories")
    def list_categories(db: Session = Depends(get_db)) -> list[dict[str, object]]:
        categories = db.scalars(
            select(Category).where(Category.active.is_(True)).order_by(Category.name)
        ).all()
        return [{"id": item.id, "name": item.name} for item in categories]

    @app.post("/api/category-suggestions")
    def suggest_category(
        body: CategorySuggestionRequest,
        _: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        rules = {
            "roads": {
                "pothole",
                "road",
                "sidewalk",
                "footpath",
                "street",
                "crack",
                "crossing",
            },
            "streetlights": {
                "streetlight",
                "streetlights",
                "lamp",
                "lighting",
                "lightpole",
                "dark",
            },
            "waste": {
                "garbage",
                "waste",
                "litter",
                "bin",
                "trash",
                "rubbish",
                "sanitation",
            },
            "water": {
                "water",
                "drain",
                "drainage",
                "sewer",
                "pipe",
                "leak",
                "flood",
            },
            "parks": {
                "park",
                "garden",
                "playground",
                "tree",
                "bench",
                "greenery",
            },
        }
        words = set(
            re.findall(r"[a-z0-9]+", f"{body.title} {body.description}".lower())
        )
        categories = db.scalars(
            select(Category).where(Category.active.is_(True)).order_by(Category.name)
        ).all()
        suggestions = []
        for category in categories:
            category_rules = rules.get(category.slug, set())
            matched_terms = sorted(words & category_rules)
            if matched_terms:
                suggestions.append(
                    {
                        "category_id": category.id,
                        "category": category.name,
                        "matched_terms": matched_terms,
                    }
                )
        suggestions.sort(
            key=lambda suggestion: (
                -len(suggestion["matched_terms"]),
                str(suggestion["category"]),
            )
        )
        return {
            "method": "transparent_keyword_rules",
            "message": (
                "Suggestions are simple keyword matches, not a trained ML prediction. "
                "Please review and choose the category yourself."
            ),
            "suggestions": suggestions,
        }

    @app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
    def register(
        body: RegisterRequest,
        request: Request,
        response: Response,
        db: Session = Depends(get_db),
    ) -> UserResponse:
        email = body.email.strip().lower()
        full_name = body.full_name.strip()
        if not EMAIL_PATTERN.fullmatch(email):
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        if len(full_name) < 2:
            raise HTTPException(status_code=422, detail="Enter your full name.")
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(
                status_code=409, detail="An account with this email already exists."
            )

        user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(body.password),
            role="citizen",
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="An account with this email already exists."
            ) from exc
        db.refresh(user)
        set_session_cookie(response, request, user.id)
        return user

    @app.post("/api/auth/login")
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        db: Session = Depends(get_db),
    ) -> UserResponse:
        email = body.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if (
            not user
            or not user.active
            or not verify_password(body.password, user.password_hash)
        ):
            raise HTTPException(
                status_code=401, detail="Email or password is incorrect."
            )
        set_session_cookie(response, request, user.id)
        return user

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(response: Response) -> Response:
        response.delete_cookie(
            "civictrack_session",
            httponly=True,
            secure=app_settings.cookie_secure,
            samesite="lax",
            path="/",
        )
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/api/auth/me")
    def current_user(user: User = Depends(get_current_user)) -> UserResponse:
        return user

    @app.get("/api/departments")
    def list_departments(
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        departments = db.scalars(select(Department).order_by(Department.name)).all()
        return [
            {
                "id": item.id,
                "name": item.name,
                "active": item.active,
                "latitude": item.latitude,
                "longitude": item.longitude,
                "category_ids": [
                    category_id
                    for category_id in db.scalars(
                        select(DepartmentCategory.category_id).where(
                            DepartmentCategory.department_id == item.id
                        )
                    )
                ],
            }
            for item in departments
        ]

    @app.get("/api/admin/users")
    def list_admin_users(
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        users = db.scalars(select(User).order_by(User.created_at.desc(), User.id)).all()
        return [
            {
                "id": user.id,
                "full_name": user.full_name,
                "email": user.email,
                "role": user.role,
                "active": user.active,
                "created_at": utc_isoformat(user.created_at),
            }
            for user in users
        ]

    @app.post("/api/departments", status_code=status.HTTP_201_CREATED)
    def create_department(
        body: DepartmentCreate,
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        category_ids = set(body.category_ids)
        categories = (
            db.scalars(select(Category).where(Category.id.in_(category_ids))).all()
            if category_ids
            else []
        )
        if len(categories) != len(category_ids) or any(
            not category.active for category in categories
        ):
            raise HTTPException(
                status_code=422, detail="Choose active issue categories."
            )

        department = Department(
            name=body.name.strip(),
            latitude=body.latitude,
            longitude=body.longitude,
            categories=categories,
        )
        db.add(department)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="A department with this name already exists."
            ) from exc
        db.refresh(department)
        return {
            "id": department.id,
            "name": department.name,
            "active": department.active,
            "latitude": department.latitude,
            "longitude": department.longitude,
            "category_ids": [category.id for category in department.categories],
        }

    @app.patch("/api/departments/{department_id}")
    def rename_department(
        department_id: int,
        body: DepartmentRename,
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        department = db.get(Department, department_id)
        if not department:
            raise HTTPException(status_code=404, detail="Department not found.")
        department.name = body.name
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="A department with this name already exists."
            ) from exc
        db.refresh(department)
        return {"id": department.id, "name": department.name}

    @app.post("/api/issues", status_code=status.HTTP_201_CREATED)
    async def create_issue(
        title: Annotated[str, Form(min_length=5, max_length=140)],
        category_id: Annotated[int, Form(gt=0)],
        description: Annotated[str | None, Form(max_length=5000)] = None,
        location_address: Annotated[str | None, Form(max_length=500)] = None,
        latitude: Annotated[float | None, Form(ge=-90, le=90)] = None,
        longitude: Annotated[float | None, Form(ge=-180, le=180)] = None,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        photo: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, object]:
        if user.role != "citizen":
            raise HTTPException(
                status_code=403, detail="Only citizen accounts can submit complaints."
            )
        category = db.get(Category, category_id)
        if not category or not category.active:
            raise HTTPException(
                status_code=422, detail="Choose a valid issue category."
            )
        normalized_description = (description or "").strip()
        if not normalized_description:
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "description",
                    "message": "Please enter a complaint description.",
                },
            )
        if len(normalized_description) < 10:
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "description",
                    "message": "Please provide a little more detail.",
                },
            )
        try:
            location = IssueLocation(
                address=location_address,
                latitude=latitude,
                longitude=longitude,
            )
        except ValidationError as exc:
            message = (
                "Provide both latitude and longitude, or leave both blank."
                if (latitude is None) != (longitude is None)
                else "Please provide an address or select a location on the map."
            )
            raise HTTPException(
                status_code=422,
                detail={"field": "location", "message": message},
            ) from exc

        issue = Issue(
            complaint_code=f"CT-{datetime.now(UTC).year}-{secrets.token_hex(5).upper()}",
            reporter_id=user.id,
            category_id=category.id,
            title=title.strip(),
            description=normalized_description,
            location_address=location.address,
            latitude=location.latitude,
            longitude=location.longitude,
            severity=category.base_severity,
            severity_reasons=["Default category priority"],
            category_source="citizen",
        )
        if len(issue.title) < 5:
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "title",
                    "message": "Please provide a little more detail.",
                },
            )

        image_record: IssueImage | None = None
        stored_path: Path | None = None
        if photo and photo.filename:
            payload = await photo.read(app_settings.max_upload_bytes + 1)
            if len(payload) > app_settings.max_upload_bytes:
                raise HTTPException(
                    status_code=413, detail="Photo must be 5 MB or smaller."
                )
            if payload:
                try:
                    with Image.open(io.BytesIO(payload)) as source_image:
                        if source_image.format not in {"JPEG", "PNG", "WEBP"}:
                            raise HTTPException(
                                status_code=415,
                                detail="Use a JPEG, PNG, or WebP photo.",
                            )
                        width, height = source_image.size
                        if width < 1 or height < 1 or width * height > 20_000_000:
                            raise HTTPException(
                                status_code=413,
                                detail="Photo dimensions are too large.",
                            )
                        source_image.verify()
                    with Image.open(io.BytesIO(payload)) as source_image:
                        safe_image = ImageOps.exif_transpose(source_image).convert(
                            "RGB"
                        )
                        buffer = io.BytesIO()
                        safe_image.save(
                            buffer, format="JPEG", quality=88, optimize=True
                        )
                    key = f"{issue.complaint_code}.jpg"
                    upload_dir = Path(app_settings.upload_dir)
                    if not upload_dir.is_absolute():
                        upload_dir = ROOT / upload_dir
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    stored_path = upload_dir / key
                    stored_path.write_bytes(buffer.getvalue())
                    width, height = safe_image.size
                    image_record = IssueImage(
                        storage_key=key,
                        media_type="image/jpeg",
                        size_bytes=stored_path.stat().st_size,
                        width=width,
                        height=height,
                    )
                except HTTPException:
                    raise
                except (
                    UnidentifiedImageError,
                    OSError,
                    ValueError,
                    Image.DecompressionBombError,
                ) as exc:
                    raise HTTPException(
                        status_code=415,
                        detail="The uploaded file is not a valid image.",
                    ) from exc

        try:
            db.add(issue)
            db.flush()
            if image_record:
                issue.image = image_record
            db.add(
                IssueEvent(
                    issue_id=issue.id,
                    actor_id=user.id,
                    event_type="created",
                    to_value="submitted",
                    note="Complaint submitted",
                )
            )
            apply_sla_deadlines(db, issue)
            eligible_departments = db.scalars(
                select(Department)
                .join(
                    DepartmentCategory,
                    DepartmentCategory.department_id == Department.id,
                )
                .where(
                    Department.active.is_(True),
                    DepartmentCategory.category_id == category.id,
                )
                .order_by(Department.id)
            ).all()
            assigned_department = choose_department(
                eligible_departments, issue.latitude, issue.longitude
            )
            if assigned_department:
                issue.assigned_department_id = assigned_department.id
                db.add(
                    IssueEvent(
                        issue_id=issue.id,
                        actor_id=user.id,
                        event_type="assignment",
                        from_value=None,
                        to_value=str(assigned_department.id),
                        note="Automatically assigned by category and distance.",
                    )
                )

            create_notification(
                db,
                user.id,
                issue.id,
                f"Complaint {issue.complaint_code} was submitted.",
            )
            if assigned_department:
                create_notification(
                    db,
                    user.id,
                    issue.id,
                    f"Complaint {issue.complaint_code} was assigned to {assigned_department.name}.",
                )
            admin_ids = db.scalars(
                select(User.id).where(User.role == "admin", User.active.is_(True))
            ).all()
            for admin_id in admin_ids:
                create_notification(
                    db,
                    admin_id,
                    issue.id,
                    f"New complaint {issue.complaint_code} was submitted.",
                )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            if stored_path:
                stored_path.unlink(missing_ok=True)
            raise
        db.refresh(issue)
        return issue_payload(issue)

    @app.get("/api/issues")
    def list_issues(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        query = select(Issue).order_by(Issue.created_at.desc())
        if user.role != "admin":
            query = query.where(Issue.reporter_id == user.id)
        return [issue_payload(item) for item in db.scalars(query).all()]

    @app.patch("/api/issues/{complaint_code}")
    def update_issue(
        complaint_code: str,
        body: IssueUpdate,
        admin: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        issue = db.scalar(select(Issue).where(Issue.complaint_code == complaint_code))
        if not issue:
            raise HTTPException(status_code=404, detail="Complaint not found.")
        assignment_was_sent = "assigned_department_id" in body.model_fields_set
        category_was_sent = "category_id" in body.model_fields_set
        severity_was_sent = "severity" in body.model_fields_set
        if (
            body.status is None
            and not assignment_was_sent
            and not category_was_sent
            and not severity_was_sent
        ):
            raise HTTPException(
                status_code=422, detail="Choose a status or department update."
            )
        if body.status is not None:
            if body.status not in ALLOWED_STATUSES:
                raise HTTPException(
                    status_code=422, detail="Choose a valid complaint status."
                )
            previous = issue.status
            if previous != body.status:
                issue.status = body.status
                db.add(
                    IssueEvent(
                        issue_id=issue.id,
                        actor_id=admin.id,
                        event_type="status",
                        from_value=previous,
                        to_value=body.status,
                        note=body.note,
                    )
                )
                if body.status in {"resolved", "rejected"}:
                    message = f"Complaint {issue.complaint_code} was {body.status}."
                else:
                    message = (
                        f"Complaint {issue.complaint_code} status changed to "
                        f"{STATUS_LABELS[body.status]}."
                    )
                create_notification(db, issue.reporter_id, issue.id, message)
        if assignment_was_sent:
            department = (
                db.get(Department, body.assigned_department_id)
                if body.assigned_department_id is not None
                else None
            )
            if body.assigned_department_id is not None and (
                not department or not department.active
            ):
                raise HTTPException(
                    status_code=422, detail="Choose an active department."
                )
            previous_department = issue.assigned_department_id
            next_department_id = department.id if department else None
            if previous_department != next_department_id:
                issue.assigned_department_id = next_department_id
                db.add(
                    IssueEvent(
                        issue_id=issue.id,
                        actor_id=admin.id,
                        event_type="assignment",
                        from_value=str(previous_department)
                        if previous_department
                        else None,
                        to_value=str(next_department_id)
                        if next_department_id
                        else None,
                        note=body.note,
                    )
                )
                assignment_message = (
                    f"Complaint {issue.complaint_code} was assigned to {department.name}."
                    if department
                    else f"Complaint {issue.complaint_code} is no longer assigned to a department."
                )
                create_notification(db, issue.reporter_id, issue.id, assignment_message)
        if category_was_sent:
            if body.category_id is None:
                raise HTTPException(
                    status_code=422, detail="Choose a valid issue category."
                )
            category = db.get(Category, body.category_id)
            if not category or not category.active:
                raise HTTPException(
                    status_code=422, detail="Choose an active issue category."
                )
            previous_category_id = issue.category_id
            issue.category_id = category.id
            issue.category_source = "admin_verified"
            apply_sla_deadlines(db, issue, starts_at=utc_now())
            db.add(
                VerifiedCategoryLabel(
                    issue_id=issue.id,
                    category_id=category.id,
                    verified_by_id=admin.id,
                    title_snapshot=issue.title,
                    description_snapshot=issue.description,
                )
            )
            db.add(
                IssueEvent(
                    issue_id=issue.id,
                    actor_id=admin.id,
                    event_type="category_verified",
                    from_value=str(previous_category_id),
                    to_value=str(category.id),
                    note=body.note,
                )
            )
            create_notification(
                db,
                issue.reporter_id,
                issue.id,
                f"The category for complaint {issue.complaint_code} was confirmed as {category.name}.",
            )
        if severity_was_sent:
            if body.severity is None:
                raise HTTPException(
                    status_code=422, detail="Choose a priority from 1 to 5."
                )
            previous_severity = issue.severity
            issue.severity = body.severity
            apply_sla_deadlines(db, issue, starts_at=utc_now())
            db.add(
                IssueEvent(
                    issue_id=issue.id,
                    actor_id=admin.id,
                    event_type="priority",
                    from_value=str(previous_severity),
                    to_value=str(body.severity),
                    note=body.note,
                )
            )
        db.commit()
        db.refresh(issue)
        return issue_payload(issue)

    @app.get("/api/notifications")
    def list_notifications(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        notifications = db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(50)
        ).all()
        unread_count = db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
        return {
            "unread_count": unread_count or 0,
            "items": [
                {
                    "id": item.id,
                    "issue_id": item.issue_id,
                    "message": item.message,
                    "read": item.read_at is not None,
                    "created_at": utc_isoformat(item.created_at),
                }
                for item in notifications
            ],
        }

    @app.patch("/api/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: int,
        body: NotificationRead,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        notification = db.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user.id,
            )
        )
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found.")
        if body.read and notification.read_at is None:
            notification.read_at = utc_now()
        elif not body.read:
            notification.read_at = None
        db.commit()
        return {"id": notification.id, "read": notification.read_at is not None}

    @app.get("/api/admin/sla-policies")
    def list_sla_policies(
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        rows = db.execute(
            select(SlaPolicy, Category.name)
            .join(Category, Category.id == SlaPolicy.category_id)
            .order_by(Category.name, SlaPolicy.severity_min)
        ).all()
        return [
            {
                "id": policy.id,
                "category_id": policy.category_id,
                "category": category_name,
                "severity_min": policy.severity_min,
                "response_minutes": policy.response_minutes,
                "resolution_minutes": policy.resolution_minutes,
            }
            for policy, category_name in rows
        ]

    @app.put("/api/admin/sla-policies")
    def upsert_sla_policy(
        body: SlaPolicyUpsert,
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        category = db.get(Category, body.category_id)
        if not category or not category.active:
            raise HTTPException(status_code=422, detail="Choose an active category.")
        policy = db.scalar(
            select(SlaPolicy).where(
                SlaPolicy.category_id == body.category_id,
                SlaPolicy.severity_min == body.severity_min,
            )
        )
        if policy is None:
            policy = SlaPolicy(
                category_id=body.category_id,
                severity_min=body.severity_min,
            )
            db.add(policy)
        policy.response_minutes = body.response_minutes
        policy.resolution_minutes = body.resolution_minutes
        db.commit()
        db.refresh(policy)
        return {
            "id": policy.id,
            "category_id": policy.category_id,
            "category": category.name,
            "severity_min": policy.severity_min,
            "response_minutes": policy.response_minutes,
            "resolution_minutes": policy.resolution_minutes,
        }

    @app.get("/api/admin/analytics")
    def admin_analytics(
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        status_rows = db.execute(
            select(Issue.status, func.count(Issue.id)).group_by(Issue.status)
        ).all()
        status_counts = {status_name: count for status_name, count in status_rows}
        category_rows = db.execute(
            select(Category.name, func.count(Issue.id))
            .outerjoin(Issue, Issue.category_id == Category.id)
            .group_by(Category.id, Category.name)
            .order_by(Category.name)
        ).all()
        department_rows = db.execute(
            select(Department.name, func.count(Issue.id))
            .outerjoin(Issue, Issue.assigned_department_id == Department.id)
            .group_by(Department.id, Department.name)
            .order_by(Department.name)
        ).all()
        unassigned_count = db.scalar(
            select(func.count(Issue.id)).where(Issue.assigned_department_id.is_(None))
        )
        if unassigned_count:
            department_rows = [*department_rows, ("Unassigned", unassigned_count)]

        total = sum(status_counts.values())
        terminal_count = status_counts.get("resolved", 0) + status_counts.get(
            "closed", 0
        )
        pending = sum(
            status_counts.get(status_name, 0)
            for status_name in ("submitted", "under_review", "in_progress")
        )
        now = utc_now()
        active_issues = db.scalars(
            select(Issue).where(Issue.status.notin_(("resolved", "closed", "rejected")))
        ).all()
        overdue_count = sum(
            issue_overdue_state(issue, now)["overdue"] for issue in active_issues
        )
        return {
            "total": total,
            "pending": pending,
            "resolved": terminal_count,
            "rejected": status_counts.get("rejected", 0),
            "overdue": overdue_count,
            "status_counts": status_counts,
            "by_category": [
                {"name": name, "count": count} for name, count in category_rows
            ],
            "by_department": [
                {"name": name, "count": count} for name, count in department_rows
            ],
        }

    @app.get("/api/admin/verified-labels")
    def list_verified_labels(
        _: User = Depends(require_admin),
        db: Session = Depends(get_db),
    ) -> list[dict[str, object]]:
        rows = db.execute(
            select(VerifiedCategoryLabel, Category.name, User.full_name)
            .join(Category, Category.id == VerifiedCategoryLabel.category_id)
            .join(User, User.id == VerifiedCategoryLabel.verified_by_id)
            .order_by(VerifiedCategoryLabel.created_at.desc())
        ).all()
        return [
            {
                "id": label.id,
                "issue_id": label.issue_id,
                "category_id": label.category_id,
                "category": category_name,
                "title": label.title_snapshot,
                "description": label.description_snapshot,
                "verified_by": reviewer_name,
                "created_at": utc_isoformat(label.created_at),
            }
            for label, category_name, reviewer_name in rows
        ]

    @app.get("/api/issues/{complaint_code}/image")
    def issue_image(
        complaint_code: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> FileResponse:
        issue = db.scalar(select(Issue).where(Issue.complaint_code == complaint_code))
        if not issue or not issue.image:
            raise HTTPException(status_code=404, detail="Complaint photo not found.")
        if user.role != "admin" and issue.reporter_id != user.id:
            raise HTTPException(
                status_code=403, detail="You cannot view this complaint photo."
            )
        upload_dir = Path(app_settings.upload_dir)
        if not upload_dir.is_absolute():
            upload_dir = ROOT / upload_dir
        image_path = upload_dir / issue.image.storage_key
        if not image_path.is_file():
            raise HTTPException(
                status_code=404, detail="Complaint photo is no longer available."
            )
        return FileResponse(
            image_path, media_type="image/jpeg", filename=image_path.name
        )

    return app


def set_session_cookie(response: Response, request: Request, user_id: int) -> None:
    settings: Settings = request.app.state.settings
    token = create_access_token(
        user_id, settings.secret_key, settings.token_expiry_minutes
    )
    response.set_cookie(
        "civictrack_session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.token_expiry_minutes * 60,
        path="/",
    )


app = create_app()
