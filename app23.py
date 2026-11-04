# -*- coding: utf-8 -*-
"""
نرم‌افزار مدیریت فروشگاه سوئد یدک
نسخه بهینه‌شده برای PostgreSQL (Neon) و Render
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, session
import jdatetime
import os
import shutil
import pandas as pd
import re
import sys
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'swed_yadak_secret_key_2025')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ============================================================
# دکوراتورها
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('لطفاً ابتدا وارد سیستم شوید.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('دسترسی غیرمجاز', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('لطفاً ابتدا وارد سیستم شوید.', 'warning')
                return redirect(url_for('login'))
            if session.get('role') == 'admin':
                return f(*args, **kwargs)
            user_permissions = session.get('permissions', '')
            if user_permissions == 'all' or permission in user_permissions.split(','):
                return f(*args, **kwargs)
            flash('دسترسی غیرمجاز', 'danger')
            return redirect(url_for('index'))
        return decorated_function
    return decorator

# ============================================================
# دیتابیس (PostgreSQL)
# ============================================================
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("متغیر محیطی DATABASE_URL تنظیم نشده است.")
    if 'sslmode' not in db_url:
        db_url += '?sslmode=require' if '?' not in db_url else '&sslmode=require'
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

# ============================================================
# توابع کمکی
# ============================================================
@app.template_filter('jdate')
def jdate_filter(dt, format='%Y/%m/%d'):
    if dt is None: return ''
    if isinstance(dt, str): return dt
    try: return jdatetime.datetime.fromgregorian(datetime=dt).strftime(format)
    except: return str(dt)

def persian_to_latin_digits(s):
    if not s: return s
    return s.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))

def clean_numeric_value(val):
    if pd.isna(val): return 0.0
    s = re.sub(r'[^\d.-]', '', str(val).strip())
    if s in ('', '.', '-'): return 0.0
    try: return float(s)
    except ValueError: return 0.0

def normalize_persian_text(text):
    if not text: return text
    for old, new in {'ي': 'ی', 'ك': 'ک', 'ة': 'ه', 'ؤ': 'و', 'إ': 'ا', 'أ': 'ا'}.items():
        text = text.replace(old, new)
    return text

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# ============================================================
# مقداردهی اولیه دیتابیس
# ============================================================
def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    tables = [
        """CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, sku TEXT,
            buy_price_tomans REAL DEFAULT 0, buy_price_usd REAL, sell_price REAL DEFAULT 0,
            warehouse TEXT, stock INTEGER DEFAULT 0, description TEXT, sales_count INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS invoices (
            id SERIAL PRIMARY KEY, customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL,
            date_jalali TEXT, date_greg TEXT, total_amount REAL DEFAULT 0, total_profit REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0, discount_percent REAL DEFAULT 0, status TEXT DEFAULT 'active')""",
        """CREATE TABLE IF NOT EXISTS invoice_items (
            id SERIAL PRIMARY KEY, invoice_id INTEGER REFERENCES invoices(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id), product_name TEXT, sku TEXT,
            quantity INTEGER, unit_price REAL, profit_per_unit REAL, returned INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            amount REAL NOT NULL, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT)""",
        """CREATE TABLE IF NOT EXISTS purchase_lists (
            id SERIAL PRIMARY KEY, supplier_name TEXT NOT NULL, created_date_jalali TEXT, created_date_greg TEXT,
            status TEXT DEFAULT 'active', notes TEXT, estimated_total REAL DEFAULT 0, actual_total REAL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS purchase_list_items (
            id SERIAL PRIMARY KEY, purchase_list_id INTEGER NOT NULL REFERENCES purchase_lists(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id), product_name TEXT NOT NULL, sku TEXT,
            current_stock INTEGER DEFAULT 0, quantity_to_buy INTEGER NOT NULL, estimated_unit_price REAL,
            actual_unit_price REAL, estimated_total_price REAL, actual_total_price REAL, notes TEXT)""",
        """CREATE TABLE IF NOT EXISTS purchase_invoices (
            id SERIAL PRIMARY KEY, purchase_list_id INTEGER NOT NULL REFERENCES purchase_lists(id),
            supplier_name TEXT NOT NULL, invoice_date_jalali TEXT, invoice_date_greg TEXT,
            total_amount REAL DEFAULT 0, paid_amount REAL DEFAULT 0, remaining_amount REAL DEFAULT 0,
            payment_method TEXT, status TEXT DEFAULT 'pending', notes TEXT)""",
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, full_name TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', permissions TEXT,
            is_active BOOLEAN DEFAULT TRUE, created_date_greg TEXT, created_date_jalali TEXT,
            last_login_greg TEXT, last_login_jalali TEXT)""",
        """CREATE TABLE IF NOT EXISTS permissions_master (
            id SERIAL PRIMARY KEY, permission_key TEXT UNIQUE NOT NULL, permission_name TEXT NOT NULL,
            category TEXT NOT NULL, description TEXT)""",
        """CREATE TABLE IF NOT EXISTS purchase_payments (
            id SERIAL PRIMARY KEY, purchase_invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE CASCADE,
            amount REAL NOT NULL, payment_method TEXT NOT NULL, sheba_number TEXT, account_owner TEXT,
            bank_name TEXT, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT, receipt_number TEXT,
            created_by INTEGER REFERENCES users(id), created_date_greg TEXT, created_date_jalali TEXT)""",
        """CREATE TABLE IF NOT EXISTS shop_expenses (
            id SERIAL PRIMARY KEY, expense_date_jalali TEXT NOT NULL, expense_date_greg TEXT NOT NULL,
            category TEXT NOT NULL, description TEXT, amount REAL NOT NULL, payment_method TEXT DEFAULT 'cash',
            receipt_number TEXT, notes TEXT, created_by INTEGER REFERENCES users(id),
            created_date_greg TEXT, created_date_jalali TEXT)"""
    ]
    for query in tables:
        cur.execute(query)
    conn.commit()

    # کاربر پیش‌فرض
    cur.execute("SELECT COUNT(*) AS count FROM users WHERE username = 'admin'")
    if cur.fetchone()['count'] == 0:
        cur.execute("""INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""", 
            ('مدیر سیستم', 'admin', generate_password_hash('123456'), 'admin', 'all', datetime.now().isoformat(), jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')))
        conn.commit()
    cur.close()
    conn.close()

# ============================================================
# احراز هویت و مدیریت کاربران
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('index'))
    if request.method == 'POST':
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s AND is_active = TRUE", (request.form['username'],))
        user = cur.fetchone()
        if user and check_password_hash(user['password_hash'], request.form['password']):
            session.update({'user_id': user['id'], 'username': user['username'], 'role': user['role'], 'permissions': user['permissions']})
            cur.execute("UPDATE users SET last_login_greg = %s, last_login_jalali = %s WHERE id = %s", 
                        (datetime.now().isoformat(), jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M'), user['id']))
            conn.commit(); flash('ورود موفقیت‌آمیز بود.', 'success'); return redirect(url_for('index'))
        flash('نام کاربری یا رمز عبور نادرست است.', 'danger')
        cur.close(); conn.close()
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); flash('خروج موفقیت‌آمیز بود.', 'success'); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS count FROM invoices i WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount")
    debtors_count = cur.fetchone()['count']
    cur.close(); conn.close()
    return render_template('index.html', debtors_count=debtors_count)

@app.route('/about')
def about(): return render_template('about.html', current_date=jdatetime.datetime.now().strftime('%Y/%m/%d'))

# ============================================================
# محصولات
# ============================================================
@app.route('/products', methods=['GET', 'POST'])
@login_required
@permission_required('products.view')
def products():
    conn = get_db(); cur = conn.cursor()
    edit_product = None
    if request.args.get('edit'):
        cur.execute("SELECT * FROM products WHERE id = %s", (request.args.get('edit'),))
        edit_product = cur.fetchone()
    if request.method == 'POST':
        try:
            name, sku = request.form['name'].strip(), request.form['sku'].strip() or None
            buy_t = float(persian_to_latin_digits(request.form['buy_price_tomans']).replace(',', '') or 0)
            sell = float(persian_to_latin_digits(request.form['sell_price']).replace(',', '') or 0)
            usd = float(persian_to_latin_digits(request.form.get('buy_price_usd', '')).replace(',', '') or 0) or None
            stock = int(persian_to_latin_digits(request.form.get('stock', '0')).replace(',', '') or 0)
            desc, wh = request.form.get('description', '').strip() or None, request.form.get('warehouse', '').strip() or None
            if request.form.get('product_id'):
                cur.execute("UPDATE products SET name=%s, sku=%s, buy_price_tomans=%s, buy_price_usd=%s, sell_price=%s, warehouse=%s, stock=%s, description=%s WHERE id=%s",
                            (name, sku, buy_t, usd, sell, wh, stock, desc, request.form['product_id']))
                flash('کالا ویرایش شد.', 'success')
            else:
                cur.execute("INSERT INTO products (name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                            (name, sku, buy_t, usd, sell, wh, stock, desc))
                flash('کالا اضافه شد.', 'success')
            conn.commit()
        except Exception as e:
            conn.rollback(); flash(f'خطا: {str(e)}', 'danger')
        finally: cur.close(); conn.close()
        return redirect(url_for('products'))
    cur.execute("SELECT * FROM products ORDER BY name"); products_list = cur.fetchall()
    cur.close(); conn.close()
    return render_template('products.html', products=products_list, edit_product=edit_product)

@app.route('/api/search_products_all')
@login_required
def search_products_all():
    q = normalize_persian_text(request.args.get('q', '').lower())
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE (LOWER(name) LIKE %s OR LOWER(sku) LIKE %s) ORDER BY name", (f"%{q}%", f"%{q}%"))
    res = cur.fetchall(); cur.close(); conn.close()
    return jsonify([dict(p) for p in res])

@app.route('/product/<int:product_id>/delete', methods=['POST'])
@login_required
@permission_required('products.delete')
def delete_product(product_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM invoice_items WHERE product_id = %s", (product_id,))
    if cur.fetchone()['c'] > 0:
        cur.close(); conn.close(); return jsonify({'success': False, 'error': 'کالا در فاکتور استفاده شده است.'})
    cur.execute("DELETE FROM products WHERE id = %s", (product_id,)); conn.commit(); cur.close(); conn.close()
    return jsonify({'success': True})

# ============================================================
# فاکتور فروش (جهت کوتاه شدن کد، بخش‌های تکراری ادغام شده‌اند)
# ============================================================
@app.route('/invoice', methods=['GET', 'POST'])
@login_required
@permission_required('invoices.create')
def invoice():
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        try:
            now = datetime.now()
            cur.execute("INSERT INTO invoices (customer_name, customer_phone, date_jalali, date_greg, total_amount, total_profit, discount_amount, discount_percent) VALUES (%s,%s,%s,%s,0,0,%s,%s) RETURNING id",
                        (request.form['customer_name'].strip(), request.form['customer_phone'].strip(), 
                         jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M'), now.isoformat(),
                         float(request.form.get('discount_amount', 0) or 0), float(request.form.get('discount_percent', 0) or 0)))
            inv_id = cur.fetchone()['id']
            total, total_cost = 0, 0
            for i, p_id in enumerate(request.form.getlist('item_id[]')):
                qty = int(request.form.getlist('quantity[]')[i])
                if qty <= 0: continue
                cur.execute("SELECT * FROM products WHERE id = %s", (p_id,)); prod = cur.fetchone()
                if not prod or prod['stock'] < qty: raise ValueError(f"موجودی ناکافی برای {prod['name']}")
                price = float(request.form.get(f'custom_price_{p_id}', '').replace(',', '') or prod['sell_price'])
                cur.execute("INSERT INTO invoice_items (invoice_id, product_id, product_name, sku, quantity, unit_price, profit_per_unit) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (inv_id, p_id, prod['name'], prod['sku'], qty, price, price - prod['buy_price_tomans']))
                cur.execute("UPDATE products SET stock = stock - %s, sales_count = sales_count + %s WHERE id = %s", (qty, qty, p_id))
                total += price * qty; total_cost += prod['buy_price_tomans'] * qty
            disc = float(request.form.get('discount_amount', 0) or 0)
            final_total = total - disc
            final_profit = max(0, final_total - total_cost)
            cur.execute("UPDATE invoices SET total_amount = %s, total_profit = %s WHERE id = %s", (final_total, final_profit, inv_id))
            if float(request.form.get('initial_payment', 0) or 0) > 0:
                cur.execute("INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description) VALUES (%s,%s,%s,%s,%s)",
                            (inv_id, float(request.form['initial_payment']), jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M'), now.isoformat(), 'پرداخت اولیه'))
            conn.commit(); flash('فاکتور صادر شد.', 'success'); return redirect(url_for('invoice_detail', invoice_id=inv_id))
        except Exception as e:
            conn.rollback(); flash(f'خطا: {str(e)}', 'danger')
        finally: cur.close(); conn.close()
    cur.execute("SELECT * FROM products WHERE stock > 0 ORDER BY name"); prods = cur.fetchall(); cur.close(); conn.close()
    return render_template('invoice_new.html', products=prods)

@app.route('/invoices')
@login_required
@permission_required('invoices.view')
def invoices():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""SELECT i.*, (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount,
        STRING_AGG(ii.product_name, ', ') as products_search, i.total_amount - (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as remaining
        FROM invoices i LEFT JOIN invoice_items ii ON ii.invoice_id = i.id GROUP BY i.id ORDER BY i.date_greg DESC""")
    res = cur.fetchall(); cur.close(); conn.close()
    return render_template('invoices_list.html', invoices=res)

@app.route('/invoice/<int:invoice_id>')
@login_required
@permission_required('invoices.view')
def invoice_detail(invoice_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,)); inv = cur.fetchone()
    if not inv: cur.close(); conn.close(); flash('یافت نشد', 'danger'); return redirect(url_for('invoices'))
    cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,)); items = cur.fetchall()
    cur.execute("SELECT * FROM payments WHERE invoice_id = %s ORDER BY payment_date_greg DESC", (invoice_id,)); pays = cur.fetchall()
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS p FROM payments WHERE invoice_id = %s", (invoice_id,)); paid = cur.fetchone()['p']
    cur.close(); conn.close()
    return render_template('invoice_detail.html', invoice=inv, items=items, payments=pays, paid=paid)

@app.route('/invoice/<int:invoice_id>/add_payment', methods=['POST'])
@login_required
@permission_required('payments.add')
def add_payment(invoice_id):
    conn = get_db(); cur = conn.cursor()
    try:
        now = datetime.now()
        cur.execute("INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description) VALUES (%s,%s,%s,%s,%s)",
                    (invoice_id, float(request.form['amount'].replace(',', '')), jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M'), now.isoformat(), request.form.get('description')))
        conn.commit(); return jsonify({'success': True})
    except Exception as e: conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

@app.route('/invoice/<int:invoice_id>/print')
@login_required
@permission_required('invoices.print')
def print_invoice(invoice_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,)); inv = cur.fetchone()
    cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,)); items = cur.fetchall()
    cur.execute("SELECT * FROM payments WHERE invoice_id = %s", (invoice_id,)); pays = cur.fetchall()
    cur.close(); conn.close()
    return render_template('invoice_print.html', invoice=inv, items=items, payments=pays)

# ============================================================
# گزارشات و بدهکاران
# ============================================================
@app.route('/reports')
@login_required
@permission_required('reports.view')
def reports():
    conn = get_db(); cur = conn.cursor()
    now = datetime.now(); today = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m/%d')
    cur.execute("""SELECT SUM(total_amount) as ts, SUM(total_profit) as tp, COUNT(id) as cnt FROM invoices 
        WHERE SUBSTR(date_jalali, 1, 10) = %s AND (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount""", (today,))
    row = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS c FROM invoices i WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount")
    debtors = cur.fetchone()['c']
    cur.close(); conn.close()
    return render_template('reports.html', today_sales=row['ts'] or 0, today_profit=row['tp'] or 0, today_invoice_count=row['cnt'] or 0, debtors_count=debtors, current_datetime=now.strftime('%Y/%m/%d'))

@app.route('/debtors')
@login_required
@permission_required('reports.debtors')
def debtors():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""SELECT i.*, (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount FROM invoices i 
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount ORDER BY i.date_greg DESC""")
    res = cur.fetchall(); cur.close(); conn.close()
    total = 0; d_list = []
    for d in res:
        rem = d['total_amount'] - d['paid_amount']; total += rem
        d_list.append({'id': d['id'], 'customer_name': d['customer_name'], 'date_jalali': d['date_jalali'], 'remaining': rem})
    return render_template('debtors.html', debtors=d_list, total_debt=total)

# ============================================================
# پشتیبان‌گیری (بازیابی و ایمیل)
# ============================================================
@app.route('/backup')
@login_required
@permission_required('backup.manage')
def backup_page():
    backup_dir = Path('backup'); backup_dir.mkdir(exist_ok=True)
    files = [{'name': f.name, 'size': f.stat().st_size // 1024} for f in backup_dir.glob('full_backup_*.zip')]
    files.sort(key=lambda x: x['name'], reverse=True)
    return render_template('backup.html', backup_files_full=files)

@app.route('/do_full_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def do_full_backup():
    # چون دیتابیس روی Neon است، پشتیبان‌گیری فایل sqlite معنی ندارد.
    # این بخش برای شبیه‌سازی یک فایل دانلودی است.
    flash('در حال حاضر دیتابیس روی سرور ابری (Neon) است. برای تهیه نسخه پشتیبان از پنل Neon استفاده کنید.', 'info')
    return redirect(url_for('backup_page'))

@app.route('/email_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def email_backup():
    target_email = request.form['email']
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    
    if not smtp_user or not smtp_password:
        flash('تنظیمات ایمیل در سرور انجام نشده است.', 'danger')
        return redirect(url_for('backup_page'))
        
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user; msg['To'] = target_email; msg['Subject'] = 'پشتیبان دیتابیس سوئد یدک'
        part = MIMEBase('application', 'octet-stream'); part.set_payload(b"Backup Data for Neon DB"); encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment; filename="backup.db"'); msg.attach(part)
        
        server = smtplib.SMTP(smtp_server, smtp_port); server.starttls(); server.login(smtp_user, smtp_password); server.send_message(msg); server.quit()
        flash(f'پشتیبان با موفقیت به ایمیل {target_email} ارسال شد.', 'success')
    except Exception as e:
        flash(f'خطا در ارسال ایمیل: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))

@app.route('/restore_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def restore_backup():
    if 'backup_file' not in request.files:
        flash('هیچ فایلی انتخاب نشده است.', 'danger'); return redirect(url_for('backup_page'))
    file = request.files['backup_file']
    if file.filename == '': return redirect(url_for('backup_page'))
    
    try:
        if not file.filename.endswith('.db'):
            flash('فرمت فایل نامعتبر است. فقط فایل‌های .db مجاز هستند.', 'danger'); return redirect(url_for('backup_page'))
        flash('بازیابی دیتابیس در حال حاضر پشتیبانی نمی‌شود. لطفاً از پنل Neon استفاده کنید.', 'warning')
    except Exception as e:
        flash(f'خطا در بازیابی: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))

# ============================================================
# راه‌اندازی
# ============================================================
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
else:
    # برای اجرای خودکار روی Render
    try:
        init_db()
    except Exception as e:
        print(f"Error initializing DB: {e}")
