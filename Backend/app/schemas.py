from pydantic import BaseModel, field_validator
from typing import List, Optional, Literal


class CreatePOItemInput(BaseModel):
    sku: str
    erp_item_name: str
    ordered_quantity: int
    agreed_unit_price: int = 0


class CreatePOInput(BaseModel):
    po_number: str
    buyer_company: str
    vendor_name: str
    created_date: str = "1405/08/15"
    items: List[CreatePOItemInput]


# ورودی و خروجی الگوی سند حسابداری اختصاصی هر شرکت
class AccountingTemplateSchema(BaseModel):
    po_number: str
    company_name: str
    template_title: str
    voucher_method: str = "GROSS_WITH_DEDUCTION"  # GROSS_WITH_DEDUCTION | NET_APPROVED
    inventory_acc_code: str
    inventory_acc_title: str
    vat_acc_code: str
    vat_acc_title: str
    payable_acc_code: str
    payable_acc_title: str
    discrepancy_acc_code: str
    discrepancy_acc_title: str
    learned_from_user_sample: bool = False


class ExtractedInvoiceItem(BaseModel):
    row_number: int = 1
    raw_item_name: str = ""
    quantity: int = 1
    unit_price: int = 0
    total_price: int = 0

    @field_validator("row_number", "quantity", "unit_price", "total_price", mode="before")
    @classmethod
    def none_to_zero(cls, v):
        if v is None or v == "":
            return 0
        try:
            return int(float(str(v).replace(",", "")))
        except Exception:
            return 0


class ExtractedInvoice(BaseModel):
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    vendor_name: Optional[str] = None
    items: List[ExtractedInvoiceItem] = []
    subtotal_amount: int = 0
    vat_amount: int = 0
    grand_total_amount: int = 0
    # لایه ۱: اصالت فیزیکی سند (مهر، امضا و دست‌خط)
    has_official_stamp: bool = True
    has_valid_signature: bool = True
    authenticity_note: Optional[str] = "مهر و امضای فروشنده در سند مشاهده شد."

    @field_validator("subtotal_amount", "vat_amount", "grand_total_amount", mode="before")
    @classmethod
    def none_to_zero_totals(cls, v):
        if v is None or v == "":
            return 0
        try:
            return int(float(str(v).replace(",", "")))
        except Exception:
            return 0


class ExtractedGRNItem(BaseModel):
    row_number: int = 1
    raw_item_name: str = ""
    received_quantity: int = 1
    notes: Optional[str] = ""

    @field_validator("row_number", "received_quantity", mode="before")
    @classmethod
    def none_to_one(cls, v):
        if v is None or v == "":
            return 1
        try:
            return int(float(str(v).replace(",", "")))
        except Exception:
            return 1


class ExtractedGRN(BaseModel):
    grn_number: Optional[str] = None
    po_number: Optional[str] = None
    items: List[ExtractedGRNItem] = []
    has_official_stamp: bool = True
    has_valid_signature: bool = True
    has_handwritten_notes: bool = False
    authenticity_note: Optional[str] = "امضای انباردار و مهر انبار بررسی شد."


class ItemSemanticMatch(BaseModel):
    invoice_row_number: int = 1
    invoice_raw_name: str = ""
    matched_erp_sku: str = "UNMATCHED"
    grn_row_number: int = 1
    is_technical_match: bool = True
    confidence_score: float = 0.90
    match_reasoning: str = ""
    learned_from_memory: bool = False

    @field_validator("matched_erp_sku", "invoice_raw_name", "match_reasoning", mode="before")
    @classmethod
    def none_to_str(cls, v):
        if v is None:
            return "UNMATCHED"
        return str(v)

    @field_validator("invoice_row_number", "grn_row_number", mode="before")
    @classmethod
    def none_to_int_one(cls, v):
        if v is None or v == "":
            return 1
        try:
            return int(v)
        except Exception:
            return 1

    @field_validator("is_technical_match", mode="before")
    @classmethod
    def none_to_bool(cls, v):
        if v is None:
            return False
        return bool(v)

    @field_validator("confidence_score", mode="before")
    @classmethod
    def none_to_float(cls, v):
        if v is None:
            return 0.15
        try:
            return float(v)
        except Exception:
            return 0.15


class SemanticMatchResult(BaseModel):
    matches: List[ItemSemanticMatch]


class LineItemReconciliation(BaseModel):
    sku: str
    erp_item_name: str
    vendor_item_name: str
    grn_item_name: str
    po_quantity: int
    invoice_quantity: int
    grn_received_quantity: int
    grn_notes: Optional[str] = None
    po_unit_price: int
    invoice_unit_price: int
    quantity_match: bool
    price_match: bool
    technical_match: bool = True
    semantic_confidence: float
    semantic_reasoning: str
    human_approved: bool = False
    learned_from_memory: bool = False
    math_ocr_correction_note: Optional[str] = None  # لایه ۲: گزارش خوداصلاح‌گر ریاضی OCR
    status: Literal["RED", "YELLOW", "GREEN"]
    discrepancy_messages: List[str]
    financial_impact_rials: int


class TaxAndTotalReconciliation(BaseModel):
    invoice_claimed_subtotal: int
    calculated_invoice_subtotal: int
    invoice_claimed_vat: int
    expected_invoice_vat: int
    vat_calculation_error: int
    invoice_claimed_grand_total: int
    true_payable_subtotal: int
    true_payable_vat: int
    true_payable_grand_total: int
    total_prevented_loss_rials: int
    has_tax_discrepancy: bool
    tax_discrepancy_message: Optional[str] = None


# لایه ۱: گزارش اصالت فیزیکی اسناد (مهر، امضا و دست‌خط)
class DocumentAuthenticityReport(BaseModel):
    invoice_has_stamp: bool
    invoice_has_signature: bool
    invoice_auth_note: str
    grn_has_stamp: bool
    grn_has_signature: bool
    grn_has_handwriting: bool
    grn_auth_note: str


# لایه ۴: گزارش کشف تقلب، فاکتور تکراری و ریسک تامین‌کننده
class FraudAndRiskReport(BaseModel):
    is_duplicate_invoice: bool = False
    submission_count: int = 1
    previous_session_id: Optional[int] = None
    duplicate_warning_message: Optional[str] = None
    vendor_total_audits: int = 1
    vendor_discrepancy_count: int = 1
    vendor_risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"


# لایه ۵: آرتیکل‌های سند حسابداری دوبل صادرشده بر اساس الگوی شرکت
class JournalEntryLine(BaseModel):
    row_no: int
    account_code: str
    account_title: str
    description: str
    debit_rials: int   # بدهکار
    credit_rials: int  # بستانکار


class AccountingVoucherReport(BaseModel):
    template_title: str
    voucher_method: str
    learned_from_user_sample: bool
    voucher_date: str
    voucher_description: str
    lines: List[JournalEntryLine]
    total_debit_rials: int
    total_credit_rials: int
    is_balanced: bool = True


class ThreeWayMatchResponse(BaseModel):
    session_id: int
    po_number: str
    buyer_company: str
    vendor_name: str
    audit_mode: str = "THREE_WAY_ERP"
    documents_swapped_auto_fixed: bool = False
    document_mismatch_warning: Optional[str] = None
    overall_status: Literal["REJECTED_DISCREPANCY", "PENDING_HUMAN_REVIEW", "APPROVED_FOR_PAYMENT"]
    authenticity_report: DocumentAuthenticityReport
    fraud_and_risk: FraudAndRiskReport
    line_items: List[LineItemReconciliation]
    financial_summary: TaxAndTotalReconciliation
    accounting_voucher: Optional[AccountingVoucherReport] = None