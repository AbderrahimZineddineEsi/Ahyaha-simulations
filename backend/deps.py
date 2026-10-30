"""FastAPI dependencies: the database session, the logged-in user, and role checks."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend import auth, db
from backend.models import User

bearer = HTTPBearer(auto_error=False)


async def get_session():
    async with db.session() as s:
        try:
            yield s
            await db.commit(s)
        except BaseException:
            await s.rollback()
            raise


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), s=Depends(get_session)) -> User:
    claims = auth.read_token(creds.credentials) if creds else None
    user = await s.get(User, claims[0]) if claims else None
    if user is None:
        raise HTTPException(401, "log in first", headers={"WWW-Authenticate": "Bearer"})
    return user


def require(*roles):
    """The user, if they have one of these roles. Admins pass wherever dispatchers do."""
    allowed = set(roles) | ({"admin"} if "dispatcher" in roles else set())

    async def check(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, f"this needs the role {' or '.join(sorted(allowed))}")
        return user
    return check
