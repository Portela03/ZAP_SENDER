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
        raise ValueError(f"Número inválido (vazio): {raw!r}")

    # Already has country code (12+ digits starting with 55)
    if digits.startswith('55') and len(digits) >= 12:
        return '+' + digits

    # Brazilian number without country code (10 or 11 digits)
    if len(digits) in (10, 11):
        return '+55' + digits

    # Has some other country code (12+ digits, not starting with 55)
    if len(digits) >= 12:
        return '+' + digits

    raise ValueError(
        f"Número inválido: {raw!r} → {digits!r} "
        f"({len(digits)} dígitos). Use formato: 11999999999 ou +5511999999999"
    )


def _read_csv(filepath: str) -> pd.DataFrame:
    """
    Read a CSV file trying different separators and handling lines with
    unquoted commas in message fields (graceful fallback).
    """
    # 1st attempt: standard comma-separated
    try:
        return pd.read_csv(filepath, dtype=str)
    except pd.errors.ParserError:
        pass

    # 2nd attempt: semicolon-separated (common in Brazilian Excel exports)
    try:
        df = pd.read_csv(filepath, dtype=str, sep=';')
        if len(df.columns) >= 2:
            return df
    except pd.errors.ParserError:
        pass

    # 3rd attempt: skip malformed lines (e.g. unquoted commas inside fields)
    df = pd.read_csv(filepath, dtype=str, on_bad_lines='skip')

    # Count skipped lines and warn
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
            total_data_lines = sum(1 for _ in fh) - 1  # minus header
        skipped = total_data_lines - len(df)
        if skipped > 0:
            print(
                f"\n⚠ {skipped} linha(s) com vírgulas não protegidas foram ignoradas.\n"
                "  Dica: se a mensagem contém vírgula, envolva-a com aspas duplas.\n"
                '  Exemplo: João Silva,11999990001,"Olá, tudo bem?"\n'
            )
    except Exception:
        pass

    return df


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
            f"Colunas obrigatórias ausentes na planilha: {', '.join(sorted(missing))}. "
            f"Colunas encontradas: {', '.join(df.columns)}"
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
            errors.append(f"Linha {line_num}: {e}")
            continue

        # Apply message template
        try:
            message = template.format_map(row_dict)
        except KeyError as e:
            errors.append(
                f"Linha {line_num}: variável {e} usada no template não existe na planilha. "
                f"Colunas disponíveis: {', '.join(df.columns)}"
            )
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

    return contacts
