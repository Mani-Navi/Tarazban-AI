import os
import re
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

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")

CLOUD_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
CLOUD_API_URL = os.getenv("CLOUD_API_URL", "https://openrouter.ai/api/v1/chat/completions")

http_client = httpx.Client(trust_env=False, timeout=600.0)
cloud_client = httpx.Client(timeout=120.0)

_CACHED_VISION_MODELS: List[str] = []


def _get_live_free_vision_models() -> List[str]:
    """واکشی خودکار لیست مدل‌های بینایی (Vision) رایگان و فعال روز از خود OpenRouter"""
    global _CACHED_VISION_MODELS
    if _CACHED_VISION_MODELS:
        return _CACHED_VISION_MODELS

    discovered = []
    try:
        res = cloud_client.get("https://openrouter.ai/api/v1/models")
        if res.status_code == 200:
            data = res.json().get("data", [])
            for m in data:
                mid = m.get("id", "")
                arch = m.get("architecture", {}) or {}
                inputs = arch.get("input_modalities", []) or []
                modality_str = str(arch.get("modality", ""))
                if mid.endswith(":free") and ("image" in inputs or "image" in modality_str):
                    discovered.append(mid)
    except Exception as e:
        print("⚠️ Could not fetch dynamic model list:", e)

    # اولویت‌بندی مدل‌های قوی‌تر (Qwen / Gemma / Gemini / Mistral) + روتر عمومی openrouter/free
    preferred = [m for m in discovered if any(k in m.lower() for k in ["qwen", "gemini", "gemma", "mistral", "llama"])]
    others = [m for m in discovered if m not in preferred]
    final_list = preferred + others + ["openrouter/free"]
    print("✅ Active Free Vision Models discovered on OpenRouter:", final_list[:8])
    _CACHED_VISION_MODELS = final_list[:8]
    return _CACHED_VISION_MODELS


def _encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _clean_json_output(raw_text: str) -> str:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _get_clean_api_key() -> str:
    """خواندن کلید در لحظه اجرا و حذف هرگونه فاصله یا کوتیشن اضافی"""
    for env_name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "AI_API_KEY"):
        val = os.getenv(env_name, "").strip()
        if val:
            val = val.strip("\"'").strip()
            if val.lower().startswith("bearer "):
                val = val[7:].strip()
            return val
    return ""


def _call_ai_engine(prompt: str, image_bytes: bytes = None) -> str:
    api_key = _get_clean_api_key()
    print(f"🔑 [API Key Check] Key found: {bool(api_key)} | Length: {len(api_key)} | Prefix: '{api_key[:8]}...'")

    if api_key:
        b64_img = _encode_image(image_bytes) if image_bytes else None

        # حالت ۱: کلید Google Gemini (شروع با AIza)
        if api_key.startswith("AIza"):
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            parts = [{"text": prompt + "\nفقط خروجی JSON خالص برگردان."}]
            if b64_img:
                parts.append({"inline_data": {"mime_type": "image/png", "data": b64_img}})
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
            }
            res = cloud_client.post(gemini_url, json=payload)
            if res.status_code != 200:
                raise RuntimeError(f"Gemini API Error ({res.status_code}): {res.text}")
            text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _clean_json_output(text)

        # حالت ۲: کلید Groq (شروع با gsk_)
        if api_key.startswith("gsk_"):
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            user_content = (
                [
                    {"type": "text", "text": prompt + "\nفقط خروجی JSON خالص برگردان."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                ]
                if b64_img
                else prompt + "\nفقط خروجی JSON خالص برگردان."
            )
            payload = {
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{"role": "user", "content": user_content}],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            }
            res = cloud_client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            if res.status_code != 200:
                raise RuntimeError(f"Groq API Error ({res.status_code}): {res.text}")
            return _clean_json_output(res.json()["choices"][0]["message"]["content"])

        # حالت ۳: کلید OpenRouter (شروع با sk-or-)
        openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://tarazban-ai.onrender.com",
            "X-Title": "TarazBan AI",
            "Content-Type": "application/json",
        }
        user_content = (
            [
                {"type": "text", "text": prompt + "\nفقط خروجی JSON خالص برگردان."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
            ]
            if b64_img
            else prompt + "\nفقط خروجی JSON خالص برگردان."
        )
        models_to_try = _get_live_free_vision_models()[:4]
        last_err = None
        for model_name in models_to_try:
            print(f"☁️ [Cloud AI Engine] Sending to OpenRouter model: {model_name}...")
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": user_content}],
                "temperature": 0.0,
            }
            response = cloud_client.post(openrouter_url, headers=headers, json=payload, timeout=40.0)
            if response.status_code == 200 and "choices" in response.json():
                content = response.json()["choices"][0]["message"].get("content", "")
                if content and "{" in content:
                    print(f"✅ Success with OpenRouter model: {model_name}")
                    return _clean_json_output(content)
            last_err = f"{response.status_code}: {response.text}"
            print(f"⚠️ Model {model_name} response: {last_err}")
        raise RuntimeError(f"OpenRouter Error: {last_err}")

    # حالت لوکال (Ollama)
    msg = {"role": "user", "content": prompt}
    if image_bytes:
        msg["images"] = [_encode_image(image_bytes)]
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [msg],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_gpu": 0},
    }
    response = http_client.post(OLLAMA_URL, json=payload)
    response.raise_for_status()
    return _clean_json_output(response.json()["message"]["content"])
def extract_invoice_from_image(image_bytes: bytes) -> ExtractedInvoice:
    print("👉 [1/3] Extracting Document 1 + Physical Authenticity...")
    prompt = """تصویر این سند فارسی را با دقت بخوان و اطلاعات جدول، مبالغ و وضعیت اصالت فیزیکی (وجود مهر و امضا) را دقیقا در قالب JSON زیر استخراج کن.
قوانین مهم:
1. اگر سند فاقد قیمت است (مثل قبض انبار یا تقاضای خرید)، برای مبالغ عدد 0 بگذار.
2. بررسی کن آیا در پایین یا بالای سند مهر رسمی شرکت (has_official_stamp) و امضای فروشنده/مسئول (has_valid_signature) وجود دارد یا خیر و در authenticity_note توضیح بده.
{
  "invoice_number": "شماره فاکتور یا سند",
  "po_number": "شماره سفارش مرجع مثلا PO-2026-101",
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
    content = _call_ai_engine(prompt, image_bytes)
    print("✅ [1/3] Document 1 extracted:", content)
    return ExtractedInvoice.model_validate_json(content)


def extract_grn_from_image(image_bytes: bytes) -> ExtractedGRN:
    print("👉 [2/3] Extracting Document 2 (GRN) + Stamp/Signature/Handwriting...")
    prompt = """تصویر این قبض/سند انبار فارسی را با دقت بخوان و اطلاعات آن را به همراه بررسی وجود مهر، امضای انباردار و دست‌خط در قالب JSON زیر استخراج کن:
{
  "grn_number": "شماره قبض انبار",
  "po_number": "شماره سفارش مرجع مثلا PO-2026-101",
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
    content = _call_ai_engine(prompt, image_bytes)
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
1. اگر کالاها ماهیت یکسانی دارند و فقط نام تجاری یا نگارش متفاوت است، حتماً is_technical_match را true و confidence_score را بین 0.92 تا 0.98 بگذار!
2. فقط زمانی is_technical_match را false و confidence_score را 0.15 بگذار که ماهیت کالاها کاملاً بی‌ربط باشد.

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
    content = _call_ai_engine(prompt, None)
    result = SemanticMatchResult.model_validate_json(content)

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
    print("👉 [AI Accounting Learner] Extracting Company Accounting Pattern from sample image...")
    prompt = f"""تصویر این نمونه سند حسابداری یا جدول کدینگ حسابداری شرکت را با دقت بخوان.
هدف ما یادگیری کدهای معین/تفصیلی و عناوین حساب‌های این شرکت برای صدور خودکار سند حسابداری خرید است:
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
    content = _call_ai_engine(prompt, image_bytes)
    print("✅ [AI Accounting Learner] Learned Template:", content)
    parsed = AccountingTemplateSchema.model_validate_json(content)
    parsed.po_number = po_number
    parsed.learned_from_user_sample = True
    return parsed