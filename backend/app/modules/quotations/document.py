"""Configurable bilingual (EN/AR + RTL) quotation document data and PDF renderer.

Generated independently from RFQ matching. Supports configurable template keys
so the final client-approved layout can be swapped without changing business logic.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from app.models import Organization, RFQ, RFQItem


QUOTATION_TEMPLATES: dict[str, dict] = {
    'standard_v1': {
        'primary_hex': '#0F172A',
        'accent_hex': '#1E3A8A',
        'tint_hex': '#F1F5F9',
        'border_hex': '#CBD5E1',
        'show_logo': True,
        'show_commercial_terms': True,
        'footer_en': 'Generated via Procumaterial — Official Commercial Quotation',
        'footer_ar': 'تم الإنشاء عبر منصة بروكيو ماتيريال — عرض سعر تجاري رسمي',
    },
    'pending_client_approval': {
        'primary_hex': '#0F172A',
        'accent_hex': '#1E3A8A',
        'tint_hex': '#F1F5F9',
        'border_hex': '#CBD5E1',
        'show_logo': True,
        'show_commercial_terms': True,
        'footer_en': 'Generated via Procumaterial — Standard Quotation Format',
        'footer_ar': 'تم الإنشاء عبر منصة بروكيو ماتيريال — نموذج عرض السعر القياسي',
    },
}

_ARABIC_FONT_REGISTERED: str | None = None


def _ensure_unicode_font() -> tuple[str, str]:
    """Register a Unicode/Arabic-capable TTF font if present on Windows or Linux."""
    global _ARABIC_FONT_REGISTERED
    if _ARABIC_FONT_REGISTERED:
        return _ARABIC_FONT_REGISTERED, _ARABIC_FONT_REGISTERED
    candidates = [
        ('ProcuraArabic', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        ('ProcuraArabic', '/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf'),
        ('ProcuraArabic', '/usr/share/fonts/truetype/freefont/FreeSans.ttf'),
        ('ProcuraArabic', 'C:/Windows/Fonts/arial.ttf'),
        ('ProcuraArabic', 'C:/Windows/Fonts/tahoma.ttf'),
    ]
    for font_name, path_str in candidates:
        p = Path(path_str)
        if p.is_file():
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(p)))
                _ARABIC_FONT_REGISTERED = font_name
                return font_name, font_name
            except Exception:
                continue
    return 'Helvetica', 'Helvetica-Bold'


def _shape_text(value: str, arabic: bool) -> str:
    text = str(value or '')
    if not arabic or not text:
        return text
    try:
        import arabic_reshaper  # type: ignore[import-not-found]
        from bidi.algorithm import get_display  # type: ignore[import-not-found]
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def quotation_document(db, quote, lines):
    from app.modules.quotations.router import invoice
    rfq = db.get(RFQ, quote.rfq_id)
    buyer = db.get(Organization, rfq.buyer_org_id)
    supplier = db.get(Organization, quote.supplier_org_id)
    requested = {item.id: item for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id))}

    def company(org):
        return {
            'name': org.name,
            'city': org.city,
            'address': org.address,
            'cr_number': org.cr_number,
            'vat_number': org.vat_number,
            'email': org.business_email,
            'phone': org.business_phone,
            'logo_file_id': org.logo_file_id,
        }

    terms = quote.commercial_terms or {}
    totals = invoice(db, quote, lines)
    doc_lines = []
    for line in lines:
        req = requested.get(line.rfq_item_id)
        line_total = (Decimal(line.quantity) * Decimal(line.unit_price)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        doc_lines.append({
            'rfq_item_id': line.rfq_item_id,
            'description': req.description if req else line.offered_description,
            'offered_description': line.offered_description,
            'brand': line.brand or '',
            'model': line.model or '',
            'specification': line.offered_specs or '',
            'quantity': str(line.quantity),
            'unit': req.unit if req else '',
            'unit_price': str(Decimal(line.unit_price).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'line_total': str(line_total),
            'subtotal': str(line_total),
            'lead_days': line.lead_days,
            'vat_rate': str(line.vat_rate if line.vat_rate is not None else quote.vat_rate),
            'compliance': line.compliance,
        })

    return {
        'template_key': terms.get('template_key', 'standard_v1'),
        'supplier': company(supplier),
        'buyer': company(buyer),
        'quotation_reference': terms.get('quotation_reference') or f'QT-{quote.id[:8].upper()}',
        'rfq_reference': rfq.reference,
        'rfq_title': rfq.project_name or rfq.title,
        'quotation_date': quote.created_at.isoformat(),
        'validity_date': terms.get('validity_until'),
        'supported_languages': ['en', 'ar'],
        'currency': quote.currency,
        'price_basis': quote.price_basis,
        'submission_source': quote.submission_source,
        'status': quote.status,
        'lines': doc_lines,
        'totals': totals,
        'commercial_terms': {
            'payment_terms': terms.get('payment_terms', ''),
            'delivery_terms': terms.get('delivery_terms', ''),
            'terms_and_conditions': terms.get('terms_and_conditions', ''),
            'discount_amount': totals.get('discount', '0.00'),
            'delivery_charge': totals.get('delivery_charge', '0.00'),
            'validity_until': terms.get('validity_until', ''),
        },
    }


def build_quotation_pdf(doc: dict, *, lang: str = 'en', template_key: str | None = None) -> bytes:
    """Render a professional bilingual (EN/AR + RTL) quotation PDF."""
    arabic = (lang or 'en').lower() == 'ar'
    tpl = QUOTATION_TEMPLATES.get(template_key or doc.get('template_key') or 'standard_v1', QUOTATION_TEMPLATES['standard_v1'])
    font_regular, font_bold = _ensure_unicode_font()
    align = TA_RIGHT if arabic else TA_LEFT

    primary = colors.HexColor(tpl['primary_hex'])
    accent = colors.HexColor(tpl['accent_hex'])
    tint = colors.HexColor(tpl['tint_hex'])
    border = colors.HexColor(tpl['border_hex'])

    base_styles = getSampleStyleSheet()
    title_style = ParagraphStyle('QTTitle', parent=base_styles['Heading1'], fontName=font_bold, fontSize=16, textColor=primary, alignment=align, spaceAfter=4)
    sub_style = ParagraphStyle('QTSub', parent=base_styles['Normal'], fontName=font_regular, fontSize=9, textColor=colors.HexColor('#475569'), alignment=align, spaceAfter=8)
    section_style = ParagraphStyle('QTSec', parent=base_styles['Heading3'], fontName=font_bold, fontSize=10.5, textColor=accent, alignment=align, spaceBefore=8, spaceAfter=4)
    cell_style = ParagraphStyle('QTCell', parent=base_styles['Normal'], fontName=font_regular, fontSize=8.5, leading=11, alignment=align)
    cell_bold = ParagraphStyle('QTCellBold', parent=cell_style, fontName=font_bold)
    header_cell = ParagraphStyle('QTHead', parent=cell_bold, textColor=colors.white, fontSize=8.5)

    def P(text: str, style=cell_style) -> Paragraph:
        return Paragraph(escape(_shape_text(text, arabic)).replace('\n', '<br/>'), style)

    labels = (
        {
            'title': 'عرض سعر تجاري',
            'qt_ref': 'رقم عرض السعر',
            'rfq_ref': 'مرجع طلب السعر',
            'date': 'التاريخ',
            'validity': 'صالح حتى',
            'supplier': 'بيانات المورد',
            'buyer': 'بيانات المشتري',
            'cr': 'السجل التجاري',
            'vat_no': 'الرقم الضريبي',
            'address': 'العنوان',
            'phone': 'الهاتف',
            'email': 'البريد الإلكتروني',
            'col_idx': '#',
            'col_desc': 'الوصف والمواصفات',
            'col_brand': 'العلامة التجارية',
            'col_unit': 'الوحدة',
            'col_qty': 'الكمية',
            'col_price': 'سعر الوحدة',
            'col_total': 'الإجمالي',
            'subtotal': 'المجموع الفرعي',
            'discount': 'الخصم',
            'delivery': 'رسوم التوصيل',
            'vat': 'ضريبة القيمة المضافة',
            'grand_total': 'الإجمالي النهائي',
            'terms': 'الشروط التجارية',
            'payment': 'شروط الدفع',
            'delivery_terms': 'شروط التسليم',
            'conditions': 'الشروط والأحكام',
            'source_auto': 'عرض سعر تلقائي من قائمة الأسعار المعتمدة',
            'source_manual': 'عرض سعر مُعد يدوياً',
        }
        if arabic
        else {
            'title': 'COMMERCIAL QUOTATION',
            'qt_ref': 'Quotation Ref',
            'rfq_ref': 'RFQ Reference',
            'date': 'Date',
            'validity': 'Valid Until',
            'supplier': 'Supplier Details',
            'buyer': 'Buyer Details',
            'cr': 'CR No.',
            'vat_no': 'VAT No.',
            'address': 'Address',
            'phone': 'Phone',
            'email': 'Email',
            'col_idx': '#',
            'col_desc': 'Material Description & Specifications',
            'col_brand': 'Brand / Model',
            'col_unit': 'Unit',
            'col_qty': 'Qty',
            'col_price': 'Unit Price',
            'col_total': 'Line Total',
            'subtotal': 'Subtotal',
            'discount': 'Discount',
            'delivery': 'Delivery Charges',
            'vat': 'VAT',
            'grand_total': 'Grand Total',
            'terms': 'Commercial Terms & Conditions',
            'payment': 'Payment Terms',
            'delivery_terms': 'Delivery Terms',
            'conditions': 'Commercial Conditions',
            'source_auto': 'Automatically generated from Supplier Private Price Book',
            'source_manual': 'Manually prepared quotation',
        }
    )

    buf = BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    story = []

    supplier = doc.get('supplier') or {}
    buyer = doc.get('buyer') or {}
    totals = doc.get('totals') or {}
    terms = doc.get('commercial_terms') or {}
    currency = doc.get('currency') or totals.get('currency') or 'SAR'

    story.append(P(f"{labels['title']} — {doc.get('quotation_reference', '')}", title_style))
    source_note = labels['source_auto'] if doc.get('submission_source') in {'automatic', 'automatic_draft'} else labels['source_manual']
    story.append(P(f"{labels['rfq_ref']}: {doc.get('rfq_reference', '')}  |  {labels['date']}: {str(doc.get('quotation_date', ''))[:10]}  |  {labels['validity']}: {doc.get('validity_date') or '-'}  |  {source_note}", sub_style))

    def party_block(heading: str, info: dict) -> list[str]:
        parts = [f"{heading}: {info.get('name') or '-'}"]
        loc = ', '.join(v for v in [info.get('address'), info.get('city')] if v)
        if loc:
            parts.append(f"{labels['address']}: {loc}")
        if info.get('cr_number'):
            parts.append(f"{labels['cr']}: {info['cr_number']}")
        if info.get('vat_number'):
            parts.append(f"{labels['vat_no']}: {info['vat_number']}")
        if info.get('email'):
            parts.append(f"{labels['email']}: {info['email']}")
        if info.get('phone'):
            parts.append(f"{labels['phone']}: {info['phone']}")
        return parts

    supplier_text = '\n'.join(party_block(labels['supplier'], supplier))
    buyer_text = '\n'.join(party_block(labels['buyer'], buyer))
    party_row = [P(buyer_text), P(supplier_text)] if arabic else [P(supplier_text), P(buyer_text)]
    party_tbl = Table([party_row], colWidths=[91 * mm, 91 * mm])
    party_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), tint),
        ('BOX', (0, 0), (-1, -1), 0.5, border),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, border),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(party_tbl)
    story.append(Spacer(1, 5 * mm))

    headers = [
        labels['col_idx'],
        labels['col_desc'],
        labels['col_brand'],
        labels['col_unit'],
        labels['col_qty'],
        f"{labels['col_price']} ({currency})",
        f"{labels['col_total']} ({currency})",
    ]
    col_widths = [9 * mm, 64 * mm, 29 * mm, 16 * mm, 16 * mm, 24 * mm, 24 * mm]
    if arabic:
        headers = list(reversed(headers))
        col_widths = list(reversed(col_widths))

    table_data = [[P(h, header_cell) for h in headers]]
    for idx, line in enumerate(doc.get('lines') or [], start=1):
        desc = line.get('offered_description') or line.get('description') or ''
        specs = line.get('specification') or ''
        if specs:
            desc = f'{desc}\n{specs}'
        brand_model = ' / '.join(v for v in [line.get('brand'), line.get('model')] if v) or '-'
        row_vals = [
            str(idx),
            desc,
            brand_model,
            line.get('unit') or '-',
            str(line.get('quantity') or '0'),
            str(line.get('unit_price') or '0.00'),
            str(line.get('line_total') or line.get('subtotal') or '0.00'),
        ]
        if arabic:
            row_vals = list(reversed(row_vals))
        table_data.append([P(v) for v in row_vals])

    items_tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.4, border),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 5 * mm))

    summary_rows = [
        (labels['subtotal'], f"{totals.get('subtotal', '0.00')} {currency}"),
    ]
    if totals.get('discount') and Decimal(str(totals['discount'])) > 0:
        summary_rows.append((labels['discount'], f"-{totals['discount']} {currency}"))
    if totals.get('delivery_charge') and Decimal(str(totals['delivery_charge'])) > 0:
        summary_rows.append((labels['delivery'], f"{totals['delivery_charge']} {currency}"))
    summary_rows.append((labels['vat'], f"{totals.get('vat', '0.00')} {currency}"))
    summary_rows.append((labels['grand_total'], f"{totals.get('total', '0.00')} {currency}"))

    totals_data = [
        ([P(v, cell_bold if i == len(summary_rows) - 1 else cell_style), P(k, cell_bold)] if arabic else [P(k, cell_bold), P(v, cell_bold if i == len(summary_rows) - 1 else cell_style)])
        for i, (k, v) in enumerate(summary_rows)
    ]
    totals_tbl = Table(totals_data, colWidths=[45 * mm, 45 * mm], hAlign='LEFT' if arabic else 'RIGHT')
    totals_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, border),
        ('BACKGROUND', (0, -1), (-1, -1), tint),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(totals_tbl)

    if tpl.get('show_commercial_terms', True):
        story.append(Spacer(1, 5 * mm))
        story.append(P(labels['terms'], section_style))
        term_lines = []
        if terms.get('payment_terms'):
            term_lines.append(f"{labels['payment']}: {terms['payment_terms']}")
        if terms.get('delivery_terms'):
            term_lines.append(f"{labels['delivery_terms']}: {terms['delivery_terms']}")
        if terms.get('terms_and_conditions'):
            term_lines.append(f"{labels['conditions']}: {terms['terms_and_conditions']}")
        if not term_lines:
            term_lines.append('-')
        story.append(P('\n'.join(term_lines)))

    story.append(Spacer(1, 6 * mm))
    story.append(P(tpl['footer_ar'] if arabic else tpl['footer_en'], sub_style))

    pdf.build(story)
    return buf.getvalue()
