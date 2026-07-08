import re
import json
import pandas as pd


def load_config() -> dict:
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def normalize_phone(raw: str) -> str:
    """Normalize phone number to international format (+55...)."""
    digits = re.sub(r'\D', '', str(raw).strip())

    if not digits:
        raise ValueError(
            f"Número vazio ou sem dígitos: {raw!r}. "
            "Exemplos válidos: 11999990001 | (11) 99999-0001 | +5511999990001"
        )

    # Already has country code (12+ digits starting with 55)
    if digits.startswith('55') and len(digits) >= 12:
        return '+' + digits

    # Brazilian number without country code (10 or 11 digits)
    if len(digits) in (10, 11):
        return '+55' + digits

    # Has some other country code (12+ digits, not starting with 55)
    if len(digits) >= 12:
        return '+' + digits

    if len(digits) < 10:
        raise ValueError(
            f"Número muito curto: {raw!r} → {digits!r} ({len(digits)} dígitos). "
            "Mínimo: 10 dígitos (DDD + número). Ex: 11999990001"
        )

    raise ValueError(
        f"Número inválido: {raw!r} → {digits!r} ({len(digits)} dígitos). "
        "Formatos aceitos: 11999999999 | +5511999999999 | (11) 99999-9999"
    )


def _read_csv(filepath: str) -> pd.DataFrame:
    """
    Read a CSV file handling messages that contain unquoted commas by
    treating everything after the 2nd comma as the message field.
    """
    rows = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
            lines = [ln.rstrip('\n\r') for ln in fh if ln.strip()]
    except Exception:
        return pd.read_csv(filepath, dtype=str)

    if not lines:
        return pd.DataFrame()

    # Detect header
    header = [h.strip().lower() for h in lines[0].split(',')]
    # Determine column positions by name or positional fallback
    try:
        i_nome = header.index('nome')
        i_num  = header.index('numero')
        i_msg  = header.index('mensagem')
    except ValueError:
        i_nome, i_num, i_msg = 0, 1, 2

    ignored = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(',')
        if len(parts) < 3:
            ignored += 1
            continue
        nome    = parts[i_nome].strip()
        numero  = parts[i_num].strip()
        # Junta todos os campos restantes como mensagem (lida com vírgulas internas)
        msg_parts = parts[i_msg:] if i_msg < len(parts) else parts[2:]
        mensagem = ','.join(msg_parts).strip().strip('"')
        if not nome or not numero or not mensagem:
            ignored += 1
            continue
        rows.append({'nome': nome, 'numero': numero, 'mensagem': mensagem})

    if ignored > 0:
        print(f"\n⚠ {ignored} linha(s) ignoradas (campos insuficientes).\n")


FORMAT_HINT = (
    "Formato esperado: nome,numero,mensagem\n"
    "Exemplos de número válidos: 11999990001 | +5511999990001 | (11) 99999-0001 | 5511999990001"
)


def load_contacts(filepath: str) -> list:
    """Load contacts from CSV or Excel file and apply message template."""
    if filepath.lower().endswith('.csv'):
        df = _read_csv(filepath)
    elif filepath.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(filepath, dtype=str)
    else:
        raise ValueError(f"Formato não suportado: {filepath}. Use .csv, .xlsx ou .xls")

    # Normalize column names to lowercase and strip spaces
    df.columns = df.columns.str.strip().str.lower()

    required_cols = {'nome', 'numero'}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(
            f"Coluna(s) obrigatória(s) ausente(s): {', '.join(sorted(missing))}. "
            f"Colunas encontradas na planilha: {', '.join(df.columns)}. "
            "Certifique-se que a primeira linha da planilha é o cabeçalho: nome,numero,mensagem"
        )

    df = df.fillna('')

    config = load_config()
    template = config.get('message_template', 'Olá {nome}, {mensagem}')

    contacts = []
    errors = []

    for idx, row in df.iterrows():
        row_dict = {k: v.strip() for k, v in row.items()}
        line_num = idx + 2  # +2: header row + 1-based index

        # Normalize phone number
        try:
            row_dict['numero'] = normalize_phone(row_dict['numero'])
        except ValueError as e:
            errors.append(f"Linha {line_num} (número inválido): {e}")
            continue

        # Apply message template
        try:
            message = template.format_map(row_dict)
        except KeyError as e:
            errors.append(
                f"Linha {line_num}: a variável {e} usada no template não existe na planilha. "
                f"Colunas disponíveis: {', '.join(df.columns)}. "
                "Edite o template em Configurações ou adicione a coluna à planilha."
            )
            continue

        if not row_dict.get('nome', '').strip():
            errors.append(f"Linha {line_num}: campo 'nome' está vazio.")
            continue

        contacts.append({
            'nome': row_dict['nome'],
            'numero': row_dict['numero'],
            'mensagem': message,
        })

    if errors:
        print(f"\n⚠ Atenção — {len(errors)} linha(s) com problema:")
        for err in errors:
            print(f"  • {err}")
        print()

    return contacts, errors
