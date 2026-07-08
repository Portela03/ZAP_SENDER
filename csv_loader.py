import csv
import re
import json
import pandas as pd

DEFAULT_COUNTRY_CODE = '55'
DEFAULT_AREA_CODE = '11'


def load_config() -> dict:
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def normalize_phone(raw: str, default_country_code: str = DEFAULT_COUNTRY_CODE,
                    default_area_code: str = DEFAULT_AREA_CODE) -> str:
    """Normalize phone number to international format (+55...)."""
    raw_text = str(raw).strip()
    digits = re.sub(r'\D', '', raw_text)

    if not digits:
        raise ValueError(
            f"Número vazio ou sem dígitos: {raw!r}. "
            "Exemplos válidos: 11999990001 | (11) 99999-0001 | +5511999990001"
        )

    explicit_international = raw_text.lstrip().startswith('+')
    if digits.startswith('00') and len(digits) > 10:
        digits = digits[2:]
        explicit_international = True

    if explicit_international:
        if len(digits) < 10:
            raise ValueError(
                f"Número internacional muito curto: {raw!r} → {digits!r}. "
                "Use DDI + DDD + número. Ex: +5511999990001"
            )
        if len(digits) > 15:
            raise ValueError(
                f"Número internacional muito longo: {raw!r} → {digits!r} ({len(digits)} dígitos)."
            )
        return '+' + digits

    # Remove o zero de operadora/tronco comum em formatos como 011999990001.
    if digits.startswith('0') and len(digits) in (11, 12):
        trunkless = digits[1:]
        if len(trunkless) in (10, 11):
            digits = trunkless

    # Already has Brazilian country code (55 + DDD + number)
    if digits.startswith(default_country_code) and len(digits) in (12, 13):
        return '+' + digits

    # Brazilian number without country code (10 or 11 digits)
    if len(digits) in (10, 11):
        return '+' + default_country_code + digits

    # Local Brazilian number without DDD (8 or 9 digits)
    if len(digits) in (8, 9):
        return '+' + default_country_code + default_area_code + digits

    # Has some other country code (12+ digits, not starting with 55)
    if 12 <= len(digits) <= 15:
        return '+' + digits

    if len(digits) < 8:
        raise ValueError(
            f"Número muito curto: {raw!r} → {digits!r} ({len(digits)} dígitos). "
            "Mínimo: 8 dígitos. Ex: 999990001, 11999990001 ou +5511999990001"
        )

    raise ValueError(
        f"Número inválido: {raw!r} → {digits!r} ({len(digits)} dígitos). "
        "Formatos aceitos: 999999999 | 11999999999 | +5511999999999 | (11) 99999-9999"
    )


def _normalize_group_candidate(groups: list, start: int, end: int) -> tuple[str, str]:
    digits = ''.join(group['digits'] for group in groups[start:end])
    raw = ' '.join(group['token'] for group in groups[start:end])
    first_digits = groups[start]['digits']
    second_digits = groups[start + 1]['digits'] if end - start > 1 else ''

    # Evita aceitar pedaços parciais de "+55 11 99999 0001" como número local.
    looks_like_split_country_code = (
        first_digits == DEFAULT_COUNTRY_CODE
        and len(second_digits) <= 2
        and len(digits) < 12
    )
    if looks_like_split_country_code:
        raise ValueError('DDI parcial')

    if groups[start]['token'].startswith('+'):
        raw = '+' + digits

    return normalize_phone(raw), raw


def extract_phone_numbers(raw_text: str) -> tuple[list[dict], list[str]]:
    """
    Extract and normalize phone numbers from pasted free text.

    Accepts numbers separated by commas, tabs, line breaks, spaces, or common
    phone punctuation like +, (), hyphen and dots.
    """
    text = str(raw_text or '').strip()
    if not text:
        return [], ['Cole pelo menos um número.']

    groups = [
        {
            'token': match.group(0),
            'digits': re.sub(r'\D', '', match.group(0)),
        }
        for match in re.finditer(r'\+?\d+', text)
    ]
    if not groups:
        return [], ['Nenhum número encontrado no texto colado.']

    numbers = []
    warnings = []
    seen = set()
    i = 0
    while i < len(groups):
        matched = None
        max_end = min(len(groups), i + 5)
        possible_ends = []

        if len(groups[i]['digits']) >= 8:
            possible_ends.append(i + 1)
        possible_ends.extend(end for end in range(i + 1, max_end + 1) if end not in possible_ends)

        for end in possible_ends:
            try:
                normalized, raw = _normalize_group_candidate(groups, i, end)
            except ValueError:
                continue
            matched = {
                'raw': raw,
                'numero': normalized,
            }
            i = end
            break

        if matched is None:
            token = groups[i]['token']
            if len(groups[i]['digits']) >= 4:
                warnings.append(f"Trecho ignorado por não parecer telefone completo: {token}")
            i += 1
            continue

        if matched['numero'] in seen:
            warnings.append(f"Número repetido ignorado: {matched['numero']}")
            continue

        seen.add(matched['numero'])
        numbers.append(matched)

    if not numbers and not warnings:
        warnings.append('Nenhum número válido encontrado.')

    return numbers, warnings


def load_manual_contacts(numbers_text: str, message: str) -> tuple[list, list]:
    """Build contacts from pasted phone numbers and one shared message."""
    clean_message = str(message or '').strip()
    if not clean_message:
        raise ValueError('Digite a mensagem que será enviada para os números.')

    parsed_numbers, warnings = extract_phone_numbers(numbers_text)
    contacts = [
        {
            'nome': f'Contato {idx}',
            'numero': item['numero'],
            'mensagem': clean_message,
        }
        for idx, item in enumerate(parsed_numbers, start=1)
    ]
    return contacts, warnings


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
