"""Local accounts, hashed sessions and single-use administrator invitations."""
from contextvars import ContextVar
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

CURRENT_USER = ContextVar('web_user', default=None)
COOKIE = 'scigblast_session'
ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def password_matches(password, encoded):
    try:
        method, iterations, salt, expected = encoded.split('$')
        if method != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def init_schema(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
            active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, last_login INTEGER
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invitations (
            id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            used_by TEXT, revoked INTEGER NOT NULL DEFAULT 0, deleted_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS auth_limits (
            bucket TEXT PRIMARY KEY, started INTEGER NOT NULL, attempts INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submission_owners (
            revision TEXT PRIMARY KEY, user_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT, action TEXT NOT NULL,
            target_id TEXT, created_at INTEGER NOT NULL
        );
    ''')
    invitation_columns = {row[1] for row in connection.execute('PRAGMA table_info(invitations)')}
    if 'deleted_at' not in invitation_columns:
        connection.execute('ALTER TABLE invitations ADD COLUMN deleted_at INTEGER')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
    if 'owner_id' not in columns:
        connection.execute('ALTER TABLE jobs ADD COLUMN owner_id TEXT')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id)')


def public_user(user):
    return {k: user[k] for k in ('id', 'username', 'display_name', 'role', 'active', 'created_at', 'last_login')}


def require_user():
    user = CURRENT_USER.get()
    if not user:
        raise HTTPException(401, '请先登录')
    return user


def require_admin():
    user = require_user()
    if user['role'] != 'admin':
        raise HTTPException(403, '仅管理员可执行此操作')
    return user


def resolve_session(db, token):
    if not token or len(token) > 256:
        return None
    with db() as connection:
        user = connection.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id '
                                  'WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',
                                  (digest(token), int(time.time()))).fetchone()
    return public_user(user) if user else None


def event(connection, actor, action, target=None):
    connection.execute('INSERT INTO auth_events(actor_id,action,target_id,created_at) VALUES(?,?,?,?)',
                       (actor, action, target, int(time.time())))


def rate_limit(db, key, maximum):
    timestamp = int(time.time())
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM auth_limits WHERE started<?', (timestamp - 900,))
        connection.execute('INSERT INTO auth_limits VALUES(?,?,1) ON CONFLICT(bucket) '
                           'DO UPDATE SET attempts=attempts+1', (digest(key), timestamp))
        attempts = connection.execute('SELECT attempts FROM auth_limits WHERE bucket=?', (digest(key),)).fetchone()[0]
    if attempts > maximum:
        raise HTTPException(429, '尝试过于频繁，请在 15 分钟后再试')


class Credentials(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=32, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    password: str = Field(min_length=6, max_length=128)


def bootstrap_admin(db):
    """Create the first administrator from deployment settings, once only."""
    username = os.environ.get('SCIGBLAST_ADMIN_USERNAME', '').strip().lower()
    password = os.environ.get('SCIGBLAST_ADMIN_PASSWORD', '')
    name = os.environ.get('SCIGBLAST_ADMIN_DISPLAY_NAME', '').strip() or username
    if not any((username, password, name)):
        return
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        # Disabled admins also count: deployment must not undo account management.
        if connection.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            return
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,31}', username):
            raise RuntimeError('SCIGBLAST_ADMIN_USERNAME must be 3–32 ASCII letters/digits/_.-')
        if not 6 <= len(password) <= 128:
            raise RuntimeError('SCIGBLAST_ADMIN_PASSWORD must contain 6–128 characters')
        if not 1 <= len(name) <= 60 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 ._-]*', name):
            raise RuntimeError('SCIGBLAST_ADMIN_DISPLAY_NAME must be 1–60 characters, start with an English '
                               'letter and contain only ASCII letters/digits/spaces/._-')
        if connection.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise RuntimeError('SCIGBLAST_ADMIN_USERNAME already belongs to a non-admin account')
        user_id = secrets.token_hex(16)
        connection.execute("INSERT INTO users(id,username,display_name,password_hash,role,created_at) "
                           "VALUES(?,?,?,?,'admin',?)",
                           (user_id, username, name, password_hash(password), int(time.time())))
        event(connection, None, 'bootstrap-admin', user_id)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z0-9 ._-]*$")
    invitation: str = Field(min_length=10, max_length=128)


class InvitationRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=168)


class ActiveRequest(BaseModel):
    active: bool


class PasswordRequest(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


def router(db):
    routes = APIRouter()
    # Unknown accounts still perform one full password derivation.
    dummy_hash = password_hash(secrets.token_urlsafe(32))

    @routes.post('/api/auth/register')
    def register(body: Registration, request: Request):
        rate_limit(db, 'register:' + request.client.host, 10)
        name = body.display_name.strip()
        if not name:
            raise HTTPException(400, '请填写姓名')
        encoded = password_hash(body.password)
        user_id = secrets.token_hex(16)
        timestamp = int(time.time())
        try:
            with db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                invite = connection.execute('SELECT i.* FROM invitations i JOIN users u ON u.id=i.created_by '
                    'WHERE i.code_hash=? AND i.used_by IS NULL AND i.revoked=0 AND i.deleted_at IS NULL AND i.expires_at>? '
                    'AND u.active=1 AND u.role=\'admin\'', (digest(body.invitation.strip()), timestamp)).fetchone()
                if not invite:
                    raise HTTPException(400, '注册码无效、已使用、已撤销或已过期')
                if not body.username and connection.execute(
                    'SELECT 1 FROM users WHERE lower(trim(display_name))=lower(?)', (name,)
                ).fetchone():
                    raise HTTPException(409, '姓名已被使用，请使用可区分的完整姓名或缩写')
                account_key = body.username.lower() if body.username else user_id
                connection.execute('INSERT INTO users(id,username,display_name,password_hash,role,created_at) '
                                   'VALUES(?,?,?,?,\'user\',?)', (user_id, account_key, name, encoded, timestamp))
                connection.execute('UPDATE invitations SET used_by=? WHERE id=?', (user_id, invite['id']))
                event(connection, user_id, 'register', invite['id'])
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '用户名已存在') from exc
        return {'ok': True}

    @routes.post('/api/auth/login')
    def login(body: Credentials, request: Request):
        rate_limit(db, 'login-ip:' + request.client.host, 30)
        identity = (body.display_name or body.username or '').strip()
        if not identity:
            raise HTTPException(400, '请填写姓名')
        rate_limit(db, 'login-user:' + identity.lower(), 20)
        with db() as connection:
            if body.display_name is not None:
                candidates = connection.execute('SELECT * FROM users WHERE lower(trim(display_name))=lower(?)', (identity,)).fetchall()
                user = candidates[0] if len(candidates) == 1 else None
            else:
                user = connection.execute('SELECT * FROM users WHERE username=?', (identity.lower(),)).fetchone()
        correct = password_matches(body.password, user['password_hash'] if user else dummy_hash)
        if not user or not correct or not user['active']:
            raise HTTPException(401, '姓名或密码错误，或账户已停用')
        token = secrets.token_urlsafe(32)
        timestamp = int(time.time())
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            latest = connection.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
            if not latest['active'] or latest['password_hash'] != user['password_hash']:
                raise HTTPException(401, '账户已变更，请重新登录')
            connection.execute('DELETE FROM sessions WHERE expires_at<=?', (timestamp,))
            connection.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), user['id'], timestamp + SESSION_SECONDS))
            connection.execute('UPDATE users SET last_login=? WHERE id=?', (timestamp, user['id']))
            event(connection, user['id'], 'login')
        response = JSONResponse({'user': public_user(user)})
        secure = os.environ.get('SCIGBLAST_COOKIE_SECURE', '').lower() in {'1', 'true'} or request.url.scheme == 'https'
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite='strict', path='/')
        return response

    @routes.get('/api/auth/me')
    def me():
        return {'user': require_user()}

    @routes.post('/api/auth/logout')
    def logout(request: Request):
        user = require_user()
        with db() as connection:
            connection.execute('DELETE FROM sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
            event(connection, user['id'], 'logout')
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @routes.post('/api/auth/password')
    def change_password(body: PasswordRequest):
        user = require_user()
        rate_limit(db, 'password:' + user['id'], 10)
        with db() as connection:
            old = connection.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        if not password_matches(body.current_password, old):
            raise HTTPException(400, '当前密码错误')
        encoded = password_hash(body.new_password)
        with db() as connection:
            updated = connection.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=?', (encoded, user['id'], old))
            if not updated.rowcount:
                raise HTTPException(409, '密码已变更，请重新登录')
            connection.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            event(connection, user['id'], 'change-password')
        return {'ok': True}

    @routes.get('/api/admin/users')
    def users():
        require_admin()
        with db() as connection:
            rows = connection.execute('SELECT u.*, i.id AS invitation_id,i.deleted_at AS invitation_deleted_at FROM users u '
                                      'LEFT JOIN invitations i ON i.used_by=u.id ORDER BY u.created_at,u.id')
            return {'users': [{**public_user(u), 'invitation_id': u['invitation_id'],
                               'invitation_deleted_at': u['invitation_deleted_at']} for u in rows]}

    @routes.post('/api/admin/users/{user_id}/active')
    def set_active(user_id: str, body: ActiveRequest):
        admin = require_admin()
        if user_id == admin['id']:
            raise HTTPException(400, '不能停用当前管理员账户')
        with db() as connection:
            if not connection.execute('UPDATE users SET active=? WHERE id=?', (int(body.active), user_id)).rowcount:
                raise HTTPException(404, '用户不存在')
            if not body.active:
                connection.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
                connection.execute('UPDATE invitations SET revoked=1 WHERE created_by=? AND used_by IS NULL', (user_id,))
            event(connection, admin['id'], 'enable-user' if body.active else 'disable-user', user_id)
        return {'ok': True}

    @routes.get('/api/admin/invitations')
    def invitations(status: str = '', q: str = Query('', max_length=200),
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        require_admin()
        timestamp = int(time.time())
        conditions = {
            '': '1=1',
            'available': 'i.used_by IS NULL AND i.revoked=0 AND i.expires_at>?',
            'used': 'i.used_by IS NOT NULL',
            'revoked': 'i.used_by IS NULL AND i.revoked=1',
            'expired': 'i.used_by IS NULL AND i.revoked=0 AND i.expires_at<=?',
            'deleted': 'i.deleted_at IS NOT NULL',
        }
        if status not in conditions:
            raise HTTPException(400, '未知注册码状态')
        where = conditions[status]
        if status != 'deleted':
            where += ' AND i.deleted_at IS NULL'
        params = [timestamp] if status in {'available', 'expired'} else []
        if q.strip():
            where += (' AND (instr(lower(i.id),lower(?))>0 OR instr(lower(u.username),lower(?))>0 '
                      'OR instr(lower(u.display_name),lower(?))>0 OR instr(lower(c.username),lower(?))>0 '
                      'OR instr(lower(c.display_name),lower(?))>0)')
            params.extend([q.strip()] * 5)
        source = (' FROM invitations i LEFT JOIN users u ON u.id=i.used_by '
                  'LEFT JOIN users c ON c.id=i.created_by WHERE ' + where)
        with db() as connection:
            total = connection.execute('SELECT COUNT(*)' + source, params).fetchone()[0]
            rows = connection.execute(
                'SELECT i.id,i.created_at,i.expires_at,i.revoked,i.used_by,i.created_by,i.deleted_at, '
                'u.username AS used_username,u.display_name AS used_display_name,u.active AS used_active, '
                'u.created_at AS used_at,c.username AS created_username,c.display_name AS created_display_name'
                + source + ' ORDER BY i.created_at DESC,i.id DESC LIMIT ? OFFSET ?',
                [*params, limit, offset]).fetchall()
        return {'invitations': [{**dict(r), 'status': '已删除' if r['deleted_at'] is not None else '已使用' if r['used_by'] else '已撤销' if r['revoked'] else '已过期' if r['expires_at'] <= timestamp else '可使用'} for r in rows],
                'total': total, 'limit': limit, 'offset': offset}

    @routes.post('/api/admin/invitations')
    def create_invitation(body: InvitationRequest):
        admin = require_admin()
        code, invitation_id = secrets.token_urlsafe(24), secrets.token_hex(8)
        timestamp = int(time.time())
        with db() as connection:
            connection.execute('INSERT INTO invitations(id,code_hash,created_by,created_at,expires_at) VALUES(?,?,?,?,?)',
                               (invitation_id, digest(code), admin['id'], timestamp, timestamp + body.hours * 3600))
            event(connection, admin['id'], 'create-invitation', invitation_id)
        return {'id': invitation_id, 'code': code, 'expires_at': timestamp + body.hours * 3600}

    @routes.post('/api/admin/invitations/{invitation_id}/revoke')
    def revoke(invitation_id: str):
        admin = require_admin()
        with db() as connection:
            if not connection.execute('UPDATE invitations SET revoked=1 WHERE id=? AND used_by IS NULL AND revoked=0 AND deleted_at IS NULL', (invitation_id,)).rowcount:
                raise HTTPException(409, '注册码不存在、已使用或已撤销')
            event(connection, admin['id'], 'revoke-invitation', invitation_id)
        return {'ok': True}

    @routes.post('/api/admin/invitations/{invitation_id}/delete')
    def delete_invitation(invitation_id: str):
        admin = require_admin()
        with db() as connection:
            updated = connection.execute('UPDATE invitations SET deleted_at=?,revoked=1 '
                                         'WHERE id=? AND deleted_at IS NULL',
                                         (int(time.time()), invitation_id))
            if not updated.rowcount:
                raise HTTPException(404, '注册码记录不存在或已删除')
            event(connection, admin['id'], 'delete-invitation', invitation_id)
        return {'ok': True}

    return routes
