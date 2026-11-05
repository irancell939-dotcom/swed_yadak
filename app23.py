def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    # ============================================================
    # ۱. ساخت تمام جداول (هم آنلاین و هم آفلاین)
    # ============================================================
    if IS_OFFLINE:
        # دستورات ساخت جدول برای SQLite
        tables = [
            "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, sku TEXT, buy_price_tomans REAL, buy_price_usd REAL, sell_price REAL, warehouse TEXT, stock INTEGER, description TEXT, sales_count INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT, customer_phone TEXT, date_jalali TEXT, date_greg TEXT, total_amount REAL, total_profit REAL, discount_amount REAL, discount_percent REAL, status TEXT)",
            "CREATE TABLE IF NOT EXISTS invoice_items (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, product_id INTEGER, product_name TEXT, sku TEXT, quantity INTEGER, unit_price REAL, profit_per_unit REAL)",
            "CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, amount REAL, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_lists (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_name TEXT, created_date_jalali TEXT, created_date_greg TEXT, status TEXT, notes TEXT, estimated_total REAL, actual_total REAL)",
            "CREATE TABLE IF NOT EXISTS purchase_list_items (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_list_id INTEGER, product_id INTEGER, product_name TEXT, sku TEXT, current_stock INTEGER, quantity_to_buy INTEGER, estimated_unit_price REAL, actual_unit_price REAL, estimated_total_price REAL, actual_total_price REAL, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_list_id INTEGER, supplier_name TEXT, invoice_date_jalali TEXT, invoice_date_greg TEXT, total_amount REAL, paid_amount REAL, remaining_amount REAL, payment_method TEXT, status TEXT, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT, username TEXT UNIQUE, password_hash TEXT, role TEXT, permissions TEXT, is_active INTEGER DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS permissions_master (id INTEGER PRIMARY KEY AUTOINCREMENT, permission_key TEXT UNIQUE, permission_name TEXT, category TEXT, description TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_payments (id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_invoice_id INTEGER, amount REAL, payment_method TEXT, sheba_number TEXT, account_owner TEXT, bank_name TEXT, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT, receipt_number TEXT, created_by INTEGER, created_date_greg TEXT, created_date_jalali TEXT)",
            "CREATE TABLE IF NOT EXISTS shop_expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, expense_date_jalali TEXT, expense_date_greg TEXT, category TEXT, description TEXT, amount REAL, payment_method TEXT, receipt_number TEXT, notes TEXT, created_by INTEGER, created_date_greg TEXT, created_date_jalali TEXT)"
        ]
    else:
        # دستورات ساخت جدول برای PostgreSQL
        tables = [
            "CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, name TEXT, sku TEXT, buy_price_tomans REAL, buy_price_usd REAL, sell_price REAL, warehouse TEXT, stock INTEGER, description TEXT, sales_count INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS invoices (id SERIAL PRIMARY KEY, customer_name TEXT, customer_phone TEXT, date_jalali TEXT, date_greg TEXT, total_amount REAL, total_profit REAL, discount_amount REAL, discount_percent REAL, status TEXT)",
            "CREATE TABLE IF NOT EXISTS invoice_items (id SERIAL PRIMARY KEY, invoice_id INTEGER REFERENCES invoices(id) ON DELETE CASCADE, product_id INTEGER, product_name TEXT, sku TEXT, quantity INTEGER, unit_price REAL, profit_per_unit REAL)",
            "CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, invoice_id INTEGER REFERENCES invoices(id) ON DELETE CASCADE, amount REAL, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_lists (id SERIAL PRIMARY KEY, supplier_name TEXT, created_date_jalali TEXT, created_date_greg TEXT, status TEXT, notes TEXT, estimated_total REAL, actual_total REAL)",
            "CREATE TABLE IF NOT EXISTS purchase_list_items (id SERIAL PRIMARY KEY, purchase_list_id INTEGER REFERENCES purchase_lists(id) ON DELETE CASCADE, product_id INTEGER, product_name TEXT, sku TEXT, current_stock INTEGER, quantity_to_buy INTEGER, estimated_unit_price REAL, actual_unit_price REAL, estimated_total_price REAL, actual_total_price REAL, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_invoices (id SERIAL PRIMARY KEY, purchase_list_id INTEGER REFERENCES purchase_lists(id), supplier_name TEXT, invoice_date_jalali TEXT, invoice_date_greg TEXT, total_amount REAL, paid_amount REAL, remaining_amount REAL, payment_method TEXT, status TEXT, notes TEXT)",
            "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, full_name TEXT, username TEXT UNIQUE, password_hash TEXT, role TEXT, permissions TEXT, is_active BOOLEAN DEFAULT TRUE)",
            "CREATE TABLE IF NOT EXISTS permissions_master (id SERIAL PRIMARY KEY, permission_key TEXT UNIQUE, permission_name TEXT, category TEXT, description TEXT)",
            "CREATE TABLE IF NOT EXISTS purchase_payments (id SERIAL PRIMARY KEY, purchase_invoice_id INTEGER REFERENCES purchase_invoices(id) ON DELETE CASCADE, amount REAL, payment_method TEXT, sheba_number TEXT, account_owner TEXT, bank_name TEXT, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT, receipt_number TEXT, created_by INTEGER REFERENCES users(id), created_date_greg TEXT, created_date_jalali TEXT)",
            "CREATE TABLE IF NOT EXISTS shop_expenses (id SERIAL PRIMARY KEY, expense_date_jalali TEXT, expense_date_greg TEXT, category TEXT, description TEXT, amount REAL, payment_method TEXT, receipt_number TEXT, notes TEXT, created_by INTEGER REFERENCES users(id), created_date_greg TEXT, created_date_jalali TEXT)"
        ]

    for query in tables:
        cur.execute(query)
    conn.commit()

    # ============================================================
    # ۲. ایجاد کاربر پیش‌فرض (admin) اگر وجود نداشته باشد
    # ============================================================
    if IS_OFFLINE:
        cur.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
        if cur.fetchone()[0] == 0:
            ph = generate_password_hash('123456')
            cur.execute("INSERT INTO users (full_name, username, password_hash, role, permissions) VALUES (?, ?, ?, ?, ?)", 
                        ('مدیر سیستم', 'admin', ph, 'admin', 'all'))
            conn.commit()
    else:
        cur.execute("SELECT COUNT(*) AS count FROM users WHERE username = 'admin'")
        if cur.fetchone()['count'] == 0:
            ph = generate_password_hash('123456')
            cur.execute("INSERT INTO users (full_name, username, password_hash, role, permissions) VALUES (%s, %s, %s, %s, %s)", 
                        ('مدیر سیستم', 'admin', ph, 'admin', 'all'))
            conn.commit()

    # ============================================================
    # ۳. درج سطوح دسترسی پیش‌فرض (Permissions)
    # ============================================================
    default_permissions = [
        ('products.view', 'مشاهده محصولات', 'مدیریت محصولات', 'دسترسی به مشاهده لیست محصولات'),
        ('products.add', 'افزودن محصول', 'مدیریت محصولات', 'دسترسی به افزودن محصول جدید'),
        ('products.edit', 'ویرایش محصول', 'مدیریت محصولات', 'دسترسی به ویرایش محصولات'),
        ('products.delete', 'حذف محصول', 'مدیریت محصولات', 'دسترسی به حذف محصولات'),
        ('products.import', 'ورود اکسل محصولات', 'مدیریت محصولات', 'دسترسی به ورود محصولات از اکسل'),
        ('invoices.create', 'صدور فاکتور', 'مدیریت فاکتورها', 'دسترسی به صدور فاکتور جدید'),
        ('invoices.view', 'مشاهده فاکتورها', 'مدیریت فاکتورها', 'دسترسی به مشاهده لیست فاکتورها'),
        ('invoices.edit', 'ویرایش فاکتور', 'مدیریت فاکتورها', 'دسترسی به ویرایش فاکتورها'),
        ('invoices.print', 'چاپ فاکتور', 'مدیریت فاکتورها', 'دسترسی به چاپ فاکتور'),
        ('payments.add', 'ثبت پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به ثبت پرداخت جدید'),
        ('payments.edit', 'ویرایش پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به ویرایش پرداخت‌ها'),
        ('payments.delete', 'حذف پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به حذف پرداخت‌ها'),
        ('reports.view', 'مشاهده گزارشات', 'گزارشات', 'دسترسی به مشاهده گزارشات فروش'),
        ('reports.stock', 'گزارش موجودی', 'گزارشات', 'دسترسی به گزارش موجودی انبار'),
        ('reports.debtors', 'گزارش بدهکاران', 'گزارشات', 'دسترسی به گزارش بدهکاران'),
        ('reports.zakat', 'محاسبه زکات', 'گزارشات', 'دسترسی به محاسبه زکات'),
        ('reports.date_range', 'گزارش بازه زمانی', 'گزارشات', 'دسترسی به گزارش بین دو تاریخ'),
        ('reports.expenses', 'مدیریت هزینه‌ها', 'گزارشات', 'دسترسی به مدیریت هزینه‌های مغازه'),
        ('purchase.view', 'مشاهده لیست خرید', 'مدیریت خرید', 'دسترسی به مشاهده لیست‌های خرید'),
        ('purchase.create', 'ایجاد لیست خرید', 'مدیریت خرید', 'دسترسی به ایجاد لیست خرید جدید'),
        ('purchase.edit', 'ویرایش لیست خرید', 'مدیریت خرید', 'دسترسی به ویرایش لیست خرید'),
        ('purchase.suppliers', 'مدیریت تامین‌کنندگان', 'مدیریت خرید', 'دسترسی به مدیریت تامین‌کنندگان'),
        ('backup.manage', 'مدیریت پشتیبان', 'سیستم', 'دسترسی به پشتیبان‌گیری و بازیابی'),
        ('users.manage', 'مدیریت کاربران', 'سیستم', 'دسترسی به مدیریت کاربران سیستم')
    ]
    
    for perm in default_permissions:
        if IS_OFFLINE:
            cur.execute("INSERT OR IGNORE INTO permissions_master (permission_key, permission_name, category, description) VALUES (?, ?, ?, ?)", perm)
        else:
            cur.execute("INSERT INTO permissions_master (permission_key, permission_name, category, description) VALUES (%s, %s, %s, %s) ON CONFLICT (permission_key) DO NOTHING", perm)
    
    conn.commit()
    cur.close()
    conn.close()

# ============================================================
# اجرای خودکار دیتابیس هنگام بالا آمدن برنامه (مخصوص Render)
# ============================================================
try:
    init_db()
except Exception as e:
    print(f"خطا در مقداردهی اولیه دیتابیس: {e}")
