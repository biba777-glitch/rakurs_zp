from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
DB_PATH = "/tmp/database.db"
import hashlib
import calendar
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = 'kristina_secret_key_2026'

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Пользователи (без оклада — оклад в salaries)
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        full_name TEXT
    )''')
    
    # Рабочие часы
    c.execute('''CREATE TABLE IF NOT EXISTS work_hours (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        date TEXT,
        start_time TEXT,
        end_time TEXT,
        location TEXT,
        hours REAL
    )''')
    
    # Оклады по месяцам (история)
    c.execute('''CREATE TABLE IF NOT EXISTS salaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        month TEXT,
        oklad REAL DEFAULT 0,
        UNIQUE(user_id, month)
    )''')
    
    # Продажи и авансы
    c.execute('''CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        month TEXT,
        personal_sales REAL DEFAULT 0,
        advance REAL DEFAULT 0,
        advance_note TEXT DEFAULT '',
        deduction REAL DEFAULT 0,
        deduction_note TEXT DEFAULT ''
    )''')
    
    # Выплаты
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        month TEXT,
        amount REAL,
        pay_date TEXT,
        note TEXT DEFAULT ''
    )''')
    
    # Оборот компании
    c.execute('''CREATE TABLE IF NOT EXISTS company (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT UNIQUE,
        total_turnover REAL DEFAULT 0
    )''')
    
    # Правила для процента
    c.execute('''CREATE TABLE IF NOT EXISTS percent_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        min_sales REAL,
        max_sales REAL,
        percent REAL
    )''')
    
    # Админ по умолчанию
    admin_pass = hashlib.sha256('admin123'.encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (username, password, role, full_name) VALUES (?, ?, ?, ?)",
              ('admin', admin_pass, 'admin', 'Администратор'))
    
    # Тестовые сотрудники
    for name in ['Иванов Иван', 'Петров Пётр', 'Сидоров Сидор']:
        pass_hash = hashlib.sha256('123456'.encode()).hexdigest()
        c.execute("INSERT OR IGNORE INTO users (username, password, role, full_name) VALUES (?, ?, ?, ?)",
                  (name.split()[0].lower(), pass_hash, 'employee', name))
    
    # Правила по умолчанию
    c.execute("SELECT COUNT(*) FROM percent_rules")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO percent_rules (min_sales, max_sales, percent) VALUES (?, ?, ?)", (500000, 999999999, 5))
        c.execute("INSERT INTO percent_rules (min_sales, max_sales, percent) VALUES (?, ?, ?)", (300000, 499999, 2))
        c.execute("INSERT INTO percent_rules (min_sales, max_sales, percent) VALUES (?, ?, ?)", (0, 299999, 1))
    
    conn.commit()
    conn.close()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ----------
def get_norm_hours(month_str):
    year, month = map(int, month_str.split('-'))
    days_in_month = calendar.monthrange(year, month)[1]
    work_days = 0
    for day in range(1, days_in_month + 1):
        if date(year, month, day).weekday() < 5:
            work_days += 1
    return work_days * 8

def get_percent(sales):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT percent FROM percent_rules WHERE ? BETWEEN min_sales AND max_sales", (sales,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def get_oklad(user_id, month):
    """Берёт оклад за месяц. Если нет — 0."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT oklad FROM salaries WHERE user_id=? AND month=?", (user_id, month))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def auto_fill_days(user_id, month):
    """Автоматически заполняет рабочие дни (пн-пт) по 8 часов, если их нет"""
    year, mon = map(int, month.split('-'))
    days_in_month = calendar.monthrange(year, mon)[1]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    for day in range(1, days_in_month + 1):
        dt = date(year, mon, day)
        if dt.weekday() < 5:  # пн-пт
            date_str = f"{year}-{mon:02d}-{day:02d}"
            # Проверяем, есть ли уже запись
            c.execute("SELECT COUNT(*) FROM work_hours WHERE user_id=? AND date=?", (user_id, date_str))
            if c.fetchone()[0] == 0:
                c.execute("INSERT INTO work_hours (user_id, date, start_time, end_time, location, hours) VALUES (?, ?, ?, ?, ?, ?)",
                          (user_id, date_str, '09:00', '18:00', 'office', 8.0))
    
    conn.commit()
    conn.close()

def get_carry_over(user_id, month):
    """Считает перенос с прошлого месяца"""
    year, mon = map(int, month.split('-'))
    if mon == 1:
        prev_month = f"{year-1}-12"
    else:
        prev_month = f"{year}-{mon-1:02d}"
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    oklad = get_oklad(user_id, prev_month)
    if oklad == 0:
        conn.close()
        return 0
    
    norm = get_norm_hours(prev_month)
    
    c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='office' AND date LIKE ?",
              (user_id, prev_month + '%'))
    office_hours = c.fetchone()[0]
    
    c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='remote' AND date LIKE ?",
              (user_id, prev_month + '%'))
    remote_hours = c.fetchone()[0]
    
    c.execute("SELECT personal_sales, advance, deduction FROM sales WHERE user_id=? AND month=?", (user_id, prev_month))
    srow = c.fetchone()
    personal_sales = srow[0] if srow else 0
    advance = srow[1] if srow else 0
    deduction = srow[2] if srow else 0
    
    c.execute("SELECT total_turnover FROM company WHERE month=?", (prev_month,))
    crow = c.fetchone()
    turnover = crow[0] if crow else 0
    
    rate = oklad / norm if norm > 0 else 0
    office_pay = rate * office_hours
    remote_pay = rate * remote_hours * 0.7
    percent = get_percent(personal_sales)
    percent_pay = personal_sales * percent / 100
    factual = office_pay + remote_pay + percent_pay
    premium = factual * 0.1 if turnover > 3000000 else 0
    nachisleno = factual + premium
    
    c.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id=? AND month=?", (user_id, prev_month))
    received = c.fetchone()[0]
    
    ostatok = nachisleno - received - advance - deduction
    
    conn.close()
    return round(ostatok, 2)

# ---------- МАРШРУТЫ ----------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('employee'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = hashlib.sha256(request.form['password'].encode()).hexdigest()
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT id, role, full_name FROM users WHERE username=? AND password=?", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['role'] = user[1]
            session['full_name'] = user[2]
            return redirect(url_for('index'))
        flash('Неверный логин или пароль')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------- СОТРУДНИК ----------
@app.route('/employee', methods=['GET', 'POST'])
def employee():
    if session.get('role') != 'employee':
        return redirect(url_for('login'))
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    user_id = session['user_id']
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    
    # Автозаполнение дней
    auto_fill_days(user_id, month)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_day':
            work_date = request.form['work_date']
            location = request.form['location']
            c.execute("INSERT INTO work_hours (user_id, date, start_time, end_time, location, hours) VALUES (?, ?, ?, ?, ?, ?)",
                      (user_id, work_date, '09:00', '18:00', location, 8.0))
            conn.commit()
            conn.close()
            return redirect(url_for('employee', month=month))
        
        elif action == 'edit_day':
            record_id = int(request.form['record_id'])
            location = request.form['location']
            start_time = request.form.get('start_time', '09:00')
            end_time = request.form.get('end_time', '18:00')
            
            # Считаем часы (минус 1 час обеда)
            start_dt = datetime.strptime(start_time, '%H:%M')
            end_dt = datetime.strptime(end_time, '%H:%M')
            hours = (end_dt - start_dt).seconds / 3600
            if hours < 0:
                hours += 24
            if hours >= 9:
                hours = 8
            else:
                hours = max(0, hours - 1)
            
            c.execute("UPDATE work_hours SET location=?, start_time=?, end_time=?, hours=? WHERE id=? AND user_id=?",
                      (location, start_time, end_time, round(hours, 2), record_id, user_id))
            conn.commit()
            conn.close()
            return redirect(url_for('employee', month=month))
    
    norm_hours = get_norm_hours(month)
    
    c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='office' AND date LIKE ?",
              (user_id, month + '%'))
    office_hours = c.fetchone()[0]
    
    c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='remote' AND date LIKE ?",
              (user_id, month + '%'))
    remote_hours = c.fetchone()[0]
    
    c.execute("SELECT personal_sales, advance, advance_note, deduction, deduction_note FROM sales WHERE user_id=? AND month=?", (user_id, month))
    srow = c.fetchone()
    if srow:
        personal_sales, advance, advance_note, deduction, deduction_note = srow
    else:
        personal_sales, advance, advance_note, deduction, deduction_note = 0, 0, '', 0, ''
    
    oklad = get_oklad(user_id, month)
    
    c.execute("SELECT total_turnover FROM company WHERE month=?", (month,))
    row = c.fetchone()
    turnover = row[0] if row else 0
    
    rate = oklad / norm_hours if norm_hours > 0 else 0
    office_pay = rate * office_hours
    remote_pay = rate * remote_hours * 0.7
    percent = get_percent(personal_sales)
    percent_pay = personal_sales * percent / 100
    factual = office_pay + remote_pay + percent_pay
    premium = factual * 0.1 if turnover > 3000000 else 0
    nachisleno = round(factual + premium, 2)
    
    c.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id=? AND month=?", (user_id, month))
    received = c.fetchone()[0]
    received_total = received + advance
    
    carry = get_carry_over(user_id, month)
    ostatok = round(nachisleno - received_total + carry, 2)
    
    c.execute("SELECT id, date, start_time, end_time, location, hours FROM work_hours WHERE user_id=? AND date LIKE ? ORDER BY date DESC",
              (user_id, month + '%'))
    records = c.fetchall()
    
    c.execute("SELECT amount, pay_date, note FROM payments WHERE user_id=? AND month=? ORDER BY pay_date DESC", (user_id, month))
    payments_list = c.fetchall()
    
    conn.close()
    return render_template('employee.html', 
                           office_hours=office_hours, remote_hours=remote_hours,
                           rate=round(rate, 2), office_pay=round(office_pay, 2),
                           remote_pay=round(remote_pay, 2),
                           personal_sales=personal_sales, percent=percent,
                           percent_pay=round(percent_pay, 2),
                           factual=round(factual, 2), premium=round(premium, 2),
                           nachisleno=nachisleno,
                           advance=advance, advance_note=advance_note,
                           deduction=deduction, deduction_note=deduction_note,
                           received=received_total, carry=carry, ostatok=ostatok,
                           month=month, records=records, norm_hours=norm_hours,
                           turnover=turnover, payments_list=payments_list,
                           oklad=oklad)

# ---------- АДМИН ----------
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    
    # Автозаполнение для всех сотрудников
    c.execute("SELECT id FROM users WHERE role='employee'")
    for (emp_id,) in c.fetchall():
        auto_fill_days(emp_id, month)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'set_turnover':
            turnover = float(request.form['turnover'])
            c.execute("INSERT OR REPLACE INTO company (month, total_turnover) VALUES (?, ?)", (month, turnover))
        
        elif action == 'set_oklad':
            user_id = int(request.form['user_id'])
            oklad = float(request.form['oklad'])
            c.execute("INSERT OR REPLACE INTO salaries (user_id, month, oklad) VALUES (?, ?, ?)", (user_id, month, oklad))
        
        elif action == 'set_sales':
            user_id = int(request.form['user_id'])
            sales = float(request.form['personal_sales'])
            advance = float(request.form['advance'])
            advance_note = request.form.get('advance_note', '')
            deduction = float(request.form['deduction'])
            deduction_note = request.form.get('deduction_note', '')
            c.execute("INSERT OR REPLACE INTO sales (user_id, month, personal_sales, advance, advance_note, deduction, deduction_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (user_id, month, sales, advance, advance_note, deduction, deduction_note))
        
        elif action == 'add_payment':
            user_id = int(request.form['user_id'])
            amount = float(request.form['amount'])
            pay_date = request.form['pay_date']
            note = request.form.get('note', '')
            c.execute("INSERT INTO payments (user_id, month, amount, pay_date, note) VALUES (?, ?, ?, ?, ?)",
                      (user_id, month, amount, pay_date, note))
        
        elif action == 'delete_payment':
            payment_id = int(request.form['payment_id'])
            c.execute("DELETE FROM payments WHERE id=?", (payment_id,))
        
        elif action == 'add_hours':
            user_id = int(request.form['user_id'])
            work_date = request.form['work_date']
            location = request.form['location']
            c.execute("INSERT INTO work_hours (user_id, date, start_time, end_time, location, hours) VALUES (?, ?, ?, ?, ?, ?)",
                      (user_id, work_date, '09:00', '18:00', location, 8.0))
        
        elif action == 'delete_hours':
            record_id = int(request.form['record_id'])
            c.execute("DELETE FROM work_hours WHERE id=?", (record_id,))
        
        elif action == 'add_rule':
            min_s = float(request.form['min_sales'])
            max_s = float(request.form['max_sales'])
            percent = float(request.form['percent'])
            c.execute("INSERT INTO percent_rules (min_sales, max_sales, percent) VALUES (?, ?, ?)", (min_s, max_s, percent))
        
        elif action == 'delete_rule':
            rule_id = int(request.form['rule_id'])
            c.execute("DELETE FROM percent_rules WHERE id=?", (rule_id,))
        
        conn.commit()
        conn.close()
        return redirect(url_for('admin', month=month))
    
    c.execute("SELECT id, full_name FROM users WHERE role='employee'")
    employees = c.fetchall()
    
    c.execute("SELECT total_turnover FROM company WHERE month=?", (month,))
    row = c.fetchone()
    turnover = row[0] if row else 0
    
    c.execute("SELECT id, min_sales, max_sales, percent FROM percent_rules ORDER BY min_sales DESC")
    rules = c.fetchall()
    
    norm_hours = get_norm_hours(month)
    
    results = []
    for emp in employees:
        emp_id, name = emp
        
        oklad = get_oklad(emp_id, month)
        
        c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='office' AND date LIKE ?",
                  (emp_id, month + '%'))
        office_hours = c.fetchone()[0]
        
        c.execute("SELECT COALESCE(SUM(hours), 0) FROM work_hours WHERE user_id=? AND location='remote' AND date LIKE ?",
                  (emp_id, month + '%'))
        remote_hours = c.fetchone()[0]
        
        c.execute("SELECT personal_sales, advance, advance_note, deduction, deduction_note FROM sales WHERE user_id=? AND month=?", (emp_id, month))
        srow = c.fetchone()
        if srow:
            personal_sales, advance, advance_note, deduction, deduction_note = srow
        else:
            personal_sales, advance, advance_note, deduction, deduction_note = 0, 0, '', 0, ''
        
        rate = oklad / norm_hours if norm_hours > 0 else 0
        office_pay = rate * office_hours
        remote_pay = rate * remote_hours * 0.7
        percent = get_percent(personal_sales)
        percent_pay = personal_sales * percent / 100
        factual = office_pay + remote_pay + percent_pay
        premium = factual * 0.1 if turnover > 3000000 else 0
        nachisleno = round(factual + premium, 2)
        
        c.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id=? AND month=?", (emp_id, month))
        received = c.fetchone()[0]
        received_total = received + advance
        
        carry = get_carry_over(emp_id, month)
        ostatok = round(nachisleno - received_total + carry, 2)
        
        # Записи часов
        c.execute("SELECT id, date, start_time, end_time, location, hours FROM work_hours WHERE user_id=? AND date LIKE ? ORDER BY date DESC",
                  (emp_id, month + '%'))
        hours_records = c.fetchall()
        
        results.append({
            'id': emp_id, 'name': name, 'oklad': oklad,
            'office_hours': office_hours, 'remote_hours': remote_hours,
            'rate': round(rate, 2), 'office_pay': round(office_pay, 2),
            'remote_pay': round(remote_pay, 2),
            'personal_sales': personal_sales, 'percent': percent,
            'percent_pay': round(percent_pay, 2),
            'factual': round(factual, 2), 'premium': round(premium, 2),
            'nachisleno': nachisleno,
            'advance': advance, 'advance_note': advance_note,
            'deduction': deduction, 'deduction_note': deduction_note,
            'received': received_total, 'carry': carry,
            'ostatok': ostatok,
            'hours_records': hours_records
        })
    
    c.execute("SELECT p.id, u.full_name, p.amount, p.pay_date, p.note FROM payments p JOIN users u ON p.user_id=u.id WHERE p.month=? ORDER BY p.pay_date DESC", (month,))
    payments_list = c.fetchall()
    
    conn.close()
    return render_template('admin.html', employees=employees, results=results,
                           month=month, turnover=turnover, rules=rules, norm_hours=norm_hours,
                           payments_list=payments_list)

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
