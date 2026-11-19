"""Minimal OOXML workbook. Strings are inline text, never spreadsheet formulas.

Bold header row, frozen first row, column widths, and `#,##0` for every number.
"""
import re
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree.ElementTree import Element, SubElement, tostring
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
STYLE_NUMBER, STYLE_BOLD = '1', '2'
ESCAPE_LOOKALIKE = re.compile(r'_(?=x[0-9A-Fa-f]{4}_)')
STYLES = ('<styleSheet xmlns="%s"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
          '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>') % NS


def clean(value):
    """Remove characters forbidden by XML 1.0 while preserving ordinary text.

    A literal `_xHHHH_` would be read back as an escaped character, so its first underscore is written as `_x005F_`.
    """
    text = ''.join(c for c in str(value) if c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
    return ESCAPE_LOOKALIKE.sub('_x005F_', text)


def column(index):
    name = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def workbook(header, rows, widths, sheet='Sheet1'):
    root = Element('worksheet', xmlns=NS)
    view = SubElement(SubElement(root, 'sheetViews'), 'sheetView', workbookViewId='0')
    SubElement(view, 'pane', ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    cols = SubElement(root, 'cols')
    for i, width in enumerate(widths, 1):
        SubElement(cols, 'col', min=str(i), max=str(i), width=str(width), customWidth='1')
    data = SubElement(root, 'sheetData')
    for index, values in enumerate([header] + rows, 1):
        row = SubElement(data, 'row', r=str(index))
        for position, value in enumerate(values):
            if value is None:
                continue
            cell = SubElement(row, 'c', r=f'{column(position)}{index}')
            if index == 1:
                cell.set('s', STYLE_BOLD)
            if type(value) is int:
                cell.set('s', STYLE_NUMBER)
                SubElement(cell, 'v').text = str(value)
            else:
                cell.set('t', 'inlineStr')
                SubElement(SubElement(cell, 'is'), 't', {XML_SPACE: 'preserve'}).text = clean(value)
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % sheet)
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', STYLES)
        archive.writestr('xl/worksheets/sheet1.xml', tostring(root, encoding='utf-8', xml_declaration=True))
    output.seek(0)
    return output


def school_order_workbook(books):
    """Match the library's seven-column order form, with editable quantity totals."""
    document = Workbook()
    sheet = document.active
    sheet.title = '구입 예정 도서 목록'
    sheet.merge_cells('A1:G1')
    sheet['A1'] = '구입 예정 도서 목록'
    sheet['A1'].font = Font(bold=True, size=16)
    sheet['A1'].alignment = Alignment(horizontal='center')
    sheet.append([None])
    sheet.append(['번호', '도서명', '지은이', '출판사', '정가(할인가X)', '수량(권)', '계'])
    for cell in sheet[3]:
        cell.font = Font(bold=True)
    for number, (title, author, publisher, price, quantity) in enumerate(books, 1):
        row = number + 3
        sheet.append([number, None, None, None, price, quantity, f'=E{row}*F{row}'])
        for position, value in enumerate((title, author, publisher), 2):
            cell = sheet.cell(row, position, value)
            cell.data_type = 's'
    end = 3 + len(books)
    sheet.append(['합계', None, None, None, None,
                  f'=SUM(F4:F{end})' if books else 0,
                  f'=SUM(G4:G{end})' if books else 0])
    sheet.merge_cells(start_row=end + 1, start_column=1, end_row=end + 1, end_column=5)
    for row in sheet.iter_rows(min_row=4, min_col=5, max_col=7):
        for cell in row:
            cell.number_format = '#,##0'
    for i, width in enumerate((8, 50, 24, 20, 18, 12, 16)):
        sheet.column_dimensions[column(i)].width = width
    sheet.freeze_panes = 'A4'
    output = BytesIO()
    document.save(output)
    output.seek(0)
    return output
