"""Minimal OOXML workbook: strings are inline text, never spreadsheet formulas."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree.ElementTree import Element, SubElement, tostring


def workbook(rows):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    root = Element('worksheet', xmlns=ns)
    data = SubElement(root, 'sheetData')
    for index, values in enumerate([['도서명', '저자', '출판사', 'ISBN', '정가', '수량']] + rows, 1):
        row = SubElement(data, 'row', r=str(index))
        for column, value in enumerate(values):
            cell = SubElement(row, 'c', r=f'{chr(65+column)}{index}')
            if type(value) is int:
                SubElement(cell, 'v').text = str(value)
            else:
                cell.set('t', 'inlineStr')
                text = SubElement(SubElement(cell, 'is'), 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
                # Remove characters forbidden by XML 1.0 while preserving ordinary text.
                text.text = ''.join(c for c in str(value) if c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="발주 목록" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', tostring(root, encoding='utf-8', xml_declaration=True))
    output.seek(0)
    return output
