import os
import sqlite3
from datetime import datetime

_data_dir = os.environ.get('DATA_DIR', '')
DB_PATH = os.path.join(_data_dir, 'campaign.db') if _data_dir else 'campaign.db'


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the campaign table if it doesn't exist."""
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS campaign (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nome       TEXT    NOT NULL,
                numero     TEXT    NOT NULL UNIQUE,
                mensagem   TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'pending',
                tentativas INTEGER NOT NULL DEFAULT 0,
                enviado_em TEXT,
                erro       TEXT
            )
        ''')


def upsert_contacts(contacts: list) -> tuple:
    """Insert new contacts or reset existing ones to pending (re-send safe)."""
    inserted = 0
    skipped = 0
    with _connect() as conn:
        for c in contacts:
            cur = conn.execute('SELECT id, status FROM campaign WHERE numero = ?', (c['numero'],)).fetchone()
            if cur is None:
                conn.execute(
                    'INSERT INTO campaign (nome, numero, mensagem) VALUES (?, ?, ?)',
                    (c['nome'], c['numero'], c['mensagem'])
                )
                inserted += 1
            else:
                # Atualiza nome/mensagem e reseta status para pending
                conn.execute(
                    'UPDATE campaign SET nome=?, mensagem=?, status=?, tentativas=0, enviado_em=NULL, erro=NULL WHERE numero=?',
                    (c['nome'], c['mensagem'], 'pending', c['numero'])
                )
                skipped += 1
    return inserted, skipped


def get_pending(limit: int) -> list:
    """Return the next batch of pending contacts ordered by id."""
    with _connect() as conn:
        rows = conn.execute(
            'SELECT id, nome, numero, mensagem FROM campaign '
            'WHERE status = ? ORDER BY id LIMIT ?',
            ('pending', limit)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(contact_id: int):
    with _connect() as conn:
        conn.execute(
            'UPDATE campaign '
            'SET status = ?, enviado_em = ?, tentativas = tentativas + 1 '
            'WHERE id = ?',
            ('sent', datetime.now().isoformat(), contact_id)
        )


def mark_failed(contact_id: int, error: str):
    with _connect() as conn:
        conn.execute(
            'UPDATE campaign '
            'SET status = ?, tentativas = tentativas + 1, erro = ? '
            'WHERE id = ?',
            ('failed', error, contact_id)
        )


def get_summary() -> dict:
    """Return counts grouped by status."""
    with _connect() as conn:
        rows = conn.execute(
            'SELECT status, COUNT(*) AS cnt FROM campaign GROUP BY status'
        ).fetchall()
    return {r['status']: r['cnt'] for r in rows}


def reset_failed():
    """Move all failed contacts back to pending for retry."""
    with _connect() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'pending', erro = NULL WHERE status = 'failed'"
        )
