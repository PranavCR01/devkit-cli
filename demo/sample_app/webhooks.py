import json

from flask import jsonify


def handle_stripe_event(payload: bytes):
    event = json.loads(payload)  # signature never verified
    if event.get("type") == "invoice.paid":
        mark_paid(event["data"]["object"]["id"])
    return jsonify({"ok": True})


def mark_paid(invoice_id: str) -> None:
    from app import db
    db().execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
