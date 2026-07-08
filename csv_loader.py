import csv
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
    text = None
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=encoding, errors='strict') as fh:
                text = fh.read()
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        return pd.read_csv(filepath, dtype=str)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    try:
        dialect = csv.Sniffer().sniff('\n'.join(lines[:20]), delimiters=',;\t')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ';' if lines[0].count(';') > lines[0].count(',') else ','

    try:
        parsed_rows = list(csv.reader(lines, delimiter=delimiter))
    except csv.Error:
        return pd.read_csv(filepath, dtype=str)

    if not parsed_rows:
        return pd.DataFrame()

    header = []
    for idx, col in enumerate(parsed_rows[0]):
        name = col.strip().lower().lstrip('\ufeff')
        header.append(name or f'coluna_{idx + 1}')

    if not header:
        return pd.DataFrame()

    message_idx = header.index('mensagem') if 'mensagem' in header else None

    rows = []
    ignored = 0
    for parts in parsed_rows[1:]:
        if not any(str(part).strip() for part in parts):
            continue

        if len(parts) > len(header) and message_idx is not None:
            extra_count = len(parts) - len(header)
            message_end = message_idx + extra_count + 1
            parts = (
                parts[:message_idx]
                + [delimiter.join(parts[message_idx:message_end])]
                + parts[message_end:]
            )

        if len(parts) < len(header):
            parts = parts + [''] * (len(header) - len(parts))

        if len(parts) != len(header):
            ignored += 1
            continue

        rows.append({header[idx]: value.strip() for idx, value in enumerate(parts)})

    if ignored > 0:
        print(f"\n⚠ {ignored} linha(s) ignoradas (campos insuficientes).\n")

    return pd.DataFrame(rows, columns=header)


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
