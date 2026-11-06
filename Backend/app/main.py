import hashlib
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import (
    SessionLocal,
    init_db_and_seed,
    PurchaseOrder,
    POLineItem,
    VendorItemAlias,
    CompanyAccountingTemplate,
    ReconciliationSession,
)
from .schemas import (
    ThreeWayMatchResponse,
    CreatePOInput,
    AccountingTemplateSchema,
    ExtractedGRN,
    ExtractedGRNItem,
    SemanticMatchResult,
    ItemSemanticMatch,
    FraudAndRiskReport,
)
from .ai_service import (
    extract_invoice_from_image,
    extract_grn_from_image,
    match_items_semantically,
    learn_accounting_template_from_image,
)
from .rule_engine import run_three_way_matching, generate_accounting_voucher

app = FastAPI(title="TarazBan AI - Enterprise 3-Way Matching API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db_and_seed()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _serialize_po(po: PurchaseOrder):
    return {
        "po_number": po.po_number,
        "buyer_company": po.buyer_company,
        "vendor_name": po.vendor_name,
        "created_date": po.created_date,
        "items": [
            {
                "sku": item.sku,
                "erp_item_name": item.erp_item_name,
                "ordered_quantity": item.ordered_quantity,
                "agreed_unit_price": item.agreed_unit_price,
            }
            for item in po.items
        ],
    }


def _build_fraud_and_risk_report(
    db: Session,
    po_number: str,
    vendor_name: str,
    invoice_number: Optional[str],
    invoice_hash: str,
) -> FraudAndRiskReport:
    """لایه ۴: بررسی تکرار صورتحساب (Double-Billing) و محاسبه ریسک تامین‌کننده"""
    prev_sessions = (
        db.query(ReconciliationSession)
        .filter(ReconciliationSession.invoice_hash == invoice_hash)
        .order_by(ReconciliationSession.id.asc())
        .all()
    )
    if not prev_sessions and invoice_number:
        prev_sessions = (
            db.query(ReconciliationSession)
            .filter(
                ReconciliationSession.po_number == po_number,
                ReconciliationSession.invoice_number == invoice_number,
            )
            .order_by(ReconciliationSession.id.asc())
            .all()
        )

    is_dup = len(prev_sessions) > 0
    sub_count = len(prev_sessions) + 1
    first_session_id = prev_sessions[0].id if is_dup else None

    dup_msg = None
    if is_dup:
        dup_msg = (
            f"هشدار کنترل تکرار صورتحساب (Double-Billing Prevention): این سند پیش‌تر در پرونده ممیزی "
            f"شماره #{first_session_id} ثبت و بررسی شده است (نوبت ارسال این سند: مرتبه {sub_count})."
        )

    all_po_sessions = db.query(ReconciliationSession).filter_by(po_number=po_number).all()
    total_audits = len(all_po_sessions) + 1
    disc_count = sum(1 for s in all_po_sessions if s.has_discrepancy) + 1
    risk_level = "HIGH" if disc_count >= 2 else "MEDIUM"

    return FraudAndRiskReport(
        is_duplicate_invoice=is_dup,
        submission_count=sub_count,
        previous_session_id=first_session_id,
        duplicate_warning_message=dup_msg,
        vendor_total_audits=total_audits,
        vendor_discrepancy_count=disc_count,
        vendor_risk_level=risk_level,
    )


@app.get("/api/po")
def list_purchase_orders(db: Session = Depends(get_db)):
    pos = db.query(PurchaseOrder).order_by(PurchaseOrder.id.asc()).all()
    return [_serialize_po(po) for po in pos]


@app.get("/api/po/{po_number}")
def get_purchase_order(po_number: str, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter_by(po_number=po_number).first()
    if not po:
        raise HTTPException(status_code=404, detail="سفارش خرید در سیستم یافت نشد.")
    return _serialize_po(po)


@app.post("/api/po/manual")
def create_manual_po(payload: CreatePOInput, db: Session = Depends(get_db)):
    existing = db.query(PurchaseOrder).filter_by(po_number=payload.po_number).first()
    if existing:
        db.delete(existing)
        db.flush()

    new_po = PurchaseOrder(
        po_number=payload.po_number,
        buyer_company=payload.buyer_company,
        vendor_name=payload.vendor_name,
        created_date=payload.created_date,
        status="OPEN",
    )
    db.add(new_po)
    db.flush()

    for item in payload.items:
        db.add(
            POLineItem(
                po_id=new_po.id,
                sku=item.sku,
                erp_item_name=item.erp_item_name,
                ordered_quantity=item.ordered_quantity,
                agreed_unit_price=item.agreed_unit_price,
            )
        )
    db.commit()
    db.refresh(new_po)
    return _serialize_po(new_po)


@app.post("/api/po/extract-from-image")
async def create_po_from_document_image(
    po_image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    image_bytes = await po_image.read()
    extracted = extract_invoice_from_image(image_bytes)

    po_num = (extracted.po_number or extracted.invoice_number or f"PO-AI-{int(datetime.utcnow().timestamp()) % 10000}").strip()
    existing = db.query(PurchaseOrder).filter_by(po_number=po_num).first()
    if existing:
        db.delete(existing)
        db.flush()

    new_po = PurchaseOrder(
        po_number=po_num,
        buyer_company="ثبت شده خودکار توسط هوش مصنوعی از تصویر سند مرجع",
        vendor_name=extracted.vendor_name or "تامین‌کننده مندرج در سند مرجع",
        created_date="1405/08/15",
        status="OPEN",
    )
    db.add(new_po)
    db.flush()

    for idx, item in enumerate(extracted.items, start=1):
        db.add(
            POLineItem(
                po_id=new_po.id,
                sku=f"AI-SKU-{idx:02d}",
                erp_item_name=item.raw_item_name or f"کالای ردیف {idx}",
                ordered_quantity=max(item.quantity, 1),
                agreed_unit_price=item.unit_price,
            )
        )

    db.commit()
    db.refresh(new_po)
    return _serialize_po(new_po)


# ========================================================
# اندپوینت‌های لایه ۵: مدیریت و یادگیری الگوی سند حسابداری هر شرکت
# ========================================================

@app.get("/api/accounting-template/{po_number}", response_model=AccountingTemplateSchema)
def get_accounting_template(po_number: str, db: Session = Depends(get_db)):
    tpl = db.query(CompanyAccountingTemplate).filter_by(po_number=po_number).first()
    if not tpl:
        return AccountingTemplateSchema(
            po_number=po_number,
            company_name="الگوی پیش‌فرض سازمانی",
            template_title="کدینگ استاندارد حسابداری مالی و صنعتی",
            voucher_method="GROSS_WITH_DEDUCTION",
            inventory_acc_code="110401",
            inventory_acc_title="موجودی مواد و کالا - انبار مرکزی",
            vat_acc_code="110802",
            vat_acc_title="پیش‌پرداخت مالیات بر ارزش افزوده خرید معتبر",
            payable_acc_code="210101",
            payable_acc_title="حساب‌های پرداختنی تجاری - تامین‌کنندگان",
            discrepancy_acc_code="210905",
            discrepancy_acc_title="حساب کنترلی کسورات و برگشت از خرید",
            learned_from_user_sample=False,
        )
    return tpl


@app.post("/api/accounting-template/{po_number}/manual", response_model=AccountingTemplateSchema)
def save_manual_accounting_template(
    po_number: str,
    payload: AccountingTemplateSchema,
    db: Session = Depends(get_db),
):
    existing = db.query(CompanyAccountingTemplate).filter_by(po_number=po_number).first()
    if existing:
        db.delete(existing)
        db.flush()

    new_tpl = CompanyAccountingTemplate(
        po_number=po_number,
        company_name=payload.company_name,
        template_title=payload.template_title,
        voucher_method=payload.voucher_method,
        inventory_acc_code=payload.inventory_acc_code,
        inventory_acc_title=payload.inventory_acc_title,
        vat_acc_code=payload.vat_acc_code,
        vat_acc_title=payload.vat_acc_title,
        payable_acc_code=payload.payable_acc_code,
        payable_acc_title=payload.payable_acc_title,
        discrepancy_acc_code=payload.discrepancy_acc_code,
        discrepancy_acc_title=payload.discrepancy_acc_title,
        learned_from_user_sample=True,
    )
    db.add(new_tpl)
    db.commit()
    db.refresh(new_tpl)
    return new_tpl


@app.post("/api/accounting-template/{po_number}/learn-from-image", response_model=AccountingTemplateSchema)
async def learn_template_from_sample_image(
    po_number: str,
    sample_image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """دریافت تصویر نمونه سند حسابداری شرکت و یادگیری کدینگ و ساختار سند آن شرکت با هوش مصنوعی"""
    po = db.query(PurchaseOrder).filter_by(po_number=po_number).first()
    company_name = po.buyer_company if po else "شرکت انتخابی"

    image_bytes = await sample_image.read()
    learned_schema = learn_accounting_template_from_image(image_bytes, po_number, company_name)

    existing = db.query(CompanyAccountingTemplate).filter_by(po_number=po_number).first()
    if existing:
        db.delete(existing)
        db.flush()

    new_tpl = CompanyAccountingTemplate(
        po_number=po_number,
        company_name=learned_schema.company_name,
        template_title=learned_schema.template_title,
        voucher_method=learned_schema.voucher_method,
        inventory_acc_code=learned_schema.inventory_acc_code,
        inventory_acc_title=learned_schema.inventory_acc_title,
        vat_acc_code=learned_schema.vat_acc_code,
        vat_acc_title=learned_schema.vat_acc_title,
        payable_acc_code=learned_schema.payable_acc_code,
        payable_acc_title=learned_schema.payable_acc_title,
        discrepancy_acc_code=learned_schema.discrepancy_acc_code,
        discrepancy_acc_title=learned_schema.discrepancy_acc_title,
        learned_from_user_sample=True,
    )
    db.add(new_tpl)
    db.commit()
    db.refresh(new_tpl)
    return new_tpl


# ========================================================
# اندپوینت‌های اصلی ممیزی و تطبیق سه‌جانبه
# ========================================================

@app.post("/api/reconcile-adhoc", response_model=ThreeWayMatchResponse)
async def reconcile_adhoc_new_invoice(
    invoice_file: UploadFile = File(...),
    grn_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    invoice_bytes = await invoice_file.read()
    inv_hash = hashlib.sha256(invoice_bytes).hexdigest()
    extracted_invoice = extract_invoice_from_image(invoice_bytes)

    temp_po_num = extracted_invoice.po_number or extracted_invoice.invoice_number or "ADHOC-INVOICE"
    temp_po = PurchaseOrder(
        po_number=f"{temp_po_num} (ممیزی بدون سابقه ERP)",
        buyer_company="ممیزی مستقیم صورتحساب وارده",
        vendor_name=extracted_invoice.vendor_name or "تامین‌کننده صورتحساب",
        created_date="1405/08/15",
    )
    temp_po.items = [
        POLineItem(
            sku=f"INV-ROW-{item.row_number}",
            erp_item_name=item.raw_item_name,
            ordered_quantity=item.quantity,
            agreed_unit_price=item.unit_price,
        )
        for item in extracted_invoice.items
    ]

    if grn_file is not None:
        grn_bytes = await grn_file.read()
        extracted_grn = extract_grn_from_image(grn_bytes)
        semantic_matches = match_items_semantically(extracted_invoice, extracted_grn, temp_po.items)
        audit_mode = "TWO_WAY_ADHOC"
    else:
        extracted_grn = ExtractedGRN(
            grn_number="بدون قبض انبار",
            po_number=temp_po_num,
            items=[
                ExtractedGRNItem(
                    row_number=item.row_number,
                    raw_item_name=item.raw_item_name,
                    received_quantity=item.quantity,
                    notes="ممیزی مستقیم فاکتور بدون پیوست قبض انبار",
                )
                for item in extracted_invoice.items
            ],
        )
        semantic_matches = SemanticMatchResult(
            matches=[
                ItemSemanticMatch(
                    invoice_row_number=item.row_number,
                    invoice_raw_name=item.raw_item_name,
                    matched_erp_sku=f"INV-ROW-{item.row_number}",
                    grn_row_number=item.row_number,
                    is_technical_match=True,
                    confidence_score=1.0,
                    match_reasoning="استخراج مستقیم از صورتحساب جدید.",
                )
                for item in extracted_invoice.items
            ]
        )
        audit_mode = "SINGLE_INVOICE_AUDIT"

    fraud_report = _build_fraud_and_risk_report(
        db=db,
        po_number=temp_po.po_number,
        vendor_name=temp_po.vendor_name,
        invoice_number=extracted_invoice.invoice_number,
        invoice_hash=inv_hash,
    )

    db_session = ReconciliationSession(
        po_number=temp_po.po_number,
        vendor_name=temp_po.vendor_name,
        invoice_number=extracted_invoice.invoice_number or "",
        invoice_hash=inv_hash,
        has_discrepancy=True,
        overall_status="PROCESSING",
        report_json="{}",
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    report = run_three_way_matching(
        session_id=db_session.id,
        po=temp_po,
        invoice=extracted_invoice,
        grn=extracted_grn,
        semantic_matches=semantic_matches,
        audit_mode=audit_mode,
        fraud_and_risk=fraud_report,
    )

    db_session.has_discrepancy = (report.overall_status == "REJECTED_DISCREPANCY")
    db_session.overall_status = report.overall_status
    db_session.report_json = report.model_dump_json()
    db.commit()
    return report


@app.post("/api/reconcile/{po_number}", response_model=ThreeWayMatchResponse)
async def reconcile_documents(
    po_number: str,
    invoice_file: UploadFile = File(...),
    grn_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    po = db.query(PurchaseOrder).filter_by(po_number=po_number).first()
    if not po:
        raise HTTPException(status_code=404, detail="سفارش خرید در ERP یافت نشد")

    invoice_bytes = await invoice_file.read()
    grn_bytes = await grn_file.read()

    extracted_invoice = extract_invoice_from_image(invoice_bytes)
    documents_swapped = False

    # تشخیص خودکار جابه‌جایی عکس فاکتور و قبض انبار
    po_is_priced = any(item.agreed_unit_price > 0 for item in po.items)
    inv_has_no_price = (extracted_invoice.subtotal_amount == 0 and all(i.unit_price == 0 for i in extracted_invoice.items))

    if po_is_priced and inv_has_no_price:
        print("🔄 [AUTO-SWAP] Swapping GRN and Invoice automatically...")
        test_second_as_invoice = extract_invoice_from_image(grn_bytes)
        if test_second_as_invoice.subtotal_amount > 0 or any(i.unit_price > 0 for i in test_second_as_invoice.items):
            extracted_invoice = test_second_as_invoice
            extracted_grn = extract_grn_from_image(invoice_bytes)
            invoice_bytes = grn_bytes
            documents_swapped = True
        else:
            extracted_grn = extract_grn_from_image(grn_bytes)
    else:
        extracted_grn = extract_grn_from_image(grn_bytes)

    inv_hash = hashlib.sha256(invoice_bytes).hexdigest()

    # لایه ۳: واکشی نام‌های مستعار یادگرفته‌شده قبلی از حافظه دیتابیس
    aliases = db.query(VendorItemAlias).filter_by(po_number=po_number).all()
    learned_aliases_map = {a.vendor_raw_name: a.matched_sku for a in aliases}

    semantic_matches = match_items_semantically(
        invoice=extracted_invoice,
        grn=extracted_grn,
        po_items=po.items,
        learned_aliases=learned_aliases_map,
    )

    # لایه ۴: بررسی تکرار صورتحساب و ریسک تامین‌کننده
    fraud_report = _build_fraud_and_risk_report(
        db=db,
        po_number=po.po_number,
        vendor_name=po.vendor_name,
        invoice_number=extracted_invoice.invoice_number,
        invoice_hash=inv_hash,
    )

    # لایه ۵: واکشی الگوی سند حسابداری اختصاصی شرکت
    acc_template = db.query(CompanyAccountingTemplate).filter_by(po_number=po.po_number).first()

    db_session = ReconciliationSession(
        po_number=po.po_number,
        vendor_name=po.vendor_name,
        invoice_number=extracted_invoice.invoice_number or "",
        invoice_hash=inv_hash,
        has_discrepancy=True,
        overall_status="PROCESSING",
        report_json="{}",
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    report = run_three_way_matching(
        session_id=db_session.id,
        po=po,
        invoice=extracted_invoice,
        grn=extracted_grn,
        semantic_matches=semantic_matches,
        audit_mode="THREE_WAY_ERP",
        documents_swapped_auto_fixed=documents_swapped,
        fraud_and_risk=fraud_report,
        accounting_template=acc_template,
    )

    db_session.has_discrepancy = (report.overall_status == "REJECTED_DISCREPANCY")
    db_session.overall_status = report.overall_status
    db_session.report_json = report.model_dump_json()
    db.commit()
    return report


@app.post("/api/sessions/{session_id}/approve-item/{sku}", response_model=ThreeWayMatchResponse)
def approve_semantic_match(session_id: int, sku: str, db: Session = Depends(get_db)):
    """تایید معادل‌سازی توسط کارشناس + ذخیره دائمی در جدول حافظه خودآموز (VendorItemAlias)"""
    db_session = db.query(ReconciliationSession).filter_by(id=session_id).first()
    if not db_session:
        raise HTTPException(status_code=404, detail="پرونده مغایرت‌گیری یافت نشد")

    report = ThreeWayMatchResponse.model_validate_json(db_session.report_json)

    for item in report.line_items:
        if item.sku == sku:
            item.human_approved = True
            item.learned_from_memory = True
            if item.quantity_match and item.price_match and item.technical_match:
                item.status = "GREEN"

            # ذخیره در حافظه خودآموز دیتابیس برای مراجعات بعدی
            existing_alias = (
                db.query(VendorItemAlias)
                .filter_by(po_number=report.po_number, vendor_raw_name=item.vendor_item_name.strip())
                .first()
            )
            if not existing_alias:
                db.add(
                    VendorItemAlias(
                        po_number=report.po_number,
                        vendor_raw_name=item.vendor_item_name.strip(),
                        matched_sku=sku,
                    )
                )

    if not any(i.status == "RED" for i in report.line_items) and not report.financial_summary.has_tax_discrepancy:
        if any(i.status == "YELLOW" for i in report.line_items):
            report.overall_status = "PENDING_HUMAN_REVIEW"
        else:
            report.overall_status = "APPROVED_FOR_PAYMENT"

    db_session.overall_status = report.overall_status
    db_session.report_json = report.model_dump_json()
    db.commit()
    return report