from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = "sqlite:///./tarazban.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String, unique=True, index=True, nullable=False)
    buyer_company = Column(String, nullable=False)
    vendor_name = Column(String, nullable=False)
    created_date = Column(String, nullable=False)
    status = Column(String, default="OPEN")

    items = relationship("POLineItem", back_populates="purchase_order", cascade="all, delete-orphan")


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    sku = Column(String, index=True, nullable=False)
    erp_item_name = Column(String, nullable=False)
    ordered_quantity = Column(Integer, nullable=False)
    agreed_unit_price = Column(Integer, nullable=False)

    purchase_order = relationship("PurchaseOrder", back_populates="items")


class VendorItemAlias(Base):
    __tablename__ = "vendor_item_aliases"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String, index=True, nullable=False)
    vendor_raw_name = Column(String, index=True, nullable=False)
    matched_sku = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CompanyAccountingTemplate(Base):
    __tablename__ = "company_accounting_templates"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String, unique=True, index=True, nullable=False)
    company_name = Column(String, nullable=False)
    template_title = Column(String, nullable=False)
    voucher_method = Column(String, default="GROSS_WITH_DEDUCTION")

    inventory_acc_code = Column(String, nullable=False)
    inventory_acc_title = Column(String, nullable=False)

    vat_acc_code = Column(String, nullable=False)
    vat_acc_title = Column(String, nullable=False)

    payable_acc_code = Column(String, nullable=False)
    payable_acc_title = Column(String, nullable=False)

    discrepancy_acc_code = Column(String, nullable=False)
    discrepancy_acc_title = Column(String, nullable=False)

    learned_from_user_sample = Column(Boolean, default=False)


class ReconciliationSession(Base):
    __tablename__ = "reconciliation_sessions"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String, index=True, nullable=False)
    vendor_name = Column(String, default="")
    invoice_number = Column(String, index=True, default="")
    invoice_hash = Column(String, index=True, default="")
    has_discrepancy = Column(Boolean, default=False)
    overall_status = Column(String, nullable=False)
    report_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db_and_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # ۱. پرونده شرکت پاکشو (تولیدی - گروه صنعتی گلرنگ)
        if not db.query(PurchaseOrder).filter_by(po_number="PO-2026-101").first():
            pakshoo_po = PurchaseOrder(
                po_number="PO-2026-101",
                buyer_company="شرکت گروه صنعتی پاکشو",
                vendor_name="شرکت صنایع بسته‌بندی و پلاستیک پارس",
                created_date="1405/08/10",
                status="OPEN"
            )
            db.add(pakshoo_po)
            db.flush()
            db.add_all([
                POLineItem(
                    po_id=pakshoo_po.id,
                    sku="PK-104",
                    erp_item_name="بطری پت ۲۵۰ سی‌سی شفاف (پریفرم ۲۸)",
                    ordered_quantity=1000,
                    agreed_unit_price=50000
                ),
                POLineItem(
                    po_id=pakshoo_po.id,
                    sku="PK-208",
                    erp_item_name="درب بطری دهانه ۲۸ استاندارد",
                    ordered_quantity=2000,
                    agreed_unit_price=12000
                ),
                POLineItem(
                    po_id=pakshoo_po.id,
                    sku="PK-315",
                    erp_item_name="کارتن ۵ لایه چاپی بسته‌بندی",
                    ordered_quantity=500,
                    agreed_unit_price=80000
                ),
            ])

        if not db.query(CompanyAccountingTemplate).filter_by(po_number="PO-2026-101").first():
            db.add(
                CompanyAccountingTemplate(
                    po_number="PO-2026-101",
                    company_name="شرکت گروه صنعتی پاکشو",
                    template_title="کدینگ استاندارد حسابداری صنعتی پاکشو (۶ رقمی)",
                    voucher_method="GROSS_WITH_DEDUCTION",
                    inventory_acc_code="110401",
                    inventory_acc_title="موجودی مواد اولیه و ملزومات بسته‌بندی - انبار مرکزی",
                    vat_acc_code="110802",
                    vat_acc_title="پیش‌پرداخت مالیات و عوارض بر ارزش افزوده خرید معتبر",
                    payable_acc_code="210101",
                    payable_acc_title="حساب‌های پرداختنی تجاری - تامین‌کنندگان داخلی",
                    discrepancy_acc_code="210905",
                    discrepancy_acc_title="حساب کنترلی کسورات، ضایعات حمل و برگشت از خرید",
                    learned_from_user_sample=False,
                )
            )

        # ۲. پرونده شرکت فروشگاه‌های زنجیره‌ای افق کوروش (فروشگاهی - گروه صنعتی گلرنگ)
        if not db.query(PurchaseOrder).filter_by(po_number="PO-2026-304").first():
            ok_po = PurchaseOrder(
                po_number="PO-2026-304",
                buyer_company="شرکت فروشگاه‌های زنجیره‌ای افق کوروش",
                vendor_name="شرکت پخش و صنایع غذایی بهروزان",
                created_date="1405/08/12",
                status="OPEN"
            )
            db.add(ok_po)
            db.flush()
            db.add_all([
                POLineItem(
                    po_id=ok_po.id,
                    sku="OK-801",
                    erp_item_name="رب گوجه‌فرنگی قوطی ۸۰۰ گرمی کلیددار",
                    ordered_quantity=1000,
                    agreed_unit_price=60000
                ),
                POLineItem(
                    po_id=ok_po.id,
                    sku="OK-802",
                    erp_item_name="روغن مایع آفتابگردان پت ۹۰۰ گرمی",
                    ordered_quantity=500,
                    agreed_unit_price=80000
                ),
            ])

        db.commit()
    finally:
        db.close()