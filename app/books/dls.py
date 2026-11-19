"""Read the DLS list export as an immutable startup snapshot."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from os import fstat
from pathlib import Path
from zipfile import BadZipFile

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from xml.etree.ElementTree import ParseError

from app.books.catalog import CatalogSnapshot
from app.books.isbn import normalize_isbn

SHEET = '리스트 반출'
REQUIRED = ('등록번호', '자료명', '저자', '출판사', '출판년도')
OPTIONAL = ('ISBN', '자료유형', '자료상태')


def load_dls(path):
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ValueError('DLS .xlsx 파일을 지정해 주세요.')
    try:
        with path.open('rb') as source:
            # Both the timestamp and the workbook come from the same open file.
            updated_at = datetime.fromtimestamp(fstat(source.fileno()).st_mtime, timezone.utc)
            workbook = load_workbook(source, read_only=True, data_only=False, keep_links=False)
            try:
                rows = read_rows(workbook)
            finally:
                workbook.close()
    except (BadZipFile, DefusedXmlException, InvalidFileException, KeyError, ParseError):
        raise ValueError('유효한 DLS .xlsx 통합 문서가 아니에요.') from None
    return CatalogSnapshot(rows, updated_at)


def read_rows(workbook):
    if SHEET not in workbook.sheetnames:
        raise ValueError(f'{SHEET} 시트가 필요해요.')
    sheet = workbook[SHEET]
    # Ignore stale worksheet dimension metadata left by some exports.
    sheet.reset_dimensions()
    data = sheet.iter_rows()
    header = tuple(str(cell.value or '').strip() for cell in next(data, ()))
    for name in (*REQUIRED, *OPTIONAL):
        if (name in REQUIRED and name not in header) or header.count(name) > 1:
            raise ValueError(f'1행의 {name} 열을 확인해 주세요.')
    columns = {name: header.index(name) for name in (*REQUIRED, *OPTIONAL) if name in header}
    rows = {}
    for number, row in enumerate(data, 2):
        if all(cell.value is None for cell in row):
            continue
        fields = {}
        for name, index in columns.items():
            cell = row[index] if index < len(row) else None
            value = cell.value if cell is not None else None
            if value is not None and (cell.data_type in ('f', 'e') or type(value) not in (str, int)):
                raise ValueError(f'{number}행의 {name} 값 형식을 확인해 주세요.')
            fields[name] = str(value).strip() if value is not None else ''
        if not fields['등록번호'] or not fields['자료명']:
            raise ValueError(f'{number}행에 등록번호와 자료명이 필요해요.')
        raw_isbn = fields.get('ISBN')
        try:
            isbn = normalize_isbn(raw_isbn) if raw_isbn else None
        except ValueError:
            raise ValueError(f'{number}행의 ISBN을 확인해 주세요.') from None
        year_text = fields['출판년도']
        year = int(year_text) if len(year_text) == 4 and year_text.isascii() and year_text.isdigit() else None
        # Preserve distinct entries with the same accession number. Collapse only
        # repeats of the same accession and bibliographic values.
        identity = [fields[name] for name in REQUIRED]
        digest = sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        previous = rows.get(digest)
        if previous and previous['isbn'] != isbn:
            raise ValueError(f'{number}행에 같은 자료의 ISBN이 두 가지로 기록되어 있어요.')
        rows[digest] = {'id': f'dls-{digest}', 'isbn': isbn,
                        'title': fields['자료명'], 'author': fields['저자'],
                        'publisher': fields['출판사'], 'year': year,
                        'material_type': fields.get('자료유형'), 'library_status': fields.get('자료상태')}
    if not rows:
        raise ValueError('소장 자료 행이 없어요.')
    return list(rows.values())
