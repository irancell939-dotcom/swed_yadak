# -*- coding: utf-8 -*-
"""
نرم‌افزار مدیریت فروشگاه سوئد یدک
نسخه سازگار با PostgreSQL (Neon) و Render
"""

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, session
import jdatetime
import os
import shutil
import pandas as pd
import zipfile
import re
import sys
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import psutil
import platform
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'swed_yadak_secret_key_2025')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('backup', exist_ok=True)


# ============================================================
# دکوراتورهای کنترل دسترسی
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
        if 'user_id' not in session:
            flash('لطفاً ابتدا وارد سیستم شوید.', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
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
# اتصال به دیتابیس (PostgreSQL / Neon)
# ============================================================
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("متغیر محیطی DATABASE_URL تنظیم نشده است.")
    if 'sslmode' not in db_url:
        db_url += '?sslmode=require' if '?' not in db_url else '&sslmode=require'
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn


# ============================================================
# توابع کمکی
# ============================================================
def get_main_executable_info():
    if getattr(sys, 'frozen', False):
        executable_path = sys.executable
    else:
        executable_path = os.path.abspath(sys.argv[0])
    executable_dir = os.path.dirname(executable_path)
    executable_name = os.path.basename(executable_path)
    return {
        'path': executable_path,
        'dir': executable_dir,
        'name': executable_name,
        'name_without_ext': os.path.splitext(executable_name)[0]
    }


def get_flash_drives():
    flash_drives = []
    if platform.system() == "Windows":
        for partition in psutil.disk_partitions():
            if 'removable' in partition.opts:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    if usage.free > 100 * 1024 * 1024:
                        flash_drives.append({
                            'device': partition.device,
                            'mountpoint': partition.mountpoint,
                            'total_size_gb': round(usage.total / (1024**3), 1),
                            'free_space_gb': round(usage.free / (1024**3), 1),
                            'free_space_mb': round(usage.free / (1024**2), 1),
                            'file_system': partition.fstype
                        })
                except (PermissionError, OSError):
                    continue
    else:
        for partition in psutil.disk_partitions():
            if partition.fstype and any(fs in partition.fstype.lower() for fs in ['vfat', 'ntfs', 'exfat', 'fuseblk']):
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    if usage.free > 100 * 1024 * 1024:
                        flash_drives.append({
                            'device': partition.device,
                            'mountpoint': partition.mountpoint,
                            'total_size_gb': round(usage.total / (1024**3), 1),
                            'free_space_gb': round(usage.free / (1024**3), 1),
                            'free_space_mb': round(usage.free / (1024**2), 1),
                            'file_system': partition.fstype
                        })
                except (PermissionError, OSError):
                    continue
    return flash_drives


def auto_copy_to_flash():
    try:
        flash_drives = get_flash_drives()
        if not flash_drives:
            return False, "هیچ فلش مموری یافت نشد"
        exec_info = get_main_executable_info()
        flash_drive = flash_drives[0]
        target_folder_name = f"SwedYadak_{exec_info['name_without_ext']}"
        target_path = os.path.join(flash_drive['mountpoint'], target_folder_name)
        os.makedirs(target_path, exist_ok=True)
        important_folders = ['templates', 'static']
        copied_files = []
        for folder in important_folders:
            source_folder = os.path.join(exec_info['dir'], folder)
            target_folder = os.path.join(target_path, folder)
            if os.path.exists(source_folder):
                shutil.copytree(source_folder, target_folder, dirs_exist_ok=True)
                copied_files.append(f"{folder}/")
        for item in os.listdir(exec_info['dir']):
            item_path = os.path.join(exec_info['dir'], item)
            target_item_path = os.path.join(target_path, item)
            if item in important_folders:
                continue
            exclude_patterns = ['__pycache__', '.git', '.vscode', 'venv', 'env', 'temp', 'tmp']
            if any(exclude in item.lower() for exclude in exclude_patterns):
                continue
            try:
                if os.path.isfile(item_path):
                    shutil.copy2(item_path, target_item_path)
                    copied_files.append(item)
                elif os.path.isdir(item_path):
                    if item in ['uploads']:
                        shutil.copytree(item_path, target_item_path, dirs_exist_ok=True)
                        copied_files.append(f"{item}/")
            except Exception as e:
                print(f"خطا در کپی {item}: {e}")
        info_content = f"""کپی خودکار سوئد یدک
تاریخ ایجاد: {jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')}
نام فایل اجرایی: {exec_info['name']}
مسیر اصلی: {exec_info['dir']}
فلش مقصد: {flash_drive['mountpoint']}
تعداد فایل‌های کپی شده: {len(copied_files)}
فایل‌های کپی شده:
{chr(10).join(f'- {file}' for file in sorted(copied_files))}
دستورات اجرای برنامه:
اگر فایل اجرایی است:
1. دوبار کلیک روی: {exec_info['name']}
اگر فایل پایتون است:
1. اطمینان حاصل کنید پایتون نصب است
2. دستور: pip install -r requirements.txt
3. دستور: python "{exec_info['name']}"
4. مرورگر: http://localhost:5000
اطلاعات سیستم:
سیستم عامل: {platform.system()}
معماری: {platform.architecture()[0]}
"""
        info_file = os.path.join(target_path, 'BACKUP_INFO.txt')
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(info_content)
        return True, f"کپی خودکار انجام شد. {len(copied_files)} فایل در {target_path}"
    except Exception as e:
        return False, f"خطا در کپی خودکار: {str(e)}"


def startup_auto_backup():
    try:
        flash_drives = get_flash_drives()
        if flash_drives:
            success, message = auto_copy_to_flash()
            if success:
                print(f"کپی خودکار هنگام راه‌اندازی: {message}")
            else:
                print(f"کپی خودکار ناموفق: {message}")
    except Exception as e:
        print(f"خطا در کپی خودکار راه‌اندازی: {e}")


@app.template_filter('jdate')
def jdate_filter(dt, format='%Y/%m/%d'):
    if dt is None:
        return ''
    if isinstance(dt, str):
        return dt
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime(format)
    except:
        return dt.strftime(format) if hasattr(dt, 'strftime') else str(dt)


def persian_to_latin_digits(s):
    if not s:
        return s
    trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return s.translate(trans)


def clean_numeric_value(val):
    if pd.isna(val):
        return 0.0
    s = str(val).strip()
    s = re.sub(r'[^\d.-]', '', s)
    if s == '' or s == '.' or s == '-':
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def normalize_persian_text(text):
    if not text:
        return text
    replacements = {
        'ي': 'ی', 'ك': 'ک', 'ة': 'ه', 'ؤ': 'و',
        'إ': 'ا', 'أ': 'ا', 'ٱ': 'ا',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


# ============================================================
# مقداردهی اولیه دیتابیس (PostgreSQL)
# ============================================================
def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            sku TEXT,
            buy_price_tomans REAL NOT NULL DEFAULT 0,
            buy_price_usd REAL,
            sell_price REAL NOT NULL DEFAULT 0,
            warehouse TEXT,
            stock INTEGER DEFAULT 0,
            description TEXT,
            sales_count INTEGER DEFAULT 0
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id SERIAL PRIMARY KEY,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            date_jalali TEXT,
            date_greg TEXT,
            total_amount REAL DEFAULT 0,
            total_profit REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0,
            discount_percent REAL DEFAULT 0,
            status TEXT DEFAULT 'active'
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS invoice_items (
            id SERIAL PRIMARY KEY,
            invoice_id INTEGER REFERENCES invoices(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id),
            product_name TEXT,
            sku TEXT,
            quantity INTEGER,
            unit_price REAL,
            profit_per_unit REAL,
            returned INTEGER DEFAULT 0
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            payment_date_jalali TEXT,
            payment_date_greg TEXT,
            description TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS purchase_lists (
            id SERIAL PRIMARY KEY,
            supplier_name TEXT NOT NULL,
            created_date_jalali TEXT,
            created_date_greg TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT,
            estimated_total REAL DEFAULT 0,
            actual_total REAL DEFAULT 0
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS purchase_list_items (
            id SERIAL PRIMARY KEY,
            purchase_list_id INTEGER NOT NULL REFERENCES purchase_lists(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id),
            product_name TEXT NOT NULL,
            sku TEXT,
            current_stock INTEGER DEFAULT 0,
            quantity_to_buy INTEGER NOT NULL,
            estimated_unit_price REAL,
            actual_unit_price REAL,
            estimated_total_price REAL,
            actual_total_price REAL,
            notes TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS purchase_invoices (
            id SERIAL PRIMARY KEY,
            purchase_list_id INTEGER NOT NULL REFERENCES purchase_lists(id),
            supplier_name TEXT NOT NULL,
            invoice_date_jalali TEXT,
            invoice_date_greg TEXT,
            total_amount REAL DEFAULT 0,
            paid_amount REAL DEFAULT 0,
            remaining_amount REAL DEFAULT 0,
            payment_method TEXT,
            status TEXT DEFAULT 'pending',
            notes TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            permissions TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_date_greg TEXT,
            created_date_jalali TEXT,
            last_login_greg TEXT,
            last_login_jalali TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS permissions_master (
            id SERIAL PRIMARY KEY,
            permission_key TEXT UNIQUE NOT NULL,
            permission_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS purchase_payments (
            id SERIAL PRIMARY KEY,
            purchase_invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            sheba_number TEXT,
            account_owner TEXT,
            bank_name TEXT,
            payment_date_jalali TEXT,
            payment_date_greg TEXT,
            description TEXT,
            receipt_number TEXT,
            created_by INTEGER REFERENCES users(id),
            created_date_greg TEXT,
            created_date_jalali TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS shop_expenses (
            id SERIAL PRIMARY KEY,
            expense_date_jalali TEXT NOT NULL,
            expense_date_greg TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            payment_method TEXT DEFAULT 'cash',
            receipt_number TEXT,
            notes TEXT,
            created_by INTEGER REFERENCES users(id),
            created_date_greg TEXT,
            created_date_jalali TEXT
        )
    ''')

    conn.commit()

    cur.execute("SELECT COUNT(*) AS count FROM users WHERE username = 'admin'")
    admin_exists = cur.fetchone()['count']
    if not admin_exists:
        password_hash = generate_password_hash('123456')
        now_greg = datetime.now().isoformat()
        now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
        cur.execute('''
            INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', ('مدیر سیستم', 'admin', password_hash, 'admin', 'all', now_greg, now_jalali))

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
        ('users.manage', 'مدیریت کاربران', 'سیستم', 'دسترسی به مدیریت کاربران سیستم'),
    ]
    for perm in default_permissions:
        cur.execute('''
            INSERT INTO permissions_master (permission_key, permission_name, category, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (permission_key) DO NOTHING
        ''', perm)

    conn.commit()
    cur.close()
    conn.close()


# ============================================================
# احراز هویت
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s AND is_active = TRUE", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['permissions'] = user['permissions']
            conn = get_db()
            cur = conn.cursor()
            now_greg = datetime.now().isoformat()
            now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
            cur.execute('''
                UPDATE users SET last_login_greg = %s, last_login_jalali = %s WHERE id = %s
            ''', (now_greg, now_jalali, user['id']))
            conn.commit()
            cur.close()
            conn.close()
            flash('ورود موفقیت‌آمیز بود.', 'success')
            return redirect(url_for('index'))
        else:
            flash('نام کاربری یا رمز عبور نادرست است.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('خروج موفقیت‌آمیز بود.', 'success')
    return redirect(url_for('login'))


@app.route('/about')
def about():
    now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d')
    return render_template('about.html', current_date=now_jalali)


# ============================================================
# مدیریت کاربران
# ============================================================
@app.route('/users')
@admin_required
def users_management():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_date_greg DESC")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('users_management.html', users=users)


@app.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    try:
        full_name = request.form['full_name'].strip()
        username = request.form['username'].strip()
        password = request.form['password']
        role = request.form['role']
        permissions = request.form.get('permissions', '').strip() or None
        if not all([full_name, username, password]):
            return jsonify({'success': False, 'error': 'پر کردن تمام فیلدها الزامی است'}), 400
        password_hash = generate_password_hash(password)
        now_greg = datetime.now().isoformat()
        now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (full_name, username, password_hash, role, permissions, now_greg, now_jalali))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت اضافه شد'})
    except psycopg2.IntegrityError:
        return jsonify({'success': False, 'error': 'نام کاربری تکراری است'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/users/<int:user_id>/edit', methods=['POST'])
@admin_required
def edit_user(user_id):
    try:
        full_name = request.form['full_name'].strip()
        username = request.form['username'].strip()
        role = request.form['role']
        permissions = request.form.get('permissions', '').strip() or None
        is_active = request.form.get('is_active', 0)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            UPDATE users SET full_name = %s, username = %s, role = %s, permissions = %s, is_active = %s
            WHERE id = %s
        ''', (full_name, username, role, permissions, is_active, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت ویرایش شد'})
    except psycopg2.IntegrityError:
        return jsonify({'success': False, 'error': 'نام کاربری تکراری است'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/users/<int:user_id>/reset_password', methods=['POST'])
@admin_required
def reset_password(user_id):
    try:
        new_password = request.form['new_password']
        password_hash = generate_password_hash(new_password)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('UPDATE users SET password_hash = %s WHERE id = %s', (password_hash, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'رمز عبور با موفقیت بازنشانی شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session['user_id']:
        return jsonify({'success': False, 'error': 'نمی‌توانید حساب خود را حذف کنید'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('DELETE FROM users WHERE id = %s', (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت حذف شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/permissions_master')
@admin_required
def get_permissions_master():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''SELECT * FROM permissions_master ORDER BY category, permission_name''')
    permissions = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(p) for p in permissions])


@app.route('/api/user_permissions/<int:user_id>')
@admin_required
def get_user_permissions(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT permissions FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user and user['permissions']:
        permissions_list = user['permissions'].split(',')
    else:
        permissions_list = []
    return jsonify({'permissions': permissions_list})


# ============================================================
# صفحه اصلی
# ============================================================
@app.route('/')
@login_required
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT COUNT(*) AS count FROM invoices i
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount
    ''')
    debtors_count = cur.fetchone()['count']
    cur.close()
    conn.close()
    return render_template('index.html', debtors_count=debtors_count)


# ============================================================
# مدیریت محصولات
# ============================================================
@app.route('/products', methods=['GET', 'POST'])
@login_required
@permission_required('products.view')
def products():
    conn = get_db()
    cur = conn.cursor()
    edit_product = None
    if request.args.get('edit'):
        pid = request.args.get('edit')
        cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
        edit_product = cur.fetchone()
        if edit_product:
            edit_product = dict(edit_product)
            for k in ('buy_price_usd', 'buy_price_tomans', 'sell_price'):
                if edit_product.get(k):
                    edit_product[k] = float(edit_product[k])

    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            sku = request.form['sku'].strip() or None
            buy_tomans_str = persian_to_latin_digits(request.form['buy_price_tomans']).replace(',', '').replace('٬', '')
            buy_tomans = float(buy_tomans_str) if buy_tomans_str else 0.0
            sell_price_str = persian_to_latin_digits(request.form['sell_price']).replace(',', '').replace('٬', '')
            sell_price = float(sell_price_str) if sell_price_str else 0.0
            usd_str = request.form.get('buy_price_usd', '').strip()
            if usd_str:
                usd_str = persian_to_latin_digits(usd_str).replace(',', '').replace('٬', '')
                usd = float(usd_str) if usd_str else None
            else:
                usd = None
            warehouse = request.form.get('warehouse', '').strip() or None
            stock_str = persian_to_latin_digits(request.form.get('stock', '0')).replace(',', '').replace('٬', '')
            stock = int(stock_str) if stock_str else 0
            description = request.form.get('description', '').strip() or None
            product_id = request.form.get('product_id')

            if product_id:
                cur.execute('''
                    UPDATE products SET name = %s, sku = %s, buy_price_tomans = %s, buy_price_usd = %s,
                    sell_price = %s, warehouse = %s, stock = %s, description = %s
                    WHERE id = %s
                ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description, product_id))
                flash('کالا با موفقیت ویرایش شد.', 'success')
            else:
                cur.execute('''
                    INSERT INTO products (name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description))
                flash('کالا با موفقیت اضافه شد.', 'success')
            conn.commit()
        except ValueError as e:
            conn.rollback()
            flash(f'لطفاً مقادیر عددی را به درستی وارد کنید. خطا: {str(e)}', 'danger')
        except Exception as e:
            conn.rollback()
            flash(f'خطا در ذخیره کالا: {str(e)}', 'danger')
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('products'))

    cur.execute("SELECT * FROM products ORDER BY name")
    products_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('products.html', products=products_list, edit_product=edit_product)


@app.route('/update_product/<int:product_id>', methods=['POST'])
@login_required
@permission_required('products.edit')
def update_product(product_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        name = request.form['name'].strip()
        sku = request.form['sku'].strip() or None
        buy_tomans_str = persian_to_latin_digits(request.form['buy_price_tomans']).replace(',', '').replace('٬', '')
        buy_tomans = float(buy_tomans_str) if buy_tomans_str else 0.0
        sell_price_str = persian_to_latin_digits(request.form['sell_price']).replace(',', '').replace('٬', '')
        sell_price = float(sell_price_str) if sell_price_str else 0.0
        usd_str = request.form.get('buy_price_usd', '').strip()
        if usd_str:
            usd_str = persian_to_latin_digits(usd_str).replace(',', '').replace('٬', '')
            usd = float(usd_str) if usd_str else None
        else:
            usd = None
        warehouse = request.form.get('warehouse', '').strip() or None
        stock_str = persian_to_latin_digits(request.form.get('stock', '0')).replace(',', '').replace('٬', '')
        stock = int(stock_str) if stock_str else 0
        description = request.form.get('description', '').strip() or None
        cur.execute('''
            UPDATE products SET name = %s, sku = %s, buy_price_tomans = %s, buy_price_usd = %s,
            sell_price = %s, warehouse = %s, stock = %s, description = %s
            WHERE id = %s
        ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description, product_id))
        conn.commit()
        flash('کالا با موفقیت ویرایش شد.', 'success')
        return redirect(url_for('products'))
    except ValueError as e:
        conn.rollback()
        flash(f'لطفاً مقادیر عددی را به درستی وارد کنید. خطا: {str(e)}', 'danger')
        return redirect(url_for('products', edit=product_id))
    except Exception as e:
        conn.rollback()
        flash(f'خطا در ویرایش کالا: {str(e)}', 'danger')
        return redirect(url_for('products', edit=product_id))
    finally:
        cur.close()
        conn.close()


@app.route('/api/search_products_all')
@login_required
def search_products_all():
    q = request.args.get('q', '').lower()
    q = normalize_persian_text(q)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM products
        WHERE (LOWER(name) LIKE %s OR LOWER(sku) LIKE %s OR
               LOWER(REPLACE(REPLACE(name, 'ي', 'ی'), 'ك', 'ک')) LIKE %s OR
               LOWER(REPLACE(REPLACE(sku, 'ي', 'ی'), 'ك', 'ک')) LIKE %s)
        ORDER BY name
    """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
    products_list = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(p) for p in products_list])


@app.route('/api/search_products_by_name')
@login_required
def search_products_by_name():
    return search_products_all()


@app.route('/api/search_products_by_tab')
@login_required
@permission_required('products.view')
def search_products_by_tab():
    try:
        tab = request.args.get('tab', '')
        query = normalize_persian_text(request.args.get('q', '').lower().strip())
        conn = get_db()
        cur = conn.cursor()
        where_conditions = []
        params = []
        if tab == 'out-of-stock':
            where_conditions.append("stock = 0")
        elif tab == 'low-stock':
            where_conditions.append("stock > 0 AND stock < 5")
        elif tab == 'normal-stock':
            where_conditions.append("stock >= 5")
        elif tab == 'top-selling':
            where_conditions.append("sales_count > 0")
        else:
            where_conditions.append("1=1")
        if query:
            where_conditions.append("""
                (LOWER(name) LIKE %s OR LOWER(sku) LIKE %s OR
                 LOWER(REPLACE(REPLACE(name, 'ي', 'ی'), 'ك', 'ک')) LIKE %s OR
                 LOWER(REPLACE(REPLACE(sku, 'ي', 'ی'), 'ك', 'ک')) LIKE %s)
            """)
            p = f"%{query}%"
            params.extend([p, p, p, p])
        where_clause = " AND ".join(where_conditions)
        order_clause = "ORDER BY sales_count DESC" if tab == 'top-selling' else "ORDER BY name"
        sql = f"SELECT * FROM products WHERE {where_clause} {order_clause}"
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(p) for p in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/product/<int:product_id>/delete', methods=['POST'])
@login_required
@permission_required('products.delete')
def delete_product(product_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM invoice_items WHERE product_id = %s", (product_id,))
    used_in_invoice = cur.fetchone()['c']
    if used_in_invoice > 0:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'error': 'این کالا در فاکتورهای صادر شده استفاده شده و قابل حذف نیست.'})
    cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


# ============================================================
# فاکتور فروش
# ============================================================
@app.route('/invoice', methods=['GET', 'POST'])
@login_required
@permission_required('invoices.create')
def invoice():
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            customer_name = request.form['customer_name'].strip()
            customer_phone = request.form['customer_phone'].strip()
            discount_percent = float(request.form.get('discount_percent', '0').replace(',', '').replace('٬', '') or 0)
            discount_amount = float(request.form.get('discount_amount', '0').replace(',', '').replace('٬', '') or 0)
            initial_payment = float(request.form.get('initial_payment', '0').replace(',', '').replace('٬', '') or 0)
            now_greg = datetime.now()
            now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')

            cur.execute('''
                INSERT INTO invoices (customer_name, customer_phone, date_jalali, date_greg, total_amount, total_profit, discount_amount, discount_percent)
                VALUES (%s, %s, %s, %s, 0, 0, %s, %s) RETURNING id
            ''', (customer_name, customer_phone, now_jalali, now_greg.isoformat(), discount_amount, discount_percent))
            invoice_id = cur.fetchone()['id']

            total = 0
            total_buy_cost = 0
            item_ids = request.form.getlist('item_id[]')
            quantities = request.form.getlist('quantity[]')

            for i, product_id in enumerate(item_ids):
                quantity = int(quantities[i])
                if quantity <= 0:
                    continue
                cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
                product = cur.fetchone()
                if not product or product['stock'] < quantity:
                    raise ValueError(f"موجودی ناکافی برای {product['name'] if product else product_id}")
                custom_price_str = request.form.get(f'custom_price_{product_id}', '').replace(',', '').replace('٬', '')
                unit_price = float(custom_price_str) if custom_price_str else product['sell_price']
                profit_per_unit = unit_price - product['buy_price_tomans']
                item_total = unit_price * quantity
                item_buy_cost = product['buy_price_tomans'] * quantity
                cur.execute('''
                    INSERT INTO invoice_items (invoice_id, product_id, product_name, sku, quantity, unit_price, profit_per_unit)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (invoice_id, product_id, product['name'], product['sku'], quantity, unit_price, profit_per_unit))
                cur.execute("UPDATE products SET stock = stock - %s, sales_count = sales_count + %s WHERE id = %s",
                            (quantity, quantity, product_id))
                total += item_total
                total_buy_cost += item_buy_cost

            if discount_percent > 0 and discount_amount == 0:
                discount_amount = total * (discount_percent / 100)
            final_total = total - discount_amount
            final_profit = max(0, final_total - total_buy_cost)
            cur.execute('''
                UPDATE invoices SET total_amount = %s, total_profit = %s, discount_amount = %s WHERE id = %s
            ''', (final_total, final_profit, discount_amount, invoice_id))

            if initial_payment > 0:
                cur.execute('''
                    INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (invoice_id, initial_payment, now_jalali, now_greg.isoformat(), 'پرداخت اولیه'))

            conn.commit()
            cur.close()
            conn.close()
            flash('فاکتور با موفقیت صادر شد.', 'success')
            return redirect(url_for('invoice_detail', invoice_id=invoice_id))
        except ValueError as ve:
            conn.rollback()
            flash(str(ve), 'danger')
        except Exception as e:
            conn.rollback()
            flash(f'خطا در صدور فاکتور: {str(e)}', 'danger')
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE stock > 0 ORDER BY name")
    products_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('invoice_new.html', products=products_list)


@app.route('/invoices')
@login_required
@permission_required('invoices.view')
def invoices():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT i.*,
               (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount,
               STRING_AGG(ii.product_name, ', ') as products_search,
               CASE WHEN (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount
                    THEN 1 ELSE 0 END as is_paid,
               i.total_amount - (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as remaining
        FROM invoices i
        LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
        GROUP BY i.id
        ORDER BY i.date_greg DESC
    ''')
    invoices_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('invoices_list.html', invoices=invoices_list)


@app.route('/invoice/<int:invoice_id>')
@login_required
@permission_required('invoices.view')
def invoice_detail(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))
    cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
    items = cur.fetchall()
    cur.execute("SELECT * FROM payments WHERE invoice_id = %s ORDER BY payment_date_greg DESC", (invoice_id,))
    payments = cur.fetchall()
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS total_paid FROM payments WHERE invoice_id = %s", (invoice_id,))
    paid = cur.fetchone()['total_paid']
    cur.close()
    conn.close()
    return render_template('invoice_detail.html', invoice=inv, items=items, payments=payments, paid=paid)


@app.route('/invoice/<int:invoice_id>/add_payment', methods=['POST'])
@login_required
@permission_required('payments.add')
def add_payment(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        amount = float(request.form['amount'].replace(',', '').replace('٬', ''))
        description = request.form.get('description', '').strip() or None
        now_greg = datetime.now()
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')
        cur.execute('''
            INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description)
            VALUES (%s, %s, %s, %s, %s)
        ''', (invoice_id, amount, now_jalali, now_greg.isoformat(), description))
        conn.commit()
        return jsonify({'success': True})
    except ValueError:
        conn.rollback()
        return jsonify({'success': False, 'error': 'مبلغ نامعتبر'}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/payment/<int:payment_id>/delete', methods=['POST'])
@login_required
@permission_required('payments.delete')
def delete_payment(payment_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM payments WHERE id = %s", (payment_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/payment/<int:payment_id>/edit', methods=['POST'])
@login_required
@permission_required('payments.edit')
def edit_payment(payment_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        amount = float(request.form['amount'].replace(',', '').replace('٬', ''))
        description = request.form.get('description', '').strip() or None
        cur.execute("UPDATE payments SET amount = %s, description = %s WHERE id = %s", (amount, description, payment_id))
        conn.commit()
        return jsonify({'success': True})
    except ValueError:
        conn.rollback()
        return jsonify({'success': False, 'error': 'مبلغ نامعتبر'}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/invoice/<int:invoice_id>/print')
@login_required
@permission_required('invoices.print')
def print_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    inv = cur.fetchone()
    cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
    items = cur.fetchall()
    cur.execute("SELECT * FROM payments WHERE invoice_id = %s ORDER BY payment_date_greg", (invoice_id,))
    payments = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('invoice_print.html', invoice=inv, items=items, payments=payments)


@app.route('/invoice/<int:invoice_id>/edit')
@login_required
@permission_required('invoices.edit')
def edit_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))
    cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
    items = cur.fetchall()
    cur.execute("SELECT * FROM products WHERE stock > 0 ORDER BY name")
    products_list = cur.fetchall()
    cur.execute("SELECT * FROM payments WHERE invoice_id = %s ORDER BY payment_date_greg DESC", (invoice_id,))
    payments = cur.fetchall()
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS p FROM payments WHERE invoice_id = %s", (invoice_id,))
    paid = cur.fetchone()['p']
    cur.close()
    conn.close()
    return render_template('invoice_edit.html', invoice=inv, items=items, products=products_list, payments=payments, paid=paid)


@app.route('/invoice/<int:invoice_id>/update', methods=['POST'])
@login_required
@permission_required('invoices.edit')
def update_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))
    try:
        discount_percent = float(request.form.get('discount_percent', '0').replace(',', '').replace('٬', '') or 0)
        discount_amount = float(request.form.get('discount_amount', '0').replace(',', '').replace('٬', '') or 0)
        item_ids = request.form.getlist('item_id[]')
        quantities = request.form.getlist('quantity[]')
        total = 0
        total_buy_cost = 0

        cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
        existing_items = cur.fetchall()
        for item in existing_items:
            cur.execute("UPDATE products SET stock = stock + %s, sales_count = sales_count - %s WHERE id = %s",
                        (item['quantity'], item['quantity'], item['product_id']))
        cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice_id,))

        for i in range(len(item_ids)):
            product_id = item_ids[i]
            quantity = int(quantities[i]) if quantities[i] else 0
            if quantity <= 0:
                continue
            custom_price_str = persian_to_latin_digits(request.form.get(f'custom_price_{product_id}', '0').replace(',', '').replace('٬', ''))
            unit_price = float(custom_price_str) if custom_price_str else 0.0
            if unit_price <= 0:
                raise ValueError(f"قیمت کالا با شناسه {product_id} نامعتبر است.")
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            product = cur.fetchone()
            if not product:
                raise ValueError(f"کالایی با شناسه {product_id} یافت نشد.")
            if product['stock'] < quantity:
                raise ValueError(f"موجودی کافی برای «{product['name']}» وجود ندارد.")
            profit_per_unit = unit_price - product['buy_price_tomans']
            item_total = unit_price * quantity
            item_buy_cost = product['buy_price_tomans'] * quantity
            cur.execute('''
                INSERT INTO invoice_items (invoice_id, product_id, product_name, sku, quantity, unit_price, profit_per_unit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (invoice_id, product_id, product['name'], product['sku'], quantity, unit_price, profit_per_unit))
            cur.execute("UPDATE products SET stock = stock - %s, sales_count = sales_count + %s WHERE id = %s",
                        (quantity, quantity, product_id))
            total += item_total
            total_buy_cost += item_buy_cost

        if discount_percent > 0 and discount_amount == 0:
            discount_amount = total * (discount_percent / 100)
        final_total = total - discount_amount
        final_profit = max(0, final_total - total_buy_cost)
        cur.execute('''
            UPDATE invoices SET total_amount = %s, total_profit = %s, discount_amount = %s, discount_percent = %s
            WHERE id = %s
        ''', (final_total, final_profit, discount_amount, discount_percent, invoice_id))
        conn.commit()
        flash('فاکتور با موفقیت ویرایش شد.', 'success')
        return redirect(url_for('invoice_detail', invoice_id=invoice_id))
    except ValueError as ve:
        conn.rollback()
        flash(str(ve), 'danger')
        return redirect(url_for('edit_invoice', invoice_id=invoice_id))
    except Exception as e:
        conn.rollback()
        flash(f'خطا در ویرایش فاکتور: {str(e)}', 'danger')
        return redirect(url_for('edit_invoice', invoice_id=invoice_id))
    finally:
        cur.close()
        conn.close()


@app.route('/invoice/<int:invoice_id>/items')
@login_required
def get_invoice_items(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT ii.*, p.stock + ii.quantity as max_stock
        FROM invoice_items ii
        LEFT JOIN products p ON p.id = ii.product_id
        WHERE ii.invoice_id = %s
    ''', (invoice_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(item) for item in items])


@app.route('/invoice/<int:invoice_id>/delete', methods=['POST'])
@login_required
@permission_required('invoices.delete')
def delete_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور یافت نشد'}), 404
        cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
        items = cur.fetchall()
        for item in items:
            cur.execute("UPDATE products SET stock = stock + %s, sales_count = sales_count - %s WHERE id = %s",
                        (item['quantity'], item['quantity'], item['product_id']))
        cur.execute("DELETE FROM payments WHERE invoice_id = %s", (invoice_id,))
        cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
        cur.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'فاکتور با موفقیت حذف شد و موجودی کالاها بازگردانی شد.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ============================================================
# لیست خرید
# ============================================================
@app.route('/purchase_lists')
@login_required
@permission_required('purchase.view')
def purchase_lists():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pl.*, COUNT(pli.id) as item_count, SUM(pli.estimated_total_price) as total_estimated
        FROM purchase_lists pl
        LEFT JOIN purchase_list_items pli ON pl.id = pli.purchase_list_id
        GROUP BY pl.id
        ORDER BY pl.created_date_greg DESC
    ''')
    purchase_lists_list = cur.fetchall()
    cur.execute("SELECT id, name, sku, stock FROM products ORDER BY name")
    products_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('purchase_lists.html', purchase_lists=purchase_lists_list, products=products_list)


@app.route('/purchase_lists/create', methods=['POST'])
@login_required
@permission_required('purchase.create')
def create_purchase_list():
    try:
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None
        now_greg = datetime.now().isoformat()
        now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO purchase_lists (supplier_name, created_date_greg, created_date_jalali, notes)
            VALUES (%s, %s, %s, %s) RETURNING id
        ''', (supplier_name, now_greg, now_jalali, notes))
        purchase_list_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'purchase_list_id': purchase_list_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/create_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.create')
def api_create_purchase_list():
    return create_purchase_list()


@app.route('/purchase_lists/<int:list_id>/add_item', methods=['POST'])
@login_required
@permission_required('purchase.create')
def add_purchase_list_item(list_id):
    try:
        product_id = request.form['product_id']
        quantity_to_buy = int(request.form['quantity_to_buy'])
        estimated_unit_price = float(request.form.get('estimated_unit_price', 0))
        notes = request.form.get('notes', '').strip() or None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, sku, stock FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()
        if not product:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'محصول یافت نشد'}), 400
        estimated_total_price = quantity_to_buy * estimated_unit_price
        cur.execute('''
            INSERT INTO purchase_list_items
            (purchase_list_id, product_id, product_name, sku, current_stock, quantity_to_buy, estimated_unit_price, estimated_total_price, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (list_id, product_id, product['name'], product['sku'], product['stock'], quantity_to_buy, estimated_unit_price, estimated_total_price, notes))
        cur.execute('''
            UPDATE purchase_lists SET estimated_total = (
                SELECT SUM(estimated_total_price) FROM purchase_list_items WHERE purchase_list_id = %s
            ) WHERE id = %s
        ''', (list_id, list_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/add_to_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.create')
def add_to_purchase_list():
    try:
        purchase_list_id = request.form['purchase_list_id']
        product_id = request.form['product_id']
        quantity = int(request.form['quantity'])
        notes = request.form.get('notes', '').strip() or None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, sku, stock, buy_price_tomans FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()
        if not product:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'محصول یافت نشد'}), 400
        cur.execute('''SELECT * FROM purchase_list_items WHERE purchase_list_id = %s AND product_id = %s''', (purchase_list_id, product_id))
        existing_item = cur.fetchone()
        if existing_item:
            cur.execute('''UPDATE purchase_list_items SET quantity_to_buy = quantity_to_buy + %s, notes = COALESCE(%s, notes) WHERE id = %s''',
                        (quantity, notes, existing_item['id']))
        else:
            estimated_unit_price = product['buy_price_tomans'] or 0
            cur.execute('''
                INSERT INTO purchase_list_items
                (purchase_list_id, product_id, product_name, sku, current_stock, quantity_to_buy, estimated_unit_price, estimated_total_price, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (purchase_list_id, product_id, product['name'], product['sku'], product['stock'], quantity, estimated_unit_price, estimated_unit_price * quantity, notes))
        cur.execute('''UPDATE purchase_lists SET estimated_total = (
            SELECT SUM(estimated_total_price) FROM purchase_list_items WHERE purchase_list_id = %s
        ) WHERE id = %s''', (purchase_list_id, purchase_list_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/update_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_list():
    try:
        purchase_list_id = request.form['purchase_list_id']
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE purchase_lists SET supplier_name = %s, notes = %s WHERE id = %s", (supplier_name, notes, purchase_list_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete_purchase_list/<int:list_id>', methods=['POST'])
@login_required
@permission_required('purchase.delete')
def delete_purchase_list(list_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('DELETE FROM purchase_list_items WHERE purchase_list_id = %s', (list_id,))
        cur.execute('DELETE FROM purchase_lists WHERE id = %s', (list_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/get_purchase_list_items/<int:list_id>')
@login_required
@permission_required('purchase.view')
def get_purchase_list_items(list_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pli.*, p.stock as current_stock
        FROM purchase_list_items pli
        LEFT JOIN products p ON pli.product_id = p.id
        WHERE pli.purchase_list_id = %s
    ''', (list_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(item) for item in items])


@app.route('/api/update_purchase_item_quantity', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_item_quantity():
    try:
        item_id = request.form['item_id']
        quantity = int(request.form['quantity'])
        if quantity <= 0:
            return jsonify({'success': False, 'error': 'تعداد باید بیشتر از صفر باشد'}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM purchase_list_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404
        estimated_total_price = (item['estimated_unit_price'] or 0) * quantity
        cur.execute("UPDATE purchase_list_items SET quantity_to_buy = %s, estimated_total_price = %s WHERE id = %s",
                    (quantity, estimated_total_price, item_id))
        cur.execute('''UPDATE purchase_lists SET estimated_total = (
            SELECT SUM(estimated_total_price) FROM purchase_list_items WHERE purchase_list_id = %s
        ) WHERE id = %s''', (item['purchase_list_id'], item['purchase_list_id']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/remove_from_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def remove_from_purchase_list():
    try:
        item_id = request.form['item_id']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT purchase_list_id FROM purchase_list_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404
        cur.execute('DELETE FROM purchase_list_items WHERE id = %s', (item_id,))
        cur.execute('''UPDATE purchase_lists SET estimated_total = (
            SELECT SUM(estimated_total_price) FROM purchase_list_items WHERE purchase_list_id = %s
        ) WHERE id = %s''', (item['purchase_list_id'], item['purchase_list_id']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/print_purchase_list/<int:purchase_list_id>')
@login_required
@permission_required('purchase.view')
def print_purchase_list(purchase_list_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM purchase_lists WHERE id = %s", (purchase_list_id,))
    purchase_list = cur.fetchone()
    if not purchase_list:
        cur.close()
        conn.close()
        flash('لیست خرید یافت نشد.', 'danger')
        return redirect(url_for('purchase_lists'))
    cur.execute('''
        SELECT pli.*, p.stock as current_stock
        FROM purchase_list_items pli
        LEFT JOIN products p ON pli.product_id = p.id
        WHERE pli.purchase_list_id = %s
    ''', (purchase_list_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    current_datetime = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
    return render_template('print_purchase_list.html', purchase_list=purchase_list, items=items, current_datetime=current_datetime)


# ============================================================
# فاکتور خرید
# ============================================================
@app.route('/convert_purchase_list_to_invoice/<int:purchase_list_id>')
@login_required
@permission_required('invoices.create')
def convert_purchase_list_to_invoice(purchase_list_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM purchase_lists WHERE id = %s", (purchase_list_id,))
        purchase_list = cur.fetchone()
        if not purchase_list:
            flash('لیست خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_lists'))
        cur.execute("SELECT * FROM purchase_list_items WHERE purchase_list_id = %s", (purchase_list_id,))
        items = cur.fetchall()
        if not items:
            flash('لیست خرید خالی است.', 'warning')
            return redirect(url_for('purchase_lists'))
        now_greg = datetime.now()
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')
        total_amount = sum(item['estimated_total_price'] or 0 for item in items)
        cur.execute('''
            INSERT INTO purchase_invoices
            (purchase_list_id, supplier_name, invoice_date_jalali, invoice_date_greg, total_amount, remaining_amount, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        ''', (purchase_list_id, purchase_list['supplier_name'], now_jalali, now_greg.isoformat(), total_amount, total_amount, 'pending', purchase_list['notes']))
        for item in items:
            if item['product_id']:
                cur.execute('''UPDATE products SET stock = stock + %s, buy_price_tomans = COALESCE(%s, buy_price_tomans) WHERE id = %s''',
                            (item['quantity_to_buy'], item['estimated_unit_price'], item['product_id']))
        cur.execute("UPDATE purchase_lists SET status = 'converted' WHERE id = %s", (purchase_list_id,))
        conn.commit()
        flash('لیست خرید با موفقیت به فاکتور خرید تبدیل شد و موجودی کالاها به‌روزرسانی شد.', 'success')
        return redirect(url_for('purchase_invoices_list'))
    except Exception as e:
        conn.rollback()
        flash(f'خطا در تبدیل لیست خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_lists'))
    finally:
        cur.close()
        conn.close()


@app.route('/purchase_invoices_list')
@login_required
@permission_required('purchase.view')
def purchase_invoices_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pi.*, pl.notes as purchase_notes,
            (SELECT COUNT(*) FROM purchase_list_items pli WHERE pli.purchase_list_id = pl.id) as item_count
        FROM purchase_invoices pi
        LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
        ORDER BY pi.invoice_date_greg DESC
    ''')
    purchase_invoices = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('purchase_invoices_list.html', purchase_invoices=purchase_invoices)


@app.route('/api/active_purchase_lists')
@login_required
@permission_required('purchase.view')
def api_active_purchase_lists():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pl.*, COUNT(pli.id) as item_count, SUM(pli.estimated_total_price) as total_estimated
        FROM purchase_lists pl
        LEFT JOIN purchase_list_items pli ON pl.id = pli.purchase_list_id
        WHERE pl.status = 'active'
        GROUP BY pl.id
        ORDER BY pl.created_date_greg DESC
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/create_purchase_invoice', methods=['POST'])
@login_required
@permission_required('purchase.create')
def api_create_purchase_invoice():
    try:
        purchase_list_id = request.form['purchase_list_id']
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM purchase_lists WHERE id = %s AND status = 'active'", (purchase_list_id,))
        purchase_list = cur.fetchone()
        if not purchase_list:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'لیست خرید یافت نشد یا غیرفعال است'}), 404
        cur.execute("SELECT * FROM purchase_invoices WHERE purchase_list_id = %s", (purchase_list_id,))
        existing_invoice = cur.fetchone()
        if existing_invoice:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'از این لیست خرید قبلاً فاکتور ایجاد شده است'}), 400
        cur.execute("SELECT SUM(estimated_total_price) as total FROM purchase_list_items WHERE purchase_list_id = %s", (purchase_list_id,))
        total_amount = cur.fetchone()['total'] or 0
        now_greg = datetime.now()
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')
        cur.execute('''
            INSERT INTO purchase_invoices
            (purchase_list_id, supplier_name, invoice_date_jalali, invoice_date_greg, total_amount, paid_amount, remaining_amount, status, notes)
            VALUES (%s, %s, %s, %s, %s, 0, %s, 'pending', %s) RETURNING id
        ''', (purchase_list_id, supplier_name, now_jalali, now_greg.isoformat(), total_amount, total_amount, notes))
        invoice_id = cur.fetchone()['id']
        cur.execute("UPDATE purchase_lists SET status = 'converted' WHERE id = %s", (purchase_list_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'invoice_id': invoice_id, 'message': 'فاکتور خرید با موفقیت ایجاد شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/purchase_invoice/<int:invoice_id>')
@login_required
@permission_required('purchase.view')
def purchase_invoice_detail(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pi.*, pl.notes as purchase_notes
        FROM purchase_invoices pi
        LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
        WHERE pi.id = %s
    ''', (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        flash('فاکتور خرید یافت نشد.', 'danger')
        return redirect(url_for('purchase_invoices_list'))
    cur.execute("SELECT * FROM purchase_list_items WHERE purchase_list_id = %s", (inv['purchase_list_id'],))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('purchase_invoice.html', invoice=inv, items=items)


@app.route('/purchase_invoice/<int:invoice_id>/print')
@login_required
@permission_required('purchase.view')
def print_purchase_invoice(invoice_id):
    return purchase_invoice_detail(invoice_id)


@app.route('/purchase_invoice/<int:invoice_id>/edit')
@login_required
@permission_required('purchase.edit')
def edit_purchase_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pi.*, pl.notes as purchase_notes
        FROM purchase_invoices pi
        LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
        WHERE pi.id = %s
    ''', (invoice_id,))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        flash('فاکتور خرید یافت نشد.', 'danger')
        return redirect(url_for('purchase_invoices_list'))
    cur.execute('''
        SELECT pli.*, p.stock as current_stock
        FROM purchase_list_items pli
        LEFT JOIN products p ON pli.product_id = p.id
        WHERE pli.purchase_list_id = %s
    ''', (inv['purchase_list_id'],))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('edit_purchase_invoice.html', invoice=inv, items=items)


@app.route('/purchase_invoice/<int:invoice_id>/update_status', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_invoice_status(invoice_id):
    try:
        status = request.form['status']
        paid_amount = float(request.form.get('paid_amount', 0))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM purchase_invoices WHERE id = %s", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404
        remaining_amount = inv['total_amount'] - paid_amount
        cur.execute("UPDATE purchase_invoices SET status = %s, paid_amount = %s, remaining_amount = %s WHERE id = %s",
                    (status, paid_amount, remaining_amount, invoice_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/purchase_invoice/<int:invoice_id>/add_payment', methods=['POST'])
@login_required
@permission_required('payments.add')
def add_purchase_invoice_payment(invoice_id):
    try:
        amount = float(request.form['amount'])
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM purchase_invoices WHERE id = %s", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404
        new_paid = inv['paid_amount'] + amount
        new_remaining = inv['total_amount'] - new_paid
        cur.execute("UPDATE purchase_invoices SET paid_amount = %s, remaining_amount = %s WHERE id = %s",
                    (new_paid, new_remaining, invoice_id))
        if new_remaining <= 0:
            cur.execute("UPDATE purchase_invoices SET status = 'paid' WHERE id = %s", (invoice_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/purchase_invoice/<int:invoice_id>/receive_stock', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def receive_purchase_invoice_stock(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''SELECT pi.*, pl.id as purchase_list_id FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id WHERE pi.id = %s''', (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404
        cur.execute("SELECT * FROM purchase_list_items WHERE purchase_list_id = %s", (inv['purchase_list_id'],))
        items = cur.fetchall()
        for item in items:
            if item['product_id']:
                cur.execute('''UPDATE products SET stock = stock + %s, buy_price_tomans = COALESCE(%s, buy_price_tomans) WHERE id = %s''',
                            (item['quantity_to_buy'], item['estimated_unit_price'], item['product_id']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'موجودی کالاها با موفقیت اضافه شد.'})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/purchase_invoice/<int:invoice_id>/delete', methods=['POST'])
@login_required
@permission_required('purchase.delete')
def delete_purchase_invoice(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''SELECT pi.*, pl.id as purchase_list_id FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id WHERE pi.id = %s''', (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404
        cur.execute("SELECT * FROM purchase_list_items WHERE purchase_list_id = %s", (inv['purchase_list_id'],))
        items = cur.fetchall()
        for item in items:
            if item['product_id']:
                cur.execute('''UPDATE products SET stock = CASE WHEN stock >= %s THEN stock - %s ELSE 0 END WHERE id = %s''',
                            (item['quantity_to_buy'], item['quantity_to_buy'], item['product_id']))
        cur.execute("DELETE FROM purchase_payments WHERE purchase_invoice_id = %s", (invoice_id,))
        cur.execute("DELETE FROM purchase_invoices WHERE id = %s", (invoice_id,))
        if inv['purchase_list_id']:
            cur.execute("UPDATE purchase_lists SET status = 'active' WHERE id = %s", (inv['purchase_list_id'],))
        conn.commit()
        return jsonify({'success': True, 'message': 'فاکتور خرید با موفقیت حذف شد و موجودی کالاها کاهش یافت.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/update_purchase_item_price', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_item_price():
    try:
        item_id = request.form['item_id']
        actual_unit_price = float(request.form['actual_unit_price'])
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT quantity_to_buy, purchase_list_id FROM purchase_list_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404
        quantity = item['quantity_to_buy']
        purchase_list_id = item['purchase_list_id']
        actual_total_price = actual_unit_price * quantity
        cur.execute("UPDATE purchase_list_items SET actual_unit_price = %s, actual_total_price = %s WHERE id = %s",
                    (actual_unit_price, actual_total_price, item_id))
        cur.execute('''SELECT COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items WHERE purchase_list_id = %s''', (purchase_list_id,))
        new_total = cur.fetchone()['total_amount']
        cur.execute('''UPDATE purchase_invoices SET total_amount = %s, remaining_amount = %s - paid_amount WHERE purchase_list_id = %s''',
                    (new_total, new_total, purchase_list_id))
        conn.commit()
        cur.execute("SELECT total_amount, paid_amount, remaining_amount FROM purchase_invoices WHERE purchase_list_id = %s", (purchase_list_id,))
        updated_invoice = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({
            'success': True,
            'actual_unit_price': actual_unit_price,
            'actual_total_price': actual_total_price,
            'new_invoice_total': new_total,
            'new_paid_amount': updated_invoice['paid_amount'],
            'new_remaining_amount': updated_invoice['remaining_amount']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/purchase_invoice_payments/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
@permission_required('payments.add')
def api_purchase_invoice_payments(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            amount = float(request.form['amount'].replace(',', '').replace('٬', ''))
            payment_method = request.form['payment_method']
            sheba_number = request.form.get('sheba_number', '').strip() or None
            account_owner = request.form.get('account_owner', '').strip() or None
            bank_name = request.form.get('bank_name', '').strip() or None
            description = request.form.get('description', '').strip() or None
            receipt_number = request.form.get('receipt_number', '').strip() or None
            now_greg = datetime.now().isoformat()
            now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
            cur.execute('''
                INSERT INTO purchase_payments
                (purchase_invoice_id, amount, payment_method, sheba_number, account_owner, bank_name,
                 payment_date_jalali, payment_date_greg, description, receipt_number,
                 created_by, created_date_greg, created_date_jalali)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (invoice_id, amount, payment_method, sheba_number, account_owner, bank_name,
                  now_jalali, now_greg, description, receipt_number, session['user_id'], now_greg, now_jalali))
            cur.execute("SELECT SUM(amount) as total_paid FROM purchase_payments WHERE purchase_invoice_id = %s", (invoice_id,))
            total_paid = cur.fetchone()['total_paid'] or 0
            cur.execute("SELECT total_amount FROM purchase_invoices WHERE id = %s", (invoice_id,))
            invoice_total = cur.fetchone()['total_amount']
            remaining_amount = invoice_total - total_paid
            new_status = 'paid' if total_paid >= invoice_total else 'pending'
            cur.execute("UPDATE purchase_invoices SET paid_amount = %s, remaining_amount = %s, status = %s WHERE id = %s",
                        (total_paid, remaining_amount, new_status, invoice_id))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True, 'message': 'پرداخت با موفقیت ثبت شد',
                            'new_paid_amount': total_paid, 'new_remaining_amount': remaining_amount, 'new_status': new_status})
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    else:
        cur.execute('''SELECT pp.*, u.username as created_by_name FROM purchase_payments pp
            LEFT JOIN users u ON pp.created_by = u.id WHERE pp.purchase_invoice_id = %s
            ORDER BY pp.payment_date_greg DESC''', (invoice_id,))
        payments = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(p) for p in payments])


@app.route('/api/purchase_payments/<int:payment_id>/delete', methods=['POST'])
@login_required
@permission_required('payments.delete')
def delete_purchase_payment(payment_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM purchase_payments WHERE id = %s", (payment_id,))
        payment = cur.fetchone()
        if not payment:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'پرداخت یافت نشد'}), 404
        invoice_id = payment['purchase_invoice_id']
        cur.execute("DELETE FROM purchase_payments WHERE id = %s", (payment_id,))
        cur.execute("SELECT SUM(amount) as total_paid FROM purchase_payments WHERE purchase_invoice_id = %s", (invoice_id,))
        total_paid = cur.fetchone()['total_paid'] or 0
        cur.execute("SELECT total_amount FROM purchase_invoices WHERE id = %s", (invoice_id,))
        invoice_total = cur.fetchone()['total_amount']
        remaining_amount = invoice_total - total_paid
        new_status = 'paid' if total_paid >= invoice_total else 'pending'
        cur.execute("UPDATE purchase_invoices SET paid_amount = %s, remaining_amount = %s, status = %s WHERE id = %s",
                    (total_paid, remaining_amount, new_status, invoice_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'پرداخت با موفقیت حذف شد',
                        'new_paid_amount': total_paid, 'new_remaining_amount': remaining_amount, 'new_status': new_status})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/purchase_invoice/<int:invoice_id>/update_info', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_info(invoice_id):
    try:
        supplier_name = request.form['supplier_name'].strip()
        status = request.form['status']
        notes = request.form.get('notes', '').strip() or None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE purchase_invoices SET supplier_name = %s, status = %s, notes = %s WHERE id = %s",
                    (supplier_name, status, notes, invoice_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'اطلاعات فاکتور با موفقیت به‌روزرسانی شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/purchase_invoice/<int:invoice_id>/update_financial', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_financial(invoice_id):
    try:
        total_amount = float(request.form['total_amount'])
        paid_amount = float(request.form['paid_amount'])
        remaining_amount = total_amount - paid_amount
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE purchase_invoices SET total_amount = %s, paid_amount = %s, remaining_amount = %s WHERE id = %s",
                    (total_amount, paid_amount, remaining_amount, invoice_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'اطلاعات مالی با موفقیت به‌روزرسانی شد',
                        'total_amount': total_amount, 'paid_amount': paid_amount, 'remaining_amount': remaining_amount})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/purchase_invoice/<int:invoice_id>/update_items', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_items(invoice_id):
    try:
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify({'success': False, 'error': 'داده‌های آیتم‌ها نامعتبر است'}), 400
        items = data['items']
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT * FROM purchase_invoices WHERE id = %s', (invoice_id,))
        invoice = cur.fetchone()
        if not invoice:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404
        for item in items:
            item_id = item['id']
            product_name = item['product_name']
            quantity = int(item['quantity_to_buy']) if item['quantity_to_buy'] else 0
            unit_price = float(item['unit_price']) if item['unit_price'] else 0
            sku = item.get('sku', '') or None
            estimated_total_price = quantity * unit_price
            if not product_name or quantity <= 0 or unit_price <= 0:
                continue
            cur.execute('''UPDATE purchase_list_items SET product_name = %s, quantity_to_buy = %s,
                estimated_unit_price = %s, sku = %s, estimated_total_price = %s WHERE id = %s''',
                        (product_name, quantity, unit_price, sku, estimated_total_price, item_id))
        cur.execute('''SELECT SUM(COALESCE(actual_total_price, estimated_total_price)) as total
            FROM purchase_list_items WHERE purchase_list_id = %s''', (invoice['purchase_list_id'],))
        new_total = cur.fetchone()['total'] or 0
        cur.execute('SELECT paid_amount FROM purchase_invoices WHERE id = %s', (invoice_id,))
        current_paid = cur.fetchone()['paid_amount']
        remaining_amount = new_total - current_paid
        cur.execute("UPDATE purchase_invoices SET total_amount = %s, remaining_amount = %s WHERE id = %s",
                    (new_total, remaining_amount, invoice_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'new_invoice_total': new_total,
                        'new_paid_amount': current_paid, 'new_remaining_amount': remaining_amount})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/update_purchase_invoice_item', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_item():
    try:
        item_id = request.form['item_id']
        product_name = request.form.get('product_name', '').strip()
        sku = request.form.get('sku', '').strip() or None
        quantity_to_buy = int(request.form['quantity_to_buy'])
        estimated_unit_price = float(request.form['estimated_unit_price'])
        notes = request.form.get('notes', '').strip() or None
        if not product_name:
            return jsonify({'success': False, 'error': 'نام کالا الزامی است'}), 400
        if quantity_to_buy <= 0:
            return jsonify({'success': False, 'error': 'تعداد باید بیشتر از صفر باشد'}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT purchase_list_id FROM purchase_list_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404
        purchase_list_id = item['purchase_list_id']
        estimated_total_price = quantity_to_buy * estimated_unit_price
        cur.execute('''UPDATE purchase_list_items SET product_name = %s, sku = %s, quantity_to_buy = %s,
            estimated_unit_price = %s, estimated_total_price = %s, notes = %s WHERE id = %s''',
                    (product_name, sku, quantity_to_buy, estimated_unit_price, estimated_total_price, notes, item_id))
        cur.execute('''SELECT COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items WHERE purchase_list_id = %s''', (purchase_list_id,))
        new_total = cur.fetchone()['total_amount']
        cur.execute("UPDATE purchase_invoices SET total_amount = %s, remaining_amount = %s - paid_amount WHERE purchase_list_id = %s",
                    (new_total, new_total, purchase_list_id))
        conn.commit()
        cur.execute("SELECT total_amount, paid_amount, remaining_amount FROM purchase_invoices WHERE purchase_list_id = %s", (purchase_list_id,))
        updated_invoice = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'آیتم با موفقیت به‌روزرسانی شد',
                        'new_invoice_total': new_total, 'new_paid_amount': updated_invoice['paid_amount'],
                        'new_remaining_amount': updated_invoice['remaining_amount']})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete_purchase_invoice_item', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_delete_purchase_invoice_item():
    try:
        item_id = request.form['item_id']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT purchase_list_id FROM purchase_list_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404
        purchase_list_id = item['purchase_list_id']
        cur.execute("DELETE FROM purchase_list_items WHERE id = %s", (item_id,))
        cur.execute('''SELECT COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items WHERE purchase_list_id = %s''', (purchase_list_id,))
        new_total = cur.fetchone()['total_amount']
        cur.execute("UPDATE purchase_invoices SET total_amount = %s, remaining_amount = %s - paid_amount WHERE purchase_list_id = %s",
                    (new_total, new_total, purchase_list_id))
        conn.commit()
        cur.execute("SELECT total_amount, paid_amount, remaining_amount FROM purchase_invoices WHERE purchase_list_id = %s", (purchase_list_id,))
        updated_invoice = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'آیتم با موفقیت حذف شد',
                        'new_invoice_total': new_total, 'new_paid_amount': updated_invoice['paid_amount'],
                        'new_remaining_amount': updated_invoice['remaining_amount']})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# گزارش‌ها
# ============================================================
@app.route('/reports')
@login_required
@permission_required('reports.view')
def reports():
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    today_jalali = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m/%d')
    current_month = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m')
    current_year = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y')

    def get_summary(prefix, value):
        cur.execute('''
            SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as invoice_count
            FROM invoices
            WHERE SUBSTR(date_jalali, 1, %s) = %s
            AND (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        ''', (len(prefix), value))
        row = cur.fetchone()
        return (row['total_sales'] or 0, row['total_profit'] or 0, row['invoice_count'] or 0)

    today_sales, today_profit, today_invoice_count = get_summary('10', today_jalali)
    month_sales, month_profit, month_invoice_count = get_summary('7', current_month)
    year_sales, year_profit, year_invoice_count = get_summary('4', current_year)

    cur.execute('''
        SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as total_invoices
        FROM invoices
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
    ''')
    total_row = cur.fetchone()
    total_sales = total_row['total_sales'] or 0
    total_profit = total_row['total_profit'] or 0
    total_invoices = total_row['total_invoices'] or 0

    cur.execute('''
        SELECT SUBSTR(date_jalali, 1, 10) as date_jalali, SUM(total_amount) as total_sales,
               SUM(total_profit) as total_profit, COUNT(id) as invoice_count
        FROM invoices
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        GROUP BY SUBSTR(date_jalali, 1, 10)
        ORDER BY MAX(date_greg) DESC LIMIT 30
    ''')
    daily_sales = cur.fetchall()

    cur.execute('''
        SELECT SUBSTR(date_jalali, 1, 7) as month, SUM(total_amount) as total_sales,
               SUM(total_profit) as total_profit, COUNT(id) as invoice_count
        FROM invoices
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        GROUP BY SUBSTR(date_jalali, 1, 7)
        ORDER BY MAX(date_greg) DESC
    ''')
    monthly_sales = cur.fetchall()

    cur.execute('''SELECT COUNT(*) as count FROM invoices i
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount''')
    debtors_count = cur.fetchone()['count'] or 0

    cur.execute('''SELECT SUM(i.total_amount - COALESCE(p.paid_amount, 0)) as total_debt
        FROM invoices i
        LEFT JOIN (SELECT invoice_id, SUM(amount) as paid_amount FROM payments GROUP BY invoice_id) p
        ON i.id = p.invoice_id
        WHERE COALESCE(p.paid_amount, 0) < i.total_amount''')
    total_debt = cur.fetchone()['total_debt'] or 0
    cur.close()
    conn.close()

    current_datetime = jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M')
    today_date = jdatetime.datetime.now().strftime('%Y/%m/%d')
    return render_template('reports.html',
                           daily_sales=daily_sales, monthly_sales=monthly_sales,
                           today_sales=today_sales, today_profit=today_profit,
                           today_invoice_count=today_invoice_count, today_paid_count=today_invoice_count,
                           month_sales=month_sales, month_profit=month_profit,
                           month_invoice_count=month_invoice_count, month_paid_count=month_invoice_count,
                           year_sales=year_sales, year_profit=year_profit,
                           year_invoice_count=year_invoice_count,
                           total_sales=total_sales, total_profit=total_profit, total_invoices=total_invoices,
                           debtors_count=debtors_count, total_debt=total_debt,
                           current_datetime=current_datetime, today_date=today_date,
                           current_month=current_month, current_year=current_year)


@app.route('/zakat')
@login_required
@permission_required('reports.zakat')
def zakat_calculator():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE stock > 0")
    products_list = cur.fetchall()
    total_value_sell = sum(p['sell_price'] * p['stock'] for p in products_list)
    total_value_buy = sum(p['buy_price_tomans'] * p['stock'] for p in products_list)
    zakat_amount = total_value_sell * 0.025
    cur.close()
    conn.close()
    return render_template('zakat.html', products=products_list, total_value_sell=total_value_sell,
                           total_value_buy=total_value_buy, zakat_amount=zakat_amount)


@app.route('/debtors')
@login_required
@permission_required('reports.debtors')
def debtors():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT i.*, (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount
        FROM invoices i
        WHERE (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount
        ORDER BY i.date_greg DESC
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    total_debt = 0
    debtors_list = []
    for d in rows:
        remaining = d['total_amount'] - d['paid_amount']
        total_debt += remaining
        debtors_list.append({
            'id': d['id'], 'customer_name': d['customer_name'], 'customer_phone': d['customer_phone'],
            'date_jalali': d['date_jalali'], 'total_amount': d['total_amount'],
            'paid_amount': d['paid_amount'], 'remaining': remaining
        })
    return render_template('debtors.html', debtors=debtors_list, total_debt=total_debt)


@app.route('/supplier_debts')
@login_required
@permission_required('purchase.suppliers')
def supplier_debts():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT pi.supplier_name, SUM(pi.total_amount - pi.paid_amount) as total_debt, COUNT(pi.id) as invoice_count
        FROM purchase_invoices pi
        WHERE pi.status = 'pending' OR pi.remaining_amount > 0
        GROUP BY pi.supplier_name
        HAVING SUM(pi.total_amount - pi.paid_amount) > 0
        ORDER BY total_debt DESC
    ''')
    debts = cur.fetchall()
    cur.execute('''SELECT SUM(total_amount - paid_amount) as total_debt FROM purchase_invoices
        WHERE status = 'pending' OR remaining_amount > 0''')
    total_debt = cur.fetchone()['total_debt'] or 0
    cur.close()
    conn.close()
    return render_template('supplier_debts.html', debts=debts, total_debt=total_debt)


@app.route('/stock_report')
@login_required
@permission_required('reports.stock')
def stock_report():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE stock = 0 ORDER BY name")
    out_of_stock = cur.fetchall()
    cur.execute("SELECT * FROM products WHERE stock > 0 AND stock < 5 ORDER BY stock")
    low_stock = cur.fetchall()
    cur.execute("SELECT * FROM products WHERE stock >= 5 ORDER BY name")
    normal_stock = cur.fetchall()
    cur.execute("SELECT * FROM products WHERE sales_count > 0 ORDER BY sales_count DESC LIMIT 20")
    top_selling = cur.fetchall()
    cur.execute("SELECT * FROM purchase_lists WHERE status = 'active' ORDER BY created_date_greg DESC")
    purchase_lists_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('stock_report.html', out_of_stock=out_of_stock, low_stock=low_stock,
                           normal_stock=normal_stock, top_selling=top_selling, purchase_lists=purchase_lists_list)


@app.route('/date_range_report')
@login_required
@permission_required('reports.date_range')
def date_range_report():
    now = datetime.now()
    current_datetime = jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M')
    return render_template('date_range_report.html', current_datetime=current_datetime)


@app.route('/api/date_range_sales')
@login_required
@permission_required('reports.date_range')
def api_date_range_sales():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if not start_date or not end_date:
        return jsonify({'error': 'لطفاً هر دو تاریخ را انتخاب کنید'}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            SELECT COUNT(*) as invoice_count, SUM(total_amount) as total_sales,
                   SUM(total_profit) as total_profit, AVG(total_amount) as avg_sale
            FROM invoices
            WHERE SUBSTR(date_jalali, 1, 10) BETWEEN %s AND %s
            AND (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        ''', (start_date, end_date))
        summary = cur.fetchone()
        cur.execute('''
            SELECT SUBSTR(date_jalali, 1, 10) as date_jalali, COUNT(*) as invoice_count,
                   SUM(total_amount) as total_sales, SUM(total_profit) as total_profit
            FROM invoices
            WHERE SUBSTR(date_jalali, 1, 10) BETWEEN %s AND %s
            AND (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
            GROUP BY SUBSTR(date_jalali, 1, 10) ORDER BY date_jalali
        ''', (start_date, end_date))
        daily_sales = cur.fetchall()
        cur.execute('''
            SELECT i.id, i.customer_name, i.date_jalali, i.total_amount, i.total_profit,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount
            FROM invoices i
            WHERE SUBSTR(i.date_jalali, 1, 10) BETWEEN %s AND %s
            AND (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount
            ORDER BY i.total_amount DESC LIMIT 10
        ''', (start_date, end_date))
        top_invoices = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            'summary': {'invoice_count': summary['invoice_count'] or 0,
                        'total_sales': summary['total_sales'] or 0,
                        'total_profit': summary['total_profit'] or 0,
                        'avg_sale': summary['avg_sale'] or 0},
            'daily_sales': [dict(d) for d in daily_sales],
            'top_invoices': [dict(i) for i in top_invoices],
            'date_range': {'start_date': start_date, 'end_date': end_date}
        })
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({'error': str(e)}), 500


# ============================================================
# هزینه‌های مغازه
# ============================================================
@app.route('/shop_expenses')
@login_required
@permission_required('reports.expenses')
def shop_expenses():
    expense_categories = [
        'اجاره مغازه', 'قبض برق', 'قبض آب', 'قبض گاز', 'قبض تلفن', 'اینترنت',
        'مالیات', 'عوارض', 'حقوق شاگرد', 'حقوق فروشنده', 'صبحانه', 'ناهار',
        'باربری', 'حمل و نقل', 'بنزین', 'تعمیرات', 'لوازم اداری', 'بسته‌بندی',
        'بازاریابی', 'سایر'
    ]
    return render_template('shop_expenses.html', categories=expense_categories)


@app.route('/api/shop_expenses', methods=['GET', 'POST'])
@login_required
@permission_required('reports.expenses')
def api_shop_expenses():
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            expense_date = request.form['expense_date']
            category = request.form['category']
            description = request.form['description']
            amount = float(request.form['amount'].replace(',', '').replace('٬', ''))
            payment_method = request.form.get('payment_method', 'cash')
            receipt_number = request.form.get('receipt_number', '').strip() or None
            notes = request.form.get('notes', '').strip() or None
            year, month, day = map(int, expense_date.split('/'))
            gregorian_date = jdatetime.date(year, month, day).togregorian()
            now_greg = datetime.now().isoformat()
            now_jalali = jdatetime.datetime.now().strftime('%Y/%m/%d %H:%M')
            cur.execute('''
                INSERT INTO shop_expenses
                (expense_date_jalali, expense_date_greg, category, description, amount,
                 payment_method, receipt_number, notes, created_by, created_date_greg, created_date_jalali)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (expense_date, gregorian_date.isoformat(), category, description, amount,
                  payment_method, receipt_number, notes, session['user_id'], now_greg, now_jalali))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت ثبت شد'})
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    else:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        category = request.args.get('category', '')
        query = '''SELECT se.*, u.username as created_by_name FROM shop_expenses se
            LEFT JOIN users u ON se.created_by = u.id WHERE 1=1'''
        params = []
        if start_date:
            query += ' AND se.expense_date_jalali >= %s'
            params.append(start_date)
        if end_date:
            query += ' AND se.expense_date_jalali <= %s'
            params.append(end_date)
        if category:
            query += ' AND se.category = %s'
            params.append(category)
        query += ' ORDER BY se.expense_date_greg DESC'
        cur.execute(query, params)
        expenses = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(e) for e in expenses])


@app.route('/api/shop_expenses/<int:expense_id>', methods=['PUT', 'DELETE'])
@login_required
@permission_required('reports.expenses')
def api_single_expense(expense_id):
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'PUT':
        try:
            data = request.get_json()
            year, month, day = map(int, data['expense_date'].split('/'))
            gregorian_date = jdatetime.date(year, month, day).togregorian()
            cur.execute('''
                UPDATE shop_expenses
                SET expense_date_jalali = %s, expense_date_greg = %s, category = %s,
                    description = %s, amount = %s, payment_method = %s, receipt_number = %s, notes = %s
                WHERE id = %s
            ''', (data['expense_date'], gregorian_date.isoformat(), data['category'],
                  data['description'], data['amount'], data.get('payment_method', 'cash'),
                  data.get('receipt_number'), data.get('notes'), expense_id))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت ویرایش شد'})
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    elif request.method == 'DELETE':
        try:
            cur.execute('DELETE FROM shop_expenses WHERE id = %s', (expense_id,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت حذف شد'})
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shop_expenses_summary')
@login_required
@permission_required('reports.expenses')
def api_shop_expenses_summary():
    conn = get_db()
    cur = conn.cursor()
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    monthly_query = '''SELECT SUBSTR(expense_date_jalali, 1, 7) as month, SUM(amount) as total_amount,
        COUNT(*) as expense_count FROM shop_expenses WHERE 1=1'''
    monthly_params = []
    if start_date:
        monthly_query += ' AND expense_date_jalali >= %s'
        monthly_params.append(start_date)
    if end_date:
        monthly_query += ' AND expense_date_jalali <= %s'
        monthly_params.append(end_date)
    monthly_query += ' GROUP BY SUBSTR(expense_date_jalali, 1, 7) ORDER BY month DESC'
    cur.execute(monthly_query, monthly_params)
    monthly_summary = cur.fetchall()

    category_query = '''SELECT category, SUM(amount) as total_amount, COUNT(*) as expense_count
        FROM shop_expenses WHERE 1=1'''
    category_params = []
    if start_date:
        category_query += ' AND expense_date_jalali >= %s'
        category_params.append(start_date)
    if end_date:
        category_query += ' AND expense_date_jalali <= %s'
        category_params.append(end_date)
    category_query += ' GROUP BY category ORDER BY total_amount DESC'
    cur.execute(category_query, category_params)
    category_summary = cur.fetchall()

    total_query = 'SELECT SUM(amount) as total FROM shop_expenses WHERE 1=1'
    total_params = []
    if start_date:
        total_query += ' AND expense_date_jalali >= %s'
        total_params.append(start_date)
    if end_date:
        total_query += ' AND expense_date_jalali <= %s'
        total_params.append(end_date)
    cur.execute(total_query, total_params)
    total_expenses = cur.fetchone()['total'] or 0
    cur.close()
    conn.close()
    return jsonify({'monthly_summary': [dict(r) for r in monthly_summary],
                    'category_summary': [dict(r) for r in category_summary],
                    'total_expenses': total_expenses})


# ============================================================
# پشتیبان‌گیری
# ============================================================
@app.route('/backup')
@login_required
@permission_required('backup.manage')
def backup_page():
    backup_dir = Path('backup')
    backup_dir.mkdir(exist_ok=True)
    backup_files_db = []
    for file_path in backup_dir.glob('backup_*.db'):
        backup_files_db.append({'name': file_path.name, 'size': file_path.stat().st_size // 1024})
    backup_files_db.sort(key=lambda x: x['name'], reverse=True)
    backup_files_full = []
    for file_path in backup_dir.glob('full_backup_*.zip'):
        backup_files_full.append({'name': file_path.name, 'size': file_path.stat().st_size // (1024 * 1024)})
    backup_files_full.sort(key=lambda x: x['name'], reverse=True)
    exec_info = get_main_executable_info()
    return render_template('backup.html', backup_files_db=backup_files_db,
                           backup_files_full=backup_files_full, exec_info=exec_info)


@app.route('/do_full_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def do_full_backup():
    try:
        timestamp = jdatetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_name = f"full_backup_{timestamp}.zip"
        zip_path = os.path.join('backup', zip_name)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            project_root = Path('.')
            for file_path in project_root.rglob('*'):
                if file_path.is_file():
                    if any(exclude in str(file_path) for exclude in ['__pycache__', '.git', '.vscode', 'venv', 'env', '.pyc']):
                        continue
                    if 'backup' in str(file_path) and str(file_path).endswith('.zip'):
                        continue
                    arcname = file_path.relative_to(project_root)
                    zipf.write(file_path, arcname)
        flash(f'پشتیبان کامل با موفقیت ایجاد شد: {zip_name}', 'success')
    except Exception as e:
        flash(f'خطا در ایجاد پشتیبان کامل: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))


@app.route('/download_backup/<filename>')
@login_required
@permission_required('backup.manage')
def download_backup(filename):
    return send_from_directory('backup', filename, as_attachment=True)


@app.route('/api/flash_drives')
@login_required
@permission_required('backup.manage')
def api_flash_drives():
    try:
        return jsonify({'success': True, 'flash_drives': get_flash_drives()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/copy_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def copy_to_flash():
    flash('این قابلیت روی سرور پشتیبانی نمی‌شود.', 'warning')
    return redirect(url_for('backup_page'))


@app.route('/copy_database_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def copy_database_to_flash():
    flash('این قابلیت روی سرور پشتیبانی نمی‌شود.', 'warning')
    return redirect(url_for('backup_page'))


@app.route('/auto_copy_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def auto_copy_to_flash_route():
    flash('این قابلیت روی سرور پشتیبانی نمی‌شود.', 'warning')
    return redirect(url_for('backup_page'))


@app.route('/do_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def do_backup():
    flash('پشتیبان‌گیری فایل SQLite روی سرور غیرفعال است. از دیتابیس Neon پشتیبان بگیرید.', 'warning')
    return redirect(url_for('backup_page'))


# ============================================================
# ورود اکسل
# ============================================================
@app.route('/import_excel', methods=['POST'])
@login_required
@permission_required('products.import')
def import_excel():
    if 'file' not in request.files:
        flash('هیچ فایلی انتخاب نشده است.', 'danger')
        return redirect(url_for('products'))
    file = request.files['file']
    if file.filename == '':
        flash('هیچ فایلی انتخاب نشده است.', 'danger')
        return redirect(url_for('products'))
    if not file.filename.lower().endswith(('.xls', '.xlsx', '.csv')):
        flash('فرمت فایل نامعتبر است. فقط XLS, XLSX یا CSV مجاز است.', 'danger')
        return redirect(url_for('products'))
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    conn = get_db()
    cur = conn.cursor()
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(filepath, encoding='utf-8-sig')
        else:
            df = pd.read_excel(filepath)
        required_cols = ['nam', 'kharid', 'fi']
        if not all(col in df.columns for col in required_cols):
            raise ValueError("ستون‌های الزامی (nam, kharid, fi) موجود نیستند.")
        added_count = 0
        for _, row in df.iterrows():
            name = str(row['nam']).strip()
            if not name:
                continue
            sku = str(row.get('shenase', '')).strip() or None
            buy_tomans = clean_numeric_value(row['kharid'])
            sell_price = clean_numeric_value(row['fi'])
            usd = clean_numeric_value(row.get('dolar', 0)) if 'dolar' in row else None
            warehouse = str(row.get('anbar', '')).strip() or None
            stock = int(clean_numeric_value(row.get('mojoodi', 0))) if 'mojoodi' in row else 0
            description = str(row.get('tozih', '')).strip() or None
            cur.execute('''
                INSERT INTO products (name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description))
            added_count += 1
        conn.commit()
        flash(f'{added_count} کالا با موفقیت اضافه شد.', 'success')
    except ValueError as ve:
        conn.rollback()
        flash(f'خطا در پردازش فایل: {str(ve)}', 'danger')
    except Exception as e:
        conn.rollback()
        flash(f'خطا در آپلود: {str(e)}', 'danger')
    finally:
        cur.close()
        conn.close()
        if os.path.exists(filepath):
            os.remove(filepath)
    return redirect(url_for('products'))


# ============================================================
# راه‌اندازی
# ============================================================
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
