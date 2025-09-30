# app.py
from flask import Flask, g, render_template_string, request, redirect, url_for, session, flash, jsonify
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "oms.db")

app = Flask(__name__)
app.secret_key = "replace_this_with_a_random_secret_in_production"

# ---------------- Database helpers ----------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    cur = db.cursor()
    # Users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        shop_name TEXT NOT NULL
    )""")
    # Inventory: key by shop and item_name+unit
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_name TEXT NOT NULL,
        oil_type TEXT NOT NULL,
        unit TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 0,
        unit_price REAL NOT NULL DEFAULT 0,
        UNIQUE(shop_name, oil_type, unit)
    )""")
    # Purchases
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_name TEXT NOT NULL,
        oil_type TEXT,
        unit TEXT,
        quantity REAL,
        price_per_unit REAL,
        total REAL,
        date TEXT
    )""")
    # Sales
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_name TEXT NOT NULL,
        oil_type TEXT,
        unit TEXT,
        quantity REAL,
        price_per_unit REAL,
        total REAL,
        date TEXT
    )""")
    # Purchase orders (simple)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_name TEXT NOT NULL,
        oil_type TEXT,
        unit TEXT,
        quantity REAL,
        status TEXT DEFAULT 'Pending',
        date TEXT
    )""")
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

# ----------------- Auth -----------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    r = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return r

# ----------------- Templates (single-file style) -----------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Inventory360 - Oil Management</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Tailwind CDN (for quick demo) -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">
  <div class="flex min-h-screen">
    <aside class="w-64 bg-slate-800 p-6">
      <h1 class="text-2xl font-bold mb-6">INVENTORY360</h1>
      <nav class="space-y-2 text-slate-200">
        <a href="{{ url_for('dashboard') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='dashboard' else '' }}">Dashboard</a>
        <a href="{{ url_for('sell') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='sell' else '' }}">Sell</a>
        <a href="{{ url_for('purchase') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='purchase' else '' }}">Add Purchase</a>
        <a href="{{ url_for('inventory') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='inventory' else '' }}">Inventory</a>
        <a href="{{ url_for('sales_history') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='sales' else '' }}">Sales History</a>
        <a href="{{ url_for('purchase_orders') }}" class="block px-3 py-2 rounded hover:bg-slate-700 {{ 'bg-slate-700' if active=='po' else '' }}">Purchase Orders</a>
        <a href="{{ url_for('logout') }}" class="block px-3 py-2 rounded hover:bg-slate-700 mt-4">Logout</a>
      </nav>
      <div class="mt-6 text-sm text-slate-400">
        Signed in as <strong>{{ user['username'] if user else '' }}</strong><br/>
        Shop: <strong>{{ user['shop_name'] if user else '' }}</strong>
      </div>
    </aside>
    <main class="flex-1 p-8">
      <div class="container mx-auto">
        <!-- SEARCH BAR -->
        <div class="mb-4">
          <form action="{{ url_for('search') }}" method="get" class="flex max-w-xl">
            <input name="q" value="{{ request.args.get('q','') }}" placeholder="Search inventory, sales, purchases..." class="flex-1 p-2 rounded-l bg-slate-700 text-white" />
            <button class="bg-emerald-500 px-4 rounded-r">Search</button>
          </form>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="mb-4">
              {% for m in messages %}
                <div class="bg-rose-600 text-white px-4 py-2 rounded mb-2">{{ m }}</div>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        {{ body | safe }}
      </div>
    </main>
  </div>
</body>
</html>
"""

# ---- Home / Dashboard ----
@app.route("/")
def index():
    if not current_user():
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    # totals
    total_sales = db.execute("SELECT IFNULL(SUM(total),0) as s FROM sales WHERE shop_name = ?", (shop,)).fetchone()["s"]
    # daily last 30 days sales breakdown
    rows = db.execute("""
      SELECT date(date) as day, IFNULL(SUM(total),0) as total 
      FROM sales 
      WHERE shop_name = ? AND date >= date('now','-29 days')
      GROUP BY day ORDER BY day
    """, (shop,)).fetchall()
    labels = [r["day"] for r in rows]
    data_points = [r["total"] for r in rows]
    # average sale value
    avg_sale = db.execute("SELECT IFNULL(AVG(total),0) as a FROM sales WHERE shop_name = ?", (shop,)).fetchone()["a"]
    # items per sale (average quantity)
    avg_items = db.execute("SELECT IFNULL(AVG(quantity),0) as ai FROM sales WHERE shop_name = ?", (shop,)).fetchone()["ai"]
    # low stock count
    low_stock = db.execute("SELECT COUNT(*) as c FROM inventory WHERE shop_name = ? AND quantity <= 5", (shop,)).fetchone()["c"]
    body = render_template_string("""
      <div class="grid grid-cols-3 gap-6">
        <div class="col-span-2 bg-slate-800 p-6 rounded">
          <h2 class="text-xl font-bold mb-2">Hi, here's what's happening in your store</h2>
          <div class="flex gap-4">
            <div class="p-4 bg-slate-700 rounded flex-1">
               <div class="text-sm text-slate-300">This month your store has sold</div>
               <div class="text-2xl font-bold">Rs. {{ total_sales }}</div>
               <div class="text-sm text-slate-400 mt-2">Overview last 30 days</div>
            </div>
            <div class="p-4 bg-slate-700 rounded w-48">
               <div class="text-sm text-slate-300">Average Sale Value</div>
               <div class="text-xl font-bold">Rs. {{ "%.2f"|format(avg_sale) }}</div>
               <div class="text-sm text-slate-400 mt-2">Avg items/sale: {{ "%.2f"|format(avg_items) }}</div>
            </div>
            <div class="p-4 bg-slate-700 rounded w-48">
               <div class="text-sm text-slate-300">Low stock items</div>
               <div class="text-2xl font-bold">{{ low_stock }}</div>
               <div class="text-sm text-slate-400 mt-2">Qty <= 5</div>
            </div>
          </div>
          <div class="mt-6">
            <canvas id="salesChart" height="120"></canvas>
          </div>
        </div>
        <div class="bg-slate-800 p-6 rounded">
          <h3 class="font-bold mb-4">Quick Actions</h3>
          <a href="{{ url_for('sell') }}" class="block bg-emerald-600 text-black px-4 py-2 rounded mb-2 text-center">Create Sale</a>
          <a href="{{ url_for('purchase') }}" class="block bg-amber-500 text-black px-4 py-2 rounded mb-2 text-center">Add Purchase</a>
          <a href="{{ url_for('inventory') }}" class="block bg-slate-700 px-4 py-2 rounded mb-2 text-center">View Inventory</a>
          <a href="{{ url_for('purchase_orders') }}" class="block bg-slate-700 px-4 py-2 rounded mb-2 text-center">Purchase Orders</a>
        </div>
      </div>

      <script>
        const ctx = document.getElementById('salesChart').getContext('2d');
        const labels = {{ labels|tojson }};
        const data = {{ data_points|tojson }};
        new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'Sales (Rs.)',
              data: data,
              tension: 0.3,
              borderColor: 'rgba(132, 204, 22, 1)',
              backgroundColor: 'rgba(132, 204, 22, 0.1)',
              fill: true,
            }]
          },
          options: {
            scales: {
              x: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } },
              y: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }
            },
            plugins: { legend: { labels: { color: '#e2e8f0' } } }
          }
        });
      </script>
    """, total_sales=total_sales, labels=labels, data_points=data_points, avg_sale=avg_sale, avg_items=avg_items, low_stock=low_stock)
    return render_template_string(BASE_HTML, body=body, user=user, active="dashboard")

# ---- Register & Login ----
AUTH_HTML = """
{% if mode=='login' %}
  <div class="max-w-md mx-auto bg-slate-800 p-8 rounded">
    <h2 class="text-2xl font-bold mb-4">Login</h2>
    <form method="post">
      <label class="block text-sm">Username</label>
      <input name="username" class="w-full p-2 rounded bg-slate-700 mb-3" />
      <label class="block text-sm">Password</label>
      <input name="password" type="password" class="w-full p-2 rounded bg-slate-700 mb-3" />
      <div class="flex gap-2">
        <button class="bg-emerald-500 px-4 py-2 rounded">Login</button>
        <a href="{{ url_for('register') }}" class="bg-slate-600 px-4 py-2 rounded">Register</a>
      </div>
    </form>
  </div>
{% else %}
  <div class="max-w-md mx-auto bg-slate-800 p-8 rounded">
    <h2 class="text-2xl font-bold mb-4">Register</h2>
    <form method="post">
      <label class="block text-sm">Username</label>
      <input name="username" class="w-full p-2 rounded bg-slate-700 mb-3" />
      <label class="block text-sm">Password</label>
      <input name="password" type="password" class="w-full p-2 rounded bg-slate-700 mb-3" />
      <label class="block text-sm">Shop name</label>
      <input name="shop_name" class="w-full p-2 rounded bg-slate-700 mb-3" />
      <div class="flex gap-2">
        <button class="bg-emerald-500 px-4 py-2 rounded">Register</button>
        <a href="{{ url_for('login') }}" class="bg-slate-600 px-4 py-2 rounded">Back to login</a>
      </div>
    </form>
  </div>
{% endif %}
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pwd = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (uname,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], pwd):
            flash("Invalid credentials")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))
    return render_template_string(BASE_HTML, body=render_template_string(AUTH_HTML, mode='login'), user=current_user(), active="")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pwd = request.form.get("password", "")
        shop = request.form.get("shop_name", "").strip() or "DefaultShop"
        if not uname or not pwd:
            flash("Fill all fields")
            return redirect(url_for("register"))
        db = get_db()
        try:
            db.execute("INSERT INTO users (username, password_hash, shop_name) VALUES (?, ?, ?)",
                       (uname, generate_password_hash(pwd), shop))
            db.commit()
        except sqlite3.IntegrityError:
            flash("Username already exists")
            return redirect(url_for("register"))
        flash("Registered. Please login.")
        return redirect(url_for("login"))
    return render_template_string(BASE_HTML, body=render_template_string(AUTH_HTML, mode='register'), user=current_user(), active="")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---- Inventory view & manage ----
@app.route("/inventory")
def inventory():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    rows = db.execute("SELECT * FROM inventory WHERE shop_name = ?", (shop,)).fetchall()
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl font-bold mb-4">Inventory - {{ shop }}</h2>
        <table class="w-full text-left">
          <thead><tr class="text-slate-300"><th>Oil Type</th><th>Unit</th><th>Quantity</th><th>Unit Price</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr class="border-t border-slate-700"><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>Rs. {{ '%.2f'|format(r['unit_price']) }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
        <div class="mt-4 flex gap-2">
          <a href="{{ url_for('add_inventory') }}" class="bg-amber-500 text-black px-3 py-2 rounded">Add / Update Stock</a>
          <a href="{{ url_for('purchase') }}" class="bg-emerald-500 text-black px-3 py-2 rounded">Record Purchase</a>
        </div>
      </div>
    """, shop=shop, rows=rows)
    return render_template_string(BASE_HTML, body=body, user=user, active="inventory")

@app.route("/inventory/add", methods=["GET", "POST"])
def add_inventory():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if request.method == "POST":
        oil = request.form.get("oil_type","").strip()
        unit = request.form.get("unit","Liters")
        try:
            qty = float(request.form.get("quantity","0") or 0)
        except:
            qty = 0.0
        try:
            price = float(request.form.get("price","0") or 0)
        except:
            price = 0.0
        shop = user["shop_name"]
        db = get_db()
        cur = db.cursor()
        # upsert
        existing = db.execute("SELECT * FROM inventory WHERE shop_name=? AND oil_type=? AND unit=?", (shop, oil, unit)).fetchone()
        if existing:
            new_qty = existing["quantity"] + qty
            db.execute("UPDATE inventory SET quantity=?, unit_price=? WHERE id=?", (new_qty, price, existing["id"]))
        else:
            db.execute("INSERT INTO inventory (shop_name, oil_type, unit, quantity, unit_price) VALUES (?,?,?,?,?)",
                       (shop, oil, unit, qty, price))
        db.commit()
        flash("Stock updated")
        return redirect(url_for("inventory"))
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded max-w-md">
        <h2 class="text-xl mb-4 font-bold">Add / Update Inventory</h2>
        <form method="post">
          <label class="block text-sm">Oil Type</label><input name="oil_type" class="w-full p-2 rounded bg-slate-700 mb-2" required>
          <label class="block text-sm">Unit</label><input name="unit" class="w-full p-2 rounded bg-slate-700 mb-2" value="Liters">
          <label class="block text-sm">Quantity</label><input name="quantity" class="w-full p-2 rounded bg-slate-700 mb-2" required>
          <label class="block text-sm">Unit Price (Rs.)</label><input name="price" class="w-full p-2 rounded bg-slate-700 mb-4" required>
          <button class="bg-emerald-500 px-4 py-2 rounded">Save</button>
        </form>
      </div>
    """)
    return render_template_string(BASE_HTML, body=body, user=user, active="inventory")

# ---- Purchase recording ----
@app.route("/purchase", methods=["GET","POST"])
def purchase():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    if request.method == "POST":
        oil = request.form.get("oil_type","").strip()
        unit = request.form.get("unit","Liters")
        try:
            qty = float(request.form.get("quantity","0") or 0)
        except:
            qty = 0.0
        try:
            price = float(request.form.get("price","0") or 0)
        except:
            price = 0.0
        total = qty * price
        db = get_db()
        db.execute("INSERT INTO purchases (shop_name, oil_type, unit, quantity, price_per_unit, total, date) VALUES (?,?,?,?,?,?,?)",
                   (shop, oil, unit, qty, price, total, datetime.now().isoformat()))
        # update inventory automatically
        ex = db.execute("SELECT * FROM inventory WHERE shop_name=? AND oil_type=? AND unit=?", (shop, oil, unit)).fetchone()
        if ex:
            new_qty = ex["quantity"] + qty
            db.execute("UPDATE inventory SET quantity=?, unit_price=? WHERE id=?", (new_qty, price, ex["id"]))
        else:
            db.execute("INSERT INTO inventory (shop_name, oil_type, unit, quantity, unit_price) VALUES (?,?,?,?,?)",
                       (shop, oil, unit, qty, price))
        db.commit()
        flash("Purchase recorded and inventory updated")
        return redirect(url_for("dashboard"))
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded max-w-md">
        <h2 class="text-xl mb-4 font-bold">Record Purchase</h2>
        <form method="post">
          <label class="block text-sm">Product</label><input name="oil_type" class="w-full p-2 rounded bg-slate-700 mb-2" required>
         <label class="block text-sm">Unit</label>
<select name="unit" class="w-full p-2 rounded bg-slate-700 mb-2 text-white">
  <option value="Liters" selected>Liters</option>
  <option value="Milliliters">Milliliters (ml)</option>
  <option value="Kilograms">Kilograms (kg)</option>
  <option value="Grams">Grams (g)</option>
  <option value="Milligrams">Milligrams (mg)</option>
  <option value="Packet">Packet</option>
  <option value="Box">Box</option>
  <option value="Dozen">Dozen</option>
  <option value="Piece">Piece</option>
  <option value="Roll">Roll</option>
  <option value="Meter">Meter</option>
  <option value="Centimeter">Centimeter</option>
  <option value="Inch">Inch</option>
  <option value="Ton">Ton</option>
  <option value="Quintal">Quintal</option>
</select>

          <label class="block text-sm">Quantity</label><input name="quantity" class="w-full p-2 rounded bg-slate-700 mb-2" required>
          <label class="block text-sm">Price per Unit (Rs.)</label><input name="price" class="w-full p-2 rounded bg-slate-700 mb-4" required>
          <button class="bg-emerald-500 px-4 py-2 rounded">Save Purchase</button>
        </form>
      </div>
    """)
    return render_template_string(BASE_HTML, body=body, user=user, active="purchase")

# ---- Sell (updated to allow multiple products and auto price) ----
@app.route("/sell", methods=["GET","POST"])
def sell():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    items = db.execute("SELECT * FROM inventory WHERE shop_name = ? ORDER BY oil_type", (shop,)).fetchall()
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        # Collect sells to perform atomically
        sells = []
        for it in items:
            iid = it["id"]
            raw_qty = request.form.get(f"qty_{iid}", "").strip()
            if not raw_qty:
                continue
            try:
                qty = float(raw_qty)
            except:
                qty = 0
            if qty <= 0:
                continue
            # get current inventory row again to ensure fresh data
            row = db.execute("SELECT * FROM inventory WHERE id = ?", (iid,)).fetchone()
            if not row:
                flash(f"Item not found: {it['oil_type']}")
                return redirect(url_for("sell"))
            if qty > row["quantity"]:
                flash(f"Not enough stock for {row['oil_type']} ({row['unit']}). Available: {row['quantity']}")
                return redirect(url_for("sell"))
            price_per_unit = row["unit_price"] or 0.0
            total = qty * price_per_unit
            sells.append({
                "inventory_id": row["id"],
                "oil_type": row["oil_type"],
                "unit": row["unit"],
                "qty": qty,
                "price_per_unit": price_per_unit,
                "total": total
            })
        if not sells:
            flash("No items selected for sale")
            return redirect(url_for("sell"))
        # perform updates
        try:
            for s in sells:
                # reduce inventory
                ex = db.execute("SELECT * FROM inventory WHERE id = ?", (s["inventory_id"],)).fetchone()
                new_qty = ex["quantity"] - s["qty"]
                if new_qty <= 0:
                    db.execute("DELETE FROM inventory WHERE id = ?", (s["inventory_id"],))
                else:
                    db.execute("UPDATE inventory SET quantity=? WHERE id=?", (new_qty, s["inventory_id"]))
                # insert a row in sales table
                db.execute("INSERT INTO sales (shop_name, oil_type, unit, quantity, price_per_unit, total, date) VALUES (?,?,?,?,?,?,?)",
                           (shop, s["oil_type"], s["unit"], s["qty"], s["price_per_unit"], s["total"], datetime.now().isoformat()))
            db.commit()
        except Exception as e:
            db.rollback()
            flash("Error recording sale: " + str(e))
            return redirect(url_for("sell"))
        flash("Sale recorded")
        return redirect(url_for("dashboard"))
    # GET: render multi-item sell form
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl mb-4 font-bold">Sell Multiple Products</h2>
        {% if not items %}
          <div>No items in inventory. <a href="{{ url_for('add_inventory') }}" class="text-amber-400">Add stock</a></div>
        {% else %}
          <form method="post" id="sellForm">
            <table class="w-full text-left mb-4">
              <thead><tr class="text-slate-300"><th>Item</th><th>Unit</th><th>Available</th><th>Unit Price (Rs.)</th><th>Quantity to sell</th><th>Total</th></tr></thead>
              <tbody>
                {% for it in items %}
                  <tr class="border-t border-slate-700">
                    <td>{{ it['oil_type'] }}</td>
                    <td>{{ it['unit'] }}</td>
                    <td id="avail_{{ it['id'] }}">{{ '%.2f'|format(it['quantity']) }}</td>
                    <td id="price_{{ it['id'] }}">{{ '%.2f'|format(it['unit_price']) }}</td>
                    <td>
                      <input name="qty_{{ it['id'] }}" id="qty_{{ it['id'] }}" data-id="{{ it['id'] }}" data-price="{{ '%.2f'|format(it['unit_price']) }}" class="w-28 p-1 rounded bg-slate-700" />
                    </td>
                    <td>Rs. <span id="line_total_{{ it['id'] }}">0.00</span></td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
            <div class="flex justify-between items-center">
              <div class="text-lg">Grand Total: Rs. <span id="grand_total">0.00</span></div>
              <div>
                <button type="submit" class="bg-emerald-500 px-4 py-2 rounded">Record Sale</button>
                <a href="{{ url_for('dashboard') }}" class="ml-2 bg-slate-600 px-4 py-2 rounded">Cancel</a>
              </div>
            </div>
          </form>
        {% endif %}
      </div>

      <script>
        function toFloat(v){
          v = v || '';
          v = v.toString().replace(',', '.');
          var f = parseFloat(v);
          if (isNaN(f)) return 0;
          return f;
        }
        function updateTotals(){
          let grand = 0;
          const inputs = document.querySelectorAll('input[name^="qty_"]');
          inputs.forEach(inp=>{
            const id = inp.dataset.id;
            const price = toFloat(inp.dataset.price);
            const qty = toFloat(inp.value);
            const line = price * qty;
            document.getElementById('line_total_' + id).innerText = line.toFixed(2);
            grand += line;
          });
          document.getElementById('grand_total').innerText = grand.toFixed(2);
        }
        document.querySelectorAll('input[name^="qty_"]').forEach(i=>{
          i.addEventListener('input', updateTotals);
        });
        // initial run
        updateTotals();

        // Optional: prevent submitting if any qty > available
        document.getElementById('sellForm')?.addEventListener('submit', function(e){
          const inputs = document.querySelectorAll('input[name^="qty_"]');
          for (let inp of inputs){
            const id = inp.dataset.id;
            const qty = toFloat(inp.value);
            if (qty <= 0) continue;
            const avail = toFloat(document.getElementById('avail_' + id).innerText);
            if (qty > avail){
              e.preventDefault();
              alert('Not enough stock for item id ' + id + '. Available: ' + avail);
              return false;
            }
          }
        });
      </script>
    """, items=items)
    return render_template_string(BASE_HTML, body=body, user=user, active="sell")

# ---- Sales/Purchases history ----
@app.route("/sales")
def sales_history():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    rows = db.execute("SELECT * FROM sales WHERE shop_name = ? ORDER BY date DESC LIMIT 200", (shop,)).fetchall()
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl font-bold mb-4">Sales History</h2>
        <table class="w-full text-left">
          <thead><tr class="text-slate-300"><th>Date</th><th>Type</th><th>Unit</th><th>Qty</th><th>Price/Unit</th><th>Total</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr class="border-t border-slate-700"><td>{{ r['date'] }}</td><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ '%.2f'|format(r['price_per_unit']) }}</td><td>{{ '%.2f'|format(r['total']) }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    """, rows=rows)
    return render_template_string(BASE_HTML, body=body, user=user, active="sales")

@app.route("/purchases")
def purchase_history():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    rows = db.execute("SELECT * FROM purchases WHERE shop_name = ? ORDER BY date DESC LIMIT 200", (shop,)).fetchall()
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl font-bold mb-4">Purchase History</h2>
        <table class="w-full text-left">
          <thead><tr class="text-slate-300"><th>Date</th><th>Type</th><th>Unit</th><th>Qty</th><th>Price/Unit</th><th>Total</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr class="border-t border-slate-700"><td>{{ r['date'] }}</td><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ '%.2f'|format(r['price_per_unit']) }}</td><td>{{ '%.2f'|format(r['total']) }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    """, rows=rows)
    return render_template_string(BASE_HTML, body=body, user=user, active="")

# ---- Purchase Orders ----
@app.route("/po", methods=["GET","POST"])
def purchase_orders():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    shop = user["shop_name"]
    db = get_db()
    if request.method == "POST":
        oil = request.form.get("oil_type")
        unit = request.form.get("unit") or "Liters"
        try:
            qty = float(request.form.get("quantity") or 0)
        except:
            qty = 0.0
        db.execute("INSERT INTO purchase_orders (shop_name, oil_type, unit, quantity, status, date) VALUES (?,?,?,?,?,?)",
                   (shop, oil, unit, qty, "Pending", datetime.now().isoformat()))
        db.commit()
        flash("Purchase order created")
        return redirect(url_for("purchase_orders"))
    rows = db.execute("SELECT * FROM purchase_orders WHERE shop_name = ? ORDER BY date DESC", (shop,)).fetchall()
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl font-bold mb-4">Purchase Orders</h2>
        <form method="post" class="mb-4">
          <div class="grid grid-cols-3 gap-2">
            <input name="oil_type" placeholder="Oil type" class="p-2 rounded bg-slate-700" required>
            <input name="unit" placeholder="Unit" class="p-2 rounded bg-slate-700" value="Liters">
            <input name="quantity" placeholder="Quantity" class="p-2 rounded bg-slate-700" required>
          </div>
          <div class="mt-2"><button class="bg-amber-500 px-3 py-2 rounded">Create PO</button></div>
        </form>
        <table class="w-full text-left">
          <thead><tr class="text-slate-300"><th>Date</th><th>Type</th><th>Qty</th><th>Status</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr class="border-t border-slate-700"><td>{{ r['date'] }}</td><td>{{ r['oil_type'] }} ({{ r['unit'] }})</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ r['status'] }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    """, rows=rows)
    return render_template_string(BASE_HTML, body=body, user=user, active="po")

# ---- Global SEARCH route ----
@app.route("/search")
def search():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    q = (request.args.get("q") or "").strip()
    shop = user["shop_name"]
    inv_rows = []
    sales_rows = []
    purchases_rows = []
    if q:
        like = f"%{q}%"
        db = get_db()
        # Search inventory by oil_type or unit (case-insensitive)
        inv_rows = db.execute("""
            SELECT * FROM inventory
            WHERE shop_name = ?
              AND (LOWER(oil_type) LIKE LOWER(?) OR LOWER(unit) LIKE LOWER(?))
            ORDER BY oil_type
        """, (shop, like, like)).fetchall()
        # Search sales by oil_type
        sales_rows = db.execute("""
            SELECT * FROM sales
            WHERE shop_name = ?
              AND (LOWER(oil_type) LIKE LOWER(?))
            ORDER BY date DESC
            LIMIT 500
        """, (shop, like)).fetchall()
        # Search purchases by oil_type
        purchases_rows = db.execute("""
            SELECT * FROM purchases
            WHERE shop_name = ?
              AND (LOWER(oil_type) LIKE LOWER(?))
            ORDER BY date DESC
            LIMIT 500
        """, (shop, like)).fetchall()

        if not (inv_rows or sales_rows or purchases_rows):
            flash(f"No record found for: {q}")
    else:
        flash("Please enter a search keyword")
        return redirect(url_for("dashboard"))

    # Render results in one page (sections)
    body = render_template_string("""
      <div class="bg-slate-800 p-6 rounded">
        <h2 class="text-xl font-bold mb-4">Search Results for "{{ q }}"</h2>

        <div class="mb-6">
          <h3 class="font-semibold mb-2">📦 Inventory ({{ inv_rows|length }})</h3>
          {% if inv_rows %}
            <table class="w-full text-left mb-4">
              <thead><tr class="text-slate-300"><th>Type</th><th>Unit</th><th>Qty</th><th>Unit Price</th></tr></thead>
              <tbody>
                {% for r in inv_rows %}
                  <tr class="border-t border-slate-700"><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ '%.2f'|format(r['unit_price']) }}</td></tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <div class="text-slate-400">No inventory items matched.</div>
          {% endif %}
        </div>

        <div class="mb-6">
          <h3 class="font-semibold mb-2">💰 Sales ({{ sales_rows|length }})</h3>
          {% if sales_rows %}
            <table class="w-full text-left mb-4">
              <thead><tr class="text-slate-300"><th>Date</th><th>Type</th><th>Unit</th><th>Qty</th><th>Total</th></tr></thead>
              <tbody>
                {% for r in sales_rows %}
                  <tr class="border-t border-slate-700"><td>{{ r['date'] }}</td><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ '%.2f'|format(r['total']) }}</td></tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <div class="text-slate-400">No sales matched.</div>
          {% endif %}
        </div>

        <div class="mb-6">
          <h3 class="font-semibold mb-2">🛒 Purchases ({{ purchases_rows|length }})</h3>
          {% if purchases_rows %}
            <table class="w-full text-left mb-4">
              <thead><tr class="text-slate-300"><th>Date</th><th>Type</th><th>Unit</th><th>Qty</th><th>Total</th></tr></thead>
              <tbody>
                {% for r in purchases_rows %}
                  <tr class="border-t border-slate-700"><td>{{ r['date'] }}</td><td>{{ r['oil_type'] }}</td><td>{{ r['unit'] }}</td><td>{{ '%.2f'|format(r['quantity']) }}</td><td>{{ '%.2f'|format(r['total']) }}</td></tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <div class="text-slate-400">No purchases matched.</div>
          {% endif %}
        </div>

        <div class="mt-4">
          <a href="{{ url_for('dashboard') }}" class="bg-slate-700 px-4 py-2 rounded">Back</a>
        </div>
      </div>
    """, q=q, inv_rows=inv_rows, sales_rows=sales_rows, purchases_rows=purchases_rows)
    return render_template_string(BASE_HTML, body=body, user=user, active="")

# ----------------- Simple API for dashboard (optional) -----------------
@app.route("/api/sales_last_30")
def api_sales_30():
    user = current_user()
    if not user:
        return jsonify({"error":"unauth"}), 401
    shop = user["shop_name"]
    db = get_db()
    rows = db.execute("""
      SELECT date(date) as day, IFNULL(SUM(total),0) as total 
      FROM sales 
      WHERE shop_name = ? AND date >= date('now','-29 days')
      GROUP BY day ORDER BY day
    """, (shop,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ----------------- Startup -----------------
if __name__ == "__main__":
    # ensure DB
    if not os.path.exists(DB_PATH):
        open(DB_PATH, "a").close()
    with app.app_context():
        init_db()
    print("Starting app on http://127.0.0.1:5000")
    app.run(debug=True)

# if __name__ == "__main__":
#     # ensure DB
#     if not os.path.exists(DB_PATH):
#         open(DB_PATH, "a").close()
#     with app.app_context():
#         init_db()
#     print("Starting app on http://0.0.0.0:5000")
#     app.run(host="0.0.0.0", port=5000, debug=False)
