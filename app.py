#!/usr/bin/env python3
"""
WhatsApp Sender — Interface Web (Multi-usuário)
Local : http://127.0.0.1:5000
Cloud : use a URL gerada pelo Render/Railway
"""

import json
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from datetime import datetime

from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)

import auth
import csv_loader
import tracker

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE_DIR, 'data')
NODE_SCRIPT = os.path.join(BASE_DIR, 'node', 'sender.js')

# ---------------------------------------------------------------------------
# Caminhos por usuário
# ---------------------------------------------------------------------------

def _get_user_data_dir(user_id: str) -> str:
    d = os.path.join(DATA_DIR, user_id)
    os.makedirs(d, exist_ok=True)
    return d


def _get_auth_dir(user_id: str) -> str:
    return os.path.join(_get_user_data_dir(user_id), 'auth_info')


def _get_db_path(user_id: str) -> str:
    return os.path.join(_get_user_data_dir(user_id), 'contacts.db')


def _get_config_path(user_id: str) -> str:
    return os.path.join(_get_user_data_dir(user_id), 'config.json')


def _get_user_config(user_id: str) -> dict:
    config_path = _get_config_path(user_id)
    if not os.path.exists(config_path):
        root_config = os.path.join(BASE_DIR, 'config.json')
        if os.path.exists(root_config):
            shutil.copy(root_config, config_path)
        else:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'message_template': 'Olá {nome}, {mensagem}',
                    'batch_size': 30,
                    'delay_min_seconds': 20,
                    'delay_max_seconds': 60,
                    'batch_pause_minutes': 60,
                    'daily_limit': 150,
                    'allowed_hours_start': 8,
                    'allowed_hours_end': 20,
                }, f)
    cfg = {
        'daily_limit': 150,
        'allowed_hours_start': 8,
        'allowed_hours_end': 20,
    }
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg.update(json.load(f))
    return cfg


def _session_exists(user_id: str) -> bool:
    return os.path.exists(os.path.join(_get_auth_dir(user_id), 'creds.json'))


def _qr_to_png_base64(qr_text: str) -> str:
    """Converte o texto bruto do QR em data:image/png;base64,... usando Python."""
    import io, base64, qrcode as _qrcode
    qr = _qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(qr_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
    return f'data:image/png;base64,{encoded}'

# ---------------------------------------------------------------------------
# Estado global por usuário
# ---------------------------------------------------------------------------

_states_meta_lock = threading.Lock()
_user_states: dict = {}   # user_id -> state dict


def _get_user_state(user_id: str) -> dict:
    with _states_meta_lock:
        if user_id not in _user_states:
            _user_states[user_id] = {
                'lock':         threading.Lock(),
                'send_running': False,
                'qr_data':      None,
                'node_proc':    None,
                'stop_event':   threading.Event(),
            }
        return _user_states[user_id]


_sse_meta_lock = threading.Lock()
_sse_listeners: dict = {}   # user_id -> list[queue.Queue]


def _push_event(user_id: str, event_type: str, **kwargs):
    """Envia evento SSE para todos os clientes do usuário conectados."""
    payload = json.dumps({'type': event_type, **kwargs})
    with _sse_meta_lock:
        listeners = _sse_listeners.get(user_id, [])
        dead = []
        for q in listeners:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            listeners.remove(q)


# ---------------------------------------------------------------------------
# Autenticação por conta de usuário
# ---------------------------------------------------------------------------

_PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'favicon', 'health'}


@app.before_request
def _check_auth():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not session.get('user_id'):
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = auth.verify_user(username, password)
        if user:
            session['user_id']  = user['id']
            session['username'] = user['username']
            _get_user_data_dir(user['id'])   # garante dir criado
            return redirect(url_for('index'))
        error = 'Usuário ou senha incorretos.'
    return render_template('login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        if not username or len(username) < 3:
            error = 'Usuário deve ter ao menos 3 caracteres.'
        elif len(password) < 6:
            error = 'Senha deve ter ao menos 6 caracteres.'
        elif password != confirm:
            error = 'As senhas não coincidem.'
        else:
            user = auth.create_user(username, password)
            if user is None:
                error = 'Esse nome de usuário já existe.'
            else:
                session['user_id']  = user['id']
                session['username'] = user['username']
                _get_user_data_dir(user['id'])
                return redirect(url_for('index'))
    return render_template('register.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    from flask import make_response
    r = make_response(render_template('index.html', username=session.get('username', '')))
    r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return r


@app.route('/api/status')
def api_status():
    user_id = session['user_id']
    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    summary = tracker.get_summary(db_path)
    cfg = _get_user_config(user_id)
    ust = _get_user_state(user_id)
    with ust['lock']:
        running = ust['send_running']
        has_qr  = ust['qr_data'] is not None
    return jsonify({
        'summary': summary,
        'send_running': running,
        'has_qr': has_qr,
        'session_exists': _session_exists(user_id),
        'sent_today': tracker.get_sent_today(db_path),
        'daily_limit': int(cfg.get('daily_limit', 150)),
    })


@app.route('/api/whatsapp/disconnect', methods=['POST'])
def api_whatsapp_disconnect():
    user_id = session['user_id']
    ust = _get_user_state(user_id)
    with ust['lock']:
        if ust['send_running']:
            return jsonify({'error': 'Pare o envio antes de desconectar.'}), 400
        proc = ust['node_proc']
        if proc and proc.poll() is None:
            proc.terminate()
    auth_dir = _get_auth_dir(user_id)
    try:
        if os.path.exists(auth_dir):
            shutil.rmtree(auth_dir)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp/connect', methods=['POST'])
def api_whatsapp_connect():
    user_id = session['user_id']
    ust = _get_user_state(user_id)
    with ust['lock']:
        if ust['send_running']:
            return jsonify({'error': 'Já em andamento.'}), 400
    ust['stop_event'].clear()
    threading.Thread(target=_connect_worker, args=(user_id,), daemon=True).start()
    return jsonify({'ok': True})


def _connect_worker(user_id: str):
    """Inicia o Node.js apenas para estabelecer sessão via QR (sem enviar mensagens)."""
    ust = _get_user_state(user_id)
    with ust['lock']:
        ust['send_running'] = True
        ust['qr_data']      = None
    try:
        if not os.path.exists(NODE_SCRIPT):
            _push_event(user_id, 'error', message='node/sender.js não encontrado.')
            return

        env = os.environ.copy()
        env['DATA_DIR'] = _get_user_data_dir(user_id)

        proc = subprocess.Popen(
            ['node', NODE_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
            env=env,
        )
        with ust['lock']:
            ust['node_proc'] = proc

        resp_queue: queue.Queue = queue.Queue()

        def _read():
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get('status') == 'qr':
                        qr_raw = msg.get('qr', '')
                        try:
                            png = _qr_to_png_base64(qr_raw)
                        except Exception as e:
                            import sys
                            print(f'[app] _qr_to_png_base64 falhou: {e}', file=sys.stderr)
                            png = qr_raw  # fallback: string bruta, JS renderiza
                        with ust['lock']:
                            ust['qr_data'] = png
                        _push_event(user_id, 'qr')
                    elif msg.get('status') == 'ready':
                        with ust['lock']:
                            ust['qr_data'] = None
                    resp_queue.put(msg)
                except json.JSONDecodeError:
                    pass

        threading.Thread(target=_read, daemon=True).start()
        _push_event(user_id, 'info', message='Aguardando conexão com WhatsApp… (escaneie o QR)')

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if ust['stop_event'].is_set():
                proc.terminate()
                _push_event(user_id, 'info', message='Conexão cancelada.')
                return
            if proc.poll() is not None:
                _push_event(user_id, 'error', message='Processo Node.js encerrou inesperadamente.')
                return
            try:
                msg = resp_queue.get(timeout=2)
            except queue.Empty:
                continue
            if msg.get('status') == 'ready':
                _push_event(user_id, 'connected', message='WhatsApp conectado! Sessão salva com sucesso.')
                break

        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        proc.wait()

    except Exception as e:
        _push_event(user_id, 'error', message=str(e))
    finally:
        with ust['lock']:
            ust['send_running'] = False
            ust['qr_data']      = None
            ust['node_proc']    = None
        _push_event(user_id, 'done')


@app.route('/api/qr')
def api_qr():
    user_id = session['user_id']
    ust = _get_user_state(user_id)
    with ust['lock']:
        qr = ust['qr_data']
    return jsonify({'qr': qr})


@app.route('/api/template')
def api_template():
    """Serve o arquivo contacts_template.csv para download."""
    template_path = os.path.join(BASE_DIR, 'contacts_template.csv')
    return send_file(
        template_path,
        mimetype='text/csv',
        as_attachment=True,
        download_name='contacts_template.csv',
    )


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


@app.route('/api/load', methods=['POST'])
def api_load():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Nome de arquivo vazio'}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.csv', '.xlsx', '.xls'):
        return jsonify({'error': f'Formato não suportado: {ext}. Use .csv, .xlsx ou .xls'}), 400

    user_id   = session['user_id']
    db_path   = _get_db_path(user_id)
    temp_path = os.path.join(_get_user_data_dir(user_id), f'_upload_temp{ext}')
    f.save(temp_path)

    try:
        tracker.init_db(db_path)
        contacts, import_errors = csv_loader.load_contacts(temp_path)
        if not contacts:
            detail = import_errors[0] if import_errors else 'Verifique se o arquivo tem as colunas: nome, numero, mensagem.'
            extra  = import_errors[1:] if len(import_errors) > 1 else []
            return jsonify({
                'error': 'Nenhum contato válido encontrado na planilha.',
                'detail': detail,
                'errors': extra,
            }), 400
        inserted, skipped = tracker.upsert_contacts(contacts, db_path)
        return jsonify({
            'inserted': inserted,
            'skipped': skipped,
            'warnings': import_errors,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.route('/api/preview')
def api_preview():
    user_id = session['user_id']
    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    contacts = tracker.get_pending_preview(db_path)
    return jsonify({
        'total': len(contacts),
        'contacts': contacts,
    })


@app.route('/api/config', methods=['GET'])
def api_config_get():
    user_id = session['user_id']
    cfg = _get_user_config(user_id)
    return jsonify(cfg)


@app.route('/api/config', methods=['POST'])
def api_config_post():
    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    config_path = _get_config_path(user_id)
    cfg = _get_user_config(user_id)
    editable = {
        'batch_size', 'delay_min_seconds', 'delay_max_seconds',
        'batch_pause_minutes', 'daily_limit',
        'allowed_hours_start', 'allowed_hours_end',
    }
    for key in editable:
        if key in data:
            try:
                cfg[key] = int(data[key])
            except (ValueError, TypeError):
                return jsonify({'error': f'Valor inválido para {key}'}), 400
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({'ok': True})


@app.route('/api/send', methods=['POST'])
def api_send():
    user_id = session['user_id']
    ust = _get_user_state(user_id)
    with ust['lock']:
        if ust['send_running']:
            return jsonify({'error': 'Envio já em andamento'}), 400

    cfg = _get_user_config(user_id)

    # Verifica horário permitido (a menos que force=true)
    force = False
    body = request.get_json(silent=True) or {}
    force = bool(body.get('force', False))
    if not force:
        hour  = datetime.now().hour
        start = int(cfg.get('allowed_hours_start', 8))
        end   = int(cfg.get('allowed_hours_end', 20))
        if not (start <= hour < end):
            return jsonify({
                'warning_hours': True,
                'message': (
                    f'Fora do horário recomendado ({start}h–{end}h). '
                    'Envios fora desse período aumentam o risco de bloqueio.'
                ),
            })

    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    summary = tracker.get_summary(db_path)
    if not summary.get('pending', 0):
        return jsonify({'error': 'Nenhuma mensagem pendente. Carregue um arquivo CSV primeiro.'}), 400

    ust['stop_event'].clear()
    threading.Thread(target=_send_worker, args=(user_id,), daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    user_id = session['user_id']
    ust = _get_user_state(user_id)
    ust['stop_event'].set()
    with ust['lock']:
        proc = ust['node_proc']
    if proc and proc.poll() is None:
        proc.terminate()
    return jsonify({'ok': True})


@app.route('/api/retry', methods=['POST'])
def api_retry():
    user_id = session['user_id']
    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    summary = tracker.get_summary(db_path)
    failed = summary.get('failed', 0)
    if failed == 0:
        return jsonify({'error': 'Nenhuma mensagem com falha para retentar.'}), 400
    tracker.reset_failed(db_path)
    return jsonify({'reset': failed})


@app.route('/api/progress')
def api_progress():
    user_id = session['user_id']
    q: queue.Queue = queue.Queue(maxsize=200)
    with _sse_meta_lock:
        if user_id not in _sse_listeners:
            _sse_listeners[user_id] = []
        _sse_listeners[user_id].append(q)

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f'data: {data}\n\n'
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _sse_meta_lock:
                listeners = _sse_listeners.get(user_id, [])
                if q in listeners:
                    listeners.remove(q)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------------------------------------------------------------------------
# Worker de envio (thread background)
# ---------------------------------------------------------------------------

def _send_worker(user_id: str):
    ust = _get_user_state(user_id)
    with ust['lock']:
        ust['send_running'] = True
        ust['qr_data']      = None
    try:
        _run_send(user_id)
    except Exception as e:
        _push_event(user_id, 'error', message=str(e))
    finally:
        with ust['lock']:
            ust['send_running'] = False
            ust['qr_data']      = None
            ust['node_proc']    = None
        _push_event(user_id, 'done')


def _run_send(user_id: str):
    config  = _get_user_config(user_id)
    db_path = _get_db_path(user_id)

    batch_size          = int(config.get('batch_size', 30))
    batch_pause_minutes = int(config.get('batch_pause_minutes', 60))
    daily_limit         = int(config.get('daily_limit', 150))

    # Verifica limite diário
    sent_today = tracker.get_sent_today(db_path)
    remaining_today = daily_limit - sent_today
    if remaining_today <= 0:
        _push_event(user_id, 'error',
                    message=f'⚠️ Limite diário de {daily_limit} mensagens atingido '
                            f'({sent_today} enviadas hoje). Retome amanhã.')
        return

    pending = tracker.get_pending(batch_size, db_path)
    if not pending:
        _push_event(user_id, 'info', message='Nenhuma mensagem pendente.')
        return

    # Trunca o lote se ultrapassaria o limite diário
    if len(pending) > remaining_today:
        _push_event(user_id, 'info',
                    message=f'Lote reduzido para {remaining_today} (limite diário: {daily_limit}, '
                            f'enviadas hoje: {sent_today})')
        pending = pending[:remaining_today]

    summary   = tracker.get_summary(db_path)
    already   = summary.get('sent', 0)
    total_all = sum(summary.values())

    _push_event(user_id, 'info', message=f'Iniciando lote: {len(pending)} mensagem(s) | {already}/{total_all} já enviadas | Hoje: {sent_today}/{daily_limit}')

    delay_min = int(config.get('delay_min_seconds', 20))
    delay_max = int(config.get('delay_max_seconds', 60))
    _push_event(user_id, 'estimate',
                batch_count=len(pending),
                total_remaining=min(summary.get('pending', 0), remaining_today),
                delay_min=delay_min,
                delay_max=delay_max,
                batch_size=batch_size,
                batch_pause_minutes=batch_pause_minutes)

    if not os.path.exists(NODE_SCRIPT):
        _push_event(user_id, 'error', message='node/sender.js não encontrado. Execute o setup primeiro.')
        return

    env = os.environ.copy()
    env['DATA_DIR'] = _get_user_data_dir(user_id)

    env['PYTHONIOENCODING'] = 'utf-8'

    proc = subprocess.Popen(
        ['node', NODE_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding='utf-8',
        bufsize=1,
        cwd=BASE_DIR,
        env=env,
    )

    ust = _get_user_state(user_id)
    with ust['lock']:
        ust['node_proc'] = proc

    resp_queue: queue.Queue = queue.Queue()

    def _read_stdout():
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get('status') == 'qr':
                    qr_raw = msg.get('qr', '')
                    try:
                        png = _qr_to_png_base64(qr_raw)
                    except Exception as e:
                        import sys
                        print(f'[app] _qr_to_png_base64 falhou: {e}', file=sys.stderr)
                        png = qr_raw  # fallback: JS renderiza
                    with ust['lock']:
                        ust['qr_data'] = png
                    _push_event(user_id, 'qr')
                elif msg.get('status') == 'ready':
                    with ust['lock']:
                        ust['qr_data'] = None
                resp_queue.put(msg)
            except json.JSONDecodeError:
                pass

    threading.Thread(target=_read_stdout, daemon=True).start()

    _push_event(user_id, 'info', message='Aguardando conexão com WhatsApp… (escaneie o QR se solicitado)')

    deadline = time.monotonic() + 300   # 5 min para escanear QR
    ready = False

    while time.monotonic() < deadline:
        if ust['stop_event'].is_set():
            proc.terminate()
            _push_event(user_id, 'info', message='Envio cancelado pelo usuário.')
            return
        if proc.poll() is not None:
            _push_event(user_id, 'error', message='Processo Node.js encerrou inesperadamente.')
            return
        try:
            msg = resp_queue.get(timeout=2)
        except queue.Empty:
            continue
        if msg.get('status') == 'ready':
            ready = True
            _push_event(user_id, 'connected', message='WhatsApp conectado! Iniciando envio…')
            break

    if not ready:
        proc.terminate()
        _push_event(user_id, 'error', message='Timeout aguardando conexão com WhatsApp.')
        return

    sent_count   = 0
    failed_count = 0

    for contact in pending:
        if ust['stop_event'].is_set():
            _push_event(user_id, 'info', message='Envio pausado pelo usuário.')
            break
        if proc.poll() is not None:
            _push_event(user_id, 'error', message='Conexão com WhatsApp perdida.')
            break

        global_idx = already + sent_count + failed_count + 1
        payload = json.dumps({
            'id':       contact['id'],
            'nome':     contact['nome'],
            'numero':   contact['numero'],
            'mensagem': contact['mensagem'],
        })

        try:
            proc.stdin.write(payload + '\n')
            proc.stdin.flush()
        except BrokenPipeError:
            _push_event(user_id, 'error', message='Conexão com Node.js perdida.')
            break

        response    = None
        msg_deadline = time.monotonic() + 90
        while time.monotonic() < msg_deadline:
            if proc.poll() is not None:
                break
            try:
                response = resp_queue.get(timeout=2)
                break
            except queue.Empty:
                continue

        if response is None:
            tracker.mark_failed(contact['id'], 'Timeout', db_path)
            failed_count += 1
            _push_event(user_id, 'result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=False, detail='timeout')
        elif response.get('status') == 'sent':
            tracker.mark_sent(contact['id'], db_path)
            sent_count += 1
            _push_event(user_id, 'result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=True)
        else:
            err = response.get('error', 'Erro desconhecido')
            tracker.mark_failed(contact['id'], err, db_path)
            failed_count += 1
            _push_event(user_id, 'result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=False, detail=err)

    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.wait()

    remaining = tracker.get_summary(db_path).get('pending', 0)
    msg = f'Lote concluído — Enviadas: {sent_count} | Falhas: {failed_count}'
    if remaining > 0:
        msg += f' | Pendentes restantes: {remaining} (aguarde {batch_pause_minutes} min e envie novamente)'
    _push_event(user_id, 'batch_done', message=msg, sent=sent_count, failed=failed_count, remaining=remaining)


# ---------------------------------------------------------------------------
# Health check — evita spin-down no Render Free Tier
# ---------------------------------------------------------------------------

@app.route('/health')
def health():
    return jsonify(status='ok'), 200


# ---------------------------------------------------------------------------
# Falhas de envio
# ---------------------------------------------------------------------------

@app.route('/api/failed')
def api_failed():
    user_id = session['user_id']
    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    return jsonify(failed=tracker.get_failed(db_path))


@app.route('/api/failed/csv')
def api_failed_csv():
    import io
    import csv as _csv
    user_id = session['user_id']
    db_path = _get_db_path(user_id)
    tracker.init_db(db_path)
    failed = tracker.get_failed(db_path)
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(['nome', 'numero', 'mensagem', 'motivo'])
    for c in failed:
        w.writerow([c['nome'], c['numero'], c.get('mensagem', ''), c.get('erro', '')])
    return Response(
        '\ufeff' + buf.getvalue(),   # BOM para Excel abrir em UTF-8
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=falhas.csv'},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    auth.init_users_db()
    os.makedirs(DATA_DIR, exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    is_cloud = bool(os.environ.get('RENDER') or os.environ.get('RAILWAY_ENVIRONMENT')
                    or os.environ.get('FLY_APP_NAME') or os.environ.get('CLOUD', ''))
    if not is_cloud:
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    print()
    print('=' * 52)
    print('  WhatsApp Sender — Interface Web (Multi-usuário)')
    if is_cloud:
        print(f'  Rodando em modo cloud na porta {port}')
    else:
        print(f'  Acesse: http://127.0.0.1:{port}')
    print('  Para encerrar: Ctrl+C')
    print('=' * 52)
    print()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
