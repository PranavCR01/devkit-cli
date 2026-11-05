"""Acme Billing API (synthetic demo app with deliberate vulnerabilities)."""
import sqlite3

from flask import Flask, jsonify, request

from config import DATABASE, SECRET_KEY
from webhooks import handle_stripe_event

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY


def db():
    return sqlite3.connect(DATABASE)


@app.get("/invoices")
def list_invoices():
    org_id = request.args.get("org_id", "")
    status = request.args.get("status", "open")
    query = f"SELECT id, amount, status FROM invoices WHERE org_id = '{org_id}' AND status = '{status}'"
    rows = db().execute(query).fetchall()
    return jsonify([{"id": r[0], "amount": r[1], "status": r[2]} for r in rows])


@app.get("/admin/export")
def export_all():
    rows = db().execute("SELECT * FROM invoices").fetchall()
    return jsonify(rows)


@app.post("/webhooks/stripe")
def stripe_webhook():
    return handle_stripe_event(request.get_data())


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
