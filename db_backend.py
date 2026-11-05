"""PostgreSQL compatibility layer for the application's SQLite-style SQL calls."""
DB_ADAPTER_VERSION = "2026-11-05-login-hotfix-2"
import re, sqlite3
from psycopg2 import connect
from psycopg2.extras import DictCursor

TABLES = {
'products': '''id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, sku TEXT, buy_price_tomans DOUBLE PRECISION NOT NULL, buy_price_usd DOUBLE PRECISION, sell_price DOUBLE PRECISION NOT NULL, warehouse TEXT, stock INTEGER DEFAULT 0, description TEXT, sales_count INTEGER DEFAULT 0''',
'invoices': '''id BIGSERIAL PRIMARY KEY, customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL, date_jalali TEXT, date_greg TEXT, total_amount DOUBLE PRECISION, total_profit DOUBLE PRECISION, discount_amount DOUBLE PRECISION DEFAULT 0, discount_percent DOUBLE PRECISION DEFAULT 0, status TEXT DEFAULT 'active' ''',
'invoice_items': '''id BIGSERIAL PRIMARY KEY, invoice_id BIGINT, product_id BIGINT, product_name TEXT, sku TEXT, quantity INTEGER, unit_price DOUBLE PRECISION, profit_per_unit DOUBLE PRECISION, returned INTEGER DEFAULT 0''',
'payments': '''id BIGSERIAL PRIMARY KEY, invoice_id BIGINT NOT NULL, amount DOUBLE PRECISION NOT NULL, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT''',
'purchase_lists': '''id BIGSERIAL PRIMARY KEY, supplier_name TEXT NOT NULL, created_date_jalali TEXT, created_date_greg TEXT, status TEXT DEFAULT 'active', notes TEXT, estimated_total DOUBLE PRECISION DEFAULT 0, actual_total DOUBLE PRECISION DEFAULT 0''',
'purchase_list_items': '''id BIGSERIAL PRIMARY KEY, purchase_list_id BIGINT NOT NULL, product_id BIGINT, product_name TEXT NOT NULL, sku TEXT, current_stock INTEGER DEFAULT 0, quantity_to_buy INTEGER NOT NULL, estimated_unit_price DOUBLE PRECISION, actual_unit_price DOUBLE PRECISION, estimated_total_price DOUBLE PRECISION, actual_total_price DOUBLE PRECISION, notes TEXT''',
'purchase_invoices': '''id BIGSERIAL PRIMARY KEY, purchase_list_id BIGINT NOT NULL, supplier_name TEXT NOT NULL, invoice_date_jalali TEXT, invoice_date_greg TEXT, total_amount DOUBLE PRECISION, paid_amount DOUBLE PRECISION DEFAULT 0, remaining_amount DOUBLE PRECISION, payment_method TEXT, status TEXT DEFAULT 'pending', notes TEXT''',
'users': '''id BIGSERIAL PRIMARY KEY, full_name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', permissions TEXT, is_active BOOLEAN DEFAULT TRUE, created_date_greg TEXT, created_date_jalali TEXT, last_login_greg TEXT, last_login_jalali TEXT''',
'permissions_master': '''id BIGSERIAL PRIMARY KEY, permission_key TEXT UNIQUE NOT NULL, permission_name TEXT NOT NULL, category TEXT NOT NULL, description TEXT''',
'purchase_payments': '''id BIGSERIAL PRIMARY KEY, purchase_invoice_id BIGINT NOT NULL, amount DOUBLE PRECISION NOT NULL, payment_method TEXT NOT NULL, sheba_number TEXT, account_owner TEXT, bank_name TEXT, payment_date_jalali TEXT, payment_date_greg TEXT, description TEXT, receipt_number TEXT, created_by BIGINT, created_date_greg TEXT, created_date_jalali TEXT''',
'shop_expenses': '''id BIGSERIAL PRIMARY KEY, expense_date_jalali TEXT NOT NULL, expense_date_greg TEXT NOT NULL, category TEXT NOT NULL, description TEXT, amount DOUBLE PRECISION NOT NULL, payment_method TEXT DEFAULT 'cash', receipt_number TEXT, notes TEXT, created_by BIGINT, created_date_greg TEXT, created_date_jalali TEXT'''
}

class Cursor:
    def __init__(self, cur, lastrowid=None): self.cur, self.lastrowid = cur, lastrowid
    @property
    def rowcount(self): return self.cur.rowcount
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self): return self.cur.fetchall()
    def __iter__(self): return iter(self.cur)

class Connection:
    def __init__(self, raw): self.raw=raw
    def execute(self, sql, params=()):
        q=sql.strip()
        if q.upper().startswith('PRAGMA TABLE_INFO('):
            table=q[q.find('(')+1:q.rfind(')')].strip().strip('"')
            cur=self.raw.cursor()
            cur.execute("SELECT ordinal_position-1 AS cid, column_name AS name, data_type AS type, CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, column_default AS dflt_value, CASE WHEN column_name='id' THEN 1 ELSE 0 END AS pk FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,))
            return Cursor(cur)
        if 'sqlite_master' in q:
            table=params[0] if params else re.search(r"name\s*=\s*'([^']+)'",q).group(1)
            cur=self.raw.cursor(); cur.execute("SELECT 'CREATE TABLE ' || c.relname || ' (...)' AS sql FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s",(table,)); return Cursor(cur)
        if 'sqlite_sequence' in q: return Cursor(self.raw.cursor())
        q=re.sub(r'\bIFNULL\s*\(', 'COALESCE(', q, flags=re.I)
        q=q.replace('INSERT OR IGNORE INTO','INSERT INTO')
        ignored='INSERT OR IGNORE' in sql.upper()
        q=q.replace('?', '%s')
        # All application inserts into tables with serial id may request lastrowid.
        is_insert=q.lstrip().upper().startswith('INSERT INTO')
        has_returning=' RETURNING ' in q.upper()
        if ignored: q += ' ON CONFLICT DO NOTHING'
        if is_insert and not has_returning and not ignored:
            q += ' RETURNING id'
        cur=self.raw.cursor(cursor_factory=DictCursor)
        cur.execute(q, params or ())
        last=None
        if is_insert and not has_returning and not ignored:
            row=cur.fetchone(); last=row['id'] if row else None
        return Cursor(cur,last)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()
    def __getattr__(self, name): return getattr(self.raw, name)


def connect_postgres(url):
    raw = connect(url, cursor_factory=DictCursor, sslmode='require', connect_timeout=15)
    # Always return our adapter, not psycopg2's raw connection.
    return Connection(raw)

def init_postgres_schema(url):
    raw=connect(url, sslmode='require', connect_timeout=15)
    try:
        with raw.cursor() as c:
            for table, ddl in TABLES.items(): c.execute(f'CREATE TABLE IF NOT EXISTS {table} ({ddl})')
        raw.commit()
    finally: raw.close()

def seed_permissions(conn):
    perms=[('products.view','مشاهده محصولات','مدیریت محصولات','دسترسی به مشاهده لیست محصولات'),('products.add','افزودن محصول','مدیریت محصولات','افزودن محصول'),('products.edit','ویرایش محصول','مدیریت محصولات','ویرایش محصول'),('products.delete','حذف محصول','مدیریت محصولات','حذف محصول'),('products.import','ورود اکسل محصولات','مدیریت محصولات','ورود اکسل'),('invoices.create','صدور فاکتور','مدیریت فاکتورها','صدور فاکتور'),('invoices.view','مشاهده فاکتورها','مدیریت فاکتورها','مشاهده فاکتور'),('invoices.edit','ویرایش فاکتور','مدیریت فاکتورها','ویرایش فاکتور'),('invoices.print','چاپ فاکتور','مدیریت فاکتورها','چاپ فاکتور'),('payments.add','ثبت پرداخت','مدیریت پرداخت‌ها','ثبت پرداخت'),('payments.edit','ویرایش پرداخت','مدیریت پرداخت‌ها','ویرایش پرداخت'),('payments.delete','حذف پرداخت','مدیریت پرداخت‌ها','حذف پرداخت'),('reports.view','مشاهده گزارشات','گزارشات','گزارشات'),('reports.stock','گزارش موجودی','گزارشات','موجودی'),('reports.debtors','گزارش بدهکاران','گزارشات','بدهکاران'),('reports.zakat','محاسبه زکات','گزارشات','زکات'),('reports.date_range','گزارش بازه زمانی','گزارشات','بازه زمانی'),('reports.expenses','مدیریت هزینه‌ها','گزارشات','هزینه‌ها'),('purchase.view','مشاهده لیست خرید','مدیریت خرید','لیست خرید'),('purchase.create','ایجاد لیست خرید','مدیریت خرید','ایجاد خرید'),('purchase.edit','ویرایش لیست خرید','مدیریت خرید','ویرایش خرید'),('purchase.suppliers','مدیریت تامین‌کنندگان','مدیریت خرید','تامین‌کنندگان'),('backup.manage','مدیریت پشتیبان','سیستم','پشتیبان‌گیری'),('users.manage','مدیریت کاربران','سیستم','مدیریت کاربران')]
    for p in perms: conn.execute('INSERT OR IGNORE INTO permissions_master (permission_key,permission_name,category,description) VALUES (?,?,?,?)',p)

def _tables_from_pg(raw):
    with raw.cursor() as c:
        c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
        return [r[0] for r in c.fetchall()]

def export_postgres_to_sqlite(url, path):
    raw=connect(url, sslmode='require', connect_timeout=15); sq=sqlite3.connect(path)
    try:
        for table in _tables_from_pg(raw):
            with raw.cursor() as c:
                c.execute('SELECT * FROM "'+table.replace('"','')+'"'); rows=c.fetchall(); cols=[d.name for d in c.description]
                c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
                type_map={name: dtype for name, dtype in c.fetchall()}
            types=[]
            for col in cols:
                dtype=type_map.get(col, 'text')
                if col=='id': types.append('INTEGER PRIMARY KEY')
                elif dtype in ('integer','bigint','smallint'): types.append('INTEGER')
                elif dtype in ('numeric','real','double precision','decimal'): types.append('REAL')
                elif dtype=='boolean': types.append('INTEGER')
                else: types.append('TEXT')
            sq.execute('CREATE TABLE IF NOT EXISTS "'+table+'" ('+', '.join('"'+c+'" '+t for c,t in zip(cols,types))+')')
            if rows: sq.executemany('INSERT INTO "'+table+'" ('+','.join('"'+c+'"' for c in cols)+') VALUES ('+','.join('?' for _ in cols)+')', [tuple(v if v is None or isinstance(v,(str,int,float,bytes)) else str(v) for v in row) for row in rows])
        sq.commit()
    finally: sq.close(); raw.close()

def import_sqlite_to_postgres(path, url):
    sq=sqlite3.connect(path); sq.row_factory=sqlite3.Row; raw=connect(url,sslmode='require',connect_timeout=15)
    try:
        names=[r[0] for r in sq.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        with raw.cursor() as c:
            c.execute('SET CONSTRAINTS ALL DEFERRED')
            for table in names:
                if table not in TABLES: continue
                rows=sq.execute('SELECT * FROM "'+table+'"').fetchall()
                c.execute('DELETE FROM "'+table+'"')
                cols=rows[0].keys() if rows else [r[1] for r in sq.execute(f'PRAGMA table_info("{table}")')]
                if rows:
                    q='INSERT INTO "'+table+'" ('+','.join('"'+x+'"' for x in cols)+') VALUES ('+','.join(['%s']*len(cols))+')'
                    c.executemany(q,[tuple(r[x] for x in cols) for r in rows])
                if 'id' in cols: c.execute("SELECT setval(pg_get_serial_sequence(%s,'id'), COALESCE((SELECT MAX(id) FROM \""+table+"\"),1), true)",(table,))
        raw.commit()
    except: raw.rollback(); raise
    finally: sq.close(); raw.close()
