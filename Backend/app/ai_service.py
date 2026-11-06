import base64
import httpx
from typing import List, Dict
from .schemas import (
    ExtractedInvoice,
    ExtractedGRN,
    SemanticMatchResult,
    AccountingTemplateSchema,
)
from .database import POLineItem

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3.5:4b"

http_client = httpx.Client(trust_env=False, timeout=600.0)


def _encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _call_ollama(payload: dict) -> str:
    response = http_client.post(OLLAMA_URL, json=payload)
    if response.status_code != 200:
        print(f"❌ OLLAMA ERROR ({response.status_code}): {response.text}")
        response.raise_for_status()
    return response.json()["message"]["content"]


def extract_invoice_from_image(image_bytes: bytes) -> ExtractedInvoice:
    print("👉 [1/3] Extracting Document 1 + Physical Authenticity (Stamp/Signature)...")
    prompt = """تصویر این سند فارسی را با دقت بخوان و اطلاعات جدول، مبالغ و وضعیت اصالت فیزیکی (وجود مهر و امضا) را دقیقا در قالب JSON زیر استخراج کن.
قوانین مهم:
1. اگر سند فاقد قیمت است (مثل قبض انبار یا تقاضای خرید)، برای مبالغ عدد 0 بگذار.
2. بررسی کن آیا در پایین یا بالای سند مهر رسمی شرکت (has_official_stamp) و امضای فروشنده/مسئول (has_valid_signature) وجود دارد یا خیر و در authenticity_note توضیح بده.
{
  "invoice_number": "شماره فاکتور یا سند",
  "po_number": "شماره سفارش مرجع مثلا PO-2026-101 یا 1365",
  "vendor_name": "نام شرکت فروشنده یا واحد متقاضی",
  "items": [
    {
      "row_number": 1,
      "raw_item_name": "شرح کامل کالا دقیقا طبق جدول",
      "quantity": 1000,
      "unit_price": 50000,
      "total_price": 50000000
    }
  ],
  "subtotal_amount": 117000000,
  "vat_amount": 12500000,
  "grand_total_amount": 129500000,
  "has_official_stamp": true,
  "has_valid_signature": true,
  "authenticity_note": "مهر رسمی واحد فروش و امضای مجاز در ذیل صورتحساب احراز گردید."
}"""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_encode_image(image_bytes)]
            }
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_gpu": 0}
    }
    content = _call_ollama(payload)
    print("✅ [1/3] Document 1 extracted:", content)
    return ExtractedInvoice.model_validate_json(content)


def extract_grn_from_image(image_bytes: bytes) -> ExtractedGRN:
    print("👉 [2/3] Extracting Document 2 (GRN) + Stamp/Signature/Handwriting...")
    prompt = """تصویر این قبض/سند انبار فارسی را با دقت بخوان و اطلاعات آن را به همراه بررسی وجود مهر، امضای انباردار و دست‌خط در قالب JSON زیر استخراج کن:
{
  "grn_number": "شماره قبض انبار",
  "po_number": "شماره سفارش مرجع مثلا PO-2026-101 یا 1210",
  "items": [
    {
      "row_number": 1,
      "raw_item_name": "شرح کالا در رسید انبار",
      "received_quantity": 950,
      "notes": "توضیحات انباردار در خصوص کسری، شکستگی یا شماره درخواست"
    }
  ],
  "has_official_stamp": true,
  "has_valid_signature": true,
  "has_handwritten_notes": true,
  "authenticity_note": "امضای انباردار، رئیس انبار و توضیحات دست‌نویس/مهر انبار در سند مشاهده شد."
}"""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_encode_image(image_bytes)]
            }
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_gpu": 0}
    }
    content = _call_ollama(payload)
    print("✅ [2/3] Document 2 extracted:", content)
    return ExtractedGRN.model_validate_json(content)


def match_items_semantically(
    invoice: ExtractedInvoice,
    grn: ExtractedGRN,
    po_items: List[POLineItem],
    learned_aliases: Dict[str, str] = None,
) -> SemanticMatchResult:
    print("👉 [3/3] Running Semantic Matching + Checking Self-Learning Alias Memory...")
    learned_aliases = learned_aliases or {}

    erp_catalog_text = "\n".join(
        [f"- کد SKU: {item.sku} | نام کالا در ERP: {item.erp_item_name}" for item in po_items]
    )
    invoice_items_text = "\n".join(
        [f"- ردیف سند اول {item.row_number}: {item.raw_item_name}" for item in invoice.items]
    )
    grn_items_text = "\n".join(
        [f"- ردیف سند انبار {item.row_number}: {item.raw_item_name} | توضیحات: {item.notes}" for item in grn.items]
    )

    prompt = f"""تو سیستم هوشمند تطبیق معنایی و فنی کالا در زنجیره تامین هستی.
هر ردیف از «سند اول» را با «کالاهای مرجع در ERP» و «ردیف‌های سند انبار» مقایسه کن.

قوانین بسیار مهم:
1. اگر کالاها ماهیت یکسانی دارند و فقط نام تجاری یا نگارش متفاوت است (مثلا «ظرف پلاستیکی ۲۵۰ میلی‌لیتر» همان «بطری پت ۲۵۰ سی‌سی» است، «سرپوش لولایی» همان «درب بطری» است، و «جعبه مقوایی ۵ لایه» همان «کارتن ۵ لایه» است)، حتماً is_technical_match را true و confidence_score را بین 0.92 تا 0.98 بگذار!
2. فقط زمانی is_technical_match را false و confidence_score را 0.15 بگذار که ماهیت کالاها کاملاً بی‌ربط باشد (مثلا «بطری پلاستیکی» در برابر «الکتروموتور صنعتی»، یا «الکتروموتور اگزاست فن ۰.۲۵ کیلووات درخواست ۱۳۶۵» در برابر «پمپ دنده‌ای کراخت ۲.۲ کیلووات درخواست ۱۲۱۰»).

کالاهای مرجع در ERP:
{erp_catalog_text}

اقلام سند اول:
{invoice_items_text}

اقلام سند انبار:
{grn_items_text}

خروجی را دقیقا در قالب JSON زیر برگردان:
{{
  "matches": [
    {{
      "invoice_row_number": 1,
      "invoice_raw_name": "نام کالا در سند اول",
      "matched_erp_sku": "کد SKU از لیست ERP بالا",
      "grn_row_number": 1,
      "is_technical_match": true,
      "confidence_score": 0.95,
      "match_reasoning": "توضیح رسمی فارسی درباره تطابق یا عدم تطابق کالا",
      "learned_from_memory": false
    }}
  ]
}}"""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_gpu": 0}
    }
    content = _call_ollama(payload)
    result = SemanticMatchResult.model_validate_json(content)

    # لایه ۳: اعمال حافظه خودآموز کدینگ کالا (Self-Learning Vendor Alias Memory)
    inv_map = {i.row_number: i.raw_item_name.strip() for i in invoice.items}
    for m in result.matches:
        raw_name = inv_map.get(m.invoice_row_number, m.invoice_raw_name.strip())
        if raw_name in learned_aliases:
            m.matched_erp_sku = learned_aliases[raw_name]
            m.learned_from_memory = True
            m.confidence_score = 1.0
            m.match_reasoning = (
                f"[بازیابی آنی از حافظه خودآموز کدینگ سازمان در ۰.۰۰۱ ثانیه] — "
                f"عنوان «{raw_name}» پیش‌تر توسط کارشناس ممیزی تایید و به کد «{m.matched_erp_sku}» متصل شده است. "
                + m.match_reasoning
            )

    print("✅ [3/3] Semantic Matching completed!")
    return result


def learn_accounting_template_from_image(
    image_bytes: bytes,
    po_number: str,
    company_name: str,
) -> AccountingTemplateSchema:
    """لایه ۵: یادگیری الگوی سند حسابداری و کدینگ حساب‌های یک شرکت از روی تصویر نمونه سند حسابداری آن شرکت"""
    print("👉 [AI Accounting Learner] Extracting Company Accounting Pattern from sample image...")
    prompt = f"""تصویر این نمونه سند حسابداری یا جدول کدینگ حسابداری شرکت را با دقت بخوان.
هدف ما یادگیری کدهای معین/تفصیلی و عناوین حساب‌های این شرکت برای صدور خودکار سند حسابداری خرید است.
اگر برخی از این ۴ حساب در تصویر نمونه نبود، کد و عنوان استاندارد هم‌خانواده با ساختار کدینگ همین تصویر برایش تولید کن:
1. حساب موجودی کالا / خرید مواد (inventory_acc_code و inventory_acc_title)
2. حساب مالیات بر ارزش افزوده خرید (vat_acc_code و vat_acc_title)
3. حساب پرداختنی تجاری / بستانکاران (payable_acc_code و payable_acc_title)
4. حساب کسورات، برگشت از خرید یا کنترل مغایرت تامین‌کنندگان (discrepancy_acc_code و discrepancy_acc_title)

خروجی را دقیقا در قالب JSON زیر برگردان:
{{
  "po_number": "{po_number}",
  "company_name": "{company_name}",
  "template_title": "الگوی استخراج‌شده خودکار از نمونه سند حسابداری شرکت",
  "voucher_method": "GROSS_WITH_DEDUCTION",
  "inventory_acc_code": "کد حساب موجودی/خرید در تصویر",
  "inventory_acc_title": "عنوان حساب موجودی/خرید در تصویر",
  "vat_acc_code": "کد حساب ارزش افزوده",
  "vat_acc_title": "عنوان حساب ارزش افزوده",
  "payable_acc_code": "کد حساب پرداختنی/تامین‌کننده",
  "payable_acc_title": "عنوان حساب پرداختنی/تامین‌کننده",
  "discrepancy_acc_code": "کد حساب کسورات/برگشت از خرید",
  "discrepancy_acc_title": "عنوان حساب کسورات/برگشت از خرید",
  "learned_from_user_sample": true
}}"""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_encode_image(image_bytes)]
            }
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_gpu": 0}
    }
    content = _call_ollama(payload)
    print("✅ [AI Accounting Learner] Learned Template:", content)
    parsed = AccountingTemplateSchema.model_validate_json(content)
    parsed.po_number = po_number
    parsed.learned_from_user_sample = True
    return parsed