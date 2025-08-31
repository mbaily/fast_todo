import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
from sqlmodel import select
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel
from .models import User
from .db import async_session
from sqlmodel import select
import secrets
from .models import Session
import logging

logger = logging.getLogger(__name__)

# config
# SECRET_KEY should be set in the environment in production. We fall back to a
# predictable value for local testing to avoid breaking tests when the env var
# is not supplied. Do NOT use the fallback in production.
SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_IN_ENV_FOR_TESTS")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
CSRF_TOKEN_EXPIRE_MINUTES = 60

# prefer a pure-Python, widely-available scheme for tests and portability;
# keep bcrypt as a fallback if available.
pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

class TokenData(BaseModel):
    username: Optional[str] = None

async def get_user_by_username(username: str) -> Optional[User]:
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == username))
    u = q.first()
    return u


async def create_session_for_user(user: User, token: Optional[str] = None, expires_delta: Optional[timedelta] = None, session_timezone: Optional[str] = None) -> str:
    """Create a server-side session and return the session token.

    If token is provided it will be used; otherwise a secure random token
    is generated.
    """
    sess_token = token or secrets.token_urlsafe(32)
    expires_at = None
    if expires_delta:
        expires_at = datetime.now(timezone.utc) + expires_delta
    async with async_session() as s:
        session_row = Session(session_token=sess_token, user_id=user.id, expires_at=expires_at, timezone=session_timezone)
        s.add(session_row)
        await s.commit()
    return sess_token


async def get_user_by_session_token(session_token: str) -> Optional[User]:
    async with async_session() as s:
        q = await s.exec(select(Session).where(Session.session_token == session_token))
        sess_row = q.first()
        if not sess_row:
            return None
        # optional expiry check
        if sess_row.expires_at and sess_row.expires_at < datetime.now(timezone.utc):
            # expired: delete row and return None
            try:
                from sqlalchemy import delete as sqlalchemy_delete
                await s.exec(sqlalchemy_delete(Session).where(Session.session_token == session_token))
                await s.commit()
            except Exception:
                logger.exception("failed to delete expired session %s", session_token)
            return None
        q2 = await s.exec(select(User).where(User.id == sess_row.user_id))
        return q2.first()


async def delete_session(session_token: str) -> None:
    async with async_session() as s:
        # delete by token
        try:
            from sqlalchemy import delete as sqlalchemy_delete
            await s.exec(sqlalchemy_delete(Session).where(Session.session_token == session_token))
            await s.commit()
        except Exception:
            logger.exception("failed to delete session %s", session_token)

async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

async def authenticate_user(username: str, password: str) -> Optional[User]:
    user = await get_user_by_username(username)
    if not user:
        return None
    if not await verify_password(password, user.password_hash):
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # RFC 7519 recommends NumericDate (seconds since epoch). Encode as int.
    to_encode.update({"exp": int(expire.timestamp())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_csrf_token(username: str, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = {"sub": username, "type": "csrf"}
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=CSRF_TOKEN_EXPIRE_MINUTES)
    # Use numeric epoch seconds for exp to avoid library-specific serialization
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_csrf_token(token: str, username: str) -> bool:
    try:
        logger.info('verify_csrf_token called: token_present=%s token_len=%s username=%s', bool(token), (len(token) if token else 0), username)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.info('verify_csrf_token decoded payload keys: %s', list(payload.keys()))
        if payload.get("type") != "csrf":
            logger.info('verify_csrf_token failed: type mismatch (expected csrf, got %s)', payload.get('type'))
            return False
        if payload.get("sub") != username:
            logger.info('verify_csrf_token failed: subject mismatch (expected %s, got %s)', username, payload.get('sub'))
            return False
        logger.info('verify_csrf_token success for user=%s', username)
        return True
    except JWTError as e:
        logger.info('verify_csrf_token JWTError: %s', str(e))
        return False
    except Exception as e:
        logger.exception('verify_csrf_token unexpected exception: %s', str(e))
        return False

async def get_current_user(token: Optional[str] = Depends(oauth2_scheme), request: Request = None) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # If the request includes an Authorization header, treat it as authoritative
    # and validate only that token. This prevents accidentally accepting a
    # valid cookie when the Authorization header contains a tampered token.
    # If `token` has been provided (non-None) validate it. This covers the
    # common case where the oauth2 dependency extracted a bearer token from the
    # Authorization header. If `token` is None (caller explicitly passed
    # token=None) we treat that as a request to use cookie-based session
    # lookup instead of Authorization header.
    if token is not None:
        # token will be validated below
        pass
    else:
        # No token provided: prefer server-side session cookie for browser
        # clients, then fall back to access_token cookie.
        if request is not None:
            session_token = request.cookies.get("session_token")
            if session_token:
                user = await get_user_by_session_token(session_token)
                if user:
                    return user
            # next fallback: access_token cookie
            token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = await get_user_by_username(token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def require_login(user: Optional[User] = Depends(get_current_user)) -> User:
    """Dependency that enforces an authenticated user.

    Returns the User when present, otherwise raises 401 Unauthorized.
    Use this in endpoints that must deny anonymous access.
    """
    logger.info('require_login called, user_present=%s', bool(user))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    return user

