"""Grouped RFQ exports (Excel and PDF) organized by Main Category -> Subcategory -> Materials."""
from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Invitation, Member, Organization, RFQ, RFQItem, User
from app.security import current_user

router = APIRouter()


def _authorize_rfq_read(db: Session, user: User, rfq_id: str) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if not rfq:
        raise HTTPException(404, 'RFQ not found')
    allowed = user.is_admin or db.scalar(
        select(Member.id)
        .join(Organization, Organization.id == Member.organization_id)
        .where(Member.user_id == user.id, Member.organization_id == rfq.buyer_org_id, Organization.is_active.is_(True))
    )
    if not allowed:
        sub = select(Member.organization_id).join(Organization, Organization.id == Member.organization_id).where(
            Member.user_id == user.id, Organization.is_active.is_(True)
        )
        allowed = db.scalar(select(Invitation.id).where(Invitation.rfq_id == rfq_id, Invitation.supplier_org_id.in_(sub)))
    if not allowed:
        raise HTTPException(404, 'RFQ not found')
    return rfq


def group_rfq_items(db: Session, rfq: RFQ, *, lang: str = 'en') -> list[dict]:
    """Group RFQ items by Main Category -> Subcategory -> Materials, preserving original item IDs."""
    items = list(db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at, RFQItem.id)).all())
    cat_ids = {cid for item in items for cid in (item.main_category_id, item.subcategory_id) if cid}
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(cat_ids))).all()} if cat_ids else {}

    def cat_label(cid: str | None, fallback: str) -> str:
        c = categories.get(cid) if cid else None
        if not c:
            return fallback
        if lang == 'ar' and c.name_ar:
            return c.name_ar
        return c.name_en

    main_groups: OrderedDict[str, dict] = OrderedDict()
    for item in items:
        main_key = item.main_category_id or '__unclassified__'
        sub_key = item.subcategory_id or '__general__'
        if main_key not in main_groups:
            main_groups[main_key] = {
                'main_category_id': item.main_category_id,
                'main_category_name': cat_label(item.main_category_id, 'Unclassified' if lang != 'ar' else 'غير مصنف'),
                'subcategories': OrderedDict(),
            }
        subs = main_groups[main_key]['subcategories']
        if sub_key not in subs:
            subs[sub_key] = {
                'subcategory_id': item.subcategory_id,
                'subcategory_name': cat_label(item.subcategory_id, 'General' if lang != 'ar' else 'عام'),
                'items': [],
            }
        subs[sub_key]['items'].append({
            'id': item.id,
            'material_id': item.material_id,
            'main_category_id': item.main_category_id,
            'subcategory_id': item.subcategory_id,
            'description': item.description,
            'specifications': item.specifications or '',
            'unit': item.unit,
            'quantity': str(item.quantity),
        })

    return [
        {
            'main_category_id': group['main_category_id'],
            'main_category_name': group['main_category_name'],
            'subcategories': list(group['subcategories'].values()),
        }
        for group in main_groups.values()
    ]


def build_rfq_excel(rfq: RFQ, grouped: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = 'RFQ Materials'

    title_font = Font(bold=True, size=14, color='0F172A')
    meta_label_font = Font(bold=True, size=10, color='334155')
    header_font = Font(bold=True, size=10, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='1E3A8A')
    main_fill = PatternFill('solid', fgColor='DBEAFE')
    main_font = Font(bold=True, size=11, color='1E3A8A')
    sub_fill = PatternFill('solid', fgColor='F1F5F9')
    sub_font = Font(bold=True, size=10, color='0F172A')
    thin = Side(border_style='thin', color='CBD5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append([f'Request for Quotation: {rfq.reference}'])
    ws['A1'].font = title_font
    ws.merge_cells('A1:F1')

    meta_rows = [
        ('Title', rfq.title, 'Status', rfq.status),
        ('Delivery City', rfq.city or 'Not specified', 'Response Deadline', rfq.response_deadline.isoformat() if rfq.response_deadline else ''),
        ('Project Reference', rfq.project_name or '-', 'Required Delivery Date', rfq.required_delivery_date.isoformat() if rfq.required_delivery_date else '-'),
    ]
    for r_idx, (k1, v1, k2, v2) in enumerate(meta_rows, start=2):
        ws.append([k1, v1, '', k2, v2, ''])
        ws.cell(r_idx, 1).font = meta_label_font
        ws.cell(r_idx, 4).font = meta_label_font

    if rfq.additional_instructions:
        ws.append(['Instructions', rfq.additional_instructions])
        ws.cell(ws.max_row, 1).font = meta_label_font
        ws.merge_cells(start_row=ws.max_row, start_column=2, end_row=ws.max_row, end_column=6)

    ws.append([])
    header_row = ws.max_row + 1
    ws.append(['#', 'Item ID', 'Material Description', 'Specifications', 'Unit', 'Quantity'])
    for col_idx in range(1, 7):
        cell = ws.cell(header_row, col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='left', vertical='center')

    seq = 1
    for main_group in grouped:
        row_idx = ws.max_row + 1
        ws.append([f"Main Category: {main_group['main_category_name']}", '', '', '', '', ''])
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=6)
        for col_idx in range(1, 7):
            cell = ws.cell(row_idx, col_idx)
            cell.font = main_font
            cell.fill = main_fill
            cell.border = border

        for sub_group in main_group['subcategories']:
            sub_row_idx = ws.max_row + 1
            ws.append([f"  Subcategory: {sub_group['subcategory_name']}", '', '', '', '', ''])
            ws.merge_cells(start_row=sub_row_idx, start_column=1, end_row=sub_row_idx, end_column=6)
            for col_idx in range(1, 7):
                cell = ws.cell(sub_row_idx, col_idx)
                cell.font = sub_font
                cell.fill = sub_fill
                cell.border = border

            for item in sub_group['items']:
                ws.append([
                    seq,
                    item['id'],
                    item['description'],
                    item['specifications'] or '-',
                    item['unit'],
                    float(item['quantity']),
                ])
                cur = ws.max_row
                for col_idx in range(1, 7):
                    cell = ws.cell(cur, col_idx)
                    cell.border = border
                    cell.alignment = Alignment(vertical='top', wrap_text=True)
                ws.cell(cur, 6).number_format = '0.###'
                seq += 1

    for col, width in zip('ABCDEF', [8, 20, 44, 34, 12, 14]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = f'A{header_row + 1}'

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_rfq_pdf(rfq: RFQ, grouped: list[dict]) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f'RFQ {rfq.reference}',
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('RfqTitle', parent=styles['Heading1'], fontSize=15, textColor=colors.HexColor('#0F172A'), spaceAfter=6)
    meta_style = ParagraphStyle('RfqMeta', parent=styles['Normal'], fontSize=9, leading=13, textColor=colors.HexColor('#334155'))
    main_style = ParagraphStyle('MainCat', parent=styles['Heading2'], fontSize=11, textColor=colors.HexColor('#1E3A8A'), spaceBefore=10, spaceAfter=4)
    sub_style = ParagraphStyle('SubCat', parent=styles['Heading3'], fontSize=9.5, textColor=colors.HexColor('#0F172A'), spaceBefore=4, spaceAfter=3)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8.5, leading=11)

    story = [
        Paragraph(f'Request for Quotation — {escape(rfq.reference)}', title_style),
        Paragraph(
            f'<b>Title:</b> {escape(rfq.title)} &nbsp;|&nbsp; '
            f'<b>Delivery City:</b> {escape(rfq.city or "Not specified")} &nbsp;|&nbsp; '
            f'<b>Status:</b> {escape(rfq.status)}',
            meta_style,
        ),
        Paragraph(
            f'<b>Deadline:</b> {escape(rfq.response_deadline.isoformat() if rfq.response_deadline else "-")} &nbsp;|&nbsp; '
            f'<b>Project:</b> {escape(rfq.project_name or "-")}',
            meta_style,
        ),
    ]
    if rfq.additional_instructions:
        story.append(Spacer(1, 4))
        story.append(Paragraph(f'<b>Instructions:</b> {escape(rfq.additional_instructions)}', meta_style))
    story.append(Spacer(1, 8))

    seq = 1
    for main_group in grouped:
        story.append(Paragraph(f'Main Category: {escape(main_group["main_category_name"])}', main_style))
        for sub_group in main_group['subcategories']:
            story.append(Paragraph(f'Subcategory: {escape(sub_group["subcategory_name"])}', sub_style))
            table_data = [['#', 'Description', 'Specifications', 'Unit', 'Quantity']]
            for item in sub_group['items']:
                table_data.append([
                    str(seq),
                    Paragraph(escape(item['description']), cell_style),
                    Paragraph(escape(item['specifications'] or '-'), cell_style),
                    escape(item['unit']),
                    escape(item['quantity']),
                ])
                seq += 1
            table = Table(table_data, colWidths=[10 * mm, 72 * mm, 54 * mm, 20 * mm, 22 * mm], repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8.5),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
            story.append(Spacer(1, 5))

    doc.build(story)
    return buf.getvalue()


@router.get('/{rfq_id}/export.xlsx', response_class=StreamingResponse)
def export_rfq_excel(
    rfq_id: str,
    lang: str = Query('en', pattern='^(en|ar)$'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rfq = _authorize_rfq_read(db, user, rfq_id)
    grouped = group_rfq_items(db, rfq, lang=lang)
    content = build_rfq_excel(rfq, grouped)
    safe_ref = Path(rfq.reference or rfq.id).name.replace(' ', '_')
    return StreamingResponse(
        BytesIO(content),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{safe_ref}-materials.xlsx"'},
    )


@router.get('/{rfq_id}/export.pdf', response_class=StreamingResponse)
def export_rfq_pdf(
    rfq_id: str,
    lang: str = Query('en', pattern='^(en|ar)$'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rfq = _authorize_rfq_read(db, user, rfq_id)
    grouped = group_rfq_items(db, rfq, lang=lang)
    content = build_rfq_pdf(rfq, grouped)
    safe_ref = Path(rfq.reference or rfq.id).name.replace(' ', '_')
    return StreamingResponse(
        BytesIO(content),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{safe_ref}-materials.pdf"'},
    )
