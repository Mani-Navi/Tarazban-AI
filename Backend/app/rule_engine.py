from typing import List, Optional
from .database import PurchaseOrder, CompanyAccountingTemplate
from .schemas import (
    ExtractedInvoice,
    ExtractedGRN,
    SemanticMatchResult,
    LineItemReconciliation,
    TaxAndTotalReconciliation,
    DocumentAuthenticityReport,
    FraudAndRiskReport,
    JournalEntryLine,
    AccountingVoucherReport,
    ThreeWayMatchResponse,
)

VAT_RATE = 0.10


def generate_accounting_voucher(
    po: PurchaseOrder,
    financial_summary: TaxAndTotalReconciliation,
    template: Optional[CompanyAccountingTemplate],
) -> Optional[AccountingVoucherReport]:
    """لایه ۵: تولید خودکار سند حسابداری دوبل (بدهکار/بستانکار) بر اساس الگوی اختصاصی شرکت"""
    if financial_summary.invoice_claimed_grand_total <= 0 and financial_summary.true_payable_grand_total <= 0:
        return None

    # اگر شرکت الگوی اختصاصی در دیتابیس داشت از همان استفاده کن، وگرنه الگوی پیش‌فرض سازمانی
    tpl_title = template.template_title if template else "الگوی استاندارد حسابداری مالی و صنعتی"
    method = template.voucher_method if template else "GROSS_WITH_DEDUCTION"
    learned = template.learned_from_user_sample if template else False

    inv_code = template.inventory_acc_code if template else "110401"
    inv_title = template.inventory_acc_title if template else "موجودی مواد و کالا - انبار مرکزی"
    vat_code = template.vat_acc_code if template else "110802"
    vat_title = template.vat_acc_title if template else "پیش‌پرداخت مالیات بر ارزش افزوده خرید معتبر"
    pay_code = template.payable_acc_code if template else "210101"
    pay_title = template.payable_acc_title if template else f"حساب‌های پرداختنی تجاری - {po.vendor_name}"
    disc_code = template.discrepancy_acc_code if template else "210905"
    disc_title = template.discrepancy_acc_title if template else "حساب کنترلی کسورات، ضایعات و اعلامیه بدهکار تامین‌کنندگان"

    lines: List[JournalEntryLine] = []

    if method == "GROSS_WITH_DEDUCTION" and financial_summary.total_prevented_loss_rials > 0:
        # روش ۱: ثبت ناخالص به همراه آرتیکل کسورات و اعلامیه بدهکار (نمایش کامل کسورات در سند حسابداری)
        gross_discrepancy = financial_summary.total_prevented_loss_rials
        lines = [
            JournalEntryLine(
                row_no=1,
                account_code=inv_code,
                account_title=inv_title,
                description=f"ثبت بهای خالص کالای سالم تحویل‌شده طبق قبض انبار و نرخ مصوب {po.po_number}",
                debit_rials=financial_summary.true_payable_subtotal,
                credit_rials=0,
            ),
            JournalEntryLine(
                row_no=2,
                account_code=vat_code,
                account_title=vat_title,
                description="ثبت ۱۰٪ مالیات و عوارض ارزش افزوده قانونی مورد تایید ممیزی",
                debit_rials=financial_summary.true_payable_vat,
                credit_rials=0,
            ),
            JournalEntryLine(
                row_no=3,
                account_code=disc_code,
                account_title=disc_title,
                description=f"اعلامیه بدهکار تامین‌کننده بابت کسری انبار، انحراف نرخ و اصلاح مالیات ({po.vendor_name})",
                debit_rials=gross_discrepancy,
                credit_rials=0,
            ),
            JournalEntryLine(
                row_no=4,
                account_code=pay_code,
                account_title=pay_title,
                description=f"بستانکاری خالص قابل پرداخت به تامین‌کننده پس از کسر مغایرت‌ها ({po.vendor_name})",
                debit_rials=0,
                credit_rials=financial_summary.true_payable_grand_total,
            ),
            JournalEntryLine(
                row_no=5,
                account_code=disc_code,
                account_title=f"ذخیره تسویه ادعا و کسورات صورتحساب ({po.vendor_name})",
                description="بستن طرف حساب کنترلی کسورات صورتحساب ارسالی فروشنده",
                debit_rials=0,
                credit_rials=gross_discrepancy,
            ),
        ]
    else:
        # روش ۲: ثبت خالص به مبلغ واقعی قابل پرداخت (NET_APPROVED)
        lines = [
            JournalEntryLine(
                row_no=1,
                account_code=inv_code,
                account_title=inv_title,
                description=f"ثبت ورود قطعی کالا به انبار بر مبنای مقدار سالم و نرخ مصوب ({po.po_number})",
                debit_rials=financial_summary.true_payable_subtotal,
                credit_rials=0,
            ),
            JournalEntryLine(
                row_no=2,
                account_code=vat_code,
                account_title=vat_title,
                description="ثبت ۱۰٪ مالیات بر ارزش افزوده قانونی بر مبنای مبلغ مورد تایید",
                debit_rials=financial_summary.true_payable_vat,
                credit_rials=0,
            ),
            JournalEntryLine(
                row_no=3,
                account_code=pay_code,
                account_title=pay_title,
                description=f"منظور نمودن مبلغ قابل تسویه نهایی در حساب بستانکاران تجاری ({po.vendor_name})",
                debit_rials=0,
                credit_rials=financial_summary.true_payable_grand_total,
            ),
        ]

    total_debit = sum(l.debit_rials for l in lines)
    total_credit = sum(l.credit_rials for l in lines)

    return AccountingVoucherReport(
        template_title=tpl_title,
        voucher_method=method,
        learned_from_user_sample=learned,
        voucher_date="1405/08/15",
        voucher_description=f"سند اتوماتیک خرید و ممیزی سه‌جانبه پرونده {po.po_number} - {po.vendor_name}",
        lines=lines,
        total_debit_rials=total_debit,
        total_credit_rials=total_credit,
        is_balanced=(total_debit == total_credit),
    )


def run_three_way_matching(
    session_id: int,
    po: PurchaseOrder,
    invoice: ExtractedInvoice,
    grn: ExtractedGRN,
    semantic_matches: SemanticMatchResult,
    audit_mode: str = "THREE_WAY_ERP",
    documents_swapped_auto_fixed: bool = False,
    fraud_and_risk: Optional[FraudAndRiskReport] = None,
    accounting_template: Optional[CompanyAccountingTemplate] = None,
) -> ThreeWayMatchResponse:
    po_map = {item.sku: item for item in po.items}
    inv_map = {item.row_number: item for item in invoice.items}
    grn_item_list = grn.items
    grn_map = {item.row_number: item for item in grn_item_list}

    # لایه ۲ (بخش الف): حل‌کننده معادله ماتریسی فاکتور برای کشف خطای دیداری OCR (مثل خواندن 12,500 به جای 13,500)
    ocr_corrections_by_row = {}
    raw_rows_sum = sum(i.quantity * i.unit_price for i in invoice.items)
    subtotal_diff = invoice.subtotal_amount - raw_rows_sum

    for inv_item in invoice.items:
        # حالت ۱: مبلغ کل ردیف با حاصلضرب تعداد در فی متفاوت است
        if inv_item.quantity > 0 and inv_item.total_price > 0:
            exact_unit = inv_item.total_price // inv_item.quantity
            if exact_unit * inv_item.quantity == inv_item.total_price and inv_item.unit_price != exact_unit:
                old_u = inv_item.unit_price
                inv_item.unit_price = exact_unit
                ocr_corrections_by_row[inv_item.row_number] = (
                    f"اصلاح خودکار خطای دیداری با تراز ریاضی ردیف (Math-Guided OCR): رقم بهای واحد در تصویر "
                    f"«{old_u:,} ریال» خوانش شده بود که بر اساس تقسیم مبلغ کل ردیف بر تعداد ({inv_item.total_price:,} ÷ {inv_item.quantity:,}) "
                    f"به «{exact_unit:,} ریال» اصلاح گردید."
                )

        # حالت ۲: حل معادله ماتریسی جمع کل فاکتور (وقتی مدل 13,500 را 12,500 و جمع ردیف را 25M خوانده، اما جمع کل فاکتور 117M است!)
        if (
            inv_item.row_number not in ocr_corrections_by_row
            and invoice.subtotal_amount > 0
            and subtotal_diff > 0
            and inv_item.quantity > 0
            and subtotal_diff % inv_item.quantity == 0
        ):
            unit_delta = subtotal_diff // inv_item.quantity
            # اگر اختلاف دقیقا ناشی از خطای دیداری ۱ رقم فارسی (مثلا 1000 ریال بین ۱۲,۵۰۰ و ۱۳,۵۰۰) باشد
            if unit_delta in (100, 1000, 10000):
                old_u = inv_item.unit_price
                corrected_u = old_u + unit_delta
                inv_item.unit_price = corrected_u
                inv_item.total_price = inv_item.quantity * corrected_u
                ocr_corrections_by_row[inv_item.row_number] = (
                    f"اصلاح خودکار خطای دیداری با حل معادله ماتریسی فاکتور (Math-Guided OCR): رقم بهای واحد در تصویر به دلیل تشابه "
                    f"نگارشی ارقام فارسی «{old_u:,} ریال» (جمع ردیف‌ها: {raw_rows_sum:,} ریال) خوانش شده بود؛ با حل معادله تراز جمع خالص "
                    f"فاکتور ({invoice.subtotal_amount:,} ریال)، خطای دیداری کشف و بهای واحد به «{corrected_u:,} ریال» اصلاح گردید."
                )
                subtotal_diff = 0

    # بررسی عدم تطابق شماره سفارش مندرج در اسناد با پرونده مرجع انتخابی
    document_mismatch_warning = None
    if audit_mode == "THREE_WAY_ERP" and invoice.po_number:
        clean_inv_po = invoice.po_number.strip().upper()
        clean_selected_po = po.po_number.strip().upper()
        if clean_inv_po and clean_selected_po not in clean_inv_po and clean_inv_po not in clean_selected_po:
            document_mismatch_warning = (
                f"عدم انطباق پرونده مرجع: اسناد بارگذاری‌شده دارای شماره عطف «{invoice.po_number}» هستند، "
                f"در حالی که پرونده انتخاب‌شده در سیستم «{po.po_number} ({po.buyer_company})» می‌باشد."
            )
    reconciled_lines: List[LineItemReconciliation] = []
    calculated_invoice_subtotal = 0
    true_payable_subtotal = 0

    for idx, match in enumerate(semantic_matches.matches, start=1):
        po_item = po_map.get(match.matched_erp_sku) or (po.items[0] if len(po.items) == 1 else None)
        inv_item = inv_map.get(match.invoice_row_number) or (invoice.items[0] if invoice.items else None)
        grn_item = grn_map.get(match.grn_row_number) or (grn_item_list[0] if grn_item_list else None)

        if not (po_item and inv_item and grn_item):
            continue

        technical_match = match.is_technical_match and (match.confidence_score >= 0.50) and (document_mismatch_warning is None)
        quantity_match = (inv_item.quantity == grn_item.received_quantity)

        effective_po_price = (
            inv_item.unit_price
            if (audit_mode != "THREE_WAY_ERP" and po_item.agreed_unit_price == 0)
            else po_item.agreed_unit_price
        )
        if not technical_match or effective_po_price == 0:
            price_match = True
        else:
            price_match = (inv_item.unit_price == effective_po_price)

        exact_name_match = (inv_item.raw_item_name.strip() == po_item.erp_item_name.strip()) or match.learned_from_memory
        discrepancy_messages = []

        if not technical_match:
            discrepancy_messages.append(
                f"عدم انطباق ماهوی و فنی اسناد: کالای مندرج در اسناد بارگذاری‌شده («{inv_item.raw_item_name}» / «{grn_item.raw_item_name}») "
                f"با کالای پرونده مرجع («{po_item.erp_item_name}») فاقد تطابق فنی و کدینگ است."
            )
            discrepancy_messages.append(f"گزارش کارشناسی تطبیق فنی: {match.match_reasoning}")
        else:
            if not quantity_match:
                diff_qty = abs(inv_item.quantity - grn_item.received_quantity)
                if grn_item.received_quantity < inv_item.quantity:
                    discrepancy_messages.append(
                        f"انحراف مقداری (کسری تحویل انبار): مقدار مندرج در صورتحساب تامین‌کننده ({inv_item.quantity:,} واحد) "
                        f"با مقدار سالم تحویل‌گرفته‌شده در قبض انبار ({grn_item.received_quantity:,} واحد) دارای {diff_qty:,} واحد کسری است."
                    )
                else:
                    discrepancy_messages.append(
                        f"انحراف مقداری (مازاد تحویل انبار نسبت به صورتحساب): در صورتحساب {inv_item.quantity:,} واحد و در قبض انبار "
                        f"{grn_item.received_quantity:,} واحد ثبت شده است ({diff_qty:,} واحد اختلاف)."
                    )

            if not price_match and effective_po_price > 0 and inv_item.unit_price > 0:
                diff_price = abs(inv_item.unit_price - effective_po_price)
                if inv_item.unit_price > effective_po_price:
                    discrepancy_messages.append(
                        f"انحراف نرخ واحد (افزایش نرخ نسبت به سفارش خرید): بهای واحد منظور شده در صورتحساب ({inv_item.unit_price:,} ریال) "
                        f"نسبت به نرخ مصوب سفارش خرید ({effective_po_price:,} ریال) دارای {diff_price:,} ریال افزایش نرخ در هر واحد است."
                    )
                else:
                    discrepancy_messages.append(
                        f"انحراف نرخ واحد: بهای واحد صورتحساب ({inv_item.unit_price:,} ریال) با نرخ سفارش خرید ({effective_po_price:,} ریال) "
                        f"دارای {diff_price:,} ریال اختلاف است."
                    )

        inv_row_total = inv_item.quantity * inv_item.unit_price
        true_row_total = (
            min(grn_item.received_quantity, po_item.ordered_quantity) * effective_po_price
            if technical_match
            else 0
        )
        financial_impact = max(inv_row_total - true_row_total, 0)

        calculated_invoice_subtotal += inv_row_total
        true_payable_subtotal += true_row_total

        if not technical_match or not quantity_match or not price_match:
            status = "RED"
        elif not exact_name_match:
            status = "YELLOW"
        else:
            status = "GREEN"

        display_sku = po_item.sku if len(semantic_matches.matches) <= len(po.items) else f"{po_item.sku}-R{idx}"

        reconciled_lines.append(
            LineItemReconciliation(
                sku=display_sku,
                erp_item_name=po_item.erp_item_name,
                vendor_item_name=inv_item.raw_item_name,
                grn_item_name=grn_item.raw_item_name,
                po_quantity=po_item.ordered_quantity,
                invoice_quantity=inv_item.quantity,
                grn_received_quantity=grn_item.received_quantity,
                grn_notes=grn_item.notes,
                po_unit_price=effective_po_price,
                invoice_unit_price=inv_item.unit_price,
                quantity_match=quantity_match,
                price_match=price_match,
                technical_match=technical_match,
                semantic_confidence=match.confidence_score if technical_match else 0.15,
                semantic_reasoning=match.match_reasoning,
                human_approved=exact_name_match,
                learned_from_memory=match.learned_from_memory,
                math_ocr_correction_note=ocr_corrections_by_row.get(inv_item.row_number),
                status=status,
                discrepancy_messages=discrepancy_messages,
                financial_impact_rials=financial_impact,
            )
        )

    expected_invoice_vat = int(round(invoice.subtotal_amount * VAT_RATE))
    vat_calculation_error = invoice.vat_amount - expected_invoice_vat
    has_tax_discrepancy = (vat_calculation_error != 0) and (invoice.subtotal_amount > 0)

    tax_msg = None
    if has_tax_discrepancy:
        tax_msg = (
            f"مغایرت محاسباتی مالیات بر ارزش افزوده (نرخ قانونی ۱۰٪): بر مبنای جمع خالص صورتحساب ({invoice.subtotal_amount:,} ریال)، "
            f"مالیات و عوارض متعلقه معادل {expected_invoice_vat:,} ریال محاسبه می‌گردد؛ لیکن تامین‌کننده مبلغ "
            f"{invoice.vat_amount:,} ریال در صورتحساب درج نموده است ({abs(vat_calculation_error):,} ریال اختلاف مالیاتی)."
        )

    true_payable_vat = int(round(true_payable_subtotal * VAT_RATE))
    true_payable_grand_total = true_payable_subtotal + true_payable_vat
    total_prevented_loss = max(invoice.grand_total_amount - true_payable_grand_total, 0)

    financial_summary = TaxAndTotalReconciliation(
        invoice_claimed_subtotal=invoice.subtotal_amount,
        calculated_invoice_subtotal=calculated_invoice_subtotal,
        invoice_claimed_vat=invoice.vat_amount,
        expected_invoice_vat=expected_invoice_vat,
        vat_calculation_error=vat_calculation_error,
        invoice_claimed_grand_total=invoice.grand_total_amount,
        true_payable_subtotal=true_payable_subtotal,
        true_payable_vat=true_payable_vat,
        true_payable_grand_total=true_payable_grand_total,
        total_prevented_loss_rials=total_prevented_loss,
        has_tax_discrepancy=has_tax_discrepancy,
        tax_discrepancy_message=tax_msg,
    )

    # لایه ۱: گزارش اصالت فیزیکی اسناد
    authenticity_report = DocumentAuthenticityReport(
        invoice_has_stamp=invoice.has_official_stamp,
        invoice_has_signature=invoice.has_valid_signature,
        invoice_auth_note=invoice.authenticity_note or "بررسی اصالت فیزیکی سند اول انجام شد.",
        grn_has_stamp=grn.has_official_stamp,
        grn_has_signature=grn.has_valid_signature,
        grn_has_handwriting=grn.has_handwritten_notes,
        grn_auth_note=grn.authenticity_note or "بررسی اصالت فیزیکی سند انبار انجام شد.",
    )

    if fraud_and_risk is None:
        fraud_and_risk = FraudAndRiskReport()

    # لایه ۵: صدور خودکار سند حسابداری دوبل منطبق با الگوی شرکت
    accounting_voucher = None
    if not document_mismatch_warning:
        accounting_voucher = generate_accounting_voucher(
            po=po,
            financial_summary=financial_summary,
            template=accounting_template,
        )

    if any(line.status == "RED" for line in reconciled_lines) or has_tax_discrepancy or document_mismatch_warning:
        overall_status = "REJECTED_DISCREPANCY"
    elif any(line.status == "YELLOW" for line in reconciled_lines):
        overall_status = "PENDING_HUMAN_REVIEW"
    else:
        overall_status = "APPROVED_FOR_PAYMENT"

    return ThreeWayMatchResponse(
        session_id=session_id,
        po_number=po.po_number,
        buyer_company=po.buyer_company,
        vendor_name=po.vendor_name,
        audit_mode=audit_mode,
        documents_swapped_auto_fixed=documents_swapped_auto_fixed,
        document_mismatch_warning=document_mismatch_warning,
        overall_status=overall_status,
        authenticity_report=authenticity_report,
        fraud_and_risk=fraud_and_risk,
        line_items=reconciled_lines,
        financial_summary=financial_summary,
        accounting_voucher=accounting_voucher,
    )