"""The five client document formats (Form Formats.pdf): RFQ, Quotation, Purchase Order,
Tax Invoice and Delivery Note.

One layout engine reproduces the client design: logo box top-left, green document
title top-right, green rule, bordered party boxes, a light grey reference panel,
a green-header line table (header repeated on every page, any number of rows),
totals block, terms box, signature/stamp lines and the footer instruction.
English and Arabic (RTL) are supported.
"""
from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from app.modules.quotations.document import _ensure_unicode_font, _shape_text

GREEN = colors.HexColor('#1F6E5B')
PANEL = colors.HexColor('#F2F4EF')
BORDER = colors.HexColor('#DADFDB')
LABEL = colors.HexColor('#5F6B66')
TEXT = colors.HexColor('#1B1E23')
RULE = colors.HexColor('#E4E8E5')

AR = {
    'REQUEST FOR QUOTATION': 'طلب عرض سعر', 'QUOTATION': 'عرض سعر', 'PURCHASE ORDER': 'أمر شراء',
    'TAX INVOICE': 'فاتورة ضريبية', 'DELIVERY NOTE': 'مذكرة تسليم',
    'FROM (BUYER)': 'من (المشتري)', 'TO (SUPPLIER)': 'إلى (المورد)', 'FROM (SUPPLIER)': 'من (المورد)',
    'TO (CUSTOMER)': 'إلى (العميل)', 'VENDOR': 'المورد', 'BILL TO': 'إرسال الفاتورة إلى', 'SHIP TO': 'الشحن إلى',
    'SUPPLIER': 'المورد', 'CUSTOMER': 'العميل', 'DELIVER TO': 'التسليم إلى',
    'RFQ NO.': 'رقم الطلب', 'RFQ DATE': 'تاريخ الطلب', 'RESPONSE DEADLINE': 'آخر موعد للرد',
    'PROJECT / REFERENCE': 'المشروع / المرجع', 'DELIVERY LOCATION': 'موقع التسليم', 'REQUIRED DELIVERY DATE': 'تاريخ التسليم المطلوب',
    'QUOTATION NO.': 'رقم العرض', 'QUOTATION DATE': 'تاريخ العرض', 'VALID UNTIL': 'صالح حتى', 'RFQ REFERENCE': 'مرجع الطلب',
    'PREPARED BY': 'أعد بواسطة', 'CURRENCY': 'العملة', 'PO NO.': 'رقم أمر الشراء', 'PO DATE': 'تاريخ أمر الشراء',
    'QUOTATION REFERENCE': 'مرجع العرض', 'PAYMENT TERMS': 'شروط الدفع', 'INVOICE NO.': 'رقم الفاتورة',
    'INVOICE DATE': 'تاريخ الفاتورة', 'DUE DATE': 'تاريخ الاستحقاق', 'DELIVERY NOTE NO.': 'رقم مذكرة التسليم',
    'DELIVERY DATE': 'تاريخ التسليم', 'VEHICLE NO.': 'رقم المركبة', 'DRIVER NAME': 'اسم السائق', 'CARRIER': 'الناقل',
    'No': 'م', 'Description / specification': 'الوصف / المواصفات', 'Description': 'الوصف', 'Qty': 'الكمية', 'UoM': 'الوحدة',
    'Unit price': 'سعر الوحدة', 'Disc. %': 'الخصم %', 'Line total': 'الإجمالي', 'VAT': 'الضريبة',
    'Ordered qty': 'الكمية المطلوبة', 'Delivered qty': 'الكمية المسلمة', 'Remarks': 'ملاحظات',
    'Subtotal': 'المجموع الفرعي', 'Discount': 'الخصم', 'Total': 'الإجمالي', 'Total due': 'المبلغ المستحق',
    'INSTRUCTIONS TO SUPPLIER': 'تعليمات للمورد', 'Quote validity': 'صلاحية العرض', 'Currency': 'العملة',
    'Payment terms': 'شروط الدفع', 'Delivery terms (Incoterms)': 'شروط التسليم (إنكوترمز)',
    'Prices include VAT (Yes / No)': 'الأسعار تشمل الضريبة (نعم / لا)', 'Attachments': 'المرفقات',
    'TERMS AND CONDITIONS': 'الشروط والأحكام', 'Delivery lead time': 'مدة التوريد', 'Warranty': 'الضمان',
    'Stock availability': 'توفر المخزون', 'ORDER TERMS': 'شروط الطلب', 'Special instructions': 'تعليمات خاصة', 'Notes': 'ملاحظات',
    'PAYMENT DETAILS': 'تفاصيل الدفع', 'Bank name': 'اسم البنك', 'Account name': 'اسم الحساب', 'IBAN': 'الآيبان',
    'SWIFT / BIC': 'سويفت', 'Amount in words': 'المبلغ كتابة', 'CONDITION ON DELIVERY': 'حالة التسليم',
    'Delivery status': 'حالة التسليم', 'Damage or shortage notes': 'ملاحظات التلف أو النقص',
    'Prepared by': 'أعد بواسطة', 'Authorized by': 'معتمد من', 'Approved by': 'اعتمد بواسطة',
    'Authorized signature and stamp': 'التوقيع والختم المعتمد', 'Customer acceptance (optional)': 'قبول العميل (اختياري)',
    'Vendor acceptance (signature, date)': 'قبول المورد (التوقيع، التاريخ)', 'Delivered by (name, signature, date)': 'سلمه (الاسم، التوقيع، التاريخ)',
    'Received by (name, signature, date)': 'استلمه (الاسم، التوقيع، التاريخ)', 'Company stamp': 'ختم الشركة',
    'Please quote each line separately and return your quotation before the response deadline.': 'يرجى تسعير كل بند على حدة وإرسال عرضكم قبل آخر موعد للرد.',
    'This quotation is valid until the date shown above. Prices are in the stated currency.': 'هذا العرض صالح حتى التاريخ المذكور أعلاه. الأسعار بالعملة المحددة.',
    'Please quote the PO number on every delivery note and invoice.': 'يرجى ذكر رقم أمر الشراء في كل مذكرة تسليم وفاتورة.',
    'Please quote the invoice number with your payment.': 'يرجى ذكر رقم الفاتورة عند الدفع.',
    'Goods are received in good order unless noted above.': 'تم استلام البضائع بحالة جيدة ما لم يذكر خلاف ذلك أعلاه.',
    'QR code, if e-invoicing applies': 'رمز الاستجابة السريعة عند تطبيق الفوترة الإلكترونية', 'Page': 'صفحة', 'of': 'من',
    'Attention': 'عناية', 'VAT no.': 'الرقم الضريبي', 'CR no.': 'السجل التجاري',
}


class _Ctx:
    def __init__(self, lang: str):
        self.arabic = lang == 'ar'
        self.font, self.bold = _ensure_unicode_font()
        if self.font == 'Helvetica':
            self.arabic = False  # no Arabic-capable font on this host; fall back to English
        if not self.arabic and self.font != 'Helvetica':
            self.font, self.bold = 'Helvetica', 'Helvetica-Bold'
        align = TA_RIGHT if self.arabic else TA_LEFT
        self.body = ParagraphStyle('body', fontName=self.font, fontSize=8.5, leading=11, textColor=TEXT, alignment=align)
        self.right = ParagraphStyle('right', parent=self.body, alignment=TA_RIGHT)
        self.label = ParagraphStyle('label', parent=self.body, fontName=self.bold, fontSize=6.8, leading=9, textColor=LABEL)
        self.title = ParagraphStyle('title', parent=self.body, fontName=self.bold, fontSize=17, leading=21, textColor=GREEN,
                                    alignment=TA_LEFT if self.arabic else TA_RIGHT)
        self.head = ParagraphStyle('head', parent=self.body, fontName=self.bold, fontSize=7.8, textColor=colors.white)
        self.head_r = ParagraphStyle('head_r', parent=self.head, alignment=TA_RIGHT)
        self.small = ParagraphStyle('small', parent=self.body, fontSize=7, leading=9, textColor=LABEL)

    def t(self, value: str) -> str:
        return AR.get(value, value) if self.arabic else value

    def p(self, value, style=None) -> Paragraph:
        text = _shape_text(str(value if value not in (None, '') else '—'), self.arabic)
        return Paragraph(escape(text).replace('\n', '<br/>'), style or self.body)


def _party(ctx: _Ctx, label: str, lines: list[str]):
    content = [ctx.p(ctx.t(label), ctx.label)] + [ctx.p(line) for line in lines if line]
    table = Table([[content]], colWidths=None)
    table.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, BORDER), ('ROUNDEDCORNERS', [4, 4, 4, 4]),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 7),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, -1), 6),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    return table


def render(spec: dict, *, lang: str = 'en') -> bytes:
    ctx = _Ctx(lang)
    buffer = BytesIO()
    width = A4[0] - 30 * mm
    footer = ctx.t(spec.get('footer', ''))

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont(ctx.font, 7)
        canvas.setFillColor(LABEL)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, _shape_text(footer, ctx.arabic))
        page = f"{ctx.t('Page')} {doc.page}"
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, _shape_text(page, ctx.arabic))
        canvas.drawString(15 * mm, 10 * mm, _shape_text(spec.get('reference', ''), ctx.arabic))
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
                            title=f"{spec['title']} {spec.get('reference', '')}", author='Procumaterial')
    story = []
    logo = Table([[ctx.p(spec.get('logo_text') or '[Company logo]', ParagraphStyle('logo', parent=ctx.small, alignment=1))]],
                 colWidths=[40 * mm], rowHeights=[14 * mm])
    logo.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#BFC6C2')), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
    title = ctx.p(ctx.t(spec['title']), ctx.title)
    header = Table([[title, logo] if ctx.arabic else [logo, title]], colWidths=[width - 40 * mm, 40 * mm] if ctx.arabic else [40 * mm, width - 40 * mm])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, 0), 1.6, GREEN),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 5 * mm)]

    parties = spec.get('parties', [])
    if parties:
        gap = 3 * mm
        col = (width - gap * (len(parties) - 1)) / len(parties)
        cells, widths = [], []
        ordered = list(reversed(parties)) if ctx.arabic else parties
        for index, (label, lines) in enumerate(ordered):
            cells.append(_party(ctx, label, lines))
            widths.append(col)
            if index < len(ordered) - 1:
                cells.append('')
                widths.append(gap)
        row = Table([cells], colWidths=widths)
        row.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
        story += [row, Spacer(1, 4 * mm)]

    info = spec.get('info', [])
    if info:
        rows = []
        for start in range(0, len(info), 3):
            chunk = info[start:start + 3] + [('', '')] * (3 - len(info[start:start + 3]))
            if ctx.arabic:
                chunk = list(reversed(chunk))
            rows.append([[ctx.p(ctx.t(label), ctx.label), ctx.p(value)] if label else '' for label, value in chunk])
        panel = Table(rows, colWidths=[width / 3] * 3)
        panel.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), PANEL), ('ROUNDEDCORNERS', [4, 4, 4, 4]), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        story += [panel, Spacer(1, 5 * mm)]

    columns = spec['columns']
    total_weight = sum(c[2] for c in columns)
    widths = [width * c[2] / total_weight for c in columns]
    header_row = [ctx.p(ctx.t(c[1]), ctx.head_r if c[3] == 'right' else ctx.head) for c in columns]
    data = [header_row]
    for row in spec.get('rows', []):
        data.append([ctx.p(row.get(c[0], ''), ctx.right if c[3] == 'right' else ctx.body) for c in columns])
    if len(data) == 1:
        data.append([ctx.p('') for _ in columns])
    if ctx.arabic:
        data = [list(reversed(r)) for r in data]
        widths = list(reversed(widths))
    table = Table(data, colWidths=widths, repeatRows=1, splitByRow=1)
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), GREEN), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LINEBELOW', (0, 1), (-1, -1), 0.5, RULE), ('TOPPADDING', (0, 0), (-1, -1), 5),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5)]))
    story += [table, Spacer(1, 4 * mm)]

    if spec.get('totals'):
        rows = []
        for label, value, strong in spec['totals']:
            style = ParagraphStyle('t', parent=ctx.body, fontName=ctx.bold if strong else ctx.font, fontSize=10 if strong else 8.5)
            rows.append([ctx.p(ctx.t(label), style), ctx.p(value, ParagraphStyle('tr', parent=style, alignment=TA_RIGHT))])
        block = Table(rows, colWidths=[38 * mm, 32 * mm])
        block.setStyle(TableStyle([('LINEABOVE', (0, len(rows) - 1), (-1, len(rows) - 1), 1, GREEN), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        wrapper = Table([[block, ''] if ctx.arabic else ['', block]], colWidths=[70 * mm, width - 70 * mm] if ctx.arabic else [width - 70 * mm, 70 * mm])
        wrapper.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
        story += [KeepTogether(wrapper), Spacer(1, 5 * mm)]

    if spec.get('qr_note'):
        qr = Table([[ctx.p(ctx.t(spec['qr_note']), ParagraphStyle('qr', parent=ctx.small, alignment=1))]], colWidths=[30 * mm], rowHeights=[22 * mm])
        qr.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, BORDER), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        story += [qr, Spacer(1, 4 * mm)]

    for box_title, entries in spec.get('boxes', []):
        rows = [[ctx.p(ctx.t(box_title), ctx.label), '']]
        for label, value in entries:
            pair = [ctx.p(ctx.t(label), ParagraphStyle('bl', parent=ctx.body, fontName=ctx.bold, textColor=LABEL)), ctx.p(value)]
            rows.append(list(reversed(pair)) if ctx.arabic else pair)
        box = Table(rows, colWidths=[width - 40 * mm, 40 * mm] if ctx.arabic else [40 * mm, width - 40 * mm])
        box.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, BORDER), ('ROUNDEDCORNERS', [4, 4, 4, 4]), ('SPAN', (0, 0), (1, 0)),
                                 ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, -1), 3)]))
        story += [KeepTogether(box), Spacer(1, 5 * mm)]

    signatures = spec.get('signatures', [])
    if signatures:
        cells = [[ctx.p(ctx.t(label), ctx.small) for label in (reversed(signatures) if ctx.arabic else signatures)]]
        sig = Table([[''] * len(signatures)] + cells, colWidths=[width / len(signatures)] * len(signatures), rowHeights=[16 * mm, None])
        sig.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, 0), 0.6, LABEL), ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 10)]))
        story += [Spacer(1, 8 * mm), KeepTogether(sig)]

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


# ----------------------------------------------------------- data -> specs
def _org_lines(org, *, contact: bool = True, cr: bool = False) -> list[str]:
    if org is None:
        return []
    lines = [org.name, ', '.join(filter(None, [org.address, org.city, org.country]))]
    if org.vat_number:
        lines.append(f'VAT: {org.vat_number}')
    if cr and org.cr_number:
        lines.append(f'CR: {org.cr_number}')
    if contact:
        lines.append(' · '.join(filter(None, [org.contact_name, org.business_email, org.business_phone])))
    return lines


def _date(value) -> str:
    from datetime import datetime
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


def _num(value, places: int = 2) -> str:
    from decimal import Decimal
    if value in (None, ''):
        return ''
    return f'{Decimal(str(value)):,.{places}f}'


def _qty(value) -> str:
    from decimal import Decimal
    if value in (None, ''):
        return ''
    text = f'{Decimal(str(value)):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def rfq_spec(db, rfq, supplier=None) -> dict:
    from app.models import Organization, RFQItem
    buyer = db.get(Organization, rfq.buyer_org_id)
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at, RFQItem.id)).all()
    return {
        'title': 'REQUEST FOR QUOTATION', 'reference': rfq.reference, 'logo_text': buyer.name if buyer else '',
        'parties': [('FROM (BUYER)', _org_lines(buyer)), ('TO (SUPPLIER)', _org_lines(supplier, contact=False) if supplier else ['All invited suppliers'])],
        'info': [('RFQ NO.', rfq.reference), ('RFQ DATE', _date(rfq.published_at or rfq.created_at)), ('RESPONSE DEADLINE', _date(rfq.response_deadline)),
                 ('PROJECT / REFERENCE', rfq.project_name or rfq.title), ('DELIVERY LOCATION', rfq.delivery_address or rfq.city),
                 ('REQUIRED DELIVERY DATE', _date(rfq.required_delivery_date) if rfq.required_delivery_date else '')],
        'columns': [('no', 'No', 6, 'left'), ('description', 'Description / specification', 64, 'left'), ('qty', 'Qty', 14, 'right'), ('uom', 'UoM', 16, 'left')],
        'rows': [{'no': n, 'description': i.description + (f'\n{i.specifications}' if i.specifications else ''), 'qty': _qty(i.quantity), 'uom': i.unit} for n, i in enumerate(items, 1)],
        'boxes': [('INSTRUCTIONS TO SUPPLIER', [('Quote validity', '30 days'), ('Currency', 'SAR'), ('Payment terms', 'As per supplier quotation'),
                                                ('Delivery terms (Incoterms)', rfq.delivery_address or rfq.city or ''), ('Prices include VAT (Yes / No)', 'No — show VAT separately'),
                                                ('Attachments', ', '.join(a.get('filename', '') for a in (rfq.attachment_records or [])) or 'None')])],
        'signatures': ['Prepared by', 'Authorized by'],
        'footer': 'Please quote each line separately and return your quotation before the response deadline.',
    }


def quotation_spec(db, quote) -> dict:
    from app.models import Organization, QuoteLine, RFQ, RFQItem
    from app.modules.quotations.router import invoice
    from app.modules.procurement.money import d
    rfq = db.get(RFQ, quote.rfq_id)
    supplier, buyer = db.get(Organization, quote.supplier_org_id), db.get(Organization, rfq.buyer_org_id)
    lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id).order_by(QuoteLine.created_at, QuoteLine.id)).all()
    items = {i.id: i for i in db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id))}
    totals = invoice(db, quote, lines)
    terms = quote.commercial_terms or {}
    leads = sorted({line.lead_days for line in lines})
    warranties = sorted({line.warranty_months for line in lines if line.warranty_months is not None})
    vat_pct = sorted({str((d(line.vat_rate if line.vat_rate is not None else quote.vat_rate) * 100).normalize()) for line in lines})
    return {
        'title': 'QUOTATION', 'reference': terms.get('quotation_reference') or f'QT-{quote.id[:8].upper()}', 'logo_text': supplier.name if supplier else '',
        'parties': [('FROM (SUPPLIER)', _org_lines(supplier)), ('TO (CUSTOMER)', _org_lines(buyer, contact=False))],
        'info': [('QUOTATION NO.', terms.get('quotation_reference') or f'QT-{quote.id[:8].upper()}'), ('QUOTATION DATE', _date(quote.submitted_at or quote.created_at)),
                 ('VALID UNTIL', str(terms.get('validity_until') or '')[:10]), ('RFQ REFERENCE', rfq.reference), ('PREPARED BY', supplier.contact_name if supplier else ''),
                 ('CURRENCY', quote.currency)],
        'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 37, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                    ('price', 'Unit price', 14, 'right'), ('disc', 'Disc. %', 9, 'right'), ('total', 'Line total', 18, 'right')],
        'rows': [{'no': n, 'description': (items[l.rfq_item_id].description if l.rfq_item_id in items else l.offered_description) + (f"\n{' '.join(filter(None, [l.brand, l.model]))}" if l.brand or l.model else ''),
                  'qty': _qty(l.quantity), 'uom': items[l.rfq_item_id].unit if l.rfq_item_id in items else '', 'price': _num(l.unit_price), 'disc': _num(l.discount_percent, 2).rstrip('0').rstrip('.') or '0',
                  'total': _num(d(l.quantity) * d(l.unit_price) * (1 - d(l.discount_percent) / 100))} for n, l in enumerate(lines, 1)],
        'totals': [('Subtotal', _num(totals['subtotal']), False), ('Discount', _num(totals.get('discount', '0')), False),
                   (f"VAT [{', '.join(vat_pct)}%]", _num(totals['vat']), False), ('Total', f"{_num(totals['total'])} {quote.currency}", True)],
        'boxes': [('TERMS AND CONDITIONS', [('Payment terms', terms.get('payment_terms', '')), ('Delivery lead time', ', '.join(f'{v} days' for v in leads)),
                                            ('Warranty', terms.get('warranty_terms') or ', '.join(f'{v} months' for v in warranties)),
                                            ('Delivery terms (Incoterms)', terms.get('delivery_terms', '')), ('Stock availability', terms.get('stock_availability') or ', '.join(sorted({l.availability for l in lines if l.availability and l.availability != 'unknown'})))])],
        'signatures': ['Authorized signature and stamp', 'Customer acceptance (optional)'],
        'footer': 'This quotation is valid until the date shown above. Prices are in the stated currency.',
    }


def po_spec(db, po) -> dict:
    from app.models import Organization, PurchaseOrderLine
    supplier, buyer = db.get(Organization, po.supplier_org_id), db.get(Organization, po.buyer_org_id)
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id).order_by(PurchaseOrderLine.line_number)).all()
    return {
        'title': 'PURCHASE ORDER', 'reference': po.reference, 'logo_text': buyer.name if buyer else '',
        'parties': [('VENDOR', _org_lines(supplier)), ('BILL TO', _org_lines(buyer, contact=False)), ('SHIP TO', [po.delivery_address, po.site_contact])],
        'info': [('PO NO.', po.reference), ('PO DATE', _date(po.issued_at or po.created_at)), ('QUOTATION REFERENCE', po.quotation_reference),
                 ('PAYMENT TERMS', po.payment_terms), ('REQUIRED DELIVERY DATE', _date(po.required_delivery_date) if po.required_delivery_date else ''), ('CURRENCY', po.currency)],
        'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 41, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                    ('price', 'Unit price', 14, 'right'), ('vat', 'VAT', 7, 'right'), ('total', 'Line total', 16, 'right')],
        'rows': [{'no': l.line_number, 'description': l.description + (f'\n{l.brand}' if l.brand else '') + (f'\nDiscount {l.discount_percent}%' if l.discount_percent else ''),
                  'qty': _qty(l.quantity), 'uom': l.unit, 'price': _num(l.unit_price), 'vat': f'{_num(l.vat_rate * 100, 0)}%', 'total': _num(l.line_total)} for l in lines],
        'totals': [('Subtotal', _num(po.subtotal), False)] + ([('Discount', _num(po.discount_total), False)] if po.discount_total else []) +
                  [('VAT', _num(po.vat_total), False), ('Total', f'{_num(po.total)} {po.currency}', True)],
        'boxes': [('ORDER TERMS', [('Delivery terms (Incoterms)', po.delivery_terms), ('Special instructions', po.special_instructions), ('Notes', po.notes)])],
        'signatures': ['Prepared by', 'Approved by', 'Vendor acceptance (signature, date)'],
        'footer': 'Please quote the PO number on every delivery note and invoice.',
    }


def invoice_spec(db, invoice) -> dict:
    from app.models import DeliveryNote, Organization, PurchaseOrder, TaxInvoiceLine
    from app.modules.procurement.money import amount_in_words
    supplier, buyer = db.get(Organization, invoice.supplier_org_id), db.get(Organization, invoice.buyer_org_id)
    po = db.get(PurchaseOrder, invoice.purchase_order_id)
    note = db.get(DeliveryNote, invoice.delivery_note_id) if invoice.delivery_note_id else None
    lines = db.scalars(select(TaxInvoiceLine).where(TaxInvoiceLine.invoice_id == invoice.id).order_by(TaxInvoiceLine.created_at, TaxInvoiceLine.id)).all()
    return {
        'title': 'TAX INVOICE', 'reference': invoice.invoice_number, 'logo_text': supplier.name if supplier else '',
        'parties': [('SUPPLIER', _org_lines(supplier, contact=False, cr=True)), ('CUSTOMER', _org_lines(buyer, contact=False))],
        'info': [('INVOICE NO.', invoice.invoice_number), ('INVOICE DATE', _date(invoice.invoice_date)), ('DUE DATE', _date(invoice.due_date) if invoice.due_date else ''),
                 ('PO NO.', po.reference if po else ''), ('DELIVERY NOTE NO.', note.reference if note else ''), ('CURRENCY', invoice.currency)],
        'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 41, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                    ('price', 'Unit price', 14, 'right'), ('vat', 'VAT', 7, 'right'), ('total', 'Line total', 16, 'right')],
        'rows': [{'no': n, 'description': l.description, 'qty': _qty(l.quantity), 'uom': l.unit, 'price': _num(l.unit_price), 'vat': f'{_num(l.vat_rate * 100, 0)}%',
                  'total': _num(l.line_total)} for n, l in enumerate(lines, 1)],
        'totals': [('Subtotal', _num(invoice.subtotal), False), ('Discount', _num(invoice.discount_total), False), ('VAT', _num(invoice.vat_total), False),
                   ('Total due', f'{_num(invoice.total)} {invoice.currency}', True)],
        'qr_note': 'QR code, if e-invoicing applies',
        'boxes': [('PAYMENT DETAILS', [('Bank name', invoice.bank_name), ('Account name', invoice.account_name), ('IBAN', invoice.iban), ('SWIFT / BIC', invoice.swift),
                                       ('Amount in words', amount_in_words(invoice.total, invoice.currency)), ('Payment terms', invoice.payment_terms)])],
        'signatures': ['Authorized signature and stamp'],
        'footer': 'Please quote the invoice number with your payment.',
    }


def delivery_spec(db, note) -> dict:
    from app.models import DeliveryNoteLine, Organization, PurchaseOrder, PurchaseOrderLine
    supplier, buyer = db.get(Organization, note.supplier_org_id), db.get(Organization, note.buyer_org_id)
    po = db.get(PurchaseOrder, note.purchase_order_id)
    rows = db.execute(select(DeliveryNoteLine, PurchaseOrderLine).join(PurchaseOrderLine, PurchaseOrderLine.id == DeliveryNoteLine.po_line_id)
                      .where(DeliveryNoteLine.delivery_note_id == note.id).order_by(PurchaseOrderLine.line_number)).all()
    issues = [f'{po_line.line_number}: received {_qty(line.received_quantity)}, damaged {_qty(line.damaged_quantity)} {line.receipt_remarks}'.strip()
              for line, po_line in rows if line.received_quantity is not None and (line.received_quantity < line.delivered_quantity or line.damaged_quantity)]
    return {
        'title': 'DELIVERY NOTE', 'reference': note.reference, 'logo_text': supplier.name if supplier else '',
        'parties': [('FROM (SUPPLIER)', _org_lines(supplier)), ('DELIVER TO', [buyer.name if buyer else '', note.delivery_address, po.site_contact if po else ''])],
        'info': [('DELIVERY NOTE NO.', note.reference), ('DELIVERY DATE', _date(note.delivery_date) if note.delivery_date else ''), ('PO NO.', po.reference if po else ''),
                 ('VEHICLE NO.', note.vehicle_no), ('DRIVER NAME', note.driver_name), ('CARRIER', note.carrier)],
        'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 39, 'left'), ('ordered', 'Ordered qty', 13, 'right'), ('delivered', 'Delivered qty', 13, 'right'),
                    ('uom', 'UoM', 8, 'left'), ('remarks', 'Remarks', 22, 'left')],
        'rows': [{'no': po_line.line_number, 'description': po_line.description, 'ordered': _qty(po_line.quantity), 'delivered': _qty(line.delivered_quantity),
                  'uom': po_line.unit, 'remarks': line.remarks} for line, po_line in rows],
        'boxes': [('CONDITION ON DELIVERY', [('Delivery status', note.status.replace('_', ' ')), ('Damage or shortage notes', '\n'.join(issues) or note.receipt_notes)])],
        'signatures': ['Delivered by (name, signature, date)', 'Received by (name, signature, date)', 'Company stamp'],
        'footer': 'Goods are received in good order unless noted above.',
    }
