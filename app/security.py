from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(user_id: int, secret_key: str, expiry_minutes: int) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=expiry_minutes)
    return jwt.encode(
        {"sub": str(user_id), "exp": expires_at}, secret_key, algorithm=ALGORITHM
    )


def decode_access_token(token: str, secret_key: str) -> int | None:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        subject = payload.get("sub")
        return int(subject) if isinstance(subject, str) else None
    except (jwt.InvalidTokenError, ValueError):
        return None
