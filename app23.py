from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, session
import sqlite3
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

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-before-deploying')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('backup', exist_ok=True)
DB_PATH = os.environ.get('SQLITE_PATH', 'database.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
USE_POSTGRES = bool(DATABASE_URL)

# دکوراتورهای کنترل دسترسی - باید در ابتدا تعریف شوند
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
            # مدیر سیستم به همه چیز دسترسی دارد
            if session.get('role') == 'admin':
                return f(*args, **kwargs)
            # بررسی دسترسی کاربر
            user_permissions = session.get('permissions', '')
            if user_permissions == 'all' or permission in user_permissions.split(','):
                return f(*args, **kwargs)
            flash('دسترسی غیرمجاز', 'danger')
            return redirect(url_for('index'))
        return decorated_function
    return decorator

# تابع برای پیدا کردن مسیر و نام فایل اجرایی اصلی
def get_main_executable_info():
    """پیدا کردن مسیر و نام فایل اصلی که برنامه از آن اجرا شده"""
    # اگر با pyinstaller بسته‌بندی شده باشد
    if getattr(sys, 'frozen', False):
        executable_path = sys.executable
        executable_dir = os.path.dirname(executable_path)
        executable_name = os.path.basename(executable_path)
    else:
        # اگر مستقیماً با پایتون اجرا شده باشد
        executable_path = os.path.abspath(sys.argv[0])
        executable_dir = os.path.dirname(executable_path)
        executable_name = os.path.basename(executable_path)
    return {
        'path': executable_path,
        'dir': executable_dir,
        'name': executable_name,
        'name_without_ext': os.path.splitext(executable_name)[0]
    }

# تابع برای کپی خودکار روی فلش
def auto_copy_to_flash():
    """کپی خودکار فایل اجرایی و تمام فایل‌های برنامه روی فلش‌های متصل"""
    try:
        flash_drives = get_flash_drives()
        if not flash_drives:
            return False, "هیچ فلش مموری یافت نشد"
        exec_info = get_main_executable_info()
        flash_drive = flash_drives[0]
        # نام پوشه مقصد با نام فایل اجرایی
        target_folder_name = f"SwedYadak_{exec_info['name_without_ext']}"
        target_path = os.path.join(flash_drive['mountpoint'], target_folder_name)
        # ایجاد پوشه مقصد
        os.makedirs(target_path, exist_ok=True)
        # لیست پوشه‌های مهم که باید حتماً کپی شوند
        important_folders = ['templates', 'static']
        # کپی تمام فایل‌های موجود در پوشه اصلی
        copied_files = []
        # اول: کپی پوشه‌های مهم
        for folder in important_folders:
            source_folder = os.path.join(exec_info['dir'], folder)
            target_folder = os.path.join(target_path, folder)
            if os.path.exists(source_folder):
                shutil.copytree(source_folder, target_folder, dirs_exist_ok=True)
                copied_files.append(f"{folder}/")
                print(f"پوشه {folder} کپی شد")
        # سپس: کپی فایل‌های دیگر
        for item in os.listdir(exec_info['dir']):
            item_path = os.path.join(exec_info['dir'], item)
            target_item_path = os.path.join(target_path, item)
            # اگر قبلاً کپی شده، رد شو
            if item in important_folders:
                continue
            # نادیده گرفتن فایل‌های موقت و پوشه‌های خاص
            exclude_patterns = ['__pycache__', '.git', '.vscode', 'venv', 'env', 'temp', 'tmp']
            if any(exclude in item.lower() for exclude in exclude_patterns):
                continue
            try:
                if os.path.isfile(item_path):
                    shutil.copy2(item_path, target_item_path)
                    copied_files.append(item)
                elif os.path.isdir(item_path):
                    # پوشه‌های دیگر (به جز موارد مهم)
                    other_important = ['uploads']
                    if item in other_important:
                        shutil.copytree(item_path, target_item_path, dirs_exist_ok=True)
                        copied_files.append(f"{item}/")
            except Exception as e:
                print(f"خطا در کپی {item}: {e}")

        # ایجاد فایل اطلاعات
        info_content = f"""کپی خودکار سوئد یدک
تاریخ ایجاد: {browser_jalali_now().strftime('%Y/%m/%d %H:%M:%S')}
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

def get_flash_drives():
    """شناسایی فلش‌های مموری متصل به سیستم"""
    flash_drives = []
    if platform.system() == "Windows":
        # برای ویندوز
        for partition in psutil.disk_partitions():
            if 'removable' in partition.opts:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    # فقط فلش‌هایی که فضای کافی دارند (بیش از 100MB آزاد)
                    if usage.free > 100 * 1024 * 1024:
                        drive_info = {
                            'device': partition.device,
                            'mountpoint': partition.mountpoint,
                            'total_size_gb': round(usage.total / (1024**3), 1),
                            'free_space_gb': round(usage.free / (1024**3), 1),
                            'free_space_mb': round(usage.free / (1024**2), 1),
                            'file_system': partition.fstype
                        }
                        flash_drives.append(drive_info)
                except (PermissionError, OSError):
                    continue
    else:
        # برای لینوکس و مک
        for partition in psutil.disk_partitions():
            if partition.fstype and ('vfat' in partition.fstype.lower() or 
                                   'ntfs' in partition.fstype.lower() or 
                                   'exfat' in partition.fstype.lower() or
                                   'fuseblk' in partition.fstype.lower()):
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    # فقط فلش‌هایی که فضای کافی دارند
                    if usage.free > 100 * 1024 * 1024:
                        drive_info = {
                            'device': partition.device,
                            'mountpoint': partition.mountpoint,
                            'total_size_gb': round(usage.total / (1024**3), 1),
                            'free_space_gb': round(usage.free / (1024**3), 1),
                            'free_space_mb': round(usage.free / (1024**2), 1),
                            'file_system': partition.fstype
                        }
                        flash_drives.append(drive_info)
                except (PermissionError, OSError):
                    continue
    return flash_drives

@app.route('/api/flash_drives')
@login_required
@permission_required('backup.manage')
def api_flash_drives():
    """API برای دریافت لیست فلش‌های مموری"""
    try:
        flash_drives = get_flash_drives()
        return jsonify({'success': True, 'flash_drives': flash_drives})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/copy_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def copy_to_flash():
    """کپی تمام فایل‌های برنامه روی فلش مموری"""
    try:
        flash_drives = get_flash_drives()
        if not flash_drives:
            flash('هیچ فلش مموری یافت نشد. لطفاً فلش را متصل کنید و دوباره تلاش کنید.', 'warning')
            return redirect(url_for('backup_page'))

        # استفاده از اولین فلش پیدا شده
        flash_drive = flash_drives[0]
        exec_info = get_main_executable_info()
        # نام پوشه مقصد با نام فایل اجرایی
        target_folder_name = f"SwedYadak_{exec_info['name_without_ext']}"
        target_path = os.path.join(flash_drive['mountpoint'], target_folder_name)
        # ایجاد پوشه مقصد
        os.makedirs(target_path, exist_ok=True)
        # کپی تمام فایل‌های موجود در پوشه اصلی
        copied_files = []
        for item in os.listdir(exec_info['dir']):
            item_path = os.path.join(exec_info['dir'], item)
            target_item_path = os.path.join(target_path, item)
            # نادیده گرفتن فایل‌های موقت و پوشه‌های خاص
            if any(exclude in item.lower() for exclude in ['__pycache__', '.git', '.vscode', 'venv', 'env', 'temp', 'tmp', 'backup']):
                continue
            try:
                if os.path.isfile(item_path):
                    shutil.copy2(item_path, target_item_path)
                    copied_files.append(item)
                elif os.path.isdir(item_path) and item not in ['backup', 'uploads']:
                    shutil.copytree(item_path, target_item_path, dirs_exist_ok=True)
                    copied_files.append(f"{item}/")
            except Exception as e:
                print(f"خطا در کپی {item}: {e}")

        # ایجاد فایل راهنما
        readme_content = f"""پشتیبان گیری سوئد یدک
تاریخ ایجاد: {browser_jalali_now().strftime('%Y/%m/%d %H:%M')}
نام فایل اجرایی: {exec_info['name']}
مسیر اصلی: {exec_info['dir']}
این پوشه حاوی فایل‌های برنامه سوئد یدک می‌باشد.
فایل‌های کپی شده ({len(copied_files)} مورد):
{chr(10).join(f'- {file}' for file in sorted(copied_files))}
دستورات اجرای برنامه:
اگر {exec_info['name']} فایل اجرایی است:
• دوبار کلیک روی آن
اگر {exec_info['name']} فایل پایتون است:
1. پایتون باید نصب باشد
2. دستور: pip install -r requirements.txt
3. دستور: python "{exec_info['name']}"
4. مرورگر: http://localhost:5000
اطلاعات سیستم:
سیستم عامل: {platform.system()}
پایتون: {sys.version}
"""
        with open(os.path.join(target_path, 'README.txt'), 'w', encoding='utf-8') as f:
            f.write(readme_content)

        flash(f'فایل‌های برنامه با موفقیت روی فلش مموری کپی شدند. ({len(copied_files)} فایل) مسیر: {target_path}', 'success')
    except Exception as e:
        flash(f'خطا در کپی روی فلش: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))

@app.route('/copy_database_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def copy_database_to_flash():
    """کپی فقط دیتابیس روی فلش مموری"""
    if USE_POSTGRES:
        flash('در Render به فلش متصل دسترسی وجود ندارد. ابتدا فایل نسخه آفلاین را دانلود و سپس روی فلش ذخیره کنید.', 'info')
        return redirect(url_for('backup_page'))
    try:
        flash_drives = get_flash_drives()
        if not flash_drives:
            flash('هیچ فلش مموری یافت نشد. لطفاً فلش را متصل کنید و دوباره تلاش کنید.', 'warning')
            return redirect(url_for('backup_page'))

        flash_drive = flash_drives[0]
        exec_info = get_main_executable_info()
        # نام پوشه مقصد با نام فایل اجرایی
        target_folder_name = f"SwedYadak_DB_{exec_info['name_without_ext']}"
        target_path = os.path.join(flash_drive['mountpoint'], target_folder_name)
        os.makedirs(target_path, exist_ok=True)
        # کپی دیتابیس
        db_backup_name = f"database_backup_{browser_jalali_now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(DB_PATH, os.path.join(target_path, db_backup_name))
        # همچنین یک کپی با نام ساده
        shutil.copy2(DB_PATH, os.path.join(target_path, 'database.db'))

        # ایجاد فایل اطلاعات
        info_content = f"""پشتیبان دیتابیس سوئد یدک
تاریخ ایجاد: {browser_jalali_now().strftime('%Y/%m/%d %H:%M')}
نام فایل اجرایی اصلی: {exec_info['name']}
این پوشه فقط حاوی فایل دیتابیس برنامه می‌باشد.
برای استفاده:
1. فایل database.db را در پوشه برنامه اصلی کپی کنید
2. برنامه را اجرا کنید
فایل اصلی برنامه: {exec_info['name']}
"""
        with open(os.path.join(target_path, 'DB_README.txt'), 'w', encoding='utf-8') as f:
            f.write(info_content)

        flash(f'دیتابیس با موفقیت روی فلش مموری کپی شد. مسیر: {target_path}', 'success')
    except Exception as e:
        flash(f'خطا در کپی دیتابیس: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))

@app.route('/auto_copy_to_flash', methods=['POST'])
@login_required
@permission_required('backup.manage')
def auto_copy_to_flash_route():
    """کپی خودکار فایل اجرایی روی فلش"""
    try:
        success, message = auto_copy_to_flash()
        if success:
            flash(f'کپی خودکار موفق: {message}', 'success')
        else:
            flash(f'کپی خودکار ناموفق: {message}', 'warning')
    except Exception as e:
        flash(f'خطا در کپی خودکار: {str(e)}', 'danger')
    return redirect(url_for('backup_page'))

# اجرای کپی خودکار هنگام راه‌اندازی برنامه
def startup_auto_backup():
    """اجرای کپی خودکار هنگام راه‌اندازی برنامه"""
    try:
        # فقط اگر فلش متصل باشد کپی انجام شود
        flash_drives = get_flash_drives()
        if flash_drives:
            success, message = auto_copy_to_flash()
            if success:
                print(f"کپی خودکار هنگام راه‌اندازی: {message}")
            else:
                print(f"کپی خودکار ناموفق: {message}")
    except Exception as e:
        print(f"خطا در کپی خودکار راه‌اندازی: {e}")

# فیلتر سفارشی برای تاریخ شمسی
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
    """تبدیل ارقام فارسی و عربی به ارقام لاتین و حفظ نقطه و کاما"""
    if not s:
        return s
    trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return s.translate(trans)

def clean_numeric_value(val):
    """
    تبدیل هر مقداری (رشته، عدد، NaN) به یک عدد اعشاری معتبر.
    کاراکترهای غیرعددی (غیر از اعداد، نقطه و منفی) پاک می‌شوند.
    """
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

def get_db():
    """Return an adapter connection; never expose a raw psycopg2 connection to routes."""
    if USE_POSTGRES:
        from db_backend import connect_postgres, Connection as PostgresConnection
        db = connect_postgres(DATABASE_URL)
        # Some deployments may still have an older db_backend.py where
        # connect_postgres() returns psycopg2's raw connection. Always normalize
        # it here, even if that older connection happens to expose other methods.
        if isinstance(db, PostgresConnection):
            return db
        raw = getattr(db, 'raw', db)
        return PostgresConnection(raw)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def browser_now():
    from flask import request, has_request_context
    from datetime import timedelta
    if has_request_context():
        try:
            # JavaScript sends the inverse of getTimezoneOffset.
            return datetime.utcnow() + timedelta(minutes=int(request.cookies.get('tz_offset', '0')))
        except Exception:
            pass
    return datetime.now()

def browser_jalali_now():
    return jdatetime.datetime.fromgregorian(datetime=browser_now())

def normalize_persian_text(text):
    """تبدیل حروف فارسی به فرم استاندارد برای جستجو"""
    if not text:
        return text
    # تبدیل حروف مختلف به فرم استاندارد
    replacements = {
        'ي': 'ی',  # ی عربی به ی فارسی
        'ك': 'ک',  # ک عربی به ک فارسی
        'ة': 'ه',  # ة عربی به ه فارسی
        'ؤ': 'و',  # ؤ به و
        'إ': 'ا',  # إ به ا
        'أ': 'ا',  # أ به ا
        'ٱ': 'ا',  # ٱ به ا
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

@app.after_request
def after_request(response):
    """افزودن هدرهای CORS برای همه درخواست‌ها"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

def init_db():
    if USE_POSTGRES:
        from db_backend import init_postgres_schema
        init_postgres_schema(DATABASE_URL)
        conn = get_db()
        if not conn.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'").fetchone()[0]:
            ph = generate_password_hash(os.environ.get('DEFAULT_ADMIN_PASSWORD', '123456'))
            conn.execute('INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         ('مدیر سیستم', 'admin', ph, 'admin', 'all', browser_now().isoformat(), browser_jalali_now().strftime('%Y/%m/%d %H:%M')))
        from db_backend import seed_permissions
        seed_permissions(conn)
        conn.commit(); conn.close()
        return
    conn = get_db()
    # کدهای موجود برای ایجاد جداول دیگر
    cursor = conn.execute("PRAGMA table_info(products)")
    cols = {row[1]: row for row in cursor.fetchall()}
    needs_rebuild = False
    if 'sku' in cols:
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE name='products'")
        sql = cursor.fetchone()
        if sql and 'NOT NULL' in sql[0] and 'sku' in sql[0]:
            needs_rebuild = True
    else:
        needs_rebuild = True

    if needs_rebuild:
        old_data = []
        if 'id' in cols:
            try:
                old_data = conn.execute("SELECT * FROM products").fetchall()
            except:
                pass
        conn.execute("DROP TABLE IF EXISTS products")
        conn.execute('''
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT,
                buy_price_tomans REAL NOT NULL,
                buy_price_usd REAL,
                sell_price REAL NOT NULL,
                warehouse TEXT,
                stock INTEGER DEFAULT 0,
                description TEXT,
                sales_count INTEGER DEFAULT 0
            )
        ''')
        if old_data:
            for row in old_data:
                sku_val = row[2] if row[2] != '' else None
                cursor = conn.execute('''
                    INSERT INTO products (id, name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description, sales_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (row[0], row[1], sku_val, row[3], row[4], row[5], row[6], row[7], row[8]))
            max_id = conn.execute("SELECT MAX(id) FROM products").fetchone()[0] or 0
            conn.execute(f"UPDATE sqlite_sequence SET seq = {max_id} WHERE name = 'products'")

    try:
        conn.execute("ALTER TABLE products ADD COLUMN sales_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # جداول موجود
    conn.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            date_jalali TEXT,
            date_greg TEXT,
            total_amount REAL,
            total_profit REAL,
            discount_amount REAL DEFAULT 0,
            discount_percent REAL DEFAULT 0,
            status TEXT DEFAULT 'active'
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            sku TEXT,
            quantity INTEGER,
            unit_price REAL,
            profit_per_unit REAL,
            returned INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_date_jalali TEXT,
            payment_date_greg TEXT,
            description TEXT,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS purchase_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_name TEXT NOT NULL,
            created_date_jalali TEXT,
            created_date_greg TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT,
            estimated_total REAL DEFAULT 0,
            actual_total REAL DEFAULT 0
        )
    ''')
    # 🔽 اصلاح جدول purchase_list_items - product_id می‌تواند NULL باشد
    conn.execute('''
        CREATE TABLE IF NOT EXISTS purchase_list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_list_id INTEGER NOT NULL,
            product_id INTEGER,  -- تغییر از NOT NULL به NULLable
            product_name TEXT NOT NULL,
            sku TEXT,
            current_stock INTEGER DEFAULT 0,
            quantity_to_buy INTEGER NOT NULL,
            estimated_unit_price REAL,
            actual_unit_price REAL,
            estimated_total_price REAL,
            actual_total_price REAL,
            notes TEXT,
            FOREIGN KEY(purchase_list_id) REFERENCES purchase_lists(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS purchase_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_list_id INTEGER NOT NULL,
            supplier_name TEXT NOT NULL,
            invoice_date_jalali TEXT,
            invoice_date_greg TEXT,
            total_amount REAL,
            paid_amount REAL DEFAULT 0,
            remaining_amount REAL,
            payment_method TEXT,
            status TEXT DEFAULT 'pending',
            notes TEXT,
            FOREIGN KEY(purchase_list_id) REFERENCES purchase_lists(id)
        )
    ''')
    # جدول کاربران
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            permissions TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_date_greg TEXT,
            created_date_jalali TEXT,
            last_login_greg TEXT,
            last_login_jalali TEXT
        )
    ''')
    # جدول سطوح دسترسی
    conn.execute('''
        CREATE TABLE IF NOT EXISTS permissions_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            permission_key TEXT UNIQUE NOT NULL,
            permission_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT
        )
    ''')
    # 🔽 جدول جدید پرداخت‌های فاکتور خرید
    conn.execute('''
        CREATE TABLE IF NOT EXISTS purchase_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            sheba_number TEXT,
            account_owner TEXT,
            bank_name TEXT,
            payment_date_jalali TEXT,
            payment_date_greg TEXT,
            description TEXT,
            receipt_number TEXT,
            created_by INTEGER,
            created_date_greg TEXT,
            created_date_jalali TEXT,
            FOREIGN KEY(purchase_invoice_id) REFERENCES purchase_invoices(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    ''')
    # 🔽 جدول جدید هزینه‌های مغازه
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shop_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_date_jalali TEXT NOT NULL,
            expense_date_greg TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            payment_method TEXT DEFAULT 'cash',
            receipt_number TEXT,
            notes TEXT,
            created_by INTEGER,
            created_date_greg TEXT,
            created_date_jalali TEXT,
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    ''')

    # کدهای موجود برای ایجاد کاربر پیش‌فرض...
    # ایجاد کاربر پیش‌فرض اگر وجود ندارد
    admin_exists = conn.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'").fetchone()[0]
    if not admin_exists:
        password_hash = generate_password_hash('123456')
        now_greg = browser_now().isoformat()
        now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')
        conn.execute('''
            INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ('مدیر سیستم', 'admin', password_hash, 'admin', 'all', now_greg, now_jalali))

    # درج سطوح دسترسی پیش‌فرض
    default_permissions = [
        # مدیریت محصولات
        ('products.view', 'مشاهده محصولات', 'مدیریت محصولات', 'دسترسی به مشاهده لیست محصولات'),
        ('products.add', 'افزودن محصول', 'مدیریت محصولات', 'دسترسی به افزودن محصول جدید'),
        ('products.edit', 'ویرایش محصول', 'مدیریت محصولات', 'دسترسی به ویرایش محصولات'),
        ('products.delete', 'حذف محصول', 'مدیریت محصولات', 'دسترسی به حذف محصولات'),
        ('products.import', 'ورود اکسل محصولات', 'مدیریت محصولات', 'دسترسی به ورود محصولات از اکسل'),
        # مدیریت فاکتورها
        ('invoices.create', 'صدور فاکتور', 'مدیریت فاکتورها', 'دسترسی به صدور فاکتور جدید'),
        ('invoices.view', 'مشاهده فاکتورها', 'مدیریت فاکتورها', 'دسترسی به مشاهده لیست فاکتورها'),
        ('invoices.edit', 'ویرایش فاکتور', 'مدیریت فاکتورها', 'دسترسی به ویرایش فاکتورها'),
        ('invoices.print', 'چاپ فاکتور', 'مدیریت فاکتورها', 'دسترسی به چاپ فاکتور'),
        # مدیریت پرداخت‌ها
        ('payments.add', 'ثبت پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به ثبت پرداخت جدید'),
        ('payments.edit', 'ویرایش پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به ویرایش پرداخت‌ها'),
        ('payments.delete', 'حذف پرداخت', 'مدیریت پرداخت‌ها', 'دسترسی به حذف پرداخت‌ها'),
        # گزارشات
        ('reports.view', 'مشاهده گزارشات', 'گزارشات', 'دسترسی به مشاهده گزارشات فروش'),
        ('reports.stock', 'گزارش موجودی', 'گزارشات', 'دسترسی به گزارش موجودی انبار'),
        ('reports.debtors', 'گزارش بدهکاران', 'گزارشات', 'دسترسی به گزارش بدهکاران'),
        ('reports.zakat', 'محاسبه زکات', 'گزارشات', 'دسترسی به محاسبه زکات'),
        ('reports.date_range', 'گزارش بازه زمانی', 'گزارشات', 'دسترسی به گزارش بین دو تاریخ'),
        ('reports.expenses', 'مدیریت هزینه‌ها', 'گزارشات', 'دسترسی به مدیریت هزینه‌های مغازه'),
        # مدیریت خرید
        ('purchase.view', 'مشاهده لیست خرید', 'مدیریت خرید', 'دسترسی به مشاهده لیست‌های خرید'),
        ('purchase.create', 'ایجاد لیست خرید', 'مدیریت خرید', 'دسترسی به ایجاد لیست خرید جدید'),
        ('purchase.edit', 'ویرایش لیست خرید', 'مدیریت خرید', 'دسترسی به ویرایش لیست خرید'),
        ('purchase.suppliers', 'مدیریت تامین‌کنندگان', 'مدیریت خرید', 'دسترسی به مدیریت تامین‌کنندگان'),
        # پشتیبان‌گیری
        ('backup.manage', 'مدیریت پشتیبان', 'سیستم', 'دسترسی به پشتیبان‌گیری و بازیابی'),
        # مدیریت کاربران
        ('users.manage', 'مدیریت کاربران', 'سیستم', 'دسترسی به مدیریت کاربران سیستم')
    ]
    for perm in default_permissions:
        try:
            conn.execute('''
                INSERT OR IGNORE INTO permissions_master (permission_key, permission_name, category, description)
                VALUES (?, ?, ?, ?)
            ''', perm)
        except:
            pass

    # 🔽 تلاش برای اصلاح ساختار جدول purchase_list_items اگر از قبل وجود دارد
    try:
        # بررسی وجود ستون product_id
        cursor = conn.execute("PRAGMA table_info(purchase_list_items)")
        columns = {row[1]: row for row in cursor.fetchall()}
        if 'product_id' in columns:
            # بررسی اینکه آیا product_id می‌تواند NULL باشد یا نه
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE name='purchase_list_items'")
            table_sql = cursor.fetchone()
            if table_sql and 'product_id INTEGER NOT NULL' in table_sql[0]:
                print("ساختار جدول purchase_list_items نیاز به به‌روزرسانی دارد...")
                # در اینجا می‌توانید منطق بازسازی جدول را اضافه کنید اگر لازم است
    except Exception as e:
        print(f"خطا در بررسی ساختار جدول: {e}")

    conn.commit()
    conn.close()

# سیستم احراز هویت
@app.route('/login', methods=['GET', 'POST'])
def login():
    # اگر کاربر قبلاً لاگین کرده، به صفحه اصلی هدایت شود
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['permissions'] = user['permissions']
            # به‌روزرسانی تاریخ آخرین ورود
            conn = get_db()
            now_greg = browser_now().isoformat()
            now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')
            conn.execute('''
                UPDATE users SET last_login_greg = ?, last_login_jalali = ? 
                WHERE id = ?
            ''', (now_greg, now_jalali, user['id']))
            conn.commit()
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

# صفحه درباره برنامه
@app.route('/about')
def about():
    now_jalali = browser_jalali_now().strftime('%Y/%m/%d')
    return render_template('about.html', current_date=now_jalali)

# مدیریت کاربران - فقط برای ادمین
@app.route('/users')
@admin_required
def users_management():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_date_greg DESC").fetchall()
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
        now_greg = browser_now().isoformat()
        now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')
        conn = get_db()
        conn.execute('''
            INSERT INTO users (full_name, username, password_hash, role, permissions, created_date_greg, created_date_jalali)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (full_name, username, password_hash, role, permissions, now_greg, now_jalali))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت اضافه شد'})
    except sqlite3.IntegrityError:
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
        conn.execute('''
            UPDATE users SET full_name = ?, username = ?, role = ?, permissions = ?, is_active = ?
            WHERE id = ?
        ''', (full_name, username, role, permissions, is_active, user_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت ویرایش شد'})
    except sqlite3.IntegrityError:
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
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
        conn.commit()
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
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'کاربر با موفقیت حذف شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# API برای مدیریت سطوح دسترسی
@app.route('/api/permissions_master')
@admin_required
def get_permissions_master():
    """دریافت لیست تمام سطوح دسترسی تعریف شده"""
    conn = get_db()
    permissions = conn.execute('''
        SELECT * FROM permissions_master 
        ORDER BY category, permission_name
    ''').fetchall()
    conn.close()
    return jsonify([dict(p) for p in permissions])

@app.route('/api/user_permissions/<int:user_id>')
@admin_required
def get_user_permissions(user_id):
    """دریافت سطوح دسترسی کاربر"""
    conn = get_db()
    user = conn.execute("SELECT permissions FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if user and user['permissions']:
        permissions_list = user['permissions'].split(',')
    else:
        permissions_list = []
    return jsonify({'permissions': permissions_list})

@app.route('/')
@login_required
def index():
    conn = get_db()
    debtors_count = conn.execute('''
        SELECT COUNT(*) FROM invoices i
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount
    ''').fetchone()[0]
    conn.close()
    return render_template('index.html', debtors_count=debtors_count)

@app.route('/products', methods=['GET', 'POST'])
@login_required
@permission_required('products.view')
def products():
    conn = get_db()
    edit_product = None
    if request.args.get('edit'):
        pid = request.args.get('edit')
        edit_product = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if edit_product:
            edit_product = dict(edit_product)
            if edit_product['buy_price_usd']:
                edit_product['buy_price_usd'] = float(edit_product['buy_price_usd'])
            if edit_product['buy_price_tomans']:
                edit_product['buy_price_tomans'] = float(edit_product['buy_price_tomans'])
            if edit_product['sell_price']:
                edit_product['sell_price'] = float(edit_product['sell_price'])

    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            sku = request.form['sku'].strip() or None
            buy_tomans_str = persian_to_latin_digits(request.form['buy_price_tomans'])
            buy_tomans_str = buy_tomans_str.replace(',', '').replace('٬', '')
            buy_tomans = float(buy_tomans_str) if buy_tomans_str else 0.0
            sell_price_str = persian_to_latin_digits(request.form['sell_price'])
            sell_price_str = sell_price_str.replace(',', '').replace('٬', '')
            sell_price = float(sell_price_str) if sell_price_str else 0.0
            usd_str = request.form.get('buy_price_usd', '').strip()
            if usd_str:
                usd_str = persian_to_latin_digits(usd_str)
                usd_str = usd_str.replace(',', '').replace('٬', '')
                usd = float(usd_str) if usd_str else None
            else:
                usd = None
            warehouse = request.form.get('warehouse', '').strip() or None
            stock_str = persian_to_latin_digits(request.form.get('stock', '0'))
            stock_str = stock_str.replace(',', '').replace('٬', '')
            stock = int(stock_str) if stock_str else 0
            description = request.form.get('description', '').strip() or None
            product_id = request.form.get('product_id')

            if product_id:
                cursor = conn.execute('''
                    UPDATE products SET 
                    name = ?, sku = ?, buy_price_tomans = ?, buy_price_usd = ?, 
                    sell_price = ?, warehouse = ?, stock = ?, description = ?
                    WHERE id = ?
                ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description, product_id))
                flash('کالا با موفقیت ویرایش شد.', 'success')
            else:
                cursor = conn.execute('''
                    INSERT INTO products (name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description))
                flash('کالا با موفقیت اضافه شد.', 'success')
            conn.commit()
        except ValueError as e:
            flash(f'لطفاً مقادیر عددی را به درستی وارد کنید. خطا: {str(e)}', 'danger')
        except Exception as e:
            flash(f'خطا در ذخیره کالا: {str(e)}', 'danger')
        finally:
            conn.close()
            return redirect(url_for('products'))

    products = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    conn.close()
    return render_template('products.html', products=products, edit_product=edit_product)

@app.route('/api/search_products_all')
@login_required
def search_products_all():
    q = request.args.get('q', '').lower()
    q = normalize_persian_text(q)
    conn = get_db()
    products = conn.execute("""
        SELECT * FROM products 
        WHERE (LOWER(name) LIKE ? OR LOWER(sku) LIKE ? OR 
               LOWER(REPLACE(REPLACE(name, 'ي', 'ی'), 'ك', 'ک')) LIKE ? OR
               LOWER(REPLACE(REPLACE(sku, 'ي', 'ی'), 'ك', 'ک')) LIKE ?)
        ORDER BY name
    """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return jsonify([dict(p) for p in products])

@app.route('/api/search_products_by_name')
@login_required
def search_products_by_name():
    q = request.args.get('q', '').lower()
    q = normalize_persian_text(q)
    conn = get_db()
    products = conn.execute("""
        SELECT * FROM products 
        WHERE (LOWER(name) LIKE ? OR LOWER(sku) LIKE ? OR 
               LOWER(REPLACE(REPLACE(name, 'ي', 'ی'), 'ك', 'ک')) LIKE ? OR
               LOWER(REPLACE(REPLACE(sku, 'ي', 'ی'), 'ك', 'ک')) LIKE ?)
        ORDER BY name
    """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return jsonify([dict(p) for p in products])

@app.route('/invoice', methods=['GET', 'POST'])
@login_required
@permission_required('invoices.create')
def invoice():
    conn = get_db()
    if request.method == 'POST':
        try:
            customer_name = request.form['customer_name'].strip()
            customer_phone = request.form['customer_phone'].strip()
            discount_percent_str = request.form.get('discount_percent', '0').replace(',', '').replace('٬', '')
            discount_amount_str = request.form.get('discount_amount', '0').replace(',', '').replace('٬', '')
            discount_percent = float(discount_percent_str) if discount_percent_str else 0
            discount_amount = float(discount_amount_str) if discount_amount_str else 0
            initial_payment_str = request.form.get('initial_payment', '0').replace(',', '').replace('٬', '')
            initial_payment = float(initial_payment_str) if initial_payment_str else 0
            print(f"🔍 پرداخت اولیه دریافت شده از فرم: {initial_payment}")  # دیباگ
            now_greg = browser_now()
            now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')

            cursor = conn.execute('''
                INSERT INTO invoices (customer_name, customer_phone, date_jalali, date_greg, total_amount, total_profit, discount_amount, discount_percent)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?)
            ''', (customer_name, customer_phone, now_jalali, now_greg.isoformat(), discount_amount, discount_percent))
            invoice_id = cursor.lastrowid

            total = 0
            total_buy_cost = 0  # جمع هزینه خرید تمام کالاها
            item_ids = request.form.getlist('item_id[]')
            quantities = request.form.getlist('quantity[]')

            for i, product_id in enumerate(item_ids):
                quantity = int(quantities[i])
                if quantity <= 0:
                    continue
                product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                if not product or product['stock'] < quantity:
                    raise ValueError(f"موجودی ناکافی برای {product['name']}")

                custom_price_str = request.form.get(f'custom_price_{product_id}', '').replace(',', '').replace('٬', '')
                unit_price = float(custom_price_str) if custom_price_str else product['sell_price']

                # محاسبه سود برای هر واحد
                profit_per_unit = unit_price - product['buy_price_tomans']
                item_total = unit_price * quantity
                item_buy_cost = product['buy_price_tomans'] * quantity  # هزینه خرید این آیتم

                conn.execute('''
                    INSERT INTO invoice_items (invoice_id, product_id, product_name, sku, quantity, unit_price, profit_per_unit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (invoice_id, product_id, product['name'], product['sku'], quantity, unit_price, profit_per_unit))

                conn.execute("UPDATE products SET stock = stock - ?, sales_count = sales_count + ? WHERE id = ?", 
                           (quantity, quantity, product_id))
                total += item_total
                total_buy_cost += item_buy_cost

            # محاسبه تخفیف
            if discount_percent > 0 and discount_amount == 0:
                discount_amount = total * (discount_percent / 100)
            final_total = total - discount_amount
            # محاسبه سود نهایی به صورت صحیح: درآمد نهایی منهای هزینه خرید
            final_profit = final_total - total_buy_cost
            # اطمینان از اینکه سود منفی نباشد
            final_profit = max(0, final_profit)

            conn.execute('''
                UPDATE invoices SET total_amount = ?, total_profit = ?, discount_amount = ?
                WHERE id = ?
            ''', (final_total, final_profit, discount_amount, invoice_id))

            # ثبت پرداخت اولیه اگر وجود دارد
            if initial_payment > 0:
                print(f"💰 در حال ثبت پرداخت اولیه: {initial_payment} برای فاکتور {invoice_id}")  # دیباگ
                conn.execute('''
                    INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description)
                    VALUES (?, ?, ?, ?, ?)
                ''', (invoice_id, initial_payment, now_jalali, now_greg.isoformat(), 'پرداخت اولیه'))
                # تأیید ثبت پرداخت
                payment_check = conn.execute("SELECT COUNT(*) as count FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()
                print(f"✅ تعداد پرداخت‌های ثبت شده: {payment_check['count']}")  # دیباگ

            conn.commit()
            # بررسی نهایی پس از commit
            final_paid = conn.execute("SELECT IFNULL(SUM(amount), 0) as total_paid FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()
            print(f"💳 مبلغ پرداختی نهایی در دیتابیس: {final_paid['total_paid']}")  # دیباگ
            print(f"📊 سود نهایی فاکتور: {final_profit} (درآمد نهایی: {final_total}, هزینه خرید: {total_buy_cost})")  # دیباگ

            flash('فاکتور با موفقیت صادر شد.', 'success')
            return redirect(url_for('invoice_detail', invoice_id=invoice_id))
        except ValueError as ve:
            conn.rollback()
            flash(str(ve), 'danger')
        except Exception as e:
            conn.rollback()
            flash(f'خطا در صدور فاکتور: {str(e)}', 'danger')
        finally:
            conn.close()

    products = conn.execute("SELECT * FROM products WHERE stock > 0 ORDER BY name").fetchall()
    conn.close()
    return render_template('invoice_new.html', products=products)

@app.route('/invoices')
@login_required
@permission_required('invoices.view')
def invoices():
    conn = get_db()
    invoices = conn.execute('''
        SELECT i.*, 
               (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount,
               GROUP_CONCAT(ii.product_name, ', ') as products_search,
               CASE WHEN (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount 
                    THEN 1 ELSE 0 END as is_paid,
               i.total_amount - (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as remaining
        FROM invoices i
        LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
        GROUP BY i.id
        ORDER BY i.date_greg DESC
    ''').fetchall()
    conn.close()
    return render_template('invoices_list.html', invoices=invoices)

@app.route('/invoice/<int:invoice_id>')
@login_required
@permission_required('invoices.view')
def invoice_detail(invoice_id):
    conn = get_db()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))

    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
    payments = conn.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date_greg DESC", (invoice_id,)).fetchall()

    # محاسبه صحیح مجموع پرداختی‌ها
    paid_result = conn.execute("SELECT IFNULL(SUM(amount), 0) as total_paid FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()
    paid = paid_result['total_paid'] if paid_result else 0
    print(f"📊 فاکتور {invoice_id} - مبلغ کل: {invoice['total_amount']}, پرداختی: {paid}")  # دیباگ

    conn.close()
    return render_template('invoice_detail.html', invoice=invoice, items=items, payments=payments, paid=paid)

@app.route('/invoice/<int:invoice_id>/add_payment', methods=['POST'])
@login_required
@permission_required('payments.add')
def add_payment(invoice_id):
    conn = get_db()
    try:
        amount_str = request.form['amount'].replace(',', '').replace('٬', '')
        amount = float(amount_str)
        description = request.form.get('description', '').strip() or None
        now_greg = browser_now()
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')

        conn.execute('''
            INSERT INTO payments (invoice_id, amount, payment_date_jalali, payment_date_greg, description)
            VALUES (?, ?, ?, ?, ?)
        ''', (invoice_id, amount, now_jalali, now_greg.isoformat(), description))
        conn.commit()
        return jsonify({'success': True})
    except ValueError:
        return jsonify({'success': False, 'error': 'مبلغ نامعتبر'}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/payment/<int:payment_id>/delete', methods=['POST'])
@login_required
@permission_required('payments.delete')
def delete_payment(payment_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/payment/<int:payment_id>/edit', methods=['POST'])
@login_required
@permission_required('payments.edit')
def edit_payment(payment_id):
    conn = get_db()
    try:
        amount_str = request.form['amount'].replace(',', '').replace('٬', '')
        amount = float(amount_str)
        description = request.form.get('description', '').strip() or None

        conn.execute('''
            UPDATE payments SET amount = ?, description = ?
            WHERE id = ?
        ''', (amount, description, payment_id))
        conn.commit()
        return jsonify({'success': True})
    except ValueError:
        return jsonify({'success': False, 'error': 'مبلغ نامعتبر'}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/invoice/<int:invoice_id>/print')
@login_required
@permission_required('invoices.print')
def print_invoice(invoice_id):
    conn = get_db()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
    payments = conn.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date_greg", (invoice_id,)).fetchall()
    conn.close()
    return render_template('invoice_print.html', invoice=invoice, items=items, payments=payments)

@app.route('/invoice/<int:invoice_id>/edit')
@login_required
@permission_required('invoices.edit')
def edit_invoice(invoice_id):
    conn = get_db()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))

    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
    products = conn.execute("SELECT * FROM products WHERE stock > 0 ORDER BY name").fetchall()
    payments = conn.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date_greg DESC", (invoice_id,)).fetchall()
    paid = conn.execute("SELECT IFNULL(SUM(amount), 0) FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()[0]
    conn.close()
    return render_template('invoice_edit.html', invoice=invoice, items=items, products=products, payments=payments, paid=paid)

@app.route('/invoice/<int:invoice_id>/update', methods=['POST'])
@login_required
@permission_required('invoices.edit')
def update_invoice(invoice_id):
    conn = get_db()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        flash('فاکتور یافت نشد.', 'danger')
        return redirect(url_for('invoices'))

    try:
        discount_percent_str = request.form.get('discount_percent', '0').replace(',', '').replace('٬', '')
        discount_amount_str = request.form.get('discount_amount', '0').replace(',', '').replace('٬', '')
        discount_percent = float(discount_percent_str) if discount_percent_str else 0
        discount_amount = float(discount_amount_str) if discount_amount_str else 0

        item_ids = request.form.getlist('item_id[]')
        quantities = request.form.getlist('quantity[]')

        total = 0
        total_buy_cost = 0  # جمع هزینه خرید تمام کالاها

        # بازگرداندن موجودی و آمار فروش به حالت قبل
        existing_items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
        for item in existing_items:
            conn.execute("UPDATE products SET stock = stock + ?, sales_count = sales_count - ? WHERE id = ?", 
                       (item['quantity'], item['quantity'], item['product_id']))

        # حذف آیتم‌های قبلی
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

        # افزودن آیتم‌های جدید
        for i in range(len(item_ids)):
            product_id = item_ids[i]
            quantity = int(quantities[i]) if quantities[i] else 0
            if quantity <= 0:
                continue
            custom_price_key = f'custom_price_{product_id}'
            custom_price_str = request.form.get(custom_price_key, '0').replace(',', '').replace('٬', '')
            custom_price_str = persian_to_latin_digits(custom_price_str)
            unit_price = float(custom_price_str) if custom_price_str else 0.0
            if unit_price <= 0:
                raise ValueError(f"قیمت کالا با شناسه {product_id} نامعتبر است.")

            product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                raise ValueError(f"کالایی با شناسه {product_id} یافت نشد.")
            if product['stock'] < quantity:
                raise ValueError(f"موجودی کافی برای «{product['name']}» وجود ندارد.")

            # محاسبه سود برای هر واحد
            profit_per_unit = unit_price - product['buy_price_tomans']
            item_total = unit_price * quantity
            item_buy_cost = product['buy_price_tomans'] * quantity  # هزینه خرید این آیتم

            conn.execute('''
                INSERT INTO invoice_items (invoice_id, product_id, product_name, sku, quantity, unit_price, profit_per_unit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (invoice_id, product_id, product['name'], product['sku'], quantity, unit_price, profit_per_unit))

            conn.execute("UPDATE products SET stock = stock - ?, sales_count = sales_count + ? WHERE id = ?", 
                       (quantity, quantity, product_id))
            total += item_total
            total_buy_cost += item_buy_cost

        # محاسبه تخفیف
        if discount_percent > 0 and discount_amount == 0:
            discount_amount = total * (discount_percent / 100)
        final_total = total - discount_amount
        # محاسبه سود نهایی به صورت صحیح: درآمد نهایی منهای هزینه خرید
        final_profit = final_total - total_buy_cost
        # اطمینان از اینکه سود منفی نباشد
        final_profit = max(0, final_profit)

        conn.execute('''
            UPDATE invoices 
            SET total_amount = ?, total_profit = ?, discount_amount = ?, discount_percent = ?
            WHERE id = ?
        ''', (final_total, final_profit, discount_amount, discount_percent, invoice_id))
        conn.commit()
        flash('فاکتور با موفقیت ویرایش شد.', 'success')
        return redirect(url_for('invoice_detail', invoice_id=invoice_id))
    except ValueError as ve:
        conn.rollback()
        flash(str(ve), 'danger')
    except Exception as e:
        conn.rollback()
        flash(f'خطا در ویرایش فاکتور: {str(e)}', 'danger')
    finally:
        conn.close()
        return redirect(url_for('edit_invoice', invoice_id=invoice_id))

@app.route('/invoice/<int:invoice_id>/items')
@login_required
def get_invoice_items(invoice_id):
    conn = get_db()
    items = conn.execute('''
        SELECT ii.*, p.stock + ii.quantity as max_stock
        FROM invoice_items ii
        LEFT JOIN products p ON p.id = ii.product_id
        WHERE ii.invoice_id = ?
    ''', (invoice_id,)).fetchall()
    conn.close()
    return jsonify([dict(item) for item in items])

@app.route('/stock_report')
@login_required
@permission_required('reports.stock')
def stock_report():
    conn = get_db()
    out_of_stock = conn.execute("SELECT * FROM products WHERE stock = 0 ORDER BY name").fetchall()
    low_stock = conn.execute("SELECT * FROM products WHERE stock > 0 AND stock < 5 ORDER BY stock").fetchall()
    normal_stock = conn.execute("SELECT * FROM products WHERE stock >= 5 ORDER BY name").fetchall()
    top_selling = conn.execute("SELECT * FROM products WHERE sales_count > 0 ORDER BY sales_count DESC LIMIT 20").fetchall()
    purchase_lists = conn.execute('''
        SELECT * FROM purchase_lists WHERE status = 'active' ORDER BY created_date_greg DESC
    ''').fetchall()
    conn.close()
    return render_template('stock_report.html', 
                         out_of_stock=out_of_stock, 
                         low_stock=low_stock, 
                         normal_stock=normal_stock,
                         top_selling=top_selling,
                         purchase_lists=purchase_lists)

@app.route('/zakat')
@login_required
@permission_required('reports.zakat')
def zakat_calculator():
    conn = get_db()
    products = conn.execute("SELECT * FROM products WHERE stock > 0").fetchall()
    total_value_sell = 0
    total_value_buy = 0
    for p in products:
        total_value_sell += p['sell_price'] * p['stock']
        total_value_buy += p['buy_price_tomans'] * p['stock']
    zakat_amount = total_value_sell * 0.025
    conn.close()
    return render_template('zakat.html', products=products, total_value_sell=total_value_sell, total_value_buy=total_value_buy, zakat_amount=zakat_amount)

@app.route('/debtors')
@login_required
@permission_required('reports.debtors')
def debtors():
    conn = get_db()
    debtors = conn.execute('''
        SELECT i.*, 
               (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount
        FROM invoices i
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount
        ORDER BY i.date_greg DESC
    ''').fetchall()
    total_debt = 0
    debtors_list = []
    for debtor in debtors:
        remaining = debtor['total_amount'] - debtor['paid_amount']
        total_debt += remaining
        debtors_list.append({
            'id': debtor['id'],
            'customer_name': debtor['customer_name'],
            'customer_phone': debtor['customer_phone'],
            'date_jalali': debtor['date_jalali'],
            'total_amount': debtor['total_amount'],
            'paid_amount': debtor['paid_amount'],
            'remaining': remaining
        })
    conn.close()
    return render_template('debtors.html', debtors=debtors_list, total_debt=total_debt)


@app.route('/download_database_copy')
@login_required
@permission_required('backup.manage')
def download_database_copy():
    from flask import send_file
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    try:
        if USE_POSTGRES:
            from db_backend import export_postgres_to_sqlite
            export_postgres_to_sqlite(DATABASE_URL, path)
        else:
            shutil.copy2(DB_PATH, path)
        return send_file(path, as_attachment=True, download_name='swedyadak_offline.db', mimetype='application/x-sqlite3')
    except Exception as e:
        try: os.remove(path)
        except OSError: pass
        flash(f'تهیه نسخه پشتیبان ناموفق بود: {e}', 'danger')
        return redirect(url_for('backup_page'))

@app.route('/restore_database_copy', methods=['POST'])
@login_required
@admin_required
def restore_database_copy():
    import tempfile
    uploaded=request.files.get('database_file')
    if not uploaded or not uploaded.filename.lower().endswith('.db'):
        flash('فایل پشتیبان SQLite با پسوند .db انتخاب کنید.', 'warning'); return redirect(url_for('backup_page'))
    fd,path=tempfile.mkstemp(suffix='.db'); os.close(fd)
    try:
        uploaded.save(path)
        if USE_POSTGRES:
            from db_backend import import_sqlite_to_postgres
            import_sqlite_to_postgres(path, DATABASE_URL)
        else:
            shutil.copy2(path, DB_PATH)
        flash('بازیابی انجام شد.', 'success')
    except Exception as e:
        flash(f'بازیابی ناموفق بود: {e}', 'danger')
    finally:
        try: os.remove(path)
        except OSError: pass
    return redirect(url_for('backup_page'))

@app.route('/backup')
@login_required
@permission_required('backup.manage')
def backup_page():
    backup_dir = Path('backup')
    backup_dir.mkdir(exist_ok=True)
    backup_files_db = []
    for file_path in backup_dir.glob('backup_*.db'):
        size_kb = file_path.stat().st_size // 1024
        backup_files_db.append({
            'name': file_path.name,
            'size': size_kb
        })
    backup_files_db.sort(key=lambda x: x['name'], reverse=True)

    backup_files_full = []
    for file_path in backup_dir.glob('full_backup_*.zip'):
        size_mb = file_path.stat().st_size // (1024 * 1024)
        backup_files_full.append({
            'name': file_path.name,
            'size': size_mb
        })
    backup_files_full.sort(key=lambda x: x['name'], reverse=True)

    # اضافه کردن اطلاعات فایل اجرایی به context
    exec_info = get_main_executable_info()
    return render_template('backup.html', 
                         backup_files_db=backup_files_db, 
                         backup_files_full=backup_files_full,
                         exec_info=exec_info)

@app.route('/do_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def do_backup():
    if USE_POSTGRES:
        flash('برای دیتابیس آنلاین از دکمه «دانلود نسخه آفلاین» استفاده کنید.', 'info')
        return redirect(url_for('backup_page'))
    name = f"backup_{browser_jalali_now().strftime('%Y%m%d_%H%M%S')}.db"
    path = os.path.join('backup', name)
    shutil.copy(DB_PATH, path)
    flash('پشتیبان‌گیری دیتابیس با موفقیت انجام شد.', 'success')
    return redirect(url_for('backup_page'))

@app.route('/do_full_backup', methods=['POST'])
@login_required
@permission_required('backup.manage')
def do_full_backup():
    if USE_POSTGRES:
        flash('در نسخه Render از دانلود نسخه آفلاین دیتابیس استفاده کنید؛ فایل‌های برنامه در GitHub نگهداری می‌شوند.', 'info')
        return redirect(url_for('backup_page'))
    try:
        timestamp = browser_jalali_now().strftime('%Y%m%d_%H%M%S')
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

@app.route('/product/<int:product_id>/delete', methods=['POST'])
@login_required
@permission_required('products.delete')
def delete_product(product_id):
    conn = get_db()
    used_in_invoice = conn.execute("SELECT COUNT(*) FROM invoice_items WHERE product_id = ?", (product_id,)).fetchone()[0]
    if used_in_invoice > 0:
        conn.close()
        return jsonify({'success': False, 'error': 'این کالا در فاکتورهای صادر شده استفاده شده و قابل حذف نیست.'})
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/reports')
@login_required
@permission_required('reports.view')
def reports():
    conn = get_db()
    now = browser_now()
    today_jalali = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m/%d')

    # امروز - فقط فاکتورهای کاملاً پرداخت شده
    today_sales_row = conn.execute('''
        SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as invoice_count
        FROM invoices
        WHERE SUBSTR(date_jalali, 1, 10) = ?
        AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
    ''', (today_jalali,)).fetchone()
    today_sales = today_sales_row['total_sales'] or 0
    today_profit = today_sales_row['total_profit'] or 0
    today_invoice_count = today_sales_row['invoice_count'] or 0
    today_paid_count_row = conn.execute('''
        SELECT COUNT(*) as paid_count
        FROM invoices i
        WHERE SUBSTR(i.date_jalali, 1, 10) = ?
        AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount
    ''', (today_jalali,)).fetchone()
    today_paid_count = today_paid_count_row['paid_count'] or 0

    # ماه جاری - فقط فاکتورهای کاملاً پرداخت شده
    current_month = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m')
    month_sales_row = conn.execute('''
        SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as invoice_count
        FROM invoices
        WHERE SUBSTR(date_jalali, 1, 7) = ?
        AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
    ''', (current_month,)).fetchone()
    month_sales = month_sales_row['total_sales'] or 0
    month_profit = month_sales_row['total_profit'] or 0
    month_invoice_count = month_sales_row['invoice_count'] or 0
    month_paid_count_row = conn.execute('''
        SELECT COUNT(*) as paid_count
        FROM invoices i
        WHERE SUBSTR(i.date_jalali, 1, 7) = ?
        AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount
    ''', (current_month,)).fetchone()
    month_paid_count = month_paid_count_row['paid_count'] or 0

    # سال جاری - فقط فاکتورهای کاملاً پرداخت شده
    current_year = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y')
    year_sales_row = conn.execute('''
        SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as invoice_count
        FROM invoices
        WHERE SUBSTR(date_jalali, 1, 4) = ?
        AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
    ''', (current_year,)).fetchone()
    year_sales = year_sales_row['total_sales'] or 0
    year_profit = year_sales_row['total_profit'] or 0
    year_invoice_count = year_sales_row['invoice_count'] or 0

    # کل - فقط فاکتورهای کاملاً پرداخت شده
    total_sales_row = conn.execute('''
        SELECT SUM(total_amount) as total_sales, SUM(total_profit) as total_profit, COUNT(id) as total_invoices
        FROM invoices
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
    ''').fetchone()
    total_sales = total_sales_row['total_sales'] or 0
    total_profit = total_sales_row['total_profit'] or 0
    total_invoices = total_sales_row['total_invoices'] or 0

    # گزارش روزانه - فقط فاکتورهای کاملاً پرداخت شده
    daily_sales = conn.execute('''
        SELECT SUBSTR(date_jalali, 1, 10) as date_jalali, 
               SUM(total_amount) as total_sales, 
               SUM(total_profit) as total_profit,
               COUNT(id) as invoice_count
        FROM invoices
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        GROUP BY SUBSTR(date_jalali, 1, 10)
        ORDER BY MAX(date_greg) DESC
        LIMIT 30
    ''').fetchall()

    # گزارش ماهانه - فقط فاکتورهای کاملاً پرداخت شده
    monthly_sales = conn.execute('''
        SELECT SUBSTR(date_jalali, 1, 7) as month, 
               SUM(total_amount) as total_sales, 
               SUM(total_profit) as total_profit,
               COUNT(id) as invoice_count
        FROM invoices
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        GROUP BY month
        ORDER BY MAX(date_greg) DESC
    ''').fetchall()

    # سایر آمارها (بدون تغییر)
    debtors_count_row = conn.execute('''
        SELECT COUNT(*) as count
        FROM invoices i
        WHERE (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) < i.total_amount
    ''').fetchone()
    debtors_count = debtors_count_row['count'] or 0
    debtors_info = conn.execute('''
        SELECT SUM(i.total_amount - IFNULL(p.paid_amount, 0)) as total_debt
        FROM invoices i
        LEFT JOIN (SELECT invoice_id, SUM(amount) as paid_amount FROM payments GROUP BY invoice_id) p 
        ON i.id = p.invoice_id
        WHERE IFNULL(p.paid_amount, 0) < i.total_amount
    ''').fetchone()
    total_debt = debtors_info['total_debt'] or 0

    conn.close()

    current_datetime = jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M')
    today_date = browser_jalali_now().strftime('%Y/%m/%d')
    current_month = browser_jalali_now().strftime('%Y/%m')
    current_year = browser_jalali_now().strftime('%Y')

    return render_template('reports.html', 
                         daily_sales=daily_sales, 
                         monthly_sales=monthly_sales, 
                         today_sales=today_sales, 
                         today_profit=today_profit, 
                         today_invoice_count=today_invoice_count, 
                         today_paid_count=today_paid_count,
                         month_sales=month_sales,
                         month_profit=month_profit,
                         month_invoice_count=month_invoice_count,
                         month_paid_count=month_paid_count,
                         year_sales=year_sales,
                         year_profit=year_profit,
                         year_invoice_count=year_invoice_count,
                         total_sales=total_sales,
                         total_profit=total_profit,
                         total_invoices=total_invoices,
                         debtors_count=debtors_count,
                         total_debt=total_debt,
                         current_datetime=current_datetime,
                         today_date=today_date,
                         current_month=current_month,
                         current_year=current_year)

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

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(filepath, encoding='utf-8-sig')
        else:
            df = pd.read_excel(filepath)

        required_cols = ['nam', 'kharid', 'fi']
        if not all(col in df.columns for col in required_cols):
            raise ValueError("ستون‌های الزامی (nam, kharid, fi) موجود نیستند.")

        conn = get_db()
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

            cursor = conn.execute('''
                INSERT INTO products (name, sku, buy_price_tomans, buy_price_usd, sell_price, warehouse, stock, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description))
            added_count += 1

        conn.commit()
        flash(f'{added_count} کالا با موفقیت اضافه شد.', 'success')
    except ValueError as ve:
        flash(f'خطا در پردازش فایل: {str(ve)}', 'danger')
    except Exception as e:
        flash(f'خطا در آپلود: {str(e)}', 'danger')
    finally:
        conn.close()
        if os.path.exists(filepath):
            os.remove(filepath)
    return redirect(url_for('products'))

# مسیرهای جدید برای گزارش بین دو تاریخ
@app.route('/date_range_report')
@login_required
@permission_required('reports.date_range')
def date_range_report():
    """گزارش مالی بین دو تاریخ"""
    now = browser_now()
    current_datetime = jdatetime.datetime.fromgregorian(datetime=now).strftime('%Y/%m/%d %H:%M')
    return render_template('date_range_report.html', current_datetime=current_datetime)

@app.route('/api/date_range_sales')
@login_required
@permission_required('reports.date_range')
def api_date_range_sales():
    """API برای دریافت گزارش فروش بین دو تاریخ"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if not start_date or not end_date:
        return jsonify({'error': 'لطفاً هر دو تاریخ را انتخاب کنید'}), 400

    conn = get_db()
    try:
        # گزارش کلی - فقط فاکتورهای کاملاً پرداخت شده
        summary = conn.execute('''
            SELECT 
                COUNT(*) as invoice_count,
                SUM(total_amount) as total_sales,
                SUM(total_profit) as total_profit,
                AVG(total_amount) as avg_sale
            FROM invoices 
            WHERE SUBSTR(date_jalali, 1, 10) BETWEEN ? AND ?
            AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
        ''', (start_date, end_date)).fetchone()

        # گزارش روزانه در بازه زمانی - فقط فاکتورهای کاملاً پرداخت شده
        daily_sales = conn.execute('''
            SELECT 
                SUBSTR(date_jalali, 1, 10) as date_jalali,
                COUNT(*) as invoice_count,
                SUM(total_amount) as total_sales,
                SUM(total_profit) as total_profit
            FROM invoices 
            WHERE SUBSTR(date_jalali, 1, 10) BETWEEN ? AND ?
            AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = invoices.id) >= total_amount
            GROUP BY SUBSTR(date_jalali, 1, 10)
            ORDER BY date_jalali
        ''', (start_date, end_date)).fetchall()

        # فاکتورهای پرفروش - فقط فاکتورهای کاملاً پرداخت شده
        top_invoices = conn.execute('''
            SELECT 
                i.id,
                i.customer_name,
                i.date_jalali,
                i.total_amount,
                i.total_profit,
                (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) as paid_amount
            FROM invoices i
            WHERE SUBSTR(i.date_jalali, 1, 10) BETWEEN ? AND ?
            AND (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id) >= i.total_amount
            ORDER BY i.total_amount DESC
            LIMIT 10
        ''', (start_date, end_date)).fetchall()

        conn.close()
        return jsonify({
            'summary': {
                'invoice_count': summary['invoice_count'] or 0,
                'total_sales': summary['total_sales'] or 0,
                'total_profit': summary['total_profit'] or 0,
                'avg_sale': summary['avg_sale'] or 0
            },
            'daily_sales': [dict(day) for day in daily_sales],
            'top_invoices': [dict(inv) for inv in top_invoices],
            'date_range': {
                'start_date': start_date,
                'end_date': end_date
            }
        })
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

# صفحه بدهی به تامین‌کنندگان
@app.route('/supplier_debts')
@login_required
@permission_required('purchase.suppliers')
def supplier_debts():
    """صفحه بدهی به تامین‌کنندگان"""
    conn = get_db()
    # محاسبه بدهی به تامین‌کنندگان (فاکتورهای خرید پرداخت نشده)
    debts = conn.execute('''
        SELECT 
            pi.supplier_name,
            SUM(pi.total_amount - pi.paid_amount) as total_debt,
            COUNT(pi.id) as invoice_count
        FROM purchase_invoices pi
        WHERE pi.status = 'pending' OR pi.remaining_amount > 0
        GROUP BY pi.supplier_name
        HAVING SUM(pi.total_amount - pi.paid_amount) > 0
        ORDER BY total_debt DESC
    ''').fetchall()

    total_debt_result = conn.execute('''
        SELECT SUM(total_amount - paid_amount) as total_debt
        FROM purchase_invoices 
        WHERE status = 'pending' OR remaining_amount > 0
    ''').fetchone()
    total_debt = total_debt_result['total_debt'] if total_debt_result and total_debt_result['total_debt'] is not None else 0

    conn.close()
    return render_template('supplier_debts.html', 
                         debts=debts,
                         total_debt=total_debt)

# لیست فاکتورهای خرید
@app.route('/purchase_invoices_list')
@login_required
@permission_required('purchase.view')
def purchase_invoices_list():
    """صفحه لیست فاکتورهای خرید"""
    conn = get_db()
    purchase_invoices = conn.execute('''
        SELECT 
            pi.*,
            pl.notes as purchase_notes,
            (SELECT COUNT(*) FROM purchase_list_items pli WHERE pli.purchase_list_id = pl.id) as item_count
        FROM purchase_invoices pi
        LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
        ORDER BY pi.invoice_date_greg DESC
    ''').fetchall()
    conn.close()
    return render_template('purchase_invoices_list.html', 
                         purchase_invoices=purchase_invoices)

# مدیریت لیست خرید
@app.route('/purchase_lists')
@login_required
@permission_required('purchase.view')
def purchase_lists():
    """صفحه مدیریت لیست‌های خرید"""
    conn = get_db()
    purchase_lists = conn.execute('''
        SELECT 
            pl.*,
            COUNT(pli.id) as item_count,
            SUM(pli.estimated_total_price) as total_estimated
        FROM purchase_lists pl
        LEFT JOIN purchase_list_items pli ON pl.id = pli.purchase_list_id
        GROUP BY pl.id
        ORDER BY pl.created_date_greg DESC
    ''').fetchall()
    products = conn.execute("SELECT id, name, sku, stock FROM products ORDER BY name").fetchall()
    conn.close()
    return render_template('purchase_lists.html', 
                         purchase_lists=purchase_lists,
                         products=products)

# ایجاد لیست خرید جدید
@app.route('/purchase_lists/create', methods=['POST'])
@login_required
@permission_required('purchase.create')
def create_purchase_list():
    """ایجاد لیست خرید جدید"""
    try:
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None
        now_greg = browser_now().isoformat()
        now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')

        conn = get_db()
        cursor = conn.execute('''
            INSERT INTO purchase_lists (supplier_name, created_date_greg, created_date_jalali, notes)
            VALUES (?, ?, ?, ?)
        ''', (supplier_name, now_greg, now_jalali, notes))
        purchase_list_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'purchase_list_id': purchase_list_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# افزودن آیتم به لیست خرید
@app.route('/purchase_lists/<int:list_id>/add_item', methods=['POST'])
@login_required
@permission_required('purchase.create')
def add_purchase_list_item(list_id):
    """افزودن آیتم به لیست خرید"""
    try:
        product_id = request.form['product_id']
        quantity_to_buy = int(request.form['quantity_to_buy'])
        estimated_unit_price = float(request.form.get('estimated_unit_price', 0))
        notes = request.form.get('notes', '').strip() or None

        conn = get_db()
        # دریافت اطلاعات محصول
        product = conn.execute("SELECT name, sku, stock FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            return jsonify({'success': False, 'error': 'محصول یافت نشد'}), 400

        estimated_total_price = quantity_to_buy * estimated_unit_price

        cursor = conn.execute('''
            INSERT INTO purchase_list_items 
            (purchase_list_id, product_id, product_name, sku, current_stock, quantity_to_buy, estimated_unit_price, estimated_total_price, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (list_id, product_id, product['name'], product['sku'], product['stock'], quantity_to_buy, estimated_unit_price, estimated_total_price, notes))

        # به‌روزرسانی مجموع تخمینی لیست خرید
        conn.execute('''
            UPDATE purchase_lists 
            SET estimated_total = (
                SELECT SUM(estimated_total_price) 
                FROM purchase_list_items 
                WHERE purchase_list_id = ?
            )
            WHERE id = ?
        ''', (list_id, list_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/update_product/<int:product_id>', methods=['POST'])
@login_required
@permission_required('products.edit')
def update_product(product_id):
    """مسیر جدید برای به‌روزرسانی محصول"""
    conn = get_db()
    try:
        name = request.form['name'].strip()
        sku = request.form['sku'].strip() or None
        buy_tomans_str = persian_to_latin_digits(request.form['buy_price_tomans'])
        buy_tomans_str = buy_tomans_str.replace(',', '').replace('٬', '')
        buy_tomans = float(buy_tomans_str) if buy_tomans_str else 0.0
        sell_price_str = persian_to_latin_digits(request.form['sell_price'])
        sell_price_str = sell_price_str.replace(',', '').replace('٬', '')
        sell_price = float(sell_price_str) if sell_price_str else 0.0
        usd_str = request.form.get('buy_price_usd', '').strip()
        if usd_str:
            usd_str = persian_to_latin_digits(usd_str)
            usd_str = usd_str.replace(',', '').replace('٬', '')
            usd = float(usd_str) if usd_str else None
        else:
            usd = None
        warehouse = request.form.get('warehouse', '').strip() or None
        stock_str = persian_to_latin_digits(request.form.get('stock', '0'))
        stock_str = stock_str.replace(',', '').replace('٬', '')
        stock = int(stock_str) if stock_str else 0
        description = request.form.get('description', '').strip() or None

        cursor = conn.execute('''
            UPDATE products SET 
            name = ?, sku = ?, buy_price_tomans = ?, buy_price_usd = ?, 
            sell_price = ?, warehouse = ?, stock = ?, description = ?
            WHERE id = ?
        ''', (name, sku, buy_tomans, usd, sell_price, warehouse, stock, description, product_id))
        conn.commit()
        flash('کالا با موفقیت ویرایش شد.', 'success')
        return redirect(url_for('products'))
    except ValueError as e:
        flash(f'لطفاً مقادیر عددی را به درستی وارد کنید. خطا: {str(e)}', 'danger')
        return redirect(url_for('products', edit=product_id))
    except Exception as e:
        flash(f'خطا در ویرایش کالا: {str(e)}', 'danger')
        return redirect(url_for('products', edit=product_id))
    finally:
        conn.close()

# مسیرهای جدید برای مدیریت هزینه‌های مغازه
@app.route('/shop_expenses')
@login_required
@permission_required('reports.view')
def shop_expenses():
    """صفحه مدیریت هزینه‌های مغازه"""
    # دسته‌بندی‌های پیش‌فرض هزینه‌ها
    expense_categories = [
        'اجاره مغازه',
        'قبض برق',
        'قبض آب',
        'قبض گاز',
        'قبض تلفن',
        'اینترنت',
        'مالیات',
        'عوارض',
        'حقوق شاگرد',
        'حقوق فروشنده',
        'صبحانه',
        'ناهار',
        'باربری',
        'حمل و نقل',
        'بنزین',
        'تعمیرات',
        'لوازم اداری',
        'بسته‌بندی',
        'بازاریابی',
        'سایر'
    ]
    return render_template('shop_expenses.html', 
                         categories=expense_categories)

@app.route('/api/shop_expenses', methods=['GET', 'POST'])
@login_required
@permission_required('reports.view')
def api_shop_expenses():
    """API برای مدیریت هزینه‌ها"""
    conn = get_db()
    if request.method == 'POST':
        try:
            # دریافت داده‌ها از فرم
            expense_date = request.form['expense_date']
            category = request.form['category']
            description = request.form['description']
            amount_str = request.form['amount'].replace(',', '').replace('٬', '')
            amount = float(amount_str)
            payment_method = request.form.get('payment_method', 'cash')
            receipt_number = request.form.get('receipt_number', '').strip() or None
            notes = request.form.get('notes', '').strip() or None

            # تبدیل تاریخ شمسی به میلادی
            year, month, day = map(int, expense_date.split('/'))
            gregorian_date = jdatetime.date(year, month, day).togregorian()
            now_greg = browser_now().isoformat()
            now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')

            # ذخیره در دیتابیس
            cursor = conn.execute('''
                INSERT INTO shop_expenses 
                (expense_date_jalali, expense_date_greg, category, description, amount, 
                 payment_method, receipt_number, notes, created_by, created_date_greg, created_date_jalali)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (expense_date, gregorian_date.isoformat(), category, description, amount,
                  payment_method, receipt_number, notes, session['user_id'], now_greg, now_jalali))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت ثبت شد'})
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    else:  # GET request
        # پارامترهای فیلتر
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        category = request.args.get('category', '')
        query = '''
            SELECT se.*, u.username as created_by_name 
            FROM shop_expenses se
            LEFT JOIN users u ON se.created_by = u.id
            WHERE 1=1
        '''
        params = []
        if start_date:
            query += ' AND se.expense_date_jalali >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND se.expense_date_jalali <= ?'
            params.append(end_date)
        if category:
            query += ' AND se.category = ?'
            params.append(category)
        query += ' ORDER BY se.expense_date_greg DESC'
        expenses = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([dict(expense) for expense in expenses])

@app.route('/api/shop_expenses/<int:expense_id>', methods=['PUT', 'DELETE'])
@login_required
@permission_required('reports.view')
def api_single_expense(expense_id):
    """API برای ویرایش و حذف هزینه"""
    conn = get_db()
    if request.method == 'PUT':
        try:
            data = request.get_json()
            # تبدیل تاریخ شمسی به میلادی
            year, month, day = map(int, data['expense_date'].split('/'))
            gregorian_date = jdatetime.date(year, month, day).togregorian()

            conn.execute('''
                UPDATE shop_expenses 
                SET expense_date_jalali = ?, expense_date_greg = ?, category = ?, 
                    description = ?, amount = ?, payment_method = ?, receipt_number = ?, notes = ?
                WHERE id = ?
            ''', (data['expense_date'], gregorian_date.isoformat(), data['category'],
                  data['description'], data['amount'], data.get('payment_method', 'cash'),
                  data.get('receipt_number'), data.get('notes'), expense_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت ویرایش شد'})
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    elif request.method == 'DELETE':
        try:
            conn.execute('DELETE FROM shop_expenses WHERE id = ?', (expense_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'هزینه با موفقیت حذف شد'})
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shop_expenses_summary')
@login_required
@permission_required('reports.view')
def api_shop_expenses_summary():
    """API برای گزارش خلاصه هزینه‌ها"""
    conn = get_db()
    # پارامترهای فیلتر
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # گزارش ماهانه
    monthly_query = '''
        SELECT 
            SUBSTR(expense_date_jalali, 1, 7) as month,
            SUM(amount) as total_amount,
            COUNT(*) as expense_count
        FROM shop_expenses 
        WHERE 1=1
    '''
    monthly_params = []
    if start_date:
        monthly_query += ' AND expense_date_jalali >= ?'
        monthly_params.append(start_date)
    if end_date:
        monthly_query += ' AND expense_date_jalali <= ?'
        monthly_params.append(end_date)
    monthly_query += ' GROUP BY SUBSTR(expense_date_jalali, 1, 7) ORDER BY month DESC'
    monthly_summary = conn.execute(monthly_query, monthly_params).fetchall()

    # گزارش بر اساس دسته‌بندی
    category_query = '''
        SELECT 
            category,
            SUM(amount) as total_amount,
            COUNT(*) as expense_count
        FROM shop_expenses 
        WHERE 1=1
    '''
    category_params = []
    if start_date:
        category_query += ' AND expense_date_jalali >= ?'
        category_params.append(start_date)
    if end_date:
        category_query += ' AND expense_date_jalali <= ?'
        category_params.append(end_date)
    category_query += ' GROUP BY category ORDER BY total_amount DESC'
    category_summary = conn.execute(category_query, category_params).fetchall()

    # مجموع کل هزینه‌ها
    total_query = 'SELECT SUM(amount) as total FROM shop_expenses WHERE 1=1'
    total_params = []
    if start_date:
        total_query += ' AND expense_date_jalali >= ?'
        total_params.append(start_date)
    if end_date:
        total_query += ' AND expense_date_jalali <= ?'
        total_params.append(end_date)
    total_result = conn.execute(total_query, total_params).fetchone()
    total_expenses = total_result['total'] or 0

    conn.close()
    return jsonify({
        'monthly_summary': [dict(row) for row in monthly_summary],
        'category_summary': [dict(row) for row in category_summary],
        'total_expenses': total_expenses
    })

@app.route('/api/shop_expenses_report')
@login_required
@permission_required('reports.view')
def api_shop_expenses_report():
    """API برای گزارش تفصیلی هزینه‌ها"""
    conn = get_db()
    # پارامترهای فیلتر
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    category = request.args.get('category', '')

    # گزارش روزانه
    daily_query = '''
        SELECT 
            expense_date_jalali as date,
            SUM(amount) as total_amount,
            COUNT(*) as expense_count
        FROM shop_expenses 
        WHERE 1=1
    '''
    daily_params = []
    if start_date:
        daily_query += ' AND expense_date_jalali >= ?'
        daily_params.append(start_date)
    if end_date:
        daily_query += ' AND expense_date_jalali <= ?'
        daily_params.append(end_date)
    if category:
        daily_query += ' AND category = ?'
        daily_params.append(category)
    daily_query += ' GROUP BY expense_date_jalali ORDER BY expense_date_greg DESC'
    daily_summary = conn.execute(daily_query, daily_params).fetchall()

    # گزارش ماهانه
    monthly_query = '''
        SELECT 
            SUBSTR(expense_date_jalali, 1, 7) as month,
            SUM(amount) as total_amount,
            COUNT(*) as expense_count
        FROM shop_expenses 
        WHERE 1=1
    '''
    monthly_params = []
    if start_date:
        monthly_query += ' AND expense_date_jalali >= ?'
        monthly_params.append(start_date)
    if end_date:
        monthly_query += ' AND expense_date_jalali <= ?'
        monthly_params.append(end_date)
    if category:
        monthly_query += ' AND category = ?'
        monthly_params.append(category)
    monthly_query += ' GROUP BY SUBSTR(expense_date_jalali, 1, 7) ORDER BY month DESC'
    monthly_summary = conn.execute(monthly_query, monthly_params).fetchall()

    # ۱۰ هزینه بزرگ
    top_expenses_query = '''
        SELECT *
        FROM shop_expenses 
        WHERE 1=1
    '''
    top_params = []
    if start_date:
        top_expenses_query += ' AND expense_date_jalali >= ?'
        top_params.append(start_date)
    if end_date:
        top_expenses_query += ' AND expense_date_jalali <= ?'
        top_params.append(end_date)
    if category:
        top_expenses_query += ' AND category = ?'
        top_params.append(category)
    top_expenses_query += ' ORDER BY amount DESC LIMIT 10'
    top_expenses = conn.execute(top_expenses_query, top_params).fetchall()

    conn.close()
    return jsonify({
        'daily_summary': [dict(row) for row in daily_summary],
        'monthly_summary': [dict(row) for row in monthly_summary],
        'top_expenses': [dict(row) for row in top_expenses]
    })

@app.route('/api/create_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.create')
def api_create_purchase_list():
    """API برای ایجاد لیست خرید جدید"""
    try:
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None
        now_greg = browser_now().isoformat()
        now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')

        conn = get_db()
        cursor = conn.execute('''
            INSERT INTO purchase_lists (supplier_name, created_date_greg, created_date_jalali, notes)
            VALUES (?, ?, ?, ?)
        ''', (supplier_name, now_greg, now_jalali, notes))
        purchase_list_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'purchase_list_id': purchase_list_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/convert_purchase_list_to_invoice/<int:purchase_list_id>')
@login_required
@permission_required('invoices.create')
def convert_purchase_list_to_invoice(purchase_list_id):
    """تبدیل لیست خرید به فاکتور خرید و اضافه کردن به موجودی"""
    conn = get_db()
    try:
        # دریافت اطلاعات لیست خرید
        purchase_list = conn.execute(
            "SELECT * FROM purchase_lists WHERE id = ?", 
            (purchase_list_id,)
        ).fetchone()
        if not purchase_list:
            flash('لیست خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_lists'))

        # دریافت آیتم‌های لیست خرید
        items = conn.execute('''
            SELECT * FROM purchase_list_items 
            WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchall()

        if not items:
            flash('لیست خرید خالی است.', 'warning')
            return redirect(url_for('purchase_lists'))

        # ایجاد فاکتور خرید
        now_greg = browser_now()
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_greg).strftime('%Y/%m/%d %H:%M')

        # محاسبه مجموع
        total_amount = sum(item['estimated_total_price'] or 0 for item in items)

        cursor = conn.execute('''
            INSERT INTO purchase_invoices 
            (purchase_list_id, supplier_name, invoice_date_jalali, invoice_date_greg, 
             total_amount, remaining_amount, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (purchase_list_id, purchase_list['supplier_name'], now_jalali, 
              now_greg.isoformat(), total_amount, total_amount, 'pending', 
              purchase_list['notes']))
        invoice_id = cursor.lastrowid

        # **افزودن کالاها به موجودی - این بخش جدید است**
        for item in items:
            if item['product_id']:
                # به‌روزرسانی موجودی محصول
                conn.execute('''
                    UPDATE products 
                    SET stock = stock + ?, 
                        buy_price_tomans = COALESCE(?, buy_price_tomans)
                    WHERE id = ?
                ''', (item['quantity_to_buy'], item['estimated_unit_price'], item['product_id']))

        # به‌روزرسانی وضعیت لیست خرید
        conn.execute(
            "UPDATE purchase_lists SET status = 'converted' WHERE id = ?", 
            (purchase_list_id,)
        )

        conn.commit()
        flash('لیست خرید با موفقیت به فاکتور خرید تبدیل شد و موجودی کالاها به‌روزرسانی شد.', 'success')
        return redirect(url_for('purchase_invoices_list'))
    except Exception as e:
        conn.rollback()
        flash(f'خطا در تبدیل لیست خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_lists'))
    finally:
        conn.close()

@app.route('/purchase_invoice/<int:invoice_id>/receive_stock', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def receive_purchase_invoice_stock(invoice_id):
    """دریافت موجودی از فاکتور خرید (اگر موجودی اضافه نشده باشد)"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور خرید
        invoice = conn.execute('''
            SELECT pi.*, pl.id as purchase_list_id
            FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
            WHERE pi.id = ?
        ''', (invoice_id,)).fetchone()
        if not invoice:
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404

        # دریافت آیتم‌های لیست خرید
        items = conn.execute('''
            SELECT * FROM purchase_list_items 
            WHERE purchase_list_id = ?
        ''', (invoice['purchase_list_id'],)).fetchall()

        # افزودن کالاها به موجودی
        for item in items:
            if item['product_id']:
                # به‌روزرسانی موجودی محصول
                conn.execute('''
                    UPDATE products 
                    SET stock = stock + ?, 
                        buy_price_tomans = COALESCE(?, buy_price_tomans)
                    WHERE id = ?
                ''', (item['quantity_to_buy'], item['estimated_unit_price'], item['product_id']))

        conn.commit()
        return jsonify({'success': True, 'message': 'موجودی کالاها با موفقیت اضافه شد.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/purchase_invoice/<int:invoice_id>/print')
@login_required
@permission_required('purchase.view')
def print_purchase_invoice(invoice_id):
    """چاپ فاکتور خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور خرید
        invoice = conn.execute('''
            SELECT pi.*, pl.notes as purchase_notes
            FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
            WHERE pi.id = ?
        ''', (invoice_id,)).fetchone()
        if not invoice:
            flash('فاکتور خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_invoices_list'))

        # دریافت آیتم‌های فاکتور خرید
        items = conn.execute('''
            SELECT pli.*
            FROM purchase_list_items pli
            WHERE pli.purchase_list_id = ?
        ''', (invoice['purchase_list_id'],)).fetchall()
        conn.close()
        return render_template('print_purchase_invoice.html', 
                             invoice=invoice, 
                             items=items)
    except Exception as e:
        conn.close()
        flash(f'خطا در بارگذاری فاکتور خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_invoices_list'))

@app.route('/print_purchase_list/<int:purchase_list_id>')
@login_required
@permission_required('purchase.view')
def print_purchase_list(purchase_list_id):
    """چاپ لیست خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات لیست خرید
        purchase_list = conn.execute(
            "SELECT * FROM purchase_lists WHERE id = ?", 
            (purchase_list_id,)
        ).fetchone()
        if not purchase_list:
            flash('لیست خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_lists'))

        # دریافت آیتم‌های لیست خرید
        items = conn.execute('''
            SELECT pli.*, p.stock as current_stock
            FROM purchase_list_items pli
            LEFT JOIN products p ON pli.product_id = p.id
            WHERE pli.purchase_list_id = ?
        ''', (purchase_list_id,)).fetchall()
        conn.close()

        # تاریخ و زمان فعلی برای چاپ
        current_datetime = browser_jalali_now().strftime('%Y/%m/%d %H:%M')
        return render_template('print_purchase_list.html', 
                             purchase_list=purchase_list, 
                             items=items,
                             current_datetime=current_datetime)
    except Exception as e:
        conn.close()
        flash(f'خطا در بارگذاری لیست خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_lists'))

@app.route('/api/get_purchase_list_items/<int:list_id>')
@login_required
@permission_required('purchase.view')
def get_purchase_list_items(list_id):
    """دریافت آیتم‌های لیست خرید"""
    conn = get_db()
    items = conn.execute('''
        SELECT pli.*, p.stock as current_stock
        FROM purchase_list_items pli
        LEFT JOIN products p ON pli.product_id = p.id
        WHERE pli.purchase_list_id = ?
    ''', (list_id,)).fetchall()
    conn.close()
    return jsonify([dict(item) for item in items])

@app.route('/api/update_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_list():
    """ویرایش لیست خرید"""
    try:
        purchase_list_id = request.form['purchase_list_id']
        supplier_name = request.form['supplier_name'].strip()
        notes = request.form.get('notes', '').strip() or None

        conn = get_db()
        conn.execute('''
            UPDATE purchase_lists 
            SET supplier_name = ?, notes = ?
            WHERE id = ?
        ''', (supplier_name, notes, purchase_list_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete_purchase_list/<int:list_id>', methods=['POST'])
@login_required
@permission_required('purchase.delete')
def delete_purchase_list(list_id):
    """حذف لیست خرید"""
    try:
        conn = get_db()
        # حذف آیتم‌های لیست خرید
        conn.execute('DELETE FROM purchase_list_items WHERE purchase_list_id = ?', (list_id,))
        # حذف لیست خرید
        conn.execute('DELETE FROM purchase_lists WHERE id = ?', (list_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/add_to_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.create')
def add_to_purchase_list():
    """افزودن محصول به لیست خرید"""
    try:
        purchase_list_id = request.form['purchase_list_id']
        product_id = request.form['product_id']
        quantity = int(request.form['quantity'])
        notes = request.form.get('notes', '').strip() or None

        conn = get_db()
        # دریافت اطلاعات محصول
        product = conn.execute(
            "SELECT name, sku, stock, buy_price_tomans FROM products WHERE id = ?", 
            (product_id,)
        ).fetchone()
        if not product:
            return jsonify({'success': False, 'error': 'محصول یافت نشد'}), 400

        # بررسی وجود آیتم در لیست
        existing_item = conn.execute('''
            SELECT * FROM purchase_list_items 
            WHERE purchase_list_id = ? AND product_id = ?
        ''', (purchase_list_id, product_id)).fetchone()

        if existing_item:
            # به‌روزرسانی تعداد
            conn.execute('''
                UPDATE purchase_list_items 
                SET quantity_to_buy = quantity_to_buy + ?, notes = COALESCE(?, notes)
                WHERE id = ?
            ''', (quantity, notes, existing_item['id']))
        else:
            # افزودن آیتم جدید
            estimated_unit_price = product['buy_price_tomans'] or 0
            estimated_total_price = estimated_unit_price * quantity

            conn.execute('''
                INSERT INTO purchase_list_items 
                (purchase_list_id, product_id, product_name, sku, current_stock, 
                 quantity_to_buy, estimated_unit_price, estimated_total_price, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (purchase_list_id, product_id, product['name'], product['sku'], 
                  product['stock'], quantity, estimated_unit_price, 
                  estimated_total_price, notes))

        # به‌روزرسانی مجموع تخمینی لیست خرید
        conn.execute('''
            UPDATE purchase_lists 
            SET estimated_total = (
                SELECT SUM(estimated_total_price) 
                FROM purchase_list_items 
                WHERE purchase_list_id = ?
            )
            WHERE id = ?
        ''', (purchase_list_id, purchase_list_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/search_products_by_tab')
@login_required
@permission_required('products.view')
def search_products_by_tab():
    """API برای جستجوی محصولات بر اساس تب"""
    try:
        tab = request.args.get('tab', '')
        query = request.args.get('q', '').lower().strip()
        query = normalize_persian_text(query)

        conn = get_db()
        # تعریف شرط WHERE بر اساس نوع تب
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
            # برای سایر تب‌ها همه محصولات
            where_conditions.append("1=1")

        # اضافه کردن شرط جستجو
        if query:
            where_conditions.append("""
                (LOWER(name) LIKE ? OR LOWER(sku) LIKE ? OR 
                 LOWER(REPLACE(REPLACE(name, 'ي', 'ی'), 'ك', 'ک')) LIKE ? OR
                 LOWER(REPLACE(REPLACE(sku, 'ي', 'ی'), 'ك', 'ک')) LIKE ?)
            """)
            search_pattern = f"%{query}%"
            params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

        # ساخت کوئری
        where_clause = " AND ".join(where_conditions)
        if tab == 'top-selling':
            order_clause = "ORDER BY sales_count DESC"
        else:
            order_clause = "ORDER BY name"

        sql = f"""
            SELECT * FROM products 
            WHERE {where_clause}
            {order_clause}
        """
        products = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify([dict(p) for p in products])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_purchase_item_quantity', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_item_quantity():
    """به‌روزرسانی تعداد آیتم در لیست خرید"""
    try:
        item_id = request.form['item_id']
        quantity = int(request.form['quantity'])
        if quantity <= 0:
            return jsonify({'success': False, 'error': 'تعداد باید بیشتر از صفر باشد'}), 400

        conn = get_db()
        # دریافت اطلاعات آیتم
        item = conn.execute(
            "SELECT * FROM purchase_list_items WHERE id = ?", 
            (item_id,)
        ).fetchone()
        if not item:
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404

        # محاسبه قیمت جدید
        estimated_total_price = item['estimated_unit_price'] * quantity

        conn.execute('''
            UPDATE purchase_list_items 
            SET quantity_to_buy = ?, estimated_total_price = ?
            WHERE id = ?
        ''', (quantity, estimated_total_price, item_id))

        # محاسبه مجموع جدید فاکتور خرید - تغییر کرده است
        total_result = conn.execute('''
            SELECT 
                COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items 
            WHERE purchase_list_id = ?
        ''', (item['purchase_list_id'],)).fetchone()
        new_total = total_result['total_amount']

        # به‌روزرسانی فاکتور خرید - تغییر کرده است
        conn.execute('''
            UPDATE purchase_invoices 
            SET total_amount = ?,
                remaining_amount = ? - paid_amount
            WHERE purchase_list_id = ?
        ''', (new_total, new_total, item['purchase_list_id']))

        conn.commit()
        # دریافت اطلاعات به‌روز شده فاکتور
        updated_invoice = conn.execute('''
            SELECT total_amount, paid_amount, remaining_amount 
            FROM purchase_invoices WHERE purchase_list_id = ?
        ''', (item['purchase_list_id'],)).fetchone()
        conn.close()

        return jsonify({
            'success': True,
            'new_invoice_total': new_total,
            'new_paid_amount': updated_invoice['paid_amount'],
            'new_remaining_amount': updated_invoice['remaining_amount']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/remove_from_purchase_list', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def remove_from_purchase_list():
    """حذف آیتم از لیست خرید (فقط قبل از تبدیل به فاکتور)"""
    try:
        item_id = request.form['item_id']
        conn = get_db()
        # دریافت اطلاعات آیتم برای پیدا کردن purchase_list_id و بررسی وضعیت لیست
        item = conn.execute('''
            SELECT pli.purchase_list_id, pl.status
            FROM purchase_list_items pli
            JOIN purchase_lists pl ON pli.purchase_list_id = pl.id
            WHERE pli.id = ?
        ''', (item_id,)).fetchone()
        if not item:
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404

        purchase_list_id = item['purchase_list_id']
        list_status = item['status']

        # اگر لیست قبلاً تبدیل شده باشد، نباید آیتم حذف شود
        if list_status == 'converted':
            return jsonify({'success': False, 'error': 'امکان حذف آیتم از یک لیست تبدیل شده به فاکتور وجود ندارد. لطفاً از صفحه ویرایش فاکتور استفاده کنید.'}), 400

        # حذف آیتم
        conn.execute('DELETE FROM purchase_list_items WHERE id = ?', (item_id,))
        # به‌روزرسانی مجموع تخمینی لیست خرید
        conn.execute('''
            UPDATE purchase_lists 
            SET estimated_total = (
                SELECT SUM(estimated_total_price) 
                FROM purchase_list_items 
                WHERE purchase_list_id = ?
            )
            WHERE id = ?
        ''', (purchase_list_id, purchase_list_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/purchase_invoice/<int:invoice_id>')
@login_required
@permission_required('purchase.view')
def purchase_invoice_detail(invoice_id):
    """صفحه نمایش فاکتور خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور خرید
        invoice = conn.execute('''
            SELECT pi.*, pl.notes as purchase_notes
            FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
            WHERE pi.id = ?
        ''', (invoice_id,)).fetchone()
        if not invoice:
            flash('فاکتور خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_invoices_list'))

        # دریافت آیتم‌های فاکتور خرید
        items = conn.execute('''
            SELECT pli.*
            FROM purchase_list_items pli
            WHERE pli.purchase_list_id = ?
        ''', (invoice['purchase_list_id'],)).fetchall()
        conn.close()
        return render_template('purchase_invoice.html', 
                             invoice=invoice, 
                             items=items)
    except Exception as e:
        conn.close()
        flash(f'خطا در بارگذاری فاکتور خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_invoices_list'))

@app.route('/purchase_invoice/<int:invoice_id>/update_status', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_invoice_status(invoice_id):
    """به‌روزرسانی وضعیت فاکتور خرید"""
    try:
        status = request.form['status']
        paid_amount = float(request.form.get('paid_amount', 0))

        conn = get_db()
        # دریافت اطلاعات فعلی فاکتور
        invoice = conn.execute(
            "SELECT * FROM purchase_invoices WHERE id = ?", 
            (invoice_id,)
        ).fetchone()
        if not invoice:
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404

        # محاسبه مبلغ باقیمانده
        remaining_amount = invoice['total_amount'] - paid_amount

        conn.execute('''
            UPDATE purchase_invoices 
            SET status = ?, paid_amount = ?, remaining_amount = ?
            WHERE id = ?
        ''', (status, paid_amount, remaining_amount, invoice_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/purchase_invoice/<int:invoice_id>/add_payment', methods=['POST'])
@login_required
@permission_required('payments.add')
def add_purchase_invoice_payment(invoice_id):
    """ثبت پرداخت برای فاکتور خرید"""
    try:
        amount = float(request.form['amount'])
        payment_method = request.form.get('payment_method', 'cash')
        description = request.form.get('description', '').strip() or None

        conn = get_db()
        # دریافت اطلاعات فعلی فاکتور
        invoice = conn.execute(
            "SELECT * FROM purchase_invoices WHERE id = ?", 
            (invoice_id,)
        ).fetchone()
        if not invoice:
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404

        # محاسبه مبالغ جدید
        new_paid_amount = invoice['paid_amount'] + amount
        new_remaining_amount = invoice['total_amount'] - new_paid_amount

        # به‌روزرسانی فاکتور
        conn.execute('''
            UPDATE purchase_invoices 
            SET paid_amount = ?, remaining_amount = ?
            WHERE id = ?
        ''', (new_paid_amount, new_remaining_amount, invoice_id))

        # اگر مبلغ پرداختی کامل شد، وضعیت را به paid تغییر بده
        if new_remaining_amount <= 0:
            conn.execute('''
                UPDATE purchase_invoices 
                SET status = 'paid'
                WHERE id = ?
            ''', (invoice_id,))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/purchase_invoice/<int:invoice_id>/delete', methods=['POST'])
@login_required
@permission_required('purchase.delete')
def delete_purchase_invoice(invoice_id):
    """حذف فاکتور خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور برای بازگرداندن موجودی
        invoice = conn.execute('''
            SELECT pi.*, pl.id as purchase_list_id
            FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
            WHERE pi.id = ?
        ''', (invoice_id,)).fetchone()
        if not invoice:
            return jsonify({'success': False, 'error': 'فاکتور خرید یافت نشد'}), 404

        # دریافت آیتم‌های لیست خرید
        items = conn.execute('''
            SELECT * FROM purchase_list_items 
            WHERE purchase_list_id = ?
        ''', (invoice['purchase_list_id'],)).fetchall()

        # **کاهش موجودی کالاها (برعکس عملیات دریافت)**
        for item in items:
            if item['product_id']:
                # استفاده از CASE برای جلوگیری از موجودی منفی
                conn.execute('''
                    UPDATE products 
                    SET stock = CASE 
                        WHEN stock >= ? THEN stock - ? 
                        ELSE 0 
                    END
                    WHERE id = ?
                ''', (item['quantity_to_buy'], item['quantity_to_buy'], item['product_id']))

        # حذف فاکتور خرید
        conn.execute('DELETE FROM purchase_invoices WHERE id = ?', (invoice_id,))

        # بازگرداندن وضعیت لیست خرید به active
        if invoice['purchase_list_id']:
            conn.execute(
                "UPDATE purchase_lists SET status = 'active' WHERE id = ?", 
                (invoice['purchase_list_id'],)
            )

        conn.commit()
        return jsonify({
            'success': True, 
            'message': 'فاکتور خرید با موفقیت حذف شد و موجودی کالاها کاهش یافت.'
        })
    except Exception as e:
        conn.rollback()
        print(f"خطا در حذف فاکتور خرید: {str(e)}")  # برای دیباگ
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

# اضافه کردن route جدید برای ویرایش قیمت آیتم‌های فاکتور خرید
@app.route('/api/update_purchase_item_price', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def update_purchase_item_price():
    """ویرایش قیمت آیتم در فاکتور خرید (و به‌روزرسانی مجموع فاکتور)"""
    try:
        item_id = request.form['item_id']
        actual_unit_price = float(request.form['actual_unit_price'])

        conn = get_db()
        # دریافت اطلاعات آیتم
        item = conn.execute('''
            SELECT quantity_to_buy, purchase_list_id 
            FROM purchase_list_items WHERE id = ?
        ''', (item_id,)).fetchone()
        if not item:
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404

        quantity = item['quantity_to_buy']
        purchase_list_id = item['purchase_list_id']
        actual_total_price = actual_unit_price * quantity

        # به‌روزرسانی قیمت واقعی
        conn.execute('''
            UPDATE purchase_list_items 
            SET actual_unit_price = ?, actual_total_price = ? 
            WHERE id = ?
        ''', (actual_unit_price, actual_total_price, item_id))

        # محاسبه مجموع جدید فاکتور خرید
        total_result = conn.execute('''
            SELECT 
                COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items 
            WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
        new_total = total_result['total_amount']

        # به‌روزرسانی فاکتور خرید
        conn.execute('''
            UPDATE purchase_invoices 
            SET total_amount = ?,
                remaining_amount = ? - paid_amount
            WHERE purchase_list_id = ?
        ''', (new_total, new_total, purchase_list_id))

        conn.commit()
        # دریافت اطلاعات به‌روز شده فاکتور
        updated_invoice = conn.execute('''
            SELECT total_amount, paid_amount, remaining_amount 
            FROM purchase_invoices WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
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

# اضافه کردن route برای مدیریت پرداخت‌های فاکتور خرید
@app.route('/api/purchase_invoice_payments/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
@permission_required('payments.add')
def api_purchase_invoice_payments(invoice_id):
    """مدیریت پرداخت‌های فاکتور خرید"""
    conn = get_db()
    if request.method == 'POST':
        try:
            # دریافت داده‌ها
            amount_str = request.form['amount'].replace(',', '').replace('٬', '')
            amount = float(amount_str)
            payment_method = request.form['payment_method']
            sheba_number = request.form.get('sheba_number', '').strip() or None
            account_owner = request.form.get('account_owner', '').strip() or None
            bank_name = request.form.get('bank_name', '').strip() or None
            description = request.form.get('description', '').strip() or None
            receipt_number = request.form.get('receipt_number', '').strip() or None

            now_greg = browser_now().isoformat()
            now_jalali = browser_jalali_now().strftime('%Y/%m/%d %H:%M')

            # ثبت پرداخت
            conn.execute('''
                INSERT INTO purchase_payments 
                (purchase_invoice_id, amount, payment_method, sheba_number, account_owner, 
                 bank_name, payment_date_jalali, payment_date_greg, description, receipt_number,
                 created_by, created_date_greg, created_date_jalali)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (invoice_id, amount, payment_method, sheba_number, account_owner,
                  bank_name, now_jalali, now_greg, description, receipt_number,
                  session['user_id'], now_greg, now_jalali))

            # محاسبه مجموع پرداخت‌ها
            total_paid_result = conn.execute('''
                SELECT SUM(amount) as total_paid 
                FROM purchase_payments 
                WHERE purchase_invoice_id = ?
            ''', (invoice_id,)).fetchone()
            total_paid = total_paid_result['total_paid'] or 0

            # به‌روزرسانی فاکتور خرید
            invoice = conn.execute('''
                SELECT total_amount FROM purchase_invoices WHERE id = ?
            ''', (invoice_id,)).fetchone()
            remaining_amount = invoice['total_amount'] - total_paid
            new_status = 'paid' if total_paid >= invoice['total_amount'] else 'pending'

            conn.execute('''
                UPDATE purchase_invoices 
                SET paid_amount = ?, remaining_amount = ?, status = ?
                WHERE id = ?
            ''', (total_paid, remaining_amount, new_status, invoice_id))

            conn.commit()
            conn.close()
            return jsonify({
                'success': True, 
                'message': 'پرداخت با موفقیت ثبت شد',
                'new_paid_amount': total_paid,
                'new_remaining_amount': remaining_amount,
                'new_status': new_status
            })
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'error': str(e)}), 500
    else:  # GET request
        # دریافت لیست پرداخت‌ها
        payments = conn.execute('''
            SELECT pp.*, u.username as created_by_name
            FROM purchase_payments pp
            LEFT JOIN users u ON pp.created_by = u.id
            WHERE pp.purchase_invoice_id = ?
            ORDER BY pp.payment_date_greg DESC
        ''', (invoice_id,)).fetchall()
        conn.close()
        return jsonify([dict(payment) for payment in payments])

@app.route('/api/purchase_payments/<int:payment_id>/delete', methods=['POST'])
@login_required
@permission_required('payments.delete')
def delete_purchase_payment(payment_id):
    """حذف پرداخت فاکتور خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات پرداخت
        payment = conn.execute('''
            SELECT * FROM purchase_payments WHERE id = ?
        ''', (payment_id,)).fetchone()
        if not payment:
            return jsonify({'success': False, 'error': 'پرداخت یافت نشد'}), 404

        invoice_id = payment['purchase_invoice_id']

        # حذف پرداخت
        conn.execute('DELETE FROM purchase_payments WHERE id = ?', (payment_id,))

        # محاسبه مجموع پرداخت‌های باقیمانده
        total_paid_result = conn.execute('''
            SELECT SUM(amount) as total_paid 
            FROM purchase_payments 
            WHERE purchase_invoice_id = ?
        ''', (invoice_id,)).fetchone()
        total_paid = total_paid_result['total_paid'] or 0

        # به‌روزرسانی فاکتور خرید
        invoice = conn.execute('''
            SELECT total_amount FROM purchase_invoices WHERE id = ?
        ''', (invoice_id,)).fetchone()
        remaining_amount = invoice['total_amount'] - total_paid
        new_status = 'paid' if total_paid >= invoice['total_amount'] else 'pending'

        conn.execute('''
            UPDATE purchase_invoices 
            SET paid_amount = ?, remaining_amount = ?, status = ?
            WHERE id = ?
        ''', (total_paid, remaining_amount, new_status, invoice_id))

        conn.commit()
        conn.close()
        return jsonify({
            'success': True, 
            'message': 'پرداخت با موفقیت حذف شد',
            'new_paid_amount': total_paid,
            'new_remaining_amount': remaining_amount,
            'new_status': new_status
        })
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/invoice/<int:invoice_id>/delete', methods=['POST'])
@login_required
@permission_required('invoices.delete')
def delete_invoice(invoice_id):
    """حذف فاکتور فروش"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور
        invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not invoice:
            return jsonify({'success': False, 'error': 'فاکتور یافت نشد'}), 404

        # بازگرداندن موجودی کالاها
        items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
        for item in items:
            conn.execute("UPDATE products SET stock = stock + ?, sales_count = sales_count - ? WHERE id = ?", 
                       (item['quantity'], item['quantity'], item['product_id']))

        # حذف پرداخت‌ها
        conn.execute("DELETE FROM payments WHERE invoice_id = ?", (invoice_id,))

        # حذف آیتم‌های فاکتور
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

        # حذف فاکتور
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'فاکتور با موفقیت حذف شد و موجودی کالاها بازگردانی شد.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/purchase_invoice/<int:invoice_id>/edit')
@login_required
@permission_required('purchase.edit')
def edit_purchase_invoice(invoice_id):
    """صفحه ویرایش فاکتور خرید"""
    conn = get_db()
    try:
        # دریافت اطلاعات فاکتور خرید
        invoice = conn.execute('''
            SELECT pi.*, pl.notes as purchase_notes
            FROM purchase_invoices pi
            LEFT JOIN purchase_lists pl ON pi.purchase_list_id = pl.id
            WHERE pi.id = ?
        ''', (invoice_id,)).fetchone()
        if not invoice:
            flash('فاکتور خرید یافت نشد.', 'danger')
            return redirect(url_for('purchase_invoices_list'))

        # دریافت آیتم‌های فاکتور خرید
        items = conn.execute('''
            SELECT pli.*, p.stock as current_stock
            FROM purchase_list_items pli
            LEFT JOIN products p ON pli.product_id = p.id
            WHERE pli.purchase_list_id = ?
        ''', (invoice['purchase_list_id'],)).fetchall()
        conn.close()
        return render_template('edit_purchase_invoice.html', 
                             invoice=invoice, 
                             items=items)
    except Exception as e:
        conn.close()
        flash(f'خطا در بارگذاری فاکتور خرید: {str(e)}', 'danger')
        return redirect(url_for('purchase_invoices_list'))

@app.route('/api/purchase_invoice/<int:invoice_id>/update_info', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_info(invoice_id):
    """به‌روزرسانی اطلاعات کلی فاکتور خرید"""
    try:
        supplier_name = request.form['supplier_name'].strip()
        status = request.form['status']
        notes = request.form.get('notes', '').strip() or None

        conn = get_db()
        # به‌روزرسانی اطلاعات فاکتور خرید
        conn.execute('''
            UPDATE purchase_invoices 
            SET supplier_name = ?, status = ?, notes = ?
            WHERE id = ?
        ''', (supplier_name, status, notes, invoice_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'اطلاعات فاکتور با موفقیت به‌روزرسانی شد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/purchase_invoice/<int:invoice_id>/update_financial', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_financial(invoice_id):
    """به‌روزرسانی اطلاعات مالی فاکتور خرید"""
    try:
        total_amount = float(request.form['total_amount'])
        paid_amount = float(request.form['paid_amount'])
        remaining_amount = total_amount - paid_amount

        conn = get_db()
        # به‌روزرسانی اطلاعات مالی فاکتور خرید
        conn.execute('''
            UPDATE purchase_invoices 
            SET total_amount = ?, paid_amount = ?, remaining_amount = ?
            WHERE id = ?
        ''', (total_amount, paid_amount, remaining_amount, invoice_id))
        conn.commit()
        conn.close()
        return jsonify({
            'success': True, 
            'message': 'اطلاعات مالی با موفقیت به‌روزرسانی شد',
            'total_amount': total_amount,
            'paid_amount': paid_amount,
            'remaining_amount': remaining_amount
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# --- APIهای جدید برای ویرایش/حذف آیتم‌های فاکتور خرید ---

@app.route('/api/update_purchase_invoice_item', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_update_purchase_invoice_item():
    """ویرایش جزئیات یک آیتم موجود در فاکتور خرید (نام، SKU، تعداد، قیمت، یادداشت)"""
    try:
        item_id = request.form['item_id']
        product_name = request.form.get('product_name', '').strip()
        sku = request.form.get('sku', '').strip() or None
        quantity_to_buy = int(request.form['quantity_to_buy'])
        estimated_unit_price = float(request.form['estimated_unit_price'])
        notes = request.form.get('notes', '').strip() or None

        # اعتبارسنجی
        if not product_name:
            return jsonify({'success': False, 'error': 'نام کالا الزامی است'}), 400
        if quantity_to_buy <= 0:
            return jsonify({'success': False, 'error': 'تعداد باید بیشتر از صفر باشد'}), 400
        if estimated_unit_price < 0: # قیمت می‌تواند صفر باشد (مثلاً نمونه کار)
            return jsonify({'success': False, 'error': 'قیمت واحد نمی‌تواند منفی باشد'}), 400

        conn = get_db()
        # دریافت اطلاعات فعلی آیتم برای پیدا کردن purchase_list_id
        item = conn.execute('''
            SELECT purchase_list_id, quantity_to_buy, estimated_unit_price
            FROM purchase_list_items
            WHERE id = ?
        ''', (item_id,)).fetchone()
        if not item:
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404

        purchase_list_id = item['purchase_list_id']
        old_quantity = item['quantity_to_buy']
        old_price = item['estimated_unit_price']

        # جستجوی یا ایجاد product_id بر اساس نام (اختیاری، ممکن است قبل از تبدیل به فاکتور ایجاد شده باشد)
        product_id = None
        if product_name:
            product = conn.execute('''
                SELECT id FROM products WHERE name = ? OR sku = ? LIMIT 1
            ''', (product_name, product_name)).fetchone()
            if product:
                product_id = product['id']

        # محاسبه قیمت کل جدید
        estimated_total_price = quantity_to_buy * estimated_unit_price

        # به‌روزرسانی آیتم
        conn.execute('''
            UPDATE purchase_list_items
            SET product_id = ?, product_name = ?, sku = ?, quantity_to_buy = ?, estimated_unit_price = ?, estimated_total_price = ?, notes = ?
            WHERE id = ?
        ''', (product_id, product_name, sku, quantity_to_buy, estimated_unit_price, estimated_total_price, notes, item_id))

        # محاسبه مجموع جدید فاکتور
        total_result = conn.execute('''
            SELECT
                COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items
            WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
        new_total = total_result['total_amount']

        # به‌روزرسانی فاکتور خرید
        conn.execute('''
            UPDATE purchase_invoices
            SET total_amount = ?,
                remaining_amount = ? - paid_amount
            WHERE purchase_list_id = ?
        ''', (new_total, new_total, purchase_list_id))

        conn.commit()
        # دریافت اطلاعات به‌روز شده فاکتور
        updated_invoice = conn.execute('''
            SELECT total_amount, paid_amount, remaining_amount
            FROM purchase_invoices WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'آیتم با موفقیت به‌روزرسانی شد',
            'new_invoice_total': new_total,
            'new_paid_amount': updated_invoice['paid_amount'],
            'new_remaining_amount': updated_invoice['remaining_amount']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete_purchase_invoice_item', methods=['POST'])
@login_required
@permission_required('purchase.edit')
def api_delete_purchase_invoice_item():
    """حذف یک آیتم از فاکتور خرید"""
    try:
        item_id = request.form['item_id']
        conn = get_db()
        # دریافت اطلاعات آیتم برای پیدا کردن purchase_list_id
        item = conn.execute('''
            SELECT purchase_list_id
            FROM purchase_list_items
            WHERE id = ?
        ''', (item_id,)).fetchone()
        if not item:
            conn.close()
            return jsonify({'success': False, 'error': 'آیتم یافت نشد'}), 404

        purchase_list_id = item['purchase_list_id']

        # حذف آیتم
        conn.execute('DELETE FROM purchase_list_items WHERE id = ?', (item_id,))

        # محاسبه مجموع جدید فاکتور
        total_result = conn.execute('''
            SELECT
                COALESCE(SUM(COALESCE(actual_total_price, estimated_total_price)), 0) as total_amount
            FROM purchase_list_items
            WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
        new_total = total_result['total_amount']

        # به‌روزرسانی فاکتور خرید
        conn.execute('''
            UPDATE purchase_invoices
            SET total_amount = ?,
                remaining_amount = ? - paid_amount
            WHERE purchase_list_id = ?
        ''', (new_total, new_total, purchase_list_id))

        conn.commit()
        # دریافت اطلاعات به‌روز شده فاکتور
        updated_invoice = conn.execute('''
            SELECT total_amount, paid_amount, remaining_amount
            FROM purchase_invoices WHERE purchase_list_id = ?
        ''', (purchase_list_id,)).fetchone()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'آیتم با موفقیت حذف شد',
            'new_invoice_total': new_total,
            'new_paid_amount': updated_invoice['paid_amount'],
            'new_remaining_amount': updated_invoice['remaining_amount']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Initialize the selected database before Gunicorn starts serving requests.
# Keep this call after init_db() and all required definitions.
try:
    init_db()
except Exception as db_init_error:
    print(f'خطا در مقداردهی اولیه دیتابیس: {db_init_error}')
    raise

if __name__ == '__main__':
    if not USE_POSTGRES:
        startup_auto_backup()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
