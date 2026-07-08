import os
import sqlite3
import uuid
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, 'data')
USERS_DB  = os.path.join(DATA_DIR, 'users.db')


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(USERS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_users_db():
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
        ''')


def create_user(username: str, password: str) -> dict | None:
    """Cria novo usuário. Retorna dict do usuário ou None se username já existe."""
    init_users_db()
    user_id = str(uuid.uuid4())
    pw_hash = generate_password_hash(password)
    try:
        with _connect() as conn:
            conn.execute(
                'INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)',
                (user_id, username.strip().lower(), pw_hash, datetime.now().isoformat()),
            )
        return {'id': user_id, 'username': username.strip().lower()}
    except sqlite3.IntegrityError:
        return None  # username já existe


def verify_user(username: str, password: str) -> dict | None:
    """Valida credenciais. Retorna dict do usuário ou None se inválido."""
    init_users_db()
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, username, password_hash FROM users WHERE username = ?',
            (username.strip().lower(),),
        ).fetchone()
    if row and check_password_hash(row['password_hash'], password):
        return {'id': row['id'], 'username': row['username']}
    return None


def get_user(user_id: str) -> dict | None:
    """Busca usuário por id."""
    init_users_db()
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, username FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()
    return dict(row) if row else None
