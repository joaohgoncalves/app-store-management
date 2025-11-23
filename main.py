# app_vendas_flet.py
# Sistema de Vendas / Compras de Funcionários em Flet + SQLite
# pip install flet bcrypt

import flet as ft
import sqlite3
import bcrypt
from datetime import datetime
import csv
import io
import os
import threading
import time
import json

DB_FILE = "sales_control.db"
db_lock = threading.RLock()

def get_db_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# --- Security settings ---
FAILED_LOGIN_ATTEMPTS = {}
LOGIN_LOCK_THRESHOLD = 5  # attempts
LOGIN_LOCK_SECONDS = 300  # lock window in seconds

def is_login_locked(username):
    rec = FAILED_LOGIN_ATTEMPTS.get(username)
    if not rec:
        return False
    count, ts = rec
    if count >= LOGIN_LOCK_THRESHOLD and (time.time() - ts) < LOGIN_LOCK_SECONDS:
        return True
    # reset if lock window passed
    if (time.time() - ts) >= LOGIN_LOCK_SECONDS:
        FAILED_LOGIN_ATTEMPTS.pop(username, None)
        return False
    return False

def record_failed_login(username):
    rec = FAILED_LOGIN_ATTEMPTS.get(username)
    if not rec:
        FAILED_LOGIN_ATTEMPTS[username] = (1, time.time())
    else:
        count, _ = rec
        FAILED_LOGIN_ATTEMPTS[username] = (count + 1, time.time())

def clear_failed_login(username):
    FAILED_LOGIN_ATTEMPTS.pop(username, None)

def validate_date_ymd(date_str):
    if not date_str:
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except Exception:
        return False

def validate_datetime(date_str):
    if not date_str:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            datetime.strptime(date_str, fmt)
            return True
        except Exception:
            continue
    return False


def format_date_for_display(date_str):
    """Return date in DD/MM/YYYY for display if possible, otherwise return '-' or the original truncated value."""
    if not date_str:
        return '-'
    ds = date_str.strip()
    # Try several parse formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(ds[:10] if len(ds) >= 10 else ds, fmt)
            return dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    # Fallback: replace '-' with '/' for common patterns and truncate to 10
    try:
        candidate = ds[:10].replace('-', '/').replace('.', '/')
        return candidate
    except Exception:
        return ds

def validate_installment_dates(dates):
    # Accepts a list/tuple of installment dates (YYYY-MM-DD).
    # Allow the common case where there's a single installment and its date field is left empty.
    if not isinstance(dates, (list, tuple)):
        return False
    if len(dates) > 12:
        return False

    # If there's only one installment and it's empty, consider it valid (no explicit due date provided).
    if len(dates) == 1 and (not dates[0] or str(dates[0]).strip() == ""):
        return True

    def convert_br_to_iso(date_str):
        # Converte DD/MM/YYYY para YYYY-MM-DD
        import re
        if re.match(r"^\d{2}/\d{2}/\d{4}$", date_str):
            try:
                d, m, y = date_str.split("/")
                return f"{y}-{m}-{d}"
            except Exception:
                return date_str
        return date_str

    for d in dates:
        if not d:
            return False
        d_iso = convert_br_to_iso(d)
        if not validate_date_ymd(d_iso):
            return False
    return True


def init_db():
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        # Users
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee'
            )
        """)
        # Ensure debt_balance column exists (SQLite ALTER TABLE ADD COLUMN is safe)
        try:
            cur.execute("PRAGMA table_info(users)")
            cols = [r[1] for r in cur.fetchall()]
            if 'debt_balance' not in cols:
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN debt_balance REAL DEFAULT 0.0")
                except Exception:
                    # older SQLite or other issues: ignore and continue
                    pass
        except Exception:
            pass
        # Products
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                category TEXT
            )
        """)
        # Sales
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                employee_id INTEGER,
                product_id INTEGER,
                quantity INTEGER,
                total_value REAL,
                sale_type TEXT DEFAULT 'cliente'
            )
        """)
        # Activity Log
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        # Default admin
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            pw_hash = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
            cur.execute("INSERT INTO users (name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                        ("Administrador", "admin", pw_hash, "admin"))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        # Ensure payments table for normal sales exists (keep employee-specific tables removed)
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sale_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER,
                    installment_index INTEGER,
                    due_date TEXT,
                    amount REAL,
                    paid INTEGER DEFAULT 0,
                    paid_date TEXT,
                    payment_method TEXT,
                    FOREIGN KEY (sale_id) REFERENCES sales(id)
                )
            """)
            conn.commit()
        except Exception:
            pass
        conn.close()

# ----------------------
# Helpers - Activity Log
# ----------------------
def log_activity(user_id, action, details=None):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO activity_log (date, user_id, action, details) VALUES (?, ?, ?, ?)",
                    (now, user_id, action, details))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        conn.close()

def get_recent_activities(limit=10):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT al.date, u.name as user_name, al.action, al.details 
            FROM activity_log al
            LEFT JOIN users u ON al.user_id = u.id
            ORDER BY al.date DESC
            LIMIT ?
        """, (limit,))
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    conn.close()
    return [dict(zip(columns, r)) for r in rows]

# ----------------------
# Helpers - Users
# ----------------------
def create_user(name, username, password, role="employee"):
    try:
        with db_lock:
            conn = get_db_conn()
            cur = conn.cursor()
            pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            cur.execute("INSERT INTO users (name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                        (name, username, pw_hash, role))
            conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        log_activity(1, "CRIAR_USUARIO", f"Usuário {username} criado")
        return True, None
    except Exception:
        return False, "Nome de usuário já existe."
    except Exception as e:
        return False, str(e)
    finally:
        if 'conn' in locals():
            conn.close()

def get_all_users():
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name, username, role, COALESCE(debt_balance, 0) as debt_balance FROM users ORDER BY name")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    conn.close()
    return [dict(zip(columns, r)) for r in rows]

def create_employee_sale(employee_id, items, total_value, num_installments, installment_dates, installment_amounts, first_payment_date=None):
    # Employee-specific sales removed. This function is deprecated and intentionally left inert.
    # If callers remain, return a failure so callers can fall back to normal sale flow.
    return False, "employee-sales feature removed"

# Employee-specific helpers removed earlier; queries should use the main `sales`/`sale_payments` tables instead.

def update_payment_status_db(arg1, arg2, payment_method=None):
    """Update payment status helper. Backwards-compatible behavior:
    - If arg2 is a boolean/int (paid flag), treat arg1 as a payment_id and update that payment row in `sale_payments`.
    - If arg2 is a string status ('Pago', 'Em Aberto', 'Parcial'), treat arg1 as a sale_id and update all installments for that sale.
    Returns (True, None) on success or (False, error_str) on failure for callers expecting a tuple.
    """
    try:
        # Case A: toggle a single payment row by id
        if isinstance(arg2, (bool, int)) or (isinstance(arg2, str) and arg2 in ('0', '1')):
            payment_id = int(arg1)
            paid_flag = bool(int(arg2)) if not isinstance(arg2, bool) else arg2
            with db_lock:
                conn = get_db_conn()
                cur = conn.cursor()
                if paid_flag:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute("UPDATE sale_payments SET paid = 1, paid_date = ?, payment_method = ? WHERE id = ?",
                                (now, payment_method or '', payment_id))
                else:
                    cur.execute("UPDATE sale_payments SET paid = 0, paid_date = '', payment_method = '' WHERE id = ?",
                                (payment_id,))
                conn.commit()
                conn.close()
            return True, None

        # Case B: update all installments for a sale based on textual status
        if isinstance(arg2, str):
            sale_id = int(arg1)
            status = arg2
            with db_lock:
                conn = get_db_conn()
                cur = conn.cursor()
                if status == 'Pago':
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute("UPDATE sale_payments SET paid = 1, paid_date = ?, payment_method = ? WHERE sale_id = ? AND paid = 0", (now, payment_method or '', sale_id))
                    cur.execute("UPDATE sales SET num_installments = num_installments, first_payment_date = first_payment_date, sale_type = sale_type, total_value = total_value, payment_method = payment_method, sale_type = sale_type WHERE id = ?", (sale_id,))
                    cur.execute("UPDATE sales SET sale_type = sale_type WHERE id = ?", (sale_id,))
                    cur.execute("UPDATE sales SET payment_status = ? WHERE id = ?", (status, sale_id)) if False else None
                    # The sales table doesn't have a standardized payment_status column in older schemas; keep changes minimal.
                elif status == 'Em Aberto':
                    cur.execute("UPDATE sale_payments SET paid = 0, paid_date = '', payment_method = '' WHERE sale_id = ?", (sale_id,))
                    try:
                        cur.execute("UPDATE sales SET payment_status = ? WHERE id = ?", (status, sale_id))
                    except Exception:
                        pass
                else:
                    # For 'Parcial' or other statuses, just set the field if it exists
                    try:
                        cur.execute("UPDATE sales SET payment_status = ? WHERE id = ?", (status, sale_id))
                    except Exception:
                        pass

                conn.commit()
                conn.close()
            return True, None

        return False, "Unsupported arguments for update_payment_status_db"
    except Exception as e:
        try:
            return False, str(e)
        finally:
            pass

def get_user_by_username(username):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        conn.close()
        if row:
            # Se for sqlite3.Row, converte para dict
            try:
                return {k: row[k] for k in row.keys()}
            except AttributeError:
                # Se for tuple, mapear manualmente para dict usando cursor.description
                columns = [desc[0] for desc in cur.description]
                return dict(zip(columns, row))
        return None

def get_user_by_id(uid):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name, username, role, debt_balance FROM users WHERE id = ?", (uid,))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]
        conn.close()
        if row:
            return dict(zip(columns, row))
        return None

def update_user(uid, name, username, role):
    try:
        with db_lock:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("UPDATE users SET name=?, username=?, role=? WHERE id = ?",
                        (name, username, role, uid))
            conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        log_activity(1, "ATUALIZAR_USUARIO", f"Usuário {username} atualizado")
        return True, None
    except Exception:
        return False, "Nome de usuário já existe."
    except Exception as e:
        return False, str(e)
    finally:
        if 'conn' in locals():
            conn.close()

def delete_user(uid):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        user = get_user_by_id(uid)
        cur.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        if user:
            log_activity(1, "EXCLUIR_USUARIO", f"Usuário {user['username']} excluído")
        conn.close()

def adjust_user_debt(uid, amount):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET debt_balance = debt_balance + ? WHERE id = ?", (amount, uid))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        conn.close()

# ----------------------
# Helpers - Products
# ----------------------
def create_product(name, price, category=None):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO products (name, price, category) VALUES (?, ?, ?)",
                    (name, price, category))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        log_activity(1, "CRIAR_PRODUTO", f"Produto {name} criado")
        conn.close()

def get_all_products():
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name, price, category FROM products ORDER BY name")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    conn.close()
    return [dict(zip(columns, r)) for r in rows]

def get_product_by_id(pid):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name, price, category FROM products WHERE id = ?", (pid,))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]
        conn.close()
        if row:
            return dict(zip(columns, row))
        return None

def update_product(pid, name, price, category):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("UPDATE products SET name=?, price=?, category=? WHERE id = ?",
                    (name, price, category, pid))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        log_activity(1, "ATUALIZAR_PRODUTO", f"Produto {name} atualizado")
        conn.close()

def delete_product(pid):
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        product = get_product_by_id(pid)
        cur.execute("DELETE FROM products WHERE id = ?", (pid,))
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        if product:
            log_activity(1, "EXCLUIR_PRODUTO", f"Produto {product['name']} excluído")
        conn.close()

def batch_create_products(products_data):
    """Cria múltiplos produtos de uma vez"""
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        success_count = 0
        errors = []
        
        for i, product in enumerate(products_data):
            try:
                # Normalizar e validar campos
                name = (product.get('name') or '').strip()
                price_raw = (product.get('price') or '').strip()
                # aceitar vírgula como separador decimal
                price_raw = price_raw.replace(',', '.')
                try:
                    price = float(price_raw) if price_raw != '' else 0.0
                except:
                    price = 0.0

                category = (product.get('category') or '').strip() or None

                # Inserir se houver nome válido (permitir preço 0.0 também)
                if name:
                    cur.execute("INSERT INTO products (name, price, category) VALUES (?, ?, ?)",
                               (name, price, category))
                    success_count += 1
                else:
                    errors.append(f"Linha {i+1}: Nome do produto ausente")
            except Exception as e:
                errors.append(f"Linha {i+1}: {str(e)}")
        
        conn.commit()
        time.sleep(0.05)  # evitar travamentos SQLite
        conn.close()
        
        if success_count > 0:
            log_activity(1, "IMPORTAR_PRODUTOS", f"{success_count} produtos importados")
        
        return success_count, errors

# ----------------------
# Helpers - Sales
# ----------------------
def record_sale(employee_id, product_id, quantity, sale_type="cliente", custom_price=None, payment_method=None, date_str=None, num_installments=1, first_payment_date=None, installment_dates=None):
    prod = get_product_by_id(product_id)
    if not prod:
        return False, "Produto não encontrado."
    if quantity <= 0:
        return False, "Quantidade inválida."

    # Usar preço customizado se fornecido, senão usar preço do produto
    unit_price = custom_price if custom_price is not None else prod["price"]
    total = unit_price * quantity
    # Se foi fornecida uma data, usá-la; caso contrário, usar data/hora atual
    now = date_str.strip() if date_str and isinstance(date_str, str) and date_str.strip() else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_rec = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'employee_id': employee_id,
        'product_id': product_id,
        'quantity': quantity,
        'unit_price': unit_price,
        'total': total,
        'sale_type': sale_type,
        'payment_method': payment_method,
        'date_str': now
    }
    try:
        with db_lock:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sales (date, employee_id, product_id, quantity, total_value, sale_type, payment_method, num_installments, first_payment_date, installment_dates) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, employee_id, product_id, quantity, total, sale_type, payment_method or '', int(num_installments) if num_installments else 1, first_payment_date or '', json.dumps(installment_dates or []))
            )
            sale_id = cur.lastrowid

            # If this is a normal sale (not employee-specific) and has installments,
            # create entries in sale_payments so installments can be tracked later.
            try:
                n_inst = int(num_installments) if num_installments else 1
            except Exception:
                n_inst = 1

            if n_inst > 1:
                # prepare installment dates and amounts
                inst_dates = installment_dates or []
                # split total into equal installments (last installment absorbs rounding)
                base = round(total / n_inst, 2)
                amounts = [base] * n_inst
                # adjust last
                diff = round(total - sum(amounts), 2)
                amounts[-1] = round(amounts[-1] + diff, 2)

                # ensure sale_payments table exists (may not, older DBs)
                try:
                    cur.execute("PRAGMA table_info(sale_payments)")
                    if not cur.fetchall():
                        # table missing; create it
                        cur.execute('''
                            CREATE TABLE IF NOT EXISTS sale_payments (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                sale_id INTEGER,
                                installment_index INTEGER,
                                due_date TEXT,
                                amount REAL,
                                paid INTEGER DEFAULT 0,
                                paid_date TEXT,
                                payment_method TEXT
                            )
                        ''')
                except Exception:
                    pass

                for idx in range(n_inst):
                    due = inst_dates[idx] if idx < len(inst_dates) else ''
                    amt = amounts[idx]
                    try:
                        cur.execute(
                            "INSERT INTO sale_payments (sale_id, installment_index, due_date, amount) VALUES (?, ?, ?, ?)",
                            (sale_id, idx + 1, due, amt)
                        )
                    except Exception:
                        # ignore individual insert failures
                        pass

            conn.commit()
            time.sleep(0.05)  # evitar travamentos SQLite
            conn.close()

        # Employee-specific debt adjustments removed (employee-tab disabled)

        log_activity(employee_id, "REGISTRAR_VENDA", f"Venda de {quantity}x {prod['name']} - R$ {total:.2f}")
        log_rec['result'] = 'ok'
        log_rec['error'] = None
        return True, None
    except Exception as e:
        log_rec['result'] = 'error'
        log_rec['error'] = str(e)
        # registrar erro também no activity_log se possível
        try:
            log_activity(employee_id, "ERRO_REGISTRAR_VENDA", str(e))
        except Exception:
            pass
        return False, str(e)
    finally:
        # sempre gravar um log de depuração para ajudar a diagnosticar problemas do UI
        try:
            with open('debug_sales.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

def get_sales():
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        # Build SELECT based on existing columns to avoid sqlite OperationalError
        cur.execute("PRAGMA table_info(sales)")
        rows = cur.fetchall()
        # Mapear cada linha para dict usando cursor.description
        columns = [desc[0] for desc in cur.description]
        dict_rows = [dict(zip(columns, r)) for r in rows]
        existing_cols = [r['name'] for r in dict_rows]

        select_cols = [
            "s.id",
            "s.date",
            "u.name as employee_name",
            "p.name as product_name",
            "s.quantity",
            "s.total_value",
            "s.sale_type",
        ]

        if 'payment_method' in existing_cols:
            select_cols.append('s.payment_method')
        else:
            select_cols.append("'' AS payment_method")

        if 'num_installments' in existing_cols:
            select_cols.append('s.num_installments')
        else:
            select_cols.append("1 AS num_installments")

        if 'first_payment_date' in existing_cols:
            select_cols.append('s.first_payment_date')
        else:
            select_cols.append("'' AS first_payment_date")

        q = f"SELECT {', '.join(select_cols)} FROM sales s LEFT JOIN users u ON s.employee_id = u.id LEFT JOIN products p ON s.product_id = p.id ORDER BY s.date DESC"
        cur.execute(q)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    conn.close()
    return [dict(zip(columns, r)) for r in rows]

    
def delete_sale(sale_id):
    """Delete a normal sale by id and log the action."""
    try:
        with db_lock:
            conn = get_db_conn()
            cur = conn.cursor()
            # Optionally adjust any side-effects here (sales don't affect debt)
            cur.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
            conn.commit()
            conn.close()
        log_activity(1, "EXCLUIR_VENDA", f"Venda {sale_id} excluída")
        return True
    except Exception as e:
        log_activity(1, "ERRO_EXCLUIR_VENDA", f"Venda {sale_id} - Erro: {e}")
        return False

def get_sales_by_period(start_date=None, end_date=None):
    """Gera relatório de vendas por período"""
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        
        query = """
            SELECT s.date, p.name as product_name, s.quantity, s.total_value, 
                   s.payment_method, s.num_installments, s.first_payment_date,
                   s.sale_type
            FROM sales s
            LEFT JOIN products p ON s.product_id = p.id
            WHERE 1=1
        """
        params = []
        
        if start_date:
            query += " AND s.date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND s.date <= ?"
            params.append(end_date)
            
        query += " ORDER BY s.date DESC"
        
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        return rows

def get_product_sales_report():
    """Gera relatório de vendas por produto"""
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.name as product_name,
                   COUNT(*) as total_sales,
                   SUM(s.quantity) as total_quantity,
                   SUM(s.total_value) as total_value,
                   AVG(s.total_value/s.quantity) as avg_unit_price
            FROM sales s
            JOIN products p ON s.product_id = p.id
            GROUP BY p.id, p.name
            ORDER BY total_value DESC
        """)
        rows = cur.fetchall()
        # map to list of dicts so callers can access by column name
        columns = [desc[0] for desc in cur.description]
        conn.close()
        return [dict(zip(columns, r)) for r in rows]

def get_payment_methods_report():
    """Gera relatório de vendas por método de pagamento"""
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        # Check whether `payment_method` column exists in `sales` table
        try:
            cur.execute("PRAGMA table_info(sales)")
            sales_cols = [r[1] for r in cur.fetchall()]
        except Exception:
            sales_cols = []

        if 'payment_method' in sales_cols:
            cur.execute("""
                SELECT payment_method,
                       COUNT(*) as total_sales,
                       SUM(total_value) as total_value
                FROM sales
                WHERE payment_method IS NOT NULL AND payment_method != ''
                GROUP BY payment_method
                ORDER BY total_value DESC
            """)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            conn.close()
            return [dict(zip(columns, r)) for r in rows]

        # Fallback: if sales.payment_method missing, try aggregating from sale_payments
        try:
            cur.execute("PRAGMA table_info(sale_payments)")
            sp_cols = [r[1] for r in cur.fetchall()]
        except Exception:
            sp_cols = []

        if 'payment_method' in sp_cols:
            # Aggregate one payment_method per sale (take max non-empty value) and sum the sale total_value
            cur.execute("""
                SELECT spm.payment_method as payment_method,
                       COUNT(*) as total_sales,
                       SUM(s.total_value) as total_value
                FROM (
                    SELECT sale_id, MAX(payment_method) AS payment_method
                    FROM sale_payments
                    WHERE payment_method IS NOT NULL AND payment_method != ''
                    GROUP BY sale_id
                ) spm
                JOIN sales s ON s.id = spm.sale_id
                GROUP BY spm.payment_method
                ORDER BY total_value DESC
            """)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            conn.close()
            return [dict(zip(columns, r)) for r in rows]

        # If neither column exists, return empty report
        conn.close()
        return []

def get_installments_report():
    """Gera relatório de vendas parceladas"""
    with db_lock:
        conn = get_db_conn()
        cur = conn.cursor()
        # Check whether `num_installments` exists in `sales` table
        try:
            cur.execute("PRAGMA table_info(sales)")
            sales_cols = [r[1] for r in cur.fetchall()]
        except Exception:
            sales_cols = []

        if 'num_installments' in sales_cols:
            cur.execute("""
                SELECT num_installments,
                       COUNT(*) as total_sales,
                       SUM(total_value) as total_value,
                       AVG(total_value) as avg_value
                FROM sales
                WHERE num_installments > 1
                GROUP BY num_installments
                ORDER BY num_installments
            """)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            conn.close()
            return [dict(zip(columns, r)) for r in rows]

        # Fallback: infer number of installments from sale_payments if available
        try:
            cur.execute("PRAGMA table_info(sale_payments)")
            sp_cols = [r[1] for r in cur.fetchall()]
        except Exception:
            sp_cols = []

        if 'id' in sp_cols:
            cur.execute("""
                SELECT sp.cnt AS num_installments,
                       COUNT(*) AS total_sales,
                       SUM(s.total_value) AS total_value,
                       AVG(s.total_value) AS avg_value
                FROM (
                    SELECT sale_id, COUNT(*) AS cnt
                    FROM sale_payments
                    GROUP BY sale_id
                    HAVING cnt > 1
                ) sp
                JOIN sales s ON s.id = sp.sale_id
                GROUP BY sp.cnt
                ORDER BY sp.cnt
            """)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            conn.close()
            return [dict(zip(columns, r)) for r in rows]

        # If we can't determine installments, return empty list
        conn.close()
        return []


# Employee-sales features and helpers removed (employee tab and related DB tables). UI and DB helpers were deleted.

COLOR_BACKGROUND = "#FFFFFF"  
COLOR_SURFACE = "#ffffff"     
COLOR_PRIMARY_START = "#FFFFFF"
COLOR_PRIMARY_END = "#f5b700"  
COLOR_TEXT_PRIMARY = "#0b0b0b" 
COLOR_TEXT_SECONDARY = "#6b6b6b"
COLOR_BORDER = "#FFFFFF"
COLOR_WARNING = "#f5b700"
COLOR_ERROR = "#FF0000"
COLOR_PAGO = "#4CAF50"

COR_GRADIENTE_INICIO = "#fdf7c2"
COR_GRADIENTE_MEIO = "#ffde4b"  
COR_GRADIENTE_FIM = "#f5b700"   
COR_Circulo_INICIO = "#d8cb5a"  
COR_Circulo_MEIO = "#cabd43"
COR_Circulo1_MEIO = "#eed600"   
COR_Circulo_FIM = "#fbc02d"     
COR_BOTAO_INICIO = COR_GRADIENTE_MEIO
COR_BOTAO_FIM = COLOR_PRIMARY_END
COR_CARTAO = COLOR_SURFACE
COR_TEXTO = COLOR_TEXT_PRIMARY


FONT_FAMILY = "Poppins"
FONT_SIZE_H1 = 28
FONT_SIZE_H2 = 22
FONT_SIZE_H3 = 18
FONT_SIZE_BODY = 14
FONT_SIZE_SMALL = 12

# Spacing
PADDING_SMALL = 8
PADDING_MEDIUM = 16
PADDING_LARGE = 24
PADDING_XLARGE = 32

# Border Radius
BORDER_RADIUS_SMALL = 8
BORDER_RADIUS_MEDIUM = 12
BORDER_RADIUS_LARGE = 16

# Shadows
SHADOW_SMALL = ft.BoxShadow(
    blur_radius=10,
    color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
    offset=ft.Offset(0, 2),
    spread_radius=0
)
SHADOW_MEDIUM = ft.BoxShadow(
    blur_radius=20,
    color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
    offset=ft.Offset(0, 4),
    spread_radius=0
)

# =========================
# UI COMPONENTS
# =========================

def create_gradient_button(text, on_click=None, width=None, expand=False):
    return ft.Container(
        content=ft.Text(
            text,
            size=FONT_SIZE_BODY,
            weight=ft.FontWeight.W_600,
            color=ft.Colors.WHITE
        ),
        width=width,
        height=48,
        border_radius=BORDER_RADIUS_MEDIUM,
        gradient=ft.LinearGradient(
            begin=ft.alignment.center_left,
            end=ft.alignment.center_right,
            colors=[COR_GRADIENTE_MEIO, COLOR_PRIMARY_END]
        ),
        alignment=ft.alignment.center,
        on_click=on_click,
        expand=expand
    )

def create_card(content, padding=PADDING_LARGE):
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=COLOR_SURFACE,
        border_radius=BORDER_RADIUS_LARGE,
        shadow=SHADOW_MEDIUM
    )

def create_input_field(label, width=300, password=False, icon=None, value=None, readonly=False):
    field = ft.TextField(
        label=label,
        width=width,
        height=48,
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER,
        focused_border_color=COLOR_PRIMARY_START,
        content_padding=15,
        text_size=FONT_SIZE_BODY,
        cursor_color=COLOR_PRIMARY_START,
        read_only=readonly,
        label_style=ft.TextStyle(
            color=COLOR_TEXT_SECONDARY, 
            size=FONT_SIZE_SMALL,
            font_family=FONT_FAMILY
        ),
        text_style=ft.TextStyle(
            color=COLOR_TEXT_PRIMARY,
            font_family=FONT_FAMILY
        ),
    prefix_icon=ft.Icon(icon, color=COLOR_TEXT_SECONDARY, size=22) if icon else None,
        filled=True,
        fill_color=COLOR_SURFACE,
        password=password,
        can_reveal_password=password
    )
    if value is not None:
        field.value = value
    return field


# Helpers: installment UI wiring and discount distribution
def wire_installment_fields(page, installments_dd, container, max_installments=12):
    """Attach dynamic installment-date fields behavior to a container and dropdown."""
    def rebuild(count):
        container.controls.clear()
        for i in range(count):
            row = ft.Row(
                controls=[
                    create_input_field(f"Data da {i+1}ª parcela (YYYY-MM-DD)", width=220, icon=ft.Icons.DATE_RANGE),
                    create_input_field(f"Valor da {i+1}ª parcela (R$)", width=160, icon=ft.Icons.ATTACH_MONEY, value="0")
                ],
                spacing=10
            )
            container.controls.append(row)
        page.update()

    def on_change(e):
        try:
            n = int(installments_dd.value)
        except:
            n = 1
        n = max(1, min(max_installments, n))
        rebuild(n)

    installments_dd.on_change = on_change
    rebuild(int(installments_dd.value) if installments_dd and installments_dd.value else 1)

def read_installment_dates(container):
    dates = []
    for row in container.controls:
        if not isinstance(row, ft.Row) or not row.controls:
            dates.append("")
            continue
            
        date_field = row.controls[0]
        raw = getattr(date_field, 'value', '') or ''
        date_str, _ = validate_date_string(raw)
        dates.append(date_str)
    return dates


def validate_date_string(date_str):
    if not date_str:
        return "", False
    date_str = date_str.strip()
    
    # Try YYYY-MM-DD
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str, True
    except ValueError:
        pass
    
    # Try DD/MM/YYYY
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        return dt.strftime("%Y-%m-%d"), True
    except ValueError:
        pass
    
    return date_str, False

def read_installment_amounts(container):
    amounts = []
    for f in container.controls:
        raw = getattr(f, 'value', '') or ''
        raw = raw.strip().replace(',', '.')
        if raw == '':
            amounts.append(0.0)
            continue
        try:
            v = float(raw)
        except Exception:
            # non-numeric entries treated as 0.0 (validation can catch if needed)
            try:
                # strip currency symbols
                cleaned = ''.join(ch for ch in raw if (ch.isdigit() or ch in '.-'))
                v = float(cleaned) if cleaned else 0.0
            except Exception:
                v = 0.0
        amounts.append(round(v, 2))
    return amounts

def read_first_installment_date(container):
    if len(container.controls) > 0:
        raw = getattr(container.controls[0], 'value', '') or ''
        raw = raw.strip()
        if not raw:
            return ''
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except Exception:
            try:
                dt = datetime.strptime(raw, "%d/%m/%Y")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                try:
                    dt = datetime.strptime(raw, "%d-%m-%Y")
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    return raw
    return ''

def distribute_discount_tuples(items, discount):
    """Return list of (item, adj_unit, adj_total) and final_total."""
    total_before = sum(item['total_price'] for item in items)
    final_total = max(0, total_before - (discount or 0))
    adjusted = []
    remaining = final_total
    if total_before > 0 and (discount or 0) > 0:
        factor = (final_total) / total_before if total_before > 0 else 1.0
    else:
        factor = 1.0
    for idx, item in enumerate(items):
        if idx < len(items) - 1:
            adj_total = round(item['total_price'] * factor, 2)
            remaining -= adj_total
        else:
            adj_total = round(remaining, 2)
        adj_unit = (adj_total / item['quantity']) if item['quantity'] else 0
        adjusted.append((item, adj_unit, adj_total))
    return adjusted, final_total

def distribute_discount_dicts(items, discount):
    """Return list of dicts suitable for employee sale items and final_total."""
    total_before = sum(item['total_price'] for item in items)
    final_total = max(0, total_before - (discount or 0))
    adjusted = []
    remaining = final_total
    if total_before > 0 and (discount or 0) > 0:
        factor = (final_total) / total_before if total_before > 0 else 1.0
    else:
        factor = 1.0
    for idx, item in enumerate(items):
        if idx < len(items) - 1:
            adj_total = round(item['total_price'] * factor, 2)
            remaining -= adj_total
        else:
            adj_total = round(remaining, 2)
        adj_unit = (adj_total / item['quantity']) if item['quantity'] else 0
        adjusted.append({
            'product_id': item['product_id'],
            'quantity': item['quantity'],
            'unit_price': adj_unit,
            'total_price': adj_total
        })
    return adjusted, final_total

def create_dashboard_card(title, value, subtitle="", icon=ft.Icons.DASHBOARD):
    return create_card(
        ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(icon, color=COLOR_PRIMARY_START, size=28),
                    padding=PADDING_SMALL,
                    bgcolor=COR_Circulo_FIM,
                    border_radius=BORDER_RADIUS_MEDIUM
                ),
            ]),
            ft.Text(
                value,
                size=28,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Text(
                title,
                size=FONT_SIZE_SMALL,
                color=COLOR_TEXT_SECONDARY,
                font_family=FONT_FAMILY
            ),
            ft.Text(
                subtitle,
                size=FONT_SIZE_SMALL,
                color=COLOR_TEXT_SECONDARY,
                font_family=FONT_FAMILY
            ) if subtitle else ft.Container(height=0)
        ], spacing=PADDING_SMALL),
        padding=PADDING_XLARGE
    )

# =========================
# App state
# =========================
# Employee Sales tab and its UI class removed.
# The employee-tab UI, table and helpers were intentionally deleted to simplify the app.

class AppState:
    def __init__(self):
        self.logged_user = None
        self.last_product_price = {}  # Armazenar último preço usado por produto

state = AppState()

# =========================
# NOVO LOGIN MODERNO E MINIMALISTA
# =========================

def login_view(page: ft.Page):
    # Campos com estilo minimalista
    campo_username = ft.TextField(
        label="Username",
        width=280,
        height=50,
        border_radius=10,
    border_color=COLOR_BORDER,
    focused_border_color=COLOR_PRIMARY_END,
    content_padding=15,
    text_size=14,
    cursor_color=COLOR_PRIMARY_END,
        label_style=ft.TextStyle(color=COR_TEXTO, size=13),
        text_style=ft.TextStyle(color=COR_TEXTO),
    prefix_icon=ft.Icon(ft.Icons.PERSON_OUTLINE, color=COLOR_TEXT_SECONDARY, size=20),
    filled=True,
    fill_color=COLOR_SURFACE
    )

    campo_senha = ft.TextField(
        label="Password",
        width=280,
        height=50,
        password=True,
        can_reveal_password=True,
        border_radius=10,
    border_color=COLOR_BORDER,
    focused_border_color=COLOR_PRIMARY_END,
    content_padding=15,
    text_size=14,
    cursor_color=COLOR_PRIMARY_END,
        label_style=ft.TextStyle(color=COR_TEXTO, size=13),
        text_style=ft.TextStyle(color=COR_TEXTO),
    prefix_icon=ft.Icon(ft.Icons.LOCK_OUTLINE, color=COLOR_TEXT_SECONDARY, size=20),
    filled=True,
    fill_color=COLOR_SURFACE
    )

    texto_status = ft.Text("", color=COLOR_ERROR, size=12, weight=ft.FontWeight.W_500)

    # Função de login
    def fazer_login(e):
        if not campo_username.value:
            texto_status.value = "Please enter your username"
            campo_username.border_color = COLOR_ERROR
            page.update()
            return
            
        if not campo_senha.value:
            texto_status.value = "Please enter your password"
            campo_senha.border_color = COLOR_ERROR
            page.update()
            return

        # bloqueio por tentativas
        if is_login_locked(campo_username.value):
            texto_status.value = "Too many failed attempts. Try later."
            texto_status.color = COLOR_ERROR
            page.update()
            return

        usuario = get_user_by_username(campo_username.value)
        if usuario and bcrypt.checkpw(campo_senha.value.encode(), usuario["password_hash"].encode()):
            state.logged_user = usuario
            clear_failed_login(campo_username.value)
            texto_status.value = "✓ Login successful!"
            texto_status.color = COLOR_PRIMARY_START
            
            # Resetar bordas
            campo_username.border_color = COLOR_BORDER
            campo_senha.border_color = COLOR_BORDER
            
            # Efeito visual de sucesso
            botao_login.bgcolor = COLOR_PRIMARY_START
            page.update()
            
            # Registrar atividade
            log_activity(usuario["id"], "LOGIN", "Login no sistema")
            
            # Navegar para home após login bem-sucedido
            page.go("/home")
            
        else:
            texto_status.value = "Invalid username or password"
            texto_status.color = COLOR_ERROR
            campo_username.border_color = COLOR_ERROR
            campo_senha.border_color = COLOR_ERROR
            # registrar falha
            record_failed_login(campo_username.value)
            
        page.update()

    # Botão de login com gradiente
    botao_login = ft.Container(
        content=ft.Text(
            "LOGIN",
            size=16,
            weight=ft.FontWeight.W_700,
            color=ft.Colors.WHITE
        ),
        width=280,
        height=50,
        border_radius=10,
        gradient=ft.LinearGradient(
            begin=ft.alignment.center_left,
            end=ft.alignment.center_right,
            colors=[COR_BOTAO_INICIO, COR_BOTAO_FIM]
        ),
        alignment=ft.alignment.center,
        on_click=fazer_login
    )

    # Efeito hover no botão
    def efeito_hover_botao(e):
        if e.data == "true":
            botao_login.scale = 1.02
        else:
            botao_login.scale = 1.0
        botao_login.update()

    botao_login.on_hover = efeito_hover_botao

    # Lado esquerdo com formas abstratas
    lado_esquerdo = ft.Container(
        expand=True,
        content=ft.Stack([
            # Formas abstratas orgânicas
            ft.Container(
                content=ft.Container(
                    width=120,
                    height=120,
                    border_radius=60,
                    bgcolor=COR_Circulo_INICIO,
                    opacity=0.3
                ),
                top=50,
                left=30
            ),
            ft.Container(
                content=ft.Container(
                    width=80,
                    height=80,
                    border_radius=40,
                    bgcolor=COR_Circulo_MEIO,
                    opacity=0.4
                ),
                top=120,
                left=100
            ),
            ft.Container(
                content=ft.Container(
                    width=100,
                    height=100,
                    border_radius=50,
                    bgcolor=COR_Circulo_FIM,
                    opacity=0.3
                ),
                top=200,
                left=40
            ),
            ft.Container(
                content=ft.Container(
                    width=60,
                    height=60,
                    border_radius=30,
                    bgcolor=COR_GRADIENTE_INICIO,
                    opacity=0.5
                ),
                top=180,
                left=150
            ),
            # Forma orgânica irregular
            ft.Container(
                content=ft.Container(
                    width=140,
                    height=140,
                    border_radius=70,
                    bgcolor=COR_Circulo1_MEIO,
                    opacity=0.2
                ),
                top=280,
                left=80
            ),
        ]),
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=[COLOR_SURFACE, COLOR_BORDER]
        )
    )

    # Lado direito com formulário
    lado_direito = ft.Container(
        expand=True,
        content=ft.Column([
            ft.Divider(height=60, color=ft.Colors.TRANSPARENT),
            ft.Container(
                content=ft.Column([
                    ft.Text(
                        "Bem-vindo de Volta",
                        size=24,
                        weight=ft.FontWeight.W_700,
                        color=COR_TEXTO
                    ),
                    ft.Divider(height=5, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Faça login para continuar",
                        size=14,
                        color=COLOR_TEXT_SECONDARY,
                        weight=ft.FontWeight.W_400
                    ),
                    ft.Divider(height=40, color=ft.Colors.TRANSPARENT),
                    campo_username,
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    campo_senha,
                    ft.Divider(height=30, color=ft.Colors.TRANSPARENT),
                    botao_login,
                    ft.Divider(height=15, color=ft.Colors.TRANSPARENT),
                    texto_status,
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=30
            ),
        ], alignment=ft.MainAxisAlignment.START),
        alignment=ft.alignment.center
    )

    # Cartão principal
    cartao_login = ft.Container(
        content=ft.Row([
            lado_esquerdo,
            lado_direito,
        ]),
        width=800,
        height=500,
        bgcolor=COR_CARTAO,
        border_radius=20,
        shadow=ft.BoxShadow(
            blur_radius=30,
            color=ft.Colors.BLACK26,
            offset=ft.Offset(0, 10),
            spread_radius=2
        )
    )

    # Layout principal com gradiente vibrante
    return ft.Container(
        content=ft.Column([
            ft.Row([
                cartao_login
            ], alignment=ft.MainAxisAlignment.CENTER),
        ], alignment=ft.MainAxisAlignment.CENTER),
        expand=True,
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=[COR_GRADIENTE_INICIO, COR_GRADIENTE_MEIO, COR_GRADIENTE_FIM]
        ),
        padding=40
    )

def home_view(page: ft.Page):
    # Calcular métricas
    sales = get_sales()
    users = get_all_users()
    total_sales = sum([s["total_value"] for s in sales]) if sales else 0.0
    total_users = len(users) if users else 0

    # Atividades recentes
    recent_activities = get_recent_activities(5)

    activity_items = []
    for activity in recent_activities:
        activity_items.append(
            ft.ListTile(
                leading=ft.Icon(ft.Icons.HISTORY, color=COLOR_TEXT_SECONDARY),
                title=ft.Text(activity["action"], font_family=FONT_FAMILY, size=FONT_SIZE_SMALL),
                subtitle=ft.Text(
                    f"{activity['user_name']} - {activity['date'][:16]}",
                    font_family=FONT_FAMILY,
                    size=FONT_SIZE_SMALL - 2
                ),
                trailing=ft.Text(
                    activity["details"] or "",
                    font_family=FONT_FAMILY,
                    size=FONT_SIZE_SMALL - 2,
                    color=COLOR_TEXT_SECONDARY
                ),
            )
        )
        if activity != recent_activities[-1]:
            activity_items.append(ft.Divider(height=1))

    # Cards de resumo
    dashboard_cards = ft.Row([
        create_dashboard_card(
            "Vendas Mensais",
            f"R$ {total_sales:.2f}",
            "+12% vs último mês",
            ft.Icons.TRENDING_UP
        ),
        create_dashboard_card(
            "Total de Funcionários",
            str(total_users),
            "Ativos no sistema",
            ft.Icons.PEOPLE_OUTLINE
        ),
        create_dashboard_card(
            "Atividade Recente",
            str(len(recent_activities)),
            "Últimas ações",
            ft.Icons.ACCESS_TIME
        ),
        create_dashboard_card(
            "Produtos Ativos",
            str(len(get_all_products())),
            "Em estoque",
            ft.Icons.INVENTORY_2
        ),
    ], scroll=ft.ScrollMode.ADAPTIVE)

    # Conteúdo principal da tela inicial
    welcome_content = ft.Column([
        ft.Text(
            "Bem-vindo ao Sistema de Vendas",
            size=FONT_SIZE_H1,
            weight=ft.FontWeight.W_700,
            color=COLOR_TEXT_PRIMARY,
            font_family=FONT_FAMILY
        ),
        ft.Text(
            f"Olá, {state.logged_user['name']}! Aqui está o resumo do seu desempenho.",
            size=FONT_SIZE_BODY,
            color=COLOR_TEXT_SECONDARY,
            font_family=FONT_FAMILY
        ),
        ft.Container(height=PADDING_XLARGE),
        dashboard_cards,
        ft.Container(height=PADDING_XLARGE),
        ft.Container(
            expand=True,
            content=create_card(
                ft.Column([
                    ft.Text(
                        "Atividade Recente",
                        size=FONT_SIZE_H2,
                        weight=ft.FontWeight.W_600,
                        color=COLOR_TEXT_PRIMARY,
                        font_family=FONT_FAMILY
                    ),
                    ft.Container(
                    content=(
                        ft.Column(
                            activity_items,
                            scroll=ft.ScrollMode.ADAPTIVE
                        )
                        if activity_items
                        else ft.Text(
                            "Nenhuma atividade recente",
                            color=COLOR_TEXT_SECONDARY,
                            font_family=FONT_FAMILY
                        )
                    ),
                    height=300
                ),
                ])
            )
        )
    ])

    # Container principal
    return ft.Container(
        content=ft.Column([
            welcome_content
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )


def users_view(page: ft.Page):
    name_f = create_input_field("Nome Completo", width=300, icon=ft.Icons.PERSON)
    username_f = create_input_field("Nome de Usuário", width=300, icon=ft.Icons.BADGE)
    password_f = create_input_field("Senha", width=300, password=True, icon=ft.Icons.LOCK)
    role_dd = ft.Dropdown(
        label="Permissão",
        width=200,
        options=[
            ft.dropdown.Option("admin", "Administrador"),
            ft.dropdown.Option("employee", "Funcionário")
        ],
        value="employee",
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER
    )
    
    msg = ft.Text("", size=FONT_SIZE_SMALL)
    
    # Diálogo de edição
    edit_name_f = create_input_field("Nome Completo", width=300, icon=ft.Icons.PERSON)
    edit_username_f = create_input_field("Nome de Usuário", width=300, icon=ft.Icons.BADGE)
    edit_role_dd = ft.Dropdown(
        label="Permissão",
        width=200,
        options=[
            ft.dropdown.Option("admin", "Administrador"),
            ft.dropdown.Option("employee", "Funcionário")
        ],
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER
    )
    
    editing_user_id = None
    
    def open_edit_dialog(user):
        nonlocal editing_user_id
        try:
            editing_user_id = user["id"]
            edit_name_f.value = user["name"]
            edit_username_f.value = user["username"]
            edit_role_dd.value = user["role"]
            page.dialog = edit_dialog
            edit_dialog.open = True
            page.update()
        except Exception as ex:
            page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao abrir diálogo de edição: {ex}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
    
    def save_edit(e):
        if editing_user_id:
            ok, err = update_user(editing_user_id, edit_name_f.value, edit_username_f.value, edit_role_dd.value)
            if not ok:
                page.snack_bar = ft.SnackBar(ft.Text(f"Erro: {err}"), bgcolor=COLOR_ERROR)
            else:
                edit_dialog.open = False
                load_table()
                page.update()
            page.snack_bar.open = True
            page.update()
    
    edit_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Editar Usuário", font_family=FONT_FAMILY),
        content=ft.Column([
            edit_name_f,
            edit_username_f,
            edit_role_dd,
        ], tight=True),
        actions=[
            ft.TextButton("Cancelar", on_click=lambda e: setattr(edit_dialog, 'open', False)),
            ft.TextButton("Salvar", on_click=save_edit),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    
    # Create modern table
    def create_user_table():
        users = get_all_users()
        rows = []
        for u in users:
            uid = u["id"]
            row = ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(str(uid), font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Text(u["name"], font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Text(u["username"], font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Container(
                        content=ft.Text(
                            "Administrador" if u["role"] == "admin" else "Funcionário",
                            color=ft.Colors.WHITE,
                            size=FONT_SIZE_SMALL,
                            weight=ft.FontWeight.W_500
                        ),
                        padding=ft.padding.symmetric(horizontal=12, vertical=6),
                        border_radius=BORDER_RADIUS_SMALL,
                        bgcolor=COLOR_PRIMARY_START if u["role"] == "admin" else COLOR_TEXT_SECONDARY
                    )),
                    ft.DataCell(ft.Text(f"R$ {u.get('debt_balance', 0.0):.2f}", font_family=FONT_FAMILY)),
                    ft.DataCell(ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=COLOR_ERROR,
                        tooltip="Excluir",
                        icon_size=20,
                        on_click=lambda e, id=uid: (delete_user(id), load_table(), page.update())
                    ))
                ]
            )
            rows.append(row)
        return rows

    data_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Nome", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Usuário", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Permissão", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Saldo", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Ações", font_family=FONT_FAMILY)),
        ],
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND,
    )

    def load_table():
        data_table.rows = create_user_table()
        page.update()

    # Dialog para mostrar compras do usuário
    user_purchases_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Compras do Funcionário", font_family=FONT_FAMILY),
        content=ft.Container(),
        actions=[
            ft.TextButton("Fechar", on_click=lambda e: setattr(user_purchases_dialog, 'open', False)),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.dialog = user_purchases_dialog

    def show_user_purchases(user_id):
        # Buscar vendas para funcionário (employee_sales)
        rows = []
        with db_lock:
            conn = get_db_conn()
            cur = conn.cursor()
            # employee-specific tables were removed; use main `sales` table where sale_type='funcionario'
            cur.execute("SELECT id, date, total_value, sale_type FROM sales WHERE employee_id = ? AND sale_type = 'funcionario' ORDER BY date DESC", (user_id,))
            rows = cur.fetchall()
            conn.close()

        items = []
        for r in rows:
            items.append(ft.ListTile(
                title=ft.Text(f"Venda {r['id']} - R$ {r['total_value']:.2f}", font_family=FONT_FAMILY),
                subtitle=ft.Text(f"{r['date'][:16]} - Status: {r['payment_status']}", font_family=FONT_FAMILY),
                trailing=ft.Row([
                    ft.IconButton(icon=ft.Icons.ATTACH_MONEY, icon_color=COLOR_ERROR, on_click=lambda e, sid=r['id']: update_payment_status_ui_simple(sid, 'Pago')),
                    ft.IconButton(icon=ft.Icons.PENDING_ACTIONS, icon_color=COLOR_WARNING, on_click=lambda e, sid=r['id']: update_payment_status_ui_simple(sid, 'Em Aberto')),
                ], spacing=4)
            ))

        user_purchases_dialog.content = ft.Container(content=ft.Column(items if items else [ft.Text("Nenhuma venda encontrada")]), height=400)
        user_purchases_dialog.open = True
        page.update()

    # Dialog para marcar pagamento (permitir marcar valor ou método, aqui apenas alteração de status simples)
    mark_payment_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Marcar Pagamento", font_family=FONT_FAMILY),
        content=ft.Column([
            ft.Text("Selecione a venda no diálogo de Compras para alterar o status."),
        ]),
        actions=[
            ft.TextButton("Fechar", on_click=lambda e: setattr(mark_payment_dialog, 'open', False)),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def open_mark_payment_dialog(user_id):
        # Para simplicidade, apenas abrir a lista de compras (onde já há botões de marcar)
        show_user_purchases(user_id)

    def update_payment_status_ui_simple(sale_id, status):
        try:
            update_payment_status_db(sale_id, status)
            page.snack_bar = ft.SnackBar(ft.Text(f"✓ Status atualizado para {status}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            # atualizar tabela de usuários para refletir mudança em dívida
            load_table()
            page.update()
        except Exception as e:
            page.snack_bar = ft.SnackBar(ft.Text(f"Erro: {e}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()

    def add_user(e):
        ok, err = create_user(name_f.value or "", (username_f.value or "").strip(), password_f.value or "1234", role_dd.value or "employee")
        if not ok:
            msg.value = err
            msg.color = COLOR_ERROR
        else:
            msg.value = "✓ Usuário criado com sucesso!"
            msg.color = COLOR_ERROR
            name_f.value = username_f.value = password_f.value = ""
            load_table()
        page.update()

    form_card = create_card(
        ft.Column([
            ft.Text(
                "Adicionar Novo Usuário",
                size=FONT_SIZE_H3,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Row([name_f, username_f], spacing=PADDING_MEDIUM),
            ft.Row([password_f, role_dd], spacing=PADDING_MEDIUM),
            ft.Row([
                create_gradient_button("Adicionar Usuário", on_click=add_user, width=200),
                ft.TextButton(
                    content=ft.Text("Atualizar Tabela", font_family=FONT_FAMILY),
                    on_click=lambda e: load_table()
                ),
            ]),
            msg
        ], spacing=PADDING_MEDIUM)
    )

    page.dialog = edit_dialog
    
    return ft.Container(
        content=ft.Column([
            ft.Text(
                "Gestão de Usuários",
                size=FONT_SIZE_H1,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(height=PADDING_LARGE),
            form_card,
            ft.Container(height=PADDING_LARGE),
            create_card(
                ft.Column([
                    ft.Text(
                        "Usuários Cadastrados",
                        size=FONT_SIZE_H3,
                        weight=ft.FontWeight.W_600,
                        color=COLOR_TEXT_PRIMARY,
                        font_family=FONT_FAMILY
                    ),
                    ft.Container(
                        content=data_table,
                        border_radius=BORDER_RADIUS_SMALL,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE
                    )
                ])
            )
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )

def products_view(page: ft.Page):
    name_f = create_input_field("Nome do Produto", width=300, icon=ft.Icons.SHOPPING_BAG)
    price_f = create_input_field("Preço (R$)", width=200, icon=ft.Icons.ATTACH_MONEY)
    cat_f = create_input_field("Categoria", width=250, icon=ft.Icons.CATEGORY)
    
    msg = ft.Text("", size=FONT_SIZE_SMALL)
    
    # Diálogo de edição
    edit_name_f = create_input_field("Nome do Produto", width=300, icon=ft.Icons.SHOPPING_BAG)
    edit_price_f = create_input_field("Preço (R$)", width=200, icon=ft.Icons.ATTACH_MONEY)
    edit_cat_f = create_input_field("Categoria", width=250, icon=ft.Icons.CATEGORY)
    
    editing_product_id = None
    
    def open_edit_dialog(product):
        nonlocal editing_product_id
        try:
            editing_product_id = product["id"]
            edit_name_f.value = product["name"]
            edit_price_f.value = str(product["price"])  # ✅ SEMPRE usa o preço real do produto
            edit_cat_f.value = product["category"] or ""
            page.dialog = edit_dialog
            edit_dialog.open = True
            page.update()
        except Exception as ex:
            page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao abrir diálogo de edição: {ex}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
    
    def save_edit(e):
        if editing_product_id:
            try:
                price = float(edit_price_f.value.replace(",", "."))
                update_product(editing_product_id, edit_name_f.value, price, edit_cat_f.value)
                page.snack_bar = ft.SnackBar(ft.Text("✓ Produto atualizado com sucesso!"), bgcolor=COLOR_ERROR)
                edit_dialog.open = False
                load_table()
                page.update()
            except Exception as ex:
                page.snack_bar = ft.SnackBar(ft.Text(f"Erro: {ex}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
    
    edit_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Editar Produto", font_family=FONT_FAMILY),
        content=ft.Column([
            edit_name_f,
            edit_price_f,
            edit_cat_f,
        ], tight=True),
        actions=[
            ft.TextButton("Cancelar", on_click=lambda e: setattr(edit_dialog, 'open', False)),
            ft.TextButton("Salvar", on_click=save_edit),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    
    # Upload em lote
    def handle_file_upload(e: ft.FilePickerResultEvent):
        if e.files:
            f = e.files[0]
            products_data = []
            try:
                # Se o FilePicker fornecer um caminho no sistema de arquivos
                if getattr(f, 'path', None):
                    file_path = f.path
                    if file_path.lower().endswith('.csv'):
                        with open(file_path, 'r', encoding='utf-8') as fh:
                            reader = csv.DictReader(fh)
                            for row in reader:
                                products_data.append(row)
                else:
                    # Caso o arquivo venha em memória (FilePickerResult), tentar ler bytes
                    content = None
                    try:
                        # Algumas versões do FilePicker expõem `bytes` ou `read()`
                        if hasattr(f, 'bytes') and f.bytes:
                            content = f.bytes
                        elif hasattr(f, 'read'):
                            content = f.read()
                    except Exception:
                        content = None

                    if content:
                        # garantir que temos bytes
                        if isinstance(content, str):
                            text = content
                        else:
                            try:
                                text = content.decode('utf-8')
                            except:
                                text = content.decode('latin-1')

                        reader = csv.DictReader(text.splitlines())
                        for row in reader:
                            products_data.append(row)

                success_count, errors = batch_create_products(products_data)
                
                if success_count > 0:
                    page.snack_bar = ft.SnackBar(
                        ft.Text(f"✓ {success_count} produtos importados com sucesso!"), 
                        bgcolor=COLOR_ERROR
                    )
                if errors:
                    error_msg = "\n".join(errors[:5])  # Mostrar apenas os primeiros 5 erros
                    page.snack_bar = ft.SnackBar(
                        ft.Text(f"Importação parcial: {error_msg}"), 
                        bgcolor=COLOR_WARNING
                    )
                
                load_table()
            except Exception as ex:
                page.snack_bar = ft.SnackBar(
                    ft.Text(f"Erro na importação: {str(ex)}"), 
                    bgcolor=COLOR_ERROR
                )
            page.snack_bar.open = True
            page.update()
    
    file_picker = ft.FilePicker(on_result=handle_file_upload)
    # Não duplicar file_picker no overlay caso a view seja recriada
    if file_picker not in page.overlay:
        page.overlay.append(file_picker)
    
    def download_template(e):
        # Criar template CSV
        template_data = (
            "name,price,category\n"
            "Produto Exemplo 1,29.99,Eletrônicos\n"
            "Produto Exemplo 2,15.50,Roupas\n"
            "Produto Exemplo 3,99.90,Casa"
        )

        # Salvar localmente
        file_path = "template_produtos.csv"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(template_data)

        page.snack_bar = ft.SnackBar(
            ft.Text("✓ Template salvo como 'template_produtos.csv'"),
            bgcolor=COLOR_ERROR
        )
        page.snack_bar.open = True
        page.update()
    
    def create_products_table():
        prods = get_all_products()
        rows = []
        for p in prods:
            pid = p["id"]
            row = ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(str(pid), font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Text(p["name"], font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Text(f"R$ {p['price']:.2f}", font_family=FONT_FAMILY)),
                    ft.DataCell(ft.Text(p["category"] or "-", font_family=FONT_FAMILY)),
                    ft.DataCell(ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=COLOR_ERROR,
                        tooltip="Excluir",
                        on_click=lambda e, id=pid: (delete_product(id), load_table(), page.update()),
                        icon_size=20
                    ))
                ]
            )
            rows.append(row)
        return rows

    data_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Nome", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Preço", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Categoria", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Ações", font_family=FONT_FAMILY)),
        ],
        rows=create_products_table(),
        border=ft.border.all(1, COLOR_BORDER),
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND,
    )

    def load_table():
        data_table.rows = create_products_table()
        page.update()

    def add_product(e):
        try:
            # ✅ Na gestão de produtos, SEMPRE usa o preço digitado
            price = float(price_f.value.replace(",", "."))
            create_product((name_f.value or "").strip(), price, (cat_f.value or "").strip())
            msg.value = "✓ Produto adicionado com sucesso!"
            msg.color = COLOR_PAGO
            name_f.value = price_f.value = cat_f.value = ""
            load_table()
        except Exception:
            msg.value = "❌ Erro nos dados do produto. Verifique o preço."
            msg.color = COLOR_ERROR
        page.update()

    page.dialog = edit_dialog
    
    form_card = create_card(
        ft.Column([
            ft.Text(
                "Adicionar Novo Produto",
                size=FONT_SIZE_H3,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Row([name_f, price_f, cat_f], spacing=PADDING_MEDIUM),
            ft.Row([
                create_gradient_button("Adicionar Produto", on_click=add_product, width=200),
                msg
            ], spacing=PADDING_MEDIUM),
        ], spacing=PADDING_MEDIUM)
    )
    
    batch_upload_card = create_card(
        ft.Column([
            ft.Text(
                "Upload em Lote",
                size=FONT_SIZE_H3,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Text(
                "Importe múltiplos produtos de uma vez usando um arquivo CSV",
                size=FONT_SIZE_SMALL,
                color=COLOR_TEXT_SECONDARY,
                font_family=FONT_FAMILY
            ),
            ft.Row([
                create_gradient_button("Baixar Template", on_click=download_template, width=180),
                create_gradient_button("Selecionar Arquivo CSV", on_click=lambda e: file_picker.pick_files(
                    allowed_extensions=['csv'],
                    allow_multiple=False
                ), width=200),
            ])
        ], spacing=PADDING_MEDIUM)
    )

    # (Export function removed as requested)

    return ft.Container(
        content=ft.Column([
            ft.Text(
                "Gestão de Produtos",
                size=FONT_SIZE_H1,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(height=PADDING_LARGE),
            form_card,
            ft.Container(height=PADDING_MEDIUM),
            batch_upload_card,
            ft.Container(height=PADDING_LARGE),
            create_card(
                ft.Column([
                    ft.Text(
                        "Produtos Cadastrados",
                        size=FONT_SIZE_H3,
                        weight=ft.FontWeight.W_600,
                        color=COLOR_TEXT_PRIMARY,
                        font_family=FONT_FAMILY
                    ),
                    ft.Container(
                        content=data_table,
                        border_radius=BORDER_RADIUS_SMALL,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE
                    )
                ])
            )
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )

def sales_view(page: ft.Page):
    prod_dd = ft.Dropdown(
        label="Produto",
        width=300,
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER
    )
    qty_f = create_input_field("Quantidade", width=120)
    qty_f.value = "1"
    # Campo de data opcional (quando vazio assume data/hora atual)
    date_f = create_input_field("Data (YYYY-MM-DD HH:MM:SS)", width=300, icon=ft.Icons.DATE_RANGE)
    date_f.value = ""  # vazio por padrão -> assume agora
    
    # Campo para preço customizado
    custom_price_f = create_input_field("Preço Unitário (R$)", width=180, icon=ft.Icons.ATTACH_MONEY)
    # Parcelamento (até 12 parcelas)
    installments_dd = ft.Dropdown(
        label="Número de Parcelas",
        width=160,
        options=[ft.dropdown.Option(str(i), str(i)) for i in range(1, 13)],
        value="1",
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER
    )

    # Campos dinâmicos para cada parcela (usando helper)
    installment_fields = ft.Column([], spacing=10)
    wire_installment_fields(page, installments_dd, installment_fields, max_installments=12)
    
    # Removido tipo de venda e adicionado método de pagamento
    payment_method_dd = ft.Dropdown(
        label="Método de Pagamento", 
        width=200,
        options=[
            ft.dropdown.Option("Dinheiro", "Dinheiro"),
            ft.dropdown.Option("Cartão", "Cartão"),
            ft.dropdown.Option("Pix", "Pix"),
            ft.dropdown.Option("Fiado", "Fiado")
        ],
        value="Dinheiro",
        border_radius=BORDER_RADIUS_SMALL,
        border_color=COLOR_BORDER
    )
    
    discount_f = create_input_field("Desconto (R$)", width=150, icon=ft.Icons.DISCOUNT)
    discount_f.value = "0"
    
    total_f = create_input_field("Valor Total (R$)", width=200, icon=ft.Icons.ATTACH_MONEY)
    total_f.read_only = True
    
    # Lista de produtos para venda múltipla
    cart_items = []
    added_products = ft.Column([], scroll=ft.ScrollMode.ADAPTIVE)
    cart_total = 0.0

    def refresh_products_dd():
        prods = get_all_products()
        prod_dd.options = [ft.dropdown.Option(str(p["id"]), text=f"{p['name']} (R$ {p['price']:.2f})") for p in prods]
        page.update()

    def calculate_total():
        nonlocal cart_total
        try:
            discount = float(discount_f.value or "0")
            cart_total = sum(item['total_price'] for item in cart_items)
            final_total = max(0, cart_total - discount)
            total_f.value = f"R$ {final_total:.2f}"
        except:
            total_f.value = "R$ 0.00"
    # Recalculate when discount changes
    discount_f.on_change = lambda e: calculate_total()
    page.update()

    def on_product_change(e):
        if prod_dd.value:
            product = get_product_by_id(int(prod_dd.value))
            if product:
                # ✅ Usar último preço se disponível, senão usar preço do produto
                last_price = state.last_product_price.get(product["id"], product["price"])
                custom_price_f.value = f"{last_price:.2f}"
                page.update()

    def add_product_to_cart(e):
        try:
            pid = int(prod_dd.value)
            qty = int(qty_f.value)
            custom_price = float(custom_price_f.value.replace(",", ".")) if custom_price_f.value else None
            
            product = get_product_by_id(pid)
            if not product:
                page.snack_bar = ft.SnackBar(ft.Text("Produto não encontrado!"), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
                page.update()
                return
            
            # ✅ Salvar último preço usado
            unit_price = custom_price if custom_price is not None else product["price"]
            state.last_product_price[pid] = unit_price
            
            total_price = unit_price * qty
            
            cart_items.append({
                'product_id': pid,
                'product_name': product["name"],
                'quantity': qty,
                'unit_price': unit_price,
                'total_price': total_price
            })
            
            update_cart_display()
            calculate_total()
            qty_f.value = "1"
            
        except Exception as ex:
            page.snack_bar = ft.SnackBar(ft.Text(f"Erro: {ex}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
        page.update()

    def update_cart_display():
        nonlocal cart_total
        added_products.controls.clear()

        for i, item in enumerate(cart_items):
            added_products.controls.append(
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.SHOPPING_BAG, color=COLOR_TEXT_SECONDARY),
                    title=ft.Text(item['product_name'], font_family=FONT_FAMILY),
                    subtitle=ft.Text(f"{item['quantity']} x R$ {item['unit_price']:.2f}", font_family=FONT_FAMILY),
                    trailing=ft.Row([
                        ft.Text(f"R$ {item['total_price']:.2f}", font_family=FONT_FAMILY),
                        ft.IconButton(
                            icon=ft.Icons.DELETE,
                            icon_color=COLOR_ERROR,
                            icon_size=20,
                            on_click=lambda e, idx=i: remove_from_cart(idx)
                        )
                    ], tight=True)
                )
            )
        
        # Adicionar subtotal (calcular localmente para evitar uso de valor desatualizado)
        subtotal = sum(item['total_price'] for item in cart_items)
        cart_total = subtotal

        added_products.controls.append(ft.Divider())
        added_products.controls.append(
            ft.ListTile(
                title=ft.Text("SUBTOTAL:", size=FONT_SIZE_BODY, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                trailing=ft.Text(f"R$ {subtotal:.2f}", size=FONT_SIZE_BODY, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
            )
        )
        # Atualizar total exibido (considerando desconto atual)
        calculate_total()

    def remove_from_cart(index):
        if 0 <= index < len(cart_items):
            cart_items.pop(index)
            update_cart_display()
            calculate_total()
            page.update()

    def clear_cart(e):
        cart_items.clear()
        update_cart_display()
        discount_f.value = "0"
        calculate_total()
        page.update()


    def register_sale(e):
        if not cart_items:
            page.snack_bar = ft.SnackBar(ft.Text("Adicione produtos ao carrinho: selecione um produto, informe a quantidade e clique 'Adicionar Produto'."), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
            return
        try:
            discount = float(discount_f.value or "0")
            total_before = sum(item['total_price'] for item in cart_items)
            final_total = max(0, total_before - discount)

            # Distribuir desconto proporcionalmente entre os itens para que
            # a soma das vendas registradas seja igual a final_total.
            remaining = final_total
            adjusted_items = []
            if total_before > 0 and discount > 0:
                factor = (final_total) / total_before if total_before > 0 else 1.0
            else:
                factor = 1.0

            for idx, item in enumerate(cart_items):
                if idx < len(cart_items) - 1:
                    adj_total = round(item['total_price'] * factor, 2)
                    remaining -= adj_total
                else:
                    # Último item absorve qualquer diferença de arredondamento
                    adj_total = round(remaining, 2)

                adj_unit = (adj_total / item['quantity']) if item['quantity'] else 0
                adjusted_items.append((item, adj_unit, adj_total))

            # Preparar string de data para cada venda (se fornecida)
            date_value = date_f.value.strip() if date_f and date_f.value else None

            # Registrar cada produto com preço ajustado
            # distribuir desconto já feito anteriormente (adjusted_items contains tuples)
            # validate installment dates before proceeding
            inst_dates = read_installment_dates(installment_fields)
            if not validate_installment_dates(inst_dates):
                page.snack_bar = ft.SnackBar(ft.Text("Preencha os vencimentos das parcelas no formato YYYY-MM-DD para cada parcela. Se for 1 parcela, deixe o único campo vazio ou informe a data."), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
                page.update()
                return

            for item, adj_unit_price, adj_total_price in adjusted_items:
                ok, err = record_sale(
                    state.logged_user["id"],
                    item['product_id'],
                    item['quantity'],
                    "cliente",
                    adj_unit_price,
                    payment_method_dd.value if 'payment_method_dd' in locals() else None,
                    date_str=date_value,
                    num_installments=int(installments_dd.value) if installments_dd and installments_dd.value else 1,
                    first_payment_date=read_first_installment_date(installment_fields),
                    installment_dates=inst_dates
                )
                if not ok:
                    page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao registrar item: {err}. Verifique os dados do produto e tente novamente."), bgcolor=COLOR_ERROR)
                    page.snack_bar.open = True
                    page.update()
                    return

            page.snack_bar = ft.SnackBar(
                ft.Text(f"✓ Venda registrada com sucesso! Total: R$ {final_total:.2f}"),
                bgcolor=COLOR_PAGO
            )

            # Limpar carrinho
            cart_items.clear()
            update_cart_display()
            discount_f.value = "0"
            calculate_total()
            load_sales()

        except Exception as ex:
            # Mostrar erro mais detalhado ao usuário e logar atividade
            err_msg = f"Erro ao registrar venda: {ex}"
            page.snack_bar = ft.SnackBar(ft.Text(err_msg), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            log_activity(state.logged_user['id'] if state.logged_user else 1, "ERRO_REGISTRAR_VENDA", err_msg)
            # tentar imprimir stack trace curto se disponível
            try:
                import traceback
                tb = traceback.format_exc()
                # também registrar stack trace no activity_log (limitar tamanho)
                log_activity(state.logged_user['id'] if state.logged_user else 1, "TRACE_REGISTRO_VENDA", tb[:1000])
            except Exception:
                pass

        page.update()

    def create_sales_table():
        sales_data = get_sales()
        rows = []
        # Helper to handle delete clicks (logs and forwards to confirm)
        def on_sales_delete_click(e, sale_id):
            try:
                log_activity(state.logged_user['id'] if state.logged_user else 1, 'CLICK_EXCLUIR_VENDA', f'clicou excluir venda {sale_id}')
            except Exception:
                pass
            try:
                with open('click_debug.log', 'a', encoding='utf-8') as fh:
                    fh.write(f"SALE_CLICK {sale_id} {datetime.now().isoformat()}\n")
            except Exception:
                pass
            # immediate deletion
            try:
                ok = delete_sale(sale_id)
                if ok:
                    page.snack_bar = ft.SnackBar(ft.Text(f"Venda {sale_id} excluída"), bgcolor=COLOR_PRIMARY_START)
                    page.snack_bar.open = True
                    load_sales()
                else:
                    page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao excluir venda {sale_id}"), bgcolor=COLOR_ERROR)
                    page.snack_bar.open = True
            except Exception as ex:
                page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao excluir venda: {ex}"), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
            page.update()
        for s in sales_data:
            # prepare safe truncated product name (we removed employee and id columns)
            prod_name_raw = s['product_name'] if s['product_name'] is not None else ''
            prod_name = (prod_name_raw[:18] + '...') if len(prod_name_raw) > 18 else (prod_name_raw or '—')

            row = ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(s["date"][:16], font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text(prod_name, font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text(str(s["quantity"]), font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text(f"R$ {s['total_value']:.2f}", font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text((s['payment_method'] if 'payment_method' in s.keys() and s['payment_method'] else '-'), font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text(str(s['num_installments']) if 'num_installments' in s.keys() and s['num_installments'] else '1', font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Text(format_date_for_display(s['first_payment_date']) if 'first_payment_date' in s.keys() else '-', font_family=FONT_FAMILY, size=10)),
                    ft.DataCell(ft.Container(
                        content=ft.Text(
                            "Cliente" if s["sale_type"] == "cliente" else "Funcionário",
                            color=ft.Colors.WHITE,
                            size=FONT_SIZE_SMALL,
                            weight=ft.FontWeight.W_500
                        ),
                        padding=ft.padding.symmetric(horizontal=12, vertical=6),
                        border_radius=BORDER_RADIUS_SMALL,
                        bgcolor=COR_BOTAO_FIM if s["sale_type"] == "cliente" else COLOR_WARNING
                    )),
                    ft.DataCell(ft.Row([
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip="Excluir",
                            icon_size=14,
                            on_click=lambda e, sid=s["id"]: on_sales_delete_click(e, sid)
                        )
                    ], spacing=0))
                ]
            )
            rows.append(row)
        return rows

    def confirm_delete_sale(e, sale_id):
        def do_delete(ev):
            ok = delete_sale(sale_id)
            if ok:
                page.snack_bar = ft.SnackBar(ft.Text(f"Venda {sale_id} excluída"), bgcolor=COLOR_PRIMARY_START)
                page.snack_bar.open = True
                load_sales()
            else:
                page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao excluir venda {sale_id}"), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
            page.update()
        # Create dialog first, then create actions so callbacks can capture dlg
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Confirmar exclusão", weight=ft.FontWeight.W_700),
            content=ft.Column([
                ft.Text(f"Deseja realmente excluir a venda {sale_id}?", font_family=FONT_FAMILY),
                ft.Divider(height=8),
                ft.Text("Esta ação é permanente. Clique Excluir para confirmar.", font_family=FONT_FAMILY, size=12)
            ]),
            actions=[]
        )

        def cancel(ev):
            dlg.open = False
            page.update()

        cancel_btn = ft.TextButton("Cancelar", on_click=cancel)
        delete_btn = ft.TextButton("Excluir", on_click=do_delete)

        dlg.actions = [cancel_btn, delete_btn]
        page.dialog = dlg
        dlg.open = True
        try:
            page.snack_bar = ft.SnackBar(ft.Text("Confirmação aberta — verifique o diálogo"), bgcolor=COLOR_PRIMARY_START)
            page.snack_bar.open = True
        except Exception:
            pass
        page.update()

    sales_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Data", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Produto", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Qtd", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Valor", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Método", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Parcelas", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("1º Vencimento", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Tipo", font_family=FONT_FAMILY)),
            ft.DataColumn(ft.Text("Ações", font_family=FONT_FAMILY)),
        ],
        rows=create_sales_table(),
        border=ft.border.all(1, COLOR_BORDER),
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND,
    )

    def load_sales():
        sales_table.rows = create_sales_table()
        page.update()

    refresh_products_dd()
    load_sales()
    
    # Vincular evento de mudança no dropdown de produtos
    prod_dd.on_change = on_product_change
    
    form_card = create_card(
        ft.Column([
            ft.Text(
                "Registrar Nova Venda",
                size=FONT_SIZE_H3,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Row([prod_dd, qty_f, custom_price_f], spacing=PADDING_MEDIUM),
                # Campo de data opcional
                ft.Row([date_f], spacing=PADDING_MEDIUM),
                ft.Row([payment_method_dd, discount_f, installments_dd], spacing=PADDING_MEDIUM),
            ft.Row([
                create_gradient_button("Adicionar Produto", on_click=add_product_to_cart, width=180),
                ft.TextButton("Limpar Carrinho", on_click=clear_cart),
            ]),
            ft.Container(height=PADDING_MEDIUM),
            ft.Text(
                "Produtos Adicionados",
                size=FONT_SIZE_BODY,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(
                content=added_products,
                height=200,
                border=ft.border.all(1, COLOR_BORDER),
                border_radius=BORDER_RADIUS_SMALL,
                padding=PADDING_SMALL,
            ),
            ft.Container(height=PADDING_MEDIUM),
            ft.Row([total_f], spacing=PADDING_MEDIUM),
            ft.Container(height=PADDING_MEDIUM),
            create_gradient_button("Registrar Venda", on_click=register_sale, width=200),
        ], spacing=PADDING_MEDIUM)
    )

    return ft.Container(
        content=ft.Column([
            ft.Text(
                "Registro de Vendas",
                size=FONT_SIZE_H1,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(height=PADDING_LARGE),
            form_card,
            ft.Container(height=PADDING_LARGE),
            create_card(
                ft.Column([
                    ft.Text(
                        "Histórico de Vendas",
                        size=FONT_SIZE_H3,
                        weight=ft.FontWeight.W_600,
                        color=COLOR_TEXT_PRIMARY,
                        font_family=FONT_FAMILY
                    ),
                    ft.Row([
                        create_gradient_button("Atualizar Tabela", on_click=lambda e: load_sales(), width=180),
                    ]),
                    ft.Container(height=PADDING_MEDIUM),
                    ft.Container(
                        content=ft.Column([
                            sales_table
                        ], scroll=ft.ScrollMode.AUTO),
                        height=520,
                        expand=True,
                        border_radius=BORDER_RADIUS_SMALL,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE
                    )
                ])
            )
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )
    # Lista de produtos adicionados
    added_products = ft.Column([], scroll=ft.ScrollMode.ADAPTIVE)
    cart_items = []
    total_value = 0.0

    # Campo de qual parcela está sendo paga
    installment_paid_dd = ft.Dropdown(
        label="Selecione a parcela paga",
        width=250,
        visible=False
    )

    def update_installment_options(e=None):
        try:
            num_inst = int(installments_dd.value) if installments_dd and installments_dd.value else 1
            options = []
            for i in range(num_inst):
                date_field = get_date_field(i)
                amount_field = get_amount_field(i)
                date_text = date_field.value if date_field and date_field.value else "sem data"
                amount_text = amount_field.value if amount_field and amount_field.value else "0"
                options.append(
                    ft.dropdown.Option(str(i + 1), f"{i+1}ª parcela - Venc: {date_text} - R$ {amount_text}")
                )
            
            installment_paid_dd.options = options
            installment_paid_dd.value = "1" if options else None
            installment_paid_dd.visible = True if options else False
            page.update()
        except Exception as e:
            print(f"Erro ao atualizar parcelas: {e}")

    # Campo de status do pagamento
    payment_status_dd = ft.Dropdown(
        label="Status do Pagamento",
        width=200,
        options=[
            ft.dropdown.Option("Em Aberto"),
            ft.dropdown.Option("Pago"),
            ft.dropdown.Option("Parcial")
        ],
        on_change=lambda e: update_installment_options() if e.control.value in ["Pago", "Parcial"] else setattr(installment_paid_dd, "visible", False) or page.update(),
        border_radius=8,
        border_color=ft.Colors.BLUE_GREY_300,
        value="Em Aberto"
    )

    installment_paid_dd = ft.Dropdown(
        label="Selecione a Parcela",
        hint_text="Escolha qual parcela foi paga",
        width=300,
        border_radius=8,
        border_color=ft.Colors.BLUE_GREY_300,
        visible=False
    )

    def _update_installment_options():
        """Atualiza as opções do dropdown de parcelas"""
        try:
            num_inst = int(installments_dd.value) if installments_dd.value else 1
        except:
            num_inst = 1

        options = []
        for i in range(num_inst):
            date_field = get_date_field(i)
            amount_field = get_amount_field(i)
            date_text = date_field.value if date_field and date_field.value else "sem data"
            amount_text = amount_field.value if amount_field and amount_field.value else "0"
            
            text = f"{i+1}ª Parcela - Venc: {date_text} - R$ {amount_text}"
            options.append(ft.dropdown.Option(str(i + 1), text))

        installment_paid_dd.options = options
        installment_paid_dd.value = "1" if options else None

    def on_payment_change(e):
        """Handler para mudança no status de pagamento"""
        is_paid = payment_status_dd.value in ["Pago", "Parcial"]
        if is_paid:
            _update_installment_options()
            installment_paid_dd.visible = True
        else:
            installment_paid_dd.visible = False
        page.update()

    # Conectar os event handlers
    payment_status_dd.on_change = on_payment_change
    # Desconto para venda do funcionário
    discount_emp_f = create_input_field("Desconto (R$)", width=150, icon=ft.Icons.DISCOUNT)
    discount_emp_f.value = "0"
    
    def refresh_employees_dd():
        users = get_all_users()
        employee_dd.options = [ft.dropdown.Option(str(u["id"]), text=f"{u['name']} ({u['username']})") for u in users if u["role"] == "employee"]
        page.update()
    
    def refresh_products_dd():
        prods = get_all_products()
        product_dd.options = [ft.dropdown.Option(str(p["id"]), text=f"{p['name']} (R$ {p['price']:.2f})") for p in prods]
        page.update()
    
    def on_product_change(e):
        if product_dd.value:
            product = get_product_by_id(int(product_dd.value))
            if product:
                # ✅ Usar último preço se disponível, senão usar preço do produto
                last_price = state.last_product_price.get(product["id"], product["price"])
                custom_price_f.value = f"{last_price:.2f}"
                page.update()
    
    def add_product_to_cart(e):
        nonlocal total_value
        try:
            product_id = int(product_dd.value)
            quantity = int(quantity_f.value)
            custom_price = float(custom_price_f.value.replace(",", ".")) if custom_price_f.value else None
            
            product = get_product_by_id(product_id)
            if not product:
                page.snack_bar = ft.SnackBar(ft.Text("Produto não encontrado!"), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
                page.update()
                return
            
            # ✅ Salvar último preço usado
            unit_price = custom_price if custom_price is not None else product["price"]
            state.last_product_price[product_id] = unit_price
            
            total_price = unit_price * quantity
            
            # Adicionar ao carrinho
            cart_items.append({
                'product_id': product_id,
                'product_name': product["name"],
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': total_price
            })
            
            total_value += total_price
            
            # Atualizar lista visual
            update_cart_display()
            
            # Limpar campos
            quantity_f.value = "1"
            page.update()
            
            page.snack_bar = ft.SnackBar(ft.Text("✓ Produto adicionado ao carrinho!"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            
        except Exception as ex:
            page.snack_bar = ft.SnackBar(ft.Text(f"Erro: {ex}"), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
    
    def update_cart_display():
        added_products.controls.clear()
        
        for i, item in enumerate(cart_items):
            added_products.controls.append(
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.SHOPPING_BAG, color=COLOR_TEXT_SECONDARY),
                    title=ft.Text(item['product_name'], font_family=FONT_FAMILY),
                    subtitle=ft.Text(f"{item['quantity']} x R$ {item['unit_price']:.2f}", font_family=FONT_FAMILY),
                    trailing=ft.Row([
                        ft.Text(f"R$ {item['total_price']:.2f}", font_family=FONT_FAMILY),
                        ft.IconButton(
                            icon=ft.Icons.DELETE,
                            icon_color=COLOR_ERROR,
                            icon_size=20,
                            on_click=lambda e, idx=i: remove_from_cart(idx)
                        )
                    ], tight=True)
                )
            )
        
        # Adicionar total
        added_products.controls.append(
            ft.Divider()
        )
        added_products.controls.append(
            ft.ListTile(
                title=ft.Text("TOTAL:", size=FONT_SIZE_BODY, weight=ft.FontWeight.W_700, font_family=FONT_FAMILY),
                trailing=ft.Text(f"R$ {total_value:.2f}", size=FONT_SIZE_BODY, weight=ft.FontWeight.W_700, font_family=FONT_FAMILY),
            )
        )
    
    def remove_from_cart(index):
        nonlocal total_value
        if 0 <= index < len(cart_items):
            total_value -= cart_items[index]['total_price']
            cart_items.pop(index)
            update_cart_display()
            page.update()
    
    def clear_cart(e):
        cart_items.clear()
        update_cart_display()
        page.update()

        # Pre-checks with explicit guidance
        if not state.logged_user:
            page.snack_bar = ft.SnackBar(ft.Text("Faça login antes de registrar vendas: clique em LOGIN e insira seu usuário e senha."), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
            return

        if not employee_dd.value:
            page.snack_bar = ft.SnackBar(ft.Text("Selecione um funcionário: abra o dropdown 'Funcionário' e escolha um funcionário."), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
            return

        if not cart_items:
            page.snack_bar = ft.SnackBar(ft.Text("Adicione produtos ao carrinho: escolha o produto, informe a quantidade e clique 'Adicionar Produto'."), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            page.update()
            return

        # Debug: log attempt inputs to activity_log and a debug file so we can inspect failures
        try:
            debug_payload = {
                'user_id': state.logged_user['id'] if state.logged_user else None,
                'employee_selected': employee_dd.value,
                'cart_items': [{'product_id': it['product_id'], 'quantity': it['quantity'], 'unit_price': it['unit_price'], 'total_price': it['total_price']} for it in cart_items],
                'discount': discount_emp_f.value,
                'num_installments': installments_dd.value if installments_dd else None,
                'installment_dates': read_installment_dates(installment_fields)
            }
            try:
                log_activity(state.logged_user['id'] if state.logged_user else 1, 'DEBUG_TENTAR_VENDA_FUNC', json.dumps(debug_payload)[:1000])
            except Exception:
                pass
            try:
                with open('last_venda_func_debug.json', 'w', encoding='utf-8') as fh:
                    fh.write(json.dumps(debug_payload, ensure_ascii=False, indent=2))
            except Exception:
                pass
        except Exception:
            # non-fatal debug logging
            pass

        try:
            employee_id = int(employee_dd.value)
            date_value = emp_date_f.value.strip() if emp_date_f and emp_date_f.value else None

            # Desconto
            try:
                discount_val = float(discount_emp_f.value or "0")
            except:
                discount_val = 0.0

            # Aplicar desconto proporcional e obter itens ajustados
            adjusted_items, final_total = distribute_discount_dicts(cart_items, discount_val)

            # Validar datas de parcelas
            try:
                inst_dates = read_installment_dates(installment_fields)
                
                # Debug log das datas lidas
                try:
                    with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                        fh.write(json.dumps({'event': 'read_dates', 'dates': inst_dates}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                
                # Converter datas se necessário
                converted_dates = []
                for date_str in inst_dates:
                    if not date_str or not date_str.strip():
                        converted_dates.append(None)
                        continue
                        
                    date_str = date_str.strip()
                    try:
                        # Tentar DD/MM/YYYY
                        if "/" in date_str:
                            day, month, year = date_str.split("/")
                            converted = f"{year}-{month}-{day}"
                            datetime.strptime(converted, "%Y-%m-%d")  # validar
                            converted_dates.append(converted)
                            continue
                            
                        # Tentar YYYY-MM-DD direto
                        datetime.strptime(date_str, "%Y-%m-%d")
                        converted_dates.append(date_str)
                        continue
                        
                    except ValueError as ve:
                        page.snack_bar = ft.SnackBar(
                            ft.Text(f"Data '{date_str}' inválida. Use o formato DD/MM/AAAA. Erro: {ve}"), 
                            bgcolor=COLOR_ERROR
                        )
                        page.snack_bar.open = True
                        page.update()
                        return
                        
                inst_dates = converted_dates
            except Exception as date_err:
                page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao validar datas: {date_err}. Use o formato DD/MM/AAAA"), bgcolor=COLOR_ERROR)
                page.snack_bar.open = True
                page.update()
                return

            # Ler valores das parcelas e validar soma
            installment_amounts = read_installment_amounts(installment_fields)
            try:
                num_inst = int(installments_dd.value) if installments_dd and installments_dd.value else 1
            except:
                num_inst = 1

            # Ensure list length matches num_inst
            if len(installment_amounts) < num_inst:
                # pad with zeros
                installment_amounts += [0.0] * (num_inst - len(installment_amounts))

            sum_installments = sum(installment_amounts[:num_inst]) if installment_amounts else 0.0

            def do_create_sale(_=None):
                # Se status é Pago ou Parcial, pegar qual parcela foi paga
                paid_installment = None
                if payment_status_dd.value in ["Pago", "Parcial"]:
                    try:
                        paid_installment = int(installment_paid_dd.value) - 1 if installment_paid_dd.value else None
                    except:
                        pass
                # Pre-log imediato para debug (garante que veremos a tentativa mesmo que a UI falhe depois)
                try:
                    pre_log = {
                        'timestamp': datetime.now().isoformat(),
                        'employee_id': employee_id,
                        'final_total': final_total,
                        'payment_status': payment_status_dd.value,
                        'num_installments': num_inst,
                        'installment_dates': inst_dates,
                        'items': [{'product_id': it['product_id'], 'quantity': it['quantity'], 'unit_price': it['unit_price'], 'total_price': it['total_price']} for it in adjusted_items]
                    }
                    with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                        fh.write(json.dumps({'event': 'before_create', **pre_log}, ensure_ascii=False) + "\n")
                except Exception:
                    pass

                try:
                    sale_id_local = create_employee_sale(
                        employee_id,
                        adjusted_items,
                        final_total,
                        payment_status_dd.value,
                        date_str=date_value,
                        num_installments=num_inst,
                        first_payment_date=read_first_installment_date(installment_fields),
                        installment_dates=inst_dates,
                        installment_amounts=installment_amounts,
                        paid_installment=paid_installment
                    )
                    # Post-log
                    try:
                        with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                            fh.write(json.dumps({'event': 'after_create', 'timestamp': datetime.now().isoformat(), 'sale_id': sale_id_local}, ensure_ascii=False) + "\n")
                    except Exception:
                        pass

                    page.snack_bar = ft.SnackBar(ft.Text(f"✓ Venda para funcionário registrada! ID: {sale_id_local}. Para consultar: abra Relatórios -> Vendas para Funcionários."), bgcolor=COLOR_PAGO)
                    page.snack_bar.open = True
                    # Limpar formulário: esvaziar carrinho e resetar campos relacionados
                    try:
                        cart_items.clear()
                    except Exception:
                        pass
                    try:
                        total_value = 0.0
                    except Exception:
                        pass
                    try:
                        update_cart_display()
                    except Exception:
                        pass
                    # Resetar campos do formulário para estado inicial
                    try:
                        discount_emp_f.value = "0"
                    except Exception:
                        pass
                    try:
                        emp_date_f.value = ""
                    except Exception:
                        pass
                    try:
                        employee_dd.value = None
                    except Exception:
                        pass
                    try:
                        product_dd.value = None
                    except Exception:
                        pass
                    try:
                        custom_price_f.value = ""
                    except Exception:
                        pass
                    try:
                        quantity_f.value = "1"
                    except Exception:
                        pass
                    try:
                        installments_dd.value = "1"
                    except Exception:
                        pass
                    try:
                        # rebuild installment fields to default
                        wire_installment_fields(page, installments_dd, installment_fields, max_installments=12)
                    except Exception:
                        pass
                    try:
                        payment_status_dd.value = "Em Aberto"
                    except Exception:
                        pass
                    try:
                        installment_paid_dd.visible = False
                    except Exception:
                        pass
                    try:
                        load_sales_table()
                    except Exception:
                        pass
                    page.update()
                except Exception as ex_create:
                    # Log de erro e feedback ao usuário
                    try:
                        with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                            fh.write(json.dumps({'event': 'create_error', 'timestamp': datetime.now().isoformat(), 'error': str(ex_create)}, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    err_msg = f"Erro ao registrar venda (funcionário): {ex_create}"
                    page.snack_bar = ft.SnackBar(ft.Text(err_msg), bgcolor=COLOR_ERROR)
                    page.snack_bar.open = True
                    log_activity(state.logged_user['id'] if state.logged_user else 1, "ERRO_REGISTRAR_VENDA_FUNC", err_msg)
                    page.update()

            # Log pre-validation
            try:
                with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                    fh.write(json.dumps({
                        'event': 'pre_validate',
                        'num_inst': num_inst,
                        'installment_amounts': installment_amounts,
                        'total_value': total_value,
                        'cart_items': len(cart_items),
                        'timestamp': datetime.now().isoformat()
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass

            # For single installment or when no amounts provided, proceed directly
            if num_inst == 1:
                # force single payment with total value
                installment_amounts = [final_total]  
                page.snack_bar = ft.SnackBar(ft.Text('Criando venda com parcela única...'), bgcolor=COLOR_PRIMARY_START)
                page.snack_bar.open = True
                page.update()
                do_create_sale()
                return
                
            # If there are multiple installments, validate their sum
            total_diff = abs(sum_installments - final_total)
            if total_diff > 0.01:  # permite uma pequena diferença por arredondamento
                msg = f"A soma das parcelas (R$ {sum_installments:.2f}) é diferente do total (R$ {final_total:.2f}). Deseja continuar?"
                try:
                    with open('debug_employee_sales.log', 'a', encoding='utf-8') as fh:
                        fh.write(json.dumps({
                            'event': 'installments_mismatch',
                            'sum_installments': sum_installments,
                            'final_total': final_total,
                            'difference': total_diff,
                            'timestamp': datetime.now().isoformat()
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass

                def confirm_yes(ev):
                    try:
                        page.dialog.open = False
                        page.update()
                        time.sleep(0.1)  # dar tempo pro diálogo fechar
                        do_create_sale()
                    except Exception as confirm_err:
                        page.snack_bar = ft.SnackBar(ft.Text(f"Erro ao confirmar: {confirm_err}"), bgcolor=COLOR_ERROR)
                        page.snack_bar.open = True
                        page.update()

                def confirm_no(ev):
                    try:
                        page.dialog.open = False
                    except Exception:
                        pass
                    page.update()
                    page.snack_bar = ft.SnackBar(ft.Text("Venda cancelada. Ajuste os valores das parcelas."), bgcolor=COLOR_WARNING)
                    page.snack_bar.open = True

                confirm_dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Confirmação de Parcelas", weight=ft.FontWeight.W_700),
                    content=ft.Column([
                        ft.Text(msg),
                        ft.Text("Clique em 'Continuar' para prosseguir com a venda, ou 'Cancelar' para ajustar os valores.", 
                               size=12, color=COLOR_TEXT_SECONDARY)
                    ]),
                    actions=[
                        ft.TextButton("Cancelar", on_click=confirm_no),
                        ft.TextButton("Continuar", on_click=confirm_yes),
                    ]
                )
                # Garantir que não há diálogo anterior aberto
                try:
                    if page.dialog and page.dialog.open:
                        page.dialog.open = False
                except Exception:
                    pass
                page.dialog = confirm_dlg
                confirm_dlg.open = True
                try:
                    # Aviso visual que requer confirmação
                    page.snack_bar = ft.SnackBar(
                        ft.Text("⚠️ Confirmação necessária: verifique o diálogo e clique 'Continuar' para registrar a venda."),
                        bgcolor=COLOR_WARNING
                    )
                    page.snack_bar.open = True
                except Exception:
                    pass
                page.update()
                return

            # Otherwise proceed normally
            do_create_sale()

        except Exception as ex:
            err_msg = f"Erro ao registrar venda (funcionário): {ex}"
            page.snack_bar = ft.SnackBar(ft.Text(err_msg), bgcolor=COLOR_ERROR)
            page.snack_bar.open = True
            log_activity(state.logged_user['id'] if state.logged_user else 1, "ERRO_REGISTRAR_VENDA_FUNC", err_msg)
            try:
                import traceback
                tb = traceback.format_exc()
                log_activity(state.logged_user['id'] if state.logged_user else 1, "TRACE_REGISTRO_VENDA_FUNC", tb[:1000])
            except Exception:
                pass
            page.update()
    
    # Employee sales UI removed (employee-tab disabled). Related tables and helpers were deleted.
    # If there are remaining references to employee sales, they will now fail explicitly.
    
    # Vincular evento de mudança no dropdown de produtos
    product_dd.on_change = on_product_change
    
    form_card = create_card(
        ft.Column([
            ft.Text(
                "Nova Venda para Funcionário",
                size=FONT_SIZE_H3,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Row(
                [
                    employee_dd,  # Funcionário
                    ft.Row(  # Status e parcela lado a lado
                        [payment_status_dd, installment_paid_dd],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START
                    ),
                    discount_emp_f  # Desconto
                ],
                spacing=PADDING_MEDIUM
            ),
            # Campo de data opcional para venda do funcionário
            ft.Row([emp_date_f], spacing=PADDING_MEDIUM),
            ft.Divider(),
            ft.Text(
                "Adicionar Produtos",
                size=FONT_SIZE_BODY,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Row([product_dd, quantity_f, custom_price_f], spacing=PADDING_MEDIUM),
            ft.Row([installments_dd], spacing=PADDING_MEDIUM),
            ft.Container(content=installment_fields),
            ft.Row([
                create_gradient_button("Adicionar Produto", on_click=add_product_to_cart, width=180),
                ft.TextButton("Limpar Carrinho", on_click=clear_cart),
            ]),
            ft.Container(height=PADDING_MEDIUM),
            ft.Text(
                "Produtos Adicionados",
                size=FONT_SIZE_BODY,
                weight=ft.FontWeight.W_600,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(
                content=added_products,
                height=200,
                border=ft.border.all(1, COLOR_BORDER),
                border_radius=BORDER_RADIUS_SMALL,
                padding=PADDING_SMALL,
            ),
            ft.Container(height=PADDING_MEDIUM),
            ft.Row([create_gradient_button("Registrar Venda", on_click=register_employee_sale, width=200)], alignment=ft.MainAxisAlignment.START),
        ], spacing=PADDING_MEDIUM)
    )
    
    return ft.Container(
        content=ft.Column([
            ft.Text(
                "Vendas para Funcionários",
                size=FONT_SIZE_H1,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(height=PADDING_LARGE),
            form_card,
            ft.Container(height=PADDING_LARGE),
            create_card(
                ft.Column([
                    ft.Text(
                        "Histórico de Vendas para Funcionários",
                        size=FONT_SIZE_H3,
                        weight=ft.FontWeight.W_600,
                        color=COLOR_TEXT_PRIMARY,
                        font_family=FONT_FAMILY
                    ),
                    ft.Row([
                        create_gradient_button("Atualizar Tabela", on_click=lambda e: load_sales_table(), width=180),
                    ]),
                    ft.Container(height=PADDING_MEDIUM),
                    ft.Container(
                        content=ft.Column([
                            employee_sales_table
                        ], scroll=ft.ScrollMode.AUTO),
                        height=520,
                        expand=True,
                        border_radius=BORDER_RADIUS_SMALL,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE
                    )
                ])
            )
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )

def reports_view(page: ft.Page):
    sales = get_sales()
    users = get_all_users()
    total_sales = sum([s["total_value"] for s in sales]) if sales else 0.0
    
    # Relatórios
    products_report = get_product_sales_report()
    payment_methods_report = get_payment_methods_report()
    installments_report = get_installments_report()
    
    def create_table_by_columns(data, columns):
        """Helper para criar tabelas de relatório"""
        rows = []
        for item in data:
            cells = []
            for col in columns:
                value = item[col["key"]]
                if isinstance(value, (int, float)):
                    if "money" in col.get("format", ""):
                        text = f"R$ {value:.2f}"
                    elif "percent" in col.get("format", ""):
                        text = f"{value:.1f}%"
                    else:
                        text = f"{value:,.0f}" if value.is_integer() else f"{value:.2f}"
                else:
                    text = str(value)
                cells.append(ft.DataCell(ft.Text(text, font_family=FONT_FAMILY)))
            rows.append(ft.DataRow(cells=cells))
        return rows



    # Tabela de Produtos Mais Vendidos
    products_columns = [
        {"key": "product_name", "title": "Produto"},
        {"key": "total_sales", "title": "Qtd. Vendas"},
        {"key": "total_quantity", "title": "Qtd. Total"},
        {"key": "total_value", "title": "Valor Total", "format": "money"},
        {"key": "avg_unit_price", "title": "Preço Médio", "format": "money"}
    ]
    products_table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(col["title"], font_family=FONT_FAMILY)) for col in products_columns],
        rows=create_table_by_columns(products_report, products_columns),
        border=ft.border.all(1, COLOR_BORDER),
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND
    )

    # Tabela de Métodos de Pagamento
    payment_columns = [
        {"key": "payment_method", "title": "Método"},
        {"key": "total_sales", "title": "Qtd. Vendas"},
        {"key": "total_value", "title": "Valor Total", "format": "money"}
    ]
    payment_table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(col["title"], font_family=FONT_FAMILY)) for col in payment_columns],
        rows=create_table_by_columns(payment_methods_report, payment_columns),
        border=ft.border.all(1, COLOR_BORDER),
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND
    )

    # Tabela de Parcelamentos
    installments_columns = [
        {"key": "num_installments", "title": "Parcelas"},
        {"key": "total_sales", "title": "Qtd. Vendas"},
        {"key": "total_value", "title": "Valor Total", "format": "money"},
        {"key": "avg_value", "title": "Valor Médio", "format": "money"}
    ]
    installments_table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(col["title"], font_family=FONT_FAMILY)) for col in installments_columns],
        rows=create_table_by_columns(installments_report, installments_columns),
        border=ft.border.all(1, COLOR_BORDER),
        border_radius=BORDER_RADIUS_SMALL,
        horizontal_margin=0,
        heading_row_color=COLOR_BACKGROUND
    )
    
    def export_report(report_data, columns, filename):
        output = io.StringIO()
        writer = csv.writer(output)
        # Cabeçalho
        writer.writerow([col["title"] for col in columns])
        # Dados
        for row in report_data:
            values = []
            for col in columns:
                value = row[col["key"]]
                if isinstance(value, (int, float)):
                    if "money" in col.get("format", ""):
                        values.append(f"{value:.2f}")
                    else:
                        values.append(str(value))
                else:
                    values.append(str(value))
            writer.writerow(values)
        
        csv_content = output.getvalue()
        with open(filename, "w", encoding="utf-8") as f:
            f.write(csv_content)

        page.snack_bar = ft.SnackBar(ft.Text(f"✓ Relatório salvo como '{filename}'"), bgcolor=COLOR_ERROR)
        page.snack_bar.open = True
        page.update()
    
    metrics_row = ft.Row([
        create_dashboard_card(
            "Total de Vendas", 
            f"R$ {total_sales:.2f}", 
            "Todas as vendas registradas",
            ft.Icons.BAR_CHART
        ),

        create_dashboard_card(
            "Total de Transações", 
            str(len(sales)), 
            "Vendas realizadas",
            ft.Icons.RECEIPT_LONG
        ),
    ], scroll=ft.ScrollMode.ADAPTIVE)



    # Relatório de Produtos
    products_card = create_card(
        ft.Column([
            ft.Row([
                ft.Text(
                    "Relatório de Vendas por Produto",
                    size=FONT_SIZE_H3,
                    weight=ft.FontWeight.W_600,
                    color=COLOR_TEXT_PRIMARY,
                    font_family=FONT_FAMILY
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    "Exportar CSV",
                    on_click=lambda e: export_report(
                        products_report,
                        products_columns,
                        "relatorio_vendas_produtos.csv"
                    ),
                    width=150
                ),
            ]),
            ft.Container(
                content=products_table,
                border_radius=BORDER_RADIUS_SMALL,
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )
        ])
    )

    # Relatório de Métodos de Pagamento
    payment_card = create_card(
        ft.Column([
            ft.Row([
                ft.Text(
                    "Relatório por Método de Pagamento",
                    size=FONT_SIZE_H3,
                    weight=ft.FontWeight.W_600,
                    color=COLOR_TEXT_PRIMARY,
                    font_family=FONT_FAMILY
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    "Exportar CSV",
                    on_click=lambda e: export_report(
                        payment_methods_report,
                        payment_columns,
                        "relatorio_metodos_pagamento.csv"
                    ),
                    width=150
                ),
            ]),
            ft.Container(
                content=payment_table,
                border_radius=BORDER_RADIUS_SMALL,
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )
        ])
    )

    # Relatório de Parcelamentos
    installments_card = create_card(
        ft.Column([
            ft.Row([
                ft.Text(
                    "Relatório de Vendas Parceladas",
                    size=FONT_SIZE_H3,
                    weight=ft.FontWeight.W_600,
                    color=COLOR_TEXT_PRIMARY,
                    font_family=FONT_FAMILY
                ),
                ft.Container(expand=True),
                create_gradient_button(
                    "Exportar CSV",
                    on_click=lambda e: export_report(
                        installments_report,
                        installments_columns,
                        "relatorio_parcelamentos.csv"
                    ),
                    width=150
                ),
            ]),
            ft.Container(
                content=installments_table,
                border_radius=BORDER_RADIUS_SMALL,
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )
        ])
    )

    return ft.Container(
        content=ft.Column([
            ft.Text(
                "Relatórios e Análises",
                size=FONT_SIZE_H1,
                weight=ft.FontWeight.W_700,
                color=COLOR_TEXT_PRIMARY,
                font_family=FONT_FAMILY
            ),
            ft.Container(height=PADDING_LARGE),
            metrics_row,
            ft.Container(height=PADDING_LARGE),
            products_card,
            ft.Container(height=PADDING_LARGE), 
            payment_card,
            ft.Container(height=PADDING_LARGE),
            installments_card,
        ], scroll=ft.ScrollMode.ADAPTIVE),
        padding=PADDING_XLARGE,
        expand=True
    )

# =========================
# MODERN SIDEBAR NAVIGATION
# =========================

def create_sidebar(page: ft.Page, current_route):
    def on_nav_change(e):
        routes = ["/home", "/funcionarios", "/produtos", "/vendas", "/relatorios"]
        if 0 <= e.control.selected_index < len(routes):
            page.go(routes[e.control.selected_index])
    
    current_routes = ["/home", "/funcionarios", "/produtos", "/vendas", "/relatorios"]
    current_index = current_routes.index(current_route) if current_route in current_routes else 0
    
    rail = ft.NavigationRail(
        selected_index=current_index,
        extended=True,
        min_extended_width=200,
        group_alignment=-0.9,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.HOME_OUTLINED,
                selected_icon=ft.Icons.HOME,
                label="Home"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.PEOPLE_OUTLINE,
                selected_icon=ft.Icons.PEOPLE,
                label="Funcionários"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                selected_icon=ft.Icons.INVENTORY_2,
                label="Produtos"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SHOPPING_CART_OUTLINED,
                selected_icon=ft.Icons.SHOPPING_CART,
                label="Vendas"
            ),
            # Employee-sales navigation removed
            ft.NavigationRailDestination(
                icon=ft.Icons.BAR_CHART_OUTLINED,
                selected_icon=ft.Icons.BAR_CHART,
                label="Relatórios"
            ),
        ],
        on_change=on_nav_change,
    )
    
    return rail

# =========================
# MODERN HEADER
# =========================

def create_header(page: ft.Page):
    return ft.AppBar(
        title=ft.Text(
            "Sistema de Vendas - TRP IMPORTS",
            size=20,
            weight=ft.FontWeight.W_600,
            color=COLOR_TEXT_PRIMARY,
            font_family=FONT_FAMILY
        ),
        bgcolor=COLOR_SURFACE,
        elevation=0,
        shadow_color=ft.Colors.TRANSPARENT,
        actions=[
            ft.Row([
                ft.Container(
                    content=ft.Column([
                        ft.Text(
                            state.logged_user['name'],
                            size=FONT_SIZE_BODY,
                            weight=ft.FontWeight.W_600,
                            color=COLOR_TEXT_PRIMARY,
                            font_family=FONT_FAMILY
                        ),
                        ft.Text(
                            "Administrador" if state.logged_user['role'] == 'admin' else "Funcionário",
                            size=FONT_SIZE_SMALL,
                            color=COLOR_TEXT_SECONDARY,
                            font_family=FONT_FAMILY
                        ),
                    ], spacing=0),
                    padding=ft.padding.symmetric(horizontal=PADDING_MEDIUM)
                ),
                ft.Container(
                    content=ft.Icon(ft.Icons.PERSON, color=COLOR_PRIMARY_START),
                    width=40,
                    height=40,
                    bgcolor=COLOR_BACKGROUND,
                    border_radius=20,
                    alignment=ft.alignment.center
                ),
                ft.IconButton(
                    icon=ft.Icons.LOGOUT,
                    icon_color=COLOR_TEXT_SECONDARY,
                    tooltip="Sair",
                    on_click=lambda e: (setattr(state, "logged_user", None), page.go("/"))
                ),
            ], spacing=PADDING_SMALL)
        ]
    )

# =========================
# Main app
# =========================
def main(page: ft.Page):
    page.title = "Sistema de Vendas - TRP IMPORTS"
    page.window.width = 1400
    page.window.height = 900
    page.window.min_width = 1200
    page.window.min_height = 800
    page.window.maximized = True  # Abrir maximizado
    page.window.center()
    
    page.icon = "assets/icon.ico"

    # Set page theme
    page.theme = ft.Theme(
        font_family=FONT_FAMILY,
    )
    # Force light theme to avoid dark background on some systems
    try:
        page.theme_mode = ft.ThemeMode.LIGHT
    except Exception:
        # Older flet versions may not support theme_mode; ignore if unavailable
        pass
    
    page.bgcolor = COLOR_BACKGROUND
    page.padding = 0

    init_db()

    def route_change(e):
        page.views.clear()
        route = page.route or "/"
        
        if not state.logged_user and route != "/":
            page.go("/")
            return
            
        if route == "/":
            page.views.append(
                ft.View("/", controls=[login_view(page)], padding=0)
            )
        else:
            # Create main app layout for authenticated users
            content_area = ft.Container(expand=True)
            
            if route == "/home":
                content_area.content = home_view(page)
            elif route == "/funcionarios":
                content_area.content = users_view(page) if state.logged_user["role"] == "admin" else ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("Acesso Restrito", size=FONT_SIZE_H1, weight=ft.FontWeight.W_700),
                            ft.Text("Apenas administradores podem acessar esta página.", size=FONT_SIZE_BODY),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER
                    ),
                    expand=True
                )
            # /vendas-funcionarios route removed (employee tab disabled)
            elif route == "/produtos":
                content_area.content = products_view(page) if state.logged_user["role"] == "admin" else ft.Container(
                    content=ft.Column([
                        ft.Text("Acesso Restrito", size=FONT_SIZE_H1, weight=ft.FontWeight.W_700),
                        ft.Text("Apenas administradores podem acessar esta página.", size=FONT_SIZE_BODY),
                    ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    expand=True,
                    alignment=ft.alignment.center
                )
            elif route == "/vendas":
                content_area.content = sales_view(page)

            elif route == "/relatorios":
                content_area.content = reports_view(page)
            
            main_layout = ft.Row([
                create_sidebar(page, route),
                ft.VerticalDivider(width=1, color=COLOR_BORDER),
                content_area,
            ], expand=True)
            
            page.views.append(
                ft.View(
                    route,
                    appbar=create_header(page),
                    controls=[main_layout],
                    padding=0
                )
            )
        
        page.update()

    page.on_route_change = route_change
    page.go("/")

if __name__ == "__main__":
    from sistemalojinha import init_db
    init_db()
    ft.app(target=main, assets_dir="assets")
