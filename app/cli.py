import argparse
import getpass
import re
import sys

from sqlalchemy import select

from app.config import load_settings
from app.database import Base, build_session_factory
from app.models import Category, SlaPolicy, User
from app.security import hash_password

CATEGORIES = [
    ("roads", "Roads and potholes", 3),
    ("streetlights", "Street lighting", 2),
    ("waste", "Waste and sanitation", 2),
    ("water", "Water and drainage", 3),
    ("parks", "Parks and public spaces", 1),
]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def initialize_database() -> None:
    settings = load_settings()
    session_factory = build_session_factory(settings.database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    with session_factory() as db:
        for slug, name, severity in CATEGORIES:
            category = db.scalar(select(Category).where(Category.slug == slug))
            if category is None:
                category = Category(slug=slug, name=name, base_severity=severity)
                db.add(category)
                db.flush()
            policy = db.scalar(
                select(SlaPolicy).where(
                    SlaPolicy.category_id == category.id,
                    SlaPolicy.severity_min == 1,
                )
            )
            if policy is None:
                db.add(
                    SlaPolicy(
                        category_id=category.id,
                        severity_min=1,
                        response_minutes=1440,
                        resolution_minutes=4320,
                    )
                )
        db.commit()
    engine.dispose()
    print("CivicTrack tables and starter categories are ready.")


def create_admin() -> None:
    settings = load_settings()
    session_factory = build_session_factory(settings.database_url)
    email = input("Administrator email: ").strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    full_name = input("Administrator name: ").strip()
    if len(full_name) < 2:
        raise ValueError("Name must contain at least 2 characters.")
    password = getpass.getpass("Password (at least 10 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")
    if password != confirmation:
        raise ValueError("Passwords do not match.")

    with session_factory() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            raise ValueError("An account with this email already exists.")
        db.add(
            User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(password),
                role="admin",
            )
        )
        db.commit()
    session_factory.kw["bind"].dispose()
    print(f"Administrator account created for {email}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="CivicTrack local setup commands")
    parser.add_argument("command", choices=("init-db", "create-admin"))
    args = parser.parse_args()
    try:
        if args.command == "init-db":
            initialize_database()
        else:
            create_admin()
    except (ValueError, OSError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
