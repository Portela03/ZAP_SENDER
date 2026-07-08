import os
import sqlite3
from datetime import datetime


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str):
    """Cria a tabela de campanha se não existir."""
    with _connect(db_path) as conn:
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


def upsert_contacts(contacts: list, db_path: str) -> tuple:
    """Insere novos contatos ou reseta existentes para pending."""
    inserted = 0
    skipped  = 0
    with _connect(db_path) as conn:
        for c in contacts:
            cur = conn.execute(
                'SELECT id FROM campaign WHERE numero = ?', (c['numero'],)
            ).fetchone()
            if cur is None:
                conn.execute(
                    'INSERT INTO campaign (nome, numero, mensagem) VALUES (?, ?, ?)',
                    (c['nome'], c['numero'], c['mensagem']),
                )
                inserted += 1
            else:
                conn.execute(
                    'UPDATE campaign SET nome=?, mensagem=?, status=?, '
                    'tentativas=0, enviado_em=NULL, erro=NULL WHERE numero=?',
                    (c['nome'], c['mensagem'], 'pending', c['numero']),
                )
                skipped += 1
    return inserted, skipped


def get_pending(limit: int, db_path: str) -> list:
    """Retorna o próximo lote de contatos pendentes ordenados por id."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            'SELECT id, nome, numero, mensagem FROM campaign '
            'WHERE status = ? ORDER BY id LIMIT ?',
            ('pending', limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_preview(db_path: str) -> list:
    """Retorna todos os contatos pendentes que ainda serão enviados."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            'SELECT id, nome, numero, mensagem FROM campaign '
            'WHERE status = ? ORDER BY id',
            ('pending',),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(contact_id: int, db_path: str):
    with _connect(db_path) as conn:
        conn.execute(
            'UPDATE campaign SET status=?, enviado_em=?, tentativas=tentativas+1 WHERE id=?',
            ('sent', datetime.now().isoformat(), contact_id),
        )


def mark_failed(contact_id: int, error: str, db_path: str):
    with _connect(db_path) as conn:
        conn.execute(
            'UPDATE campaign SET status=?, tentativas=tentativas+1, erro=? WHERE id=?',
            ('failed', error, contact_id),
        )


def get_summary(db_path: str) -> dict:
    """Retorna contagens agrupadas por status."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            'SELECT status, COUNT(*) AS cnt FROM campaign GROUP BY status'
        ).fetchall()
    return {r['status']: r['cnt'] for r in rows}


def reset_failed(db_path: str):
    """Move todos os contatos com falha de volta para pending."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE campaign SET status='pending', erro=NULL WHERE status='failed'"
        )


def get_sent_today(db_path: str) -> int:
    """Retorna o número de mensagens enviadas hoje (dia corrente local)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM campaign "
            "WHERE status = 'sent' AND date(enviado_em) = date('now')"
        ).fetchone()
    return row['cnt'] if row else 0


def get_failed(db_path: str) -> list:
    """Retorna todos os contatos com falha, incluindo nome, número, mensagem e erro."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT nome, numero, mensagem, erro FROM campaign "
            "WHERE status = 'failed' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
