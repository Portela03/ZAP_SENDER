#!/usr/bin/env python3
"""
WhatsApp Sender — Orquestrador principal
Uso:
  python main.py --load contacts.csv    # carregar contatos
  python main.py --send                 # enviar próximo lote
  python main.py --status               # ver progresso
  python main.py --retry                # retentar falhas
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time

import csv_loader
import tracker

# Caminho do script Node.js (relativo a este arquivo)
NODE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'node', 'sender.js')


# ---------------------------------------------------------------------------
# Helpers de exibição
# ---------------------------------------------------------------------------

def _bar(char: str = '=', width: int = 50) -> str:
    return char * width


def _print_header(title: str):
    print(f"\n{_bar()}")
    print(f"  {title}")
    print(_bar())


def _print_line(index: int, total: int, nome: str, numero: str, ok: bool, extra: str = ''):
    icon = '✓' if ok else '✗'
    suffix = f'  {extra}' if extra else ''
    print(f"  [{index}/{total}] {icon} {nome} ({numero}){suffix}")


# ---------------------------------------------------------------------------
# Comando: --load
# ---------------------------------------------------------------------------

def cmd_load(filepath: str):
    if not os.path.exists(filepath):
        print(f"[ERRO] Arquivo não encontrado: {filepath}")
        sys.exit(1)

    print(f"Carregando contatos de: {filepath}")
    tracker.init_db()

    contacts, import_errors = csv_loader.load_contacts(filepath)
    if import_errors:
        for err in import_errors:
            print(f"  ⚠ {err}")
    if not contacts:
        print("[ERRO] Nenhum contato válido encontrado na planilha.")
        sys.exit(1)

    inserted, skipped = tracker.upsert_contacts(contacts)

    print(f"  ✓ {inserted} contato(s) novo(s) adicionado(s)")
    if skipped:
        print(f"  — {skipped} contato(s) já existente(s) ignorado(s) (sem reenvio)")

    summary = tracker.get_summary()
    print(f"\nPendentes para envio: {summary.get('pending', 0)}")
    print("Use  python main.py --send  para iniciar o envio.")


# ---------------------------------------------------------------------------
# Comando: --send
# ---------------------------------------------------------------------------

def cmd_send():
    tracker.init_db()

    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    batch_size = int(config.get('batch_size', 50))
    batch_pause_minutes = int(config.get('batch_pause_minutes', 60))

    pending = tracker.get_pending(batch_size)
    if not pending:
        print("Nenhuma mensagem pendente.")
        print("Use --status para ver o resumo ou --retry para retentar falhas.")
        return

    summary = tracker.get_summary()
    total_all = sum(summary.values())
    already_sent = summary.get('sent', 0)

    _print_header("INICIANDO ENVIO DE LOTE")
    print(f"  Lote atual : {len(pending)} mensagens")
    print(f"  Já enviadas: {already_sent} de {total_all} total")
    print(_bar())
    print()

    # Verificar se o script Node.js existe
    if not os.path.exists(NODE_SCRIPT):
        print("[ERRO] node/sender.js não encontrado.")
        print("  Execute  bash setup.sh  antes de usar --send.")
        sys.exit(1)

    # Iniciar processo Node.js (Baileys)
    proc = subprocess.Popen(
        ['node', NODE_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,   # stderr vai direto ao terminal (QR code, logs)
        text=True,
        bufsize=1,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    response_queue: queue.Queue = queue.Queue()

    def _read_stdout():
        """Lê respostas JSON do Node.js em thread separada."""
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                response_queue.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    reader = threading.Thread(target=_read_stdout, daemon=True)
    reader.start()

    # -----------------------------------------------------------------------
    # Aguardar sinal "ready" do Baileys (QR scan pode levar ~60s)
    # -----------------------------------------------------------------------
    print("Aguardando conexão com WhatsApp...")
    print("(Se aparecer um QR code acima, escaneie com seu celular)")
    print()

    CONNECT_TIMEOUT = 300  # 5 minutos para escaneamento do QR
    try:
        while True:
            if proc.poll() is not None:
                print("[ERRO] O processo Node.js encerrou inesperadamente.")
                sys.exit(1)
            try:
                msg = response_queue.get(timeout=5)
            except queue.Empty:
                continue
            if msg.get('status') == 'ready':
                print("[OK] WhatsApp conectado! Iniciando envio...\n")
                break
            # Se chegou outra mensagem antes de 'ready', descarta e continua
    except KeyboardInterrupt:
        proc.terminate()
        print("\nCancelado pelo usuário.")
        return

    # -----------------------------------------------------------------------
    # Loop de envio
    # -----------------------------------------------------------------------
    sent_count = 0
    failed_count = 0
    total_batch = len(pending)

    try:
        for i, contact in enumerate(pending, start=1):
            global_index = already_sent + sent_count + failed_count + 1

            # Enviar contato para o Node.js via stdin
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
                print("[ERRO] Conexão com Node.js perdida.")
                break

            # Aguardar resposta (timeout: 90s por mensagem)
            response = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    response = response_queue.get(timeout=2)
                    break
                except queue.Empty:
                    continue

            if response is None:
                tracker.mark_failed(contact['id'], 'Timeout aguardando resposta')
                failed_count += 1
                _print_line(global_index, total_all, contact['nome'], contact['numero'], False, '(timeout)')
                continue

            if response.get('status') == 'sent':
                tracker.mark_sent(contact['id'])
                sent_count += 1
                _print_line(global_index, total_all, contact['nome'], contact['numero'], True)
            else:
                error_msg = response.get('error', 'Erro desconhecido')
                tracker.mark_failed(contact['id'], error_msg)
                failed_count += 1
                _print_line(global_index, total_all, contact['nome'], contact['numero'], False, error_msg)

    except KeyboardInterrupt:
        print("\n\nEnvio pausado pelo usuário.")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()

    # -----------------------------------------------------------------------
    # Resumo do lote
    # -----------------------------------------------------------------------
    _print_header("LOTE CONCLUÍDO")
    print(f"  Enviadas: {sent_count}  |  Falhas: {failed_count}")

    remaining = tracker.get_summary().get('pending', 0)
    if remaining > 0:
        print(f"\n  Pendentes restantes: {remaining}")
        print(f"  Aguarde {batch_pause_minutes} min e execute  python main.py --send  novamente.")
    else:
        print("\n  Todas as mensagens foram processadas!")
    print(_bar())
    print()


# ---------------------------------------------------------------------------
# Comando: --status
# ---------------------------------------------------------------------------

def cmd_status():
    tracker.init_db()
    summary = tracker.get_summary()

    if not summary:
        print("Nenhum dado encontrado. Use  python main.py --load <arquivo>  para começar.")
        return

    total = sum(summary.values())
    _print_header("STATUS DA CAMPANHA")
    print(f"  Pendentes : {summary.get('pending', 0)}")
    print(f"  Enviadas  : {summary.get('sent', 0)}")
    print(f"  Falhas    : {summary.get('failed', 0)}")
    print(f"  Total     : {total}")
    print(_bar())
    print()


# ---------------------------------------------------------------------------
# Comando: --retry
# ---------------------------------------------------------------------------

def cmd_retry():
    tracker.init_db()
    summary_before = tracker.get_summary()
    failed_before = summary_before.get('failed', 0)

    if failed_before == 0:
        print("Nenhuma mensagem com falha para retentar.")
        return

    tracker.reset_failed()
    print(f"  {failed_before} mensagem(s) redefinida(s) para pendente.")
    print("Use  python main.py --send  para retentar o envio.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='WhatsApp Sender — envio automatizado via Baileys (Android/Termux)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Exemplos:\n'
            '  python main.py --load contacts.csv\n'
            '  python main.py --send\n'
            '  python main.py --status\n'
            '  python main.py --retry\n'
        )
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--load',   metavar='ARQUIVO', help='Carregar contatos de CSV ou Excel')
    group.add_argument('--send',   action='store_true', help='Enviar próximo lote de mensagens')
    group.add_argument('--status', action='store_true', help='Ver status da campanha')
    group.add_argument('--retry',  action='store_true', help='Retentar mensagens com falha')

    args = parser.parse_args()

    if args.load:
        cmd_load(args.load)
    elif args.send:
        cmd_send()
    elif args.status:
        cmd_status()
    elif args.retry:
        cmd_retry()


if __name__ == '__main__':
    main()
