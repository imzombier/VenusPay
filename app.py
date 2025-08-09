from flask import Flask, render_template_string, request, url_for, redirect, session, jsonify
import os, sqlite3, time
from werkzeug.utils import secure_filename
from pathlib import Path
import requests
import threading

# ---------------- Config ----------------
APP_ROOT = Path(__file__).parent
UPLOAD_FOLDER = APP_ROOT / "uploads"
STATIC_FOLDER = APP_ROOT / "static"
DB_FILE = APP_ROOT / "data.db"
ADMIN_PASSWORD = os.environ.get("VENUSPAY_ADMIN_PW", "admin123")  # change via env var in production

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_FOLDER), static_url_path="/static")
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.secret_key = os.environ.get("VENUSPAY_SECRET", "venus_secret_key")  # change in production

# ---------------- DB utils ----------------
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            upi_id TEXT,
            receiver_name TEXT,
            loan_number TEXT,
            emi_amount REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY,
            amount REAL,
            screenshot TEXT,
            status TEXT DEFAULT 'Pending',
            created_at INTEGER
        )
    """)
    # insert default settings if empty
    row = c.execute("SELECT COUNT(*) as cnt FROM settings").fetchone()
    if row['cnt'] == 0:
        c.execute("INSERT INTO settings (upi_id, receiver_name, loan_number, emi_amount) VALUES (?,?,?,?)",
                  ("blackheart.in@ybl", "PAYU MODE", "LN123456", 2500.00))
    conn.commit()
    conn.close()

init_db()

# ---------------- HTML template ----------------
# NOTE: using render_template_string for single-file convenience.
TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>VenusPay</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
  <style>
    body { background: #f0f2f5; padding-bottom: 80px; }
    .card { margin-top: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); border-radius: 15px; }
    .upi-logos { display: flex; justify-content: center; gap: 20px; margin-bottom: 10px; }
    .upi-logos img { height: 42px; object-fit: contain; box-shadow: 0px 6px 12px rgba(0,0,0,0.18); border-radius: 8px; background:white; padding:6px; transition: transform .14s; }
    .upi-logos img:hover{ transform: scale(1.06); }
    #qrContainer { padding: 12px; border: 3px solid #000; border-radius: 10px; background:white; }
    .qr-wrapper { display:flex; justify-content:center; align-items:center; margin-top:8px; }
    .pending-badge { background:#ffc107; color:#000; padding:.25rem .55rem; border-radius:999px; font-weight:600; }
    .admin-header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .small-img { max-width:160px; }
    .muted { color:#6c757d; }
  </style>
</head>
<body>
<div class="container">
  <div class="card p-3">
    <div class="admin-header mb-2">
      <h4 class="mb-0">💳 VenusPay</h4>
      <div>
        <a href="/admin" class="btn btn-outline-dark btn-sm">Admin</a>
      </div>
    </div>
    <hr>

    {% if success %}
      <div class="alert alert-success">{{ success }}</div>
    {% endif %}

    {% if emi_due %}
    <div class="row">
      <div class="col-lg-6">
        <form method="post" enctype="multipart/form-data" class="mb-3">
          <div class="mb-3">
            <label class="form-label">💰 Amount (₹)</label>
            <input type="number" name="amount" id="amountInput" class="form-control" value="{{ settings.emi_amount }}" required min="1" step="0.01">
          </div>

          <div class="mb-3">
            <label class="form-label">📲 Pay via UPI</label>
            <div class="upi-logos" aria-hidden="true">
              <a id="upiLink" href="#" target="_blank"><img src="{{ url_for('static', filename='phonepe.png') }}" alt="PhonePe"></a>
              <a id="upiLink2" href="#" target="_blank"><img src="{{ url_for('static', filename='paytm.png') }}" alt="Paytm"></a>
              <a id="upiLink3" href="#" target="_blank"><img src="{{ url_for('static', filename='gpay.png') }}" alt="GPay"></a>
              <a id="upiLink4" href="#" target="_blank"><img src="{{ url_for('static', filename='bhim.png') }}" alt="BHIM"></a>
            </div>

            <div class="qr-wrapper">
              <div id="qrContainer" class="text-center"></div>
            </div>
          </div>

          <div class="mb-3">
            <label class="form-label">🖼 Upload Screenshot (required)</label>
            <input type="file" name="screenshot" accept="image/*" class="form-control" required>
          </div>

          <button type="submit" class="btn btn-success w-100">✅ Submit Payment</button>
        </form>
      </div>
    </div>
    {% else %}
      <div class="alert alert-info">✅ All EMIs are paid. No dues now.</div>
    {% endif %}
  </div>
</div>

<script>
  const amountInput = document.getElementById("amountInput");
  const qrContainer = document.getElementById("qrContainer");
  const links = [
    document.getElementById("upiLink"),
    document.getElementById("upiLink2"),
    document.getElementById("upiLink3"),
    document.getElementById("upiLink4")
  ];

  const upi_id = "{{ settings.upi_id }}";
  const receiver = "{{ settings.receiver_name }}";
  const loan_number = "{{ settings.loan_number }}";

  function generateQR(amt){
    const upi_url = `upi://pay?pa=${encodeURIComponent(upi_id)}&pn=${encodeURIComponent(receiver)}&am=${amt.toFixed(2)}&cu=INR&tn=${encodeURIComponent('Loan EMI - ' + loan_number)}`;
    links.forEach(l => l.href = upi_url);
    qrContainer.innerHTML = "";
    new QRCode(qrContainer, { text: upi_url, width: 180, height: 180 });
  }

  // init
  generateQR(parseFloat(amountInput.value || {{ settings.emi_amount }}));

  amountInput.addEventListener('input', () => {
    const val = parseFloat(amountInput.value) || 0;
    if (val > 0) generateQR(val);
  });
</script>
</body>
</html>
"""

# ---------------- Routes ----------------
@app.route("/", methods=["GET", "POST"])
def index():
    conn = get_conn()
    settings_row = conn.execute("SELECT * FROM settings LIMIT 1").fetchone()
    settings = {
        "upi_id": settings_row["upi_id"],
        "receiver_name": settings_row["receiver_name"],
        "loan_number": settings_row["loan_number"],
        "emi_amount": settings_row["emi_amount"]
    }
    success = ""
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", 0))
        except:
            amount = 0.0
        screenshot = request.files.get("screenshot")
        if amount <= 0:
            success = "❌ Invalid amount."
        elif not screenshot:
            success = "❌ Please upload screenshot."
        else:
            filename = secure_filename(f"{int(time.time())}_{screenshot.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            screenshot.save(filepath)
            # save payment record
            conn.execute("INSERT INTO payments (amount, screenshot, status, created_at) VALUES (?,?,?,?)",
                         (amount, filename, "Pending", int(time.time())))
            conn.commit()
            success = f"✅ ₹{amount:.2f} payment submitted successfully. Awaiting approval."
    conn.close()
    return render_template_string(TEMPLATE, settings=settings, success=success, emi_due=True)

# serve uploads
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return app.send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------- Admin ----------------
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == ADMIN_PASSWORD:
            session["admin_logged"] = True
            return redirect("/admin")
        else:
            return render_template_string("""
                <div style="padding:30px"><h4>Login failed</h4><a href="/admin/login">Back</a></div>
            """)
    return render_template_string("""
        <div style="max-width:420px;margin:80px auto;padding:20px;border-radius:8px;background:#fff;box-shadow:0 4px 20px rgba(0,0,0,0.06);">
          <h4>Admin Login</h4>
          <form method="post">
            <div class="mb-2"><input class="form-control" type="password" name="password" placeholder="Password"></div>
            <button class="btn btn-primary w-100" type="submit">Login</button>
          </form>
        </div>
    """)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged", None)
    return redirect("/")

@app.route("/admin", methods=["GET","POST"])
def admin():
    if not session.get("admin_logged"):
        return redirect("/admin/login")
    conn = get_conn()
    if request.method == "POST":
        # update settings
        upi = request.form.get("upi_id","").strip()
        rec = request.form.get("receiver_name","").strip()
        loan = request.form.get("loan_number","").strip()
        try:
            emi = float(request.form.get("emi_amount","0"))
        except:
            emi = 0.0
        conn.execute("UPDATE settings SET upi_id=?, receiver_name=?, loan_number=?, emi_amount=? WHERE id=1",
                     (upi, rec, loan, emi))
        conn.commit()
        return redirect("/admin")

    settings_row = conn.execute("SELECT * FROM settings LIMIT 1").fetchone()
    payments = conn.execute("SELECT * FROM payments ORDER BY id DESC").fetchall()
    pending_count = conn.execute("SELECT COUNT(*) as cnt FROM payments WHERE status='Pending'").fetchone()["cnt"]
    conn.close()

    return render_template_string("""
    <!doctype html><html><head>
    <meta charset="utf-8"><title>Admin - VenusPay</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body{background:#f6f8fb;padding:20px}</style></head><body>
    <div class="container">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h3>Admin Panel</h3>
        <div>
          <a class="btn btn-outline-secondary btn-sm" href="/">Go to site</a>
          <a class="btn btn-danger btn-sm" href="/admin/logout">Logout</a>
        </div>
      </div>

      <div class="mb-3">
        <div class="alert alert-warning">Pending payments: <strong>{{ pending_count }}</strong></div>
      </div>

      <div class="card mb-3 p-3">
        <h5>Settings</h5>
        <form method="post">
          <div class="mb-2">
            <label>UPI ID</label>
            <input name="upi_id" class="form-control" value="{{ settings.upi_id }}">
          </div>
          <div class="mb-2">
            <label>Receiver Name</label>
            <input name="receiver_name" class="form-control" value="{{ settings.receiver_name }}">
          </div>
          <div class="mb-2">
            <label>Loan Number</label>
            <input name="loan_number" class="form-control" value="{{ settings.loan_number }}">
          </div>
          <div class="mb-2">
            <label>EMI Amount</label>
            <input name="emi_amount" class="form-control" value="{{ settings.emi_amount }}">
          </div>
          <button class="btn btn-primary">Update</button>
        </form>
      </div>

      <div class="card p-3">
        <h5>Payments</h5>
        <table class="table table-sm">
          <thead><tr><th>ID</th><th>Amount</th><th>Screenshot</th><th>Status</th><th>Time</th><th>Actions</th></tr></thead>
          <tbody>
            {% for p in payments %}
              <tr class="{{ 'table-warning' if p['status']=='Pending' else '' }}">
                <td>{{ p['id'] }}</td>
                <td>₹{{ '%.2f'|format(p['amount']) }}</td>
                <td>
                  {% if p['screenshot'] %}
                    <a href="{{ url_for('uploaded_file', filename=p['screenshot']) }}" target="_blank">
                      <img src="{{ url_for('uploaded_file', filename=p['screenshot']) }}" style="height:60px;object-fit:cover;border-radius:6px;">
                    </a>
                  {% endif %}
                </td>
                <td>{{ p['status'] }}</td>
                <td>{{ (p['created_at']|int)|timestamp_to_string }}</td>
                <td>
                  {% if p['status']=='Pending' %}
                    <a class="btn btn-sm btn-success" href="{{ url_for('approve_payment', pid=p['id']) }}">Approve</a>
                  {% endif %}
                  <a class="btn btn-sm btn-danger" href="{{ url_for('delete_payment', pid=p['id']) }}" onclick="return confirm('Delete payment?')">Delete</a>
                </td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    </body></html>
    """, settings=dict(settings_row), payments=payments, pending_count=pending_count)

# helpers for template: timestamp formatting
@app.template_filter('timestamp_to_string')
def timestamp_to_string_filter(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(ts)))
    except:
        return "-"

# Approve payment
@app.route("/admin/approve/<int:pid>")
def approve_payment(pid):
    if not session.get("admin_logged"):
        return redirect("/admin/login")
    conn = get_conn()
    conn.execute("UPDATE payments SET status='Approved' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return redirect("/admin")

# Delete payment
@app.route("/admin/delete/<int:pid>")
def delete_payment(pid):
    if not session.get("admin_logged"):
        return redirect("/admin/login")
    conn = get_conn()
    row = conn.execute("SELECT screenshot FROM payments WHERE id=?", (pid,)).fetchone()
    if row and row["screenshot"]:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], row["screenshot"]))
        except:
            pass
    conn.execute("DELETE FROM payments WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return redirect("/admin")

# endpoint to return pending count (useful for polling)
@app.route("/admin/pending_count")
def pending_count():
    if not session.get("admin_logged"):
        return jsonify({"error":"not_auth"}), 401
    conn = get_conn()
    cnt = conn.execute("SELECT COUNT(*) as cnt FROM payments WHERE status='Pending'").fetchone()["cnt"]
    conn.close()
    return jsonify({"pending": cnt})

def keep_alive():
    url = os.environ.get("RENDER_URL")
    if not url:
        raise ValueError("RENDER_URL environment variable is missing! Set it before running the script.")

    while True:
        try:
            response = requests.get(url)
            print(f"Keep-alive ping sent! Status: {response.status_code}")
        except Exception as e:
            print(f"Keep-alive request failed: {e}")
        time.sleep(49)

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
