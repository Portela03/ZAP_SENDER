#!/usr/bin/env python3
"""
WhatsApp Sender — Interface Web
Local : http://127.0.0.1:5000
Cloud : use a URL gerada pelo Render/Railway
"""

import json
import os
import queue
import secrets
import subprocess
import threading
import time
import webbrowser

from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, session, url_for,
)

import csv_loader
import tracker

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

ACCESS_PASSWORD = os.environ.get('ACCESS_PASSWORD', '')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NODE_SCRIPT = os.path.join(BASE_DIR, 'node', 'sender.js')


def _get_auth_dir() -> str:
    """Caminho da pasta auth_info, respeitando DATA_DIR em cloud."""
    data_dir = os.environ.get('DATA_DIR', '')
    if data_dir:
        return os.path.join(data_dir, 'auth_info')
    return os.path.join(BASE_DIR, 'node', 'auth_info')


def _session_exists() -> bool:
    return os.path.exists(os.path.join(_get_auth_dir(), 'creds.json'))


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
# Estado global
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
state = {
    'send_running': False,
    'qr_data':      None,   # data:image/png;base64,...
    'node_proc':    None,
    'stop_event':   threading.Event(),
}

_sse_lock = threading.Lock()
_sse_listeners: list = []   # list[queue.Queue]


def _push_event(event_type: str, **kwargs):
    """Envia evento SSE para todos os clientes conectados."""
    payload = json.dumps({'type': event_type, **kwargs})
    with _sse_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)


# ---------------------------------------------------------------------------
# Autenticação por senha (opcional — ativa quando ACCESS_PASSWORD está definida)
# ---------------------------------------------------------------------------

_PUBLIC_ENDPOINTS = {'login', 'static'}


@app.before_request
def _check_auth():
    if not ACCESS_PASSWORD:
        return  # sem senha configurada → acesso livre (uso local)
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not session.get('authenticated'):
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ACCESS_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        error = 'Senha incorreta.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    resp = render_template('index.html')
    from flask import make_response
    r = make_response(resp)
    r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return r


@app.route('/api/status')
def api_status():
    tracker.init_db()
    summary = tracker.get_summary()
    with _state_lock:
        running = state['send_running']
        has_qr  = state['qr_data'] is not None
    return jsonify({
        'summary': summary,
        'send_running': running,
        'has_qr': has_qr,
        'session_exists': _session_exists(),
    })


@app.route('/api/whatsapp/disconnect', methods=['POST'])
def api_whatsapp_disconnect():
    import shutil
    with _state_lock:
        if state['send_running']:
            return jsonify({'error': 'Pare o envio antes de desconectar.'}), 400
        proc = state['node_proc']
        if proc and proc.poll() is None:
            proc.terminate()
    auth_dir = _get_auth_dir()
    try:
        if os.path.exists(auth_dir):
            shutil.rmtree(auth_dir)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp/connect', methods=['POST'])
def api_whatsapp_connect():
    with _state_lock:
        if state['send_running']:
            return jsonify({'error': 'Já em andamento.'}), 400
    state['stop_event'].clear()
    threading.Thread(target=_connect_worker, daemon=True).start()
    return jsonify({'ok': True})


def _connect_worker():
    """Inicia o Node.js apenas para estabelecer sessão via QR (sem enviar mensagens)."""
    with _state_lock:
        state['send_running'] = True
        state['qr_data']      = None
    try:
        if not os.path.exists(NODE_SCRIPT):
            _push_event('error', message='node/sender.js não encontrado.')
            return

        proc = subprocess.Popen(
            ['node', NODE_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
        )
        with _state_lock:
            state['node_proc'] = proc

        resp_queue: queue.Queue = queue.Queue()

        def _read():
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get('status') == 'qr':
                        raw = msg.get('qr', '')
                        try:
                            png = _qr_to_png_base64(raw)
                        except Exception as e:
                            import sys
                            print(f'[app] _qr_to_png_base64 falhou: {e}', file=sys.stderr)
                            png = raw  # fallback: envia string bruta, JS renderiza
                        with _state_lock:
                            state['qr_data'] = png
                        _push_event('qr')
                    elif msg.get('status') == 'ready':
                        with _state_lock:
                            state['qr_data'] = None
                    resp_queue.put(msg)
                except json.JSONDecodeError:
                    pass

        threading.Thread(target=_read, daemon=True).start()
        _push_event('info', message='Aguardando conexão com WhatsApp… (escaneie o QR)')

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if state['stop_event'].is_set():
                proc.terminate()
                _push_event('info', message='Conexão cancelada.')
                return
            if proc.poll() is not None:
                _push_event('error', message='Processo Node.js encerrou inesperadamente.')
                return
            try:
                msg = resp_queue.get(timeout=2)
            except queue.Empty:
                continue
            if msg.get('status') == 'ready':
                _push_event('connected', message='WhatsApp conectado! Sessão salva com sucesso.')
                break

        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        proc.wait()

    except Exception as e:
        _push_event('error', message=str(e))
    finally:
        with _state_lock:
            state['send_running'] = False
            state['qr_data']      = None
            state['node_proc']    = None
        _push_event('done')


@app.route('/api/qr')
def api_qr():
    with _state_lock:
        qr = state['qr_data']
    return jsonify({'qr': qr})


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

    temp_path = os.path.join(BASE_DIR, f'_upload_temp{ext}')
    f.save(temp_path)

    try:
        tracker.init_db()
        contacts = csv_loader.load_contacts(temp_path)
        if not contacts:
            return jsonify({'error': 'Nenhum contato válido encontrado na planilha.'}), 400
        inserted, skipped = tracker.upsert_contacts(contacts)
        return jsonify({'inserted': inserted, 'skipped': skipped})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.route('/api/send', methods=['POST'])
def api_send():
    with _state_lock:
        if state['send_running']:
            return jsonify({'error': 'Envio já em andamento'}), 400

    tracker.init_db()
    summary = tracker.get_summary()
    if not summary.get('pending', 0):
        return jsonify({'error': 'Nenhuma mensagem pendente. Carregue um arquivo CSV primeiro.'}), 400

    state['stop_event'].clear()
    threading.Thread(target=_send_worker, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    state['stop_event'].set()
    with _state_lock:
        proc = state['node_proc']
    if proc and proc.poll() is None:
        proc.terminate()
    return jsonify({'ok': True})


@app.route('/api/retry', methods=['POST'])
def api_retry():
    tracker.init_db()
    summary = tracker.get_summary()
    failed = summary.get('failed', 0)
    if failed == 0:
        return jsonify({'error': 'Nenhuma mensagem com falha para retentar.'}), 400
    tracker.reset_failed()
    return jsonify({'reset': failed})


@app.route('/api/progress')
def api_progress():
    q: queue.Queue = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_listeners.append(q)

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f'data: {data}\n\n'
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _sse_lock:
                if q in _sse_listeners:
                    _sse_listeners.remove(q)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------------------------------------------------------------------------
# Worker de envio (thread background)
# ---------------------------------------------------------------------------

def _send_worker():
    with _state_lock:
        state['send_running'] = True
        state['qr_data']      = None

    try:
        _run_send()
    except Exception as e:
        _push_event('error', message=str(e))
    finally:
        with _state_lock:
            state['send_running'] = False
            state['qr_data']      = None
            state['node_proc']    = None
        _push_event('done')


def _run_send():
    config_path = os.path.join(BASE_DIR, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    batch_size          = int(config.get('batch_size', 50))
    batch_pause_minutes = int(config.get('batch_pause_minutes', 60))

    pending = tracker.get_pending(batch_size)
    if not pending:
        _push_event('info', message='Nenhuma mensagem pendente.')
        return

    summary    = tracker.get_summary()
    already    = summary.get('sent', 0)
    total_all  = sum(summary.values())

    _push_event('info', message=f'Iniciando lote: {len(pending)} mensagem(s) | {already}/{total_all} já enviadas')

    if not os.path.exists(NODE_SCRIPT):
        _push_event('error', message='node/sender.js não encontrado. Execute o setup primeiro.')
        return

    proc = subprocess.Popen(
        ['node', NODE_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
    )

    with _state_lock:
        state['node_proc'] = proc

    resp_queue: queue.Queue = queue.Queue()

    def _read_stdout():
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                # Captura QR e converte para PNG base64 (Python-side)
                if msg.get('status') == 'qr':
                    raw = msg.get('qr', '')
                    try:
                        png = _qr_to_png_base64(raw)
                    except Exception as e:
                        import sys
                        print(f'[app] _qr_to_png_base64 falhou: {e}', file=sys.stderr)
                        png = raw  # fallback: JS renderiza
                    with _state_lock:
                        state['qr_data'] = png
                    _push_event('qr')
                elif msg.get('status') == 'ready':
                    with _state_lock:
                        state['qr_data'] = None
                resp_queue.put(msg)
            except json.JSONDecodeError:
                pass

    threading.Thread(target=_read_stdout, daemon=True).start()

    _push_event('info', message='Aguardando conexão com WhatsApp… (escaneie o QR se solicitado)')

    deadline = time.monotonic() + 300   # 5 min para escanear QR
    ready = False

    while time.monotonic() < deadline:
        if state['stop_event'].is_set():
            proc.terminate()
            _push_event('info', message='Envio cancelado pelo usuário.')
            return
        if proc.poll() is not None:
            _push_event('error', message='Processo Node.js encerrou inesperadamente.')
            return
        try:
            msg = resp_queue.get(timeout=2)
        except queue.Empty:
            continue
        if msg.get('status') == 'ready':
            ready = True
            _push_event('connected', message='WhatsApp conectado! Iniciando envio…')
            break

    if not ready:
        proc.terminate()
        _push_event('error', message='Timeout aguardando conexão com WhatsApp.')
        return

    sent_count   = 0
    failed_count = 0

    for contact in pending:
        if state['stop_event'].is_set():
            _push_event('info', message='Envio pausado pelo usuário.')
            break
        if proc.poll() is not None:
            _push_event('error', message='Conexão com WhatsApp perdida.')
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
            _push_event('error', message='Conexão com Node.js perdida.')
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
            tracker.mark_failed(contact['id'], 'Timeout')
            failed_count += 1
            _push_event('result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=False, detail='timeout')
        elif response.get('status') == 'sent':
            tracker.mark_sent(contact['id'])
            sent_count += 1
            _push_event('result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=True)
        else:
            err = response.get('error', 'Erro desconhecido')
            tracker.mark_failed(contact['id'], err)
            failed_count += 1
            _push_event('result', index=global_idx, total=total_all,
                        nome=contact['nome'], numero=contact['numero'],
                        ok=False, detail=err)

    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.wait()

    remaining = tracker.get_summary().get('pending', 0)
    msg = f'Lote concluído — Enviadas: {sent_count} | Falhas: {failed_count}'
    if remaining > 0:
        msg += f' | Pendentes restantes: {remaining} (aguarde {batch_pause_minutes} min e envie novamente)'
    _push_event('batch_done', message=msg, sent=sent_count, failed=failed_count, remaining=remaining)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tracker.init_db()
    port = int(os.environ.get('PORT', 5000))
    is_cloud = bool(os.environ.get('RENDER') or os.environ.get('RAILWAY_ENVIRONMENT')
                    or os.environ.get('FLY_APP_NAME') or os.environ.get('CLOUD', ''))
    if not is_cloud:
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    print()
    print('=' * 52)
    print('  WhatsApp Sender — Interface Web')
    if is_cloud:
        print(f'  Rodando em modo cloud na porta {port}')
        if ACCESS_PASSWORD:
            print('  Senha de acesso: configurada via ACCESS_PASSWORD')
        else:
            print('  AVISO: ACCESS_PASSWORD nao definida — acesso publico!')
    else:
        print(f'  Acesse: http://127.0.0.1:{port}')
    print('  Para encerrar: Ctrl+C')
    print('=' * 52)
    print()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
