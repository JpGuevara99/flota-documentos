import os
import sqlite3
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
import smtplib

DB_FILE = os.getenv("DB_FILE", "vehiculos.db")

DOC_TYPES = [
    "Permiso de Circulación",
    "Cert. Revision Tecnica c/6 meses",
    "Cert. Emision Contaminantes c/6 meses",
    "SOAP (1 vez al año)",
    "Mantención General",
]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def parse_iso_date(s: str):
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None


def days_left(expiry):
    if not expiry:
        return None
    return (expiry - date.today()).days


def state_from_days(d):
    if d is None:
        return "Rojo"
    if d <= 15:
        return "Rojo"
    if d <= 30:
        return "Amarillo"
    return "Verde"


def human_days_label(d):
    if d is None:
        return "No registrado"
    if d < 0:
        x = abs(d)
        return "Vencido hace 1 día" if x == 1 else f"Vencido hace {x} días"
    if d == 0:
        return "Hoy expira"
    if d == 1:
        return "1 día para expirar"
    return f"{d} días para expirar"


def get_conn() -> sqlite3.Connection:
    Path(DB_FILE).touch(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_notifications_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notificaciones (
            documento_id INTEGER PRIMARY KEY,
            last_state TEXT,
            last_expiry TEXT,
            last_notified_state TEXT,
            last_notified_5_expiry TEXT,
            last_checked_at TEXT
        );
        """
    )


def load_config():
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_tls = os.getenv("SMTP_TLS", "1") == "1"
    email_from = os.getenv("EMAIL_FROM", "")
    email_to = [x.strip() for x in os.getenv("EMAIL_TO", "").split(",") if x.strip()]

    if not smtp_host or not smtp_port or not email_from or not email_to:
        raise SystemExit(
            "Falta configuración SMTP. Define SMTP_HOST, SMTP_PORT, EMAIL_FROM, EMAIL_TO (y opcional SMTP_USER/SMTP_PASS)."
        )
    return smtp_host, smtp_port, smtp_user, smtp_pass, smtp_tls, email_from, email_to


def send_messages(messages):
    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_tls, email_from, email_to = load_config()

    sent = 0
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_tls:
            server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        for subj, body in messages:
            msg = EmailMessage()
            msg["From"] = email_from
            msg["To"] = ", ".join(email_to)
            msg["Subject"] = subj
            msg.set_content(body)
            server.send_message(msg)
            sent += 1
    return sent


def main(dry_run: bool = False) -> int:
    conn = get_conn()
    ensure_notifications_table(conn)

    vehicles = conn.execute(
        """
        SELECT id, patente, proyecto
        FROM vehiculos
        ORDER BY patente
        """
    ).fetchall()

    pending = []  # list of (doc_id, subj, body, state, expiry_s, want_state_notify, want_5_notify)

    for v in vehicles:
        docs = conn.execute(
            """
            SELECT id, tipo, fecha_vencimiento
            FROM documentos
            WHERE vehiculo_id = ?
            """,
            (v["id"],),
        ).fetchall()
        docs_by_type = {d["tipo"]: d for d in docs}

        for tipo in DOC_TYPES:
            doc = docs_by_type.get(tipo)
            if not doc or not doc["fecha_vencimiento"]:
                continue

            expiry = parse_iso_date(doc["fecha_vencimiento"])
            d = days_left(expiry)
            state = state_from_days(d)

            n = conn.execute("SELECT * FROM notificaciones WHERE documento_id = ?", (doc["id"],)).fetchone()
            last_state = n["last_state"] if n else None
            last_notified_5_expiry = n["last_notified_5_expiry"] if n else None

            want_state_notify = False
            if last_state and last_state != state:
                if (last_state, state) in [("Verde", "Amarillo"), ("Amarillo", "Rojo")]:
                    want_state_notify = True

            want_5_notify = (d == 5) and (last_notified_5_expiry != doc["fecha_vencimiento"])

            if want_state_notify or want_5_notify:
                patente = (v["patente"] or "").upper()
                subj = f"[Flota] {patente} – {tipo} – {state} – vence {doc['fecha_vencimiento']}"
                body = (
                    f"Vehículo: {patente}\n"
                    f"Proyecto: {v['proyecto'] or '-'}\n"
                    f"Documento: {tipo}\n"
                    f"Vencimiento: {doc['fecha_vencimiento']}\n"
                    f"Estado: {state} ({human_days_label(d)})\n"
                )
                if want_5_notify:
                    body += "\nAlerta: quedan 5 días para expirar y no se ha renovado.\n"
                if want_state_notify:
                    body += f"\nCambio de estado: {last_state} → {state}\n"
                pending.append((doc["id"], subj, body, state, doc["fecha_vencimiento"], want_state_notify, want_5_notify))

            # Snapshot state
            if n:
                conn.execute(
                    """
                    UPDATE notificaciones
                    SET last_state=?, last_expiry=?, last_checked_at=?
                    WHERE documento_id=?
                    """,
                    (state, doc["fecha_vencimiento"], now_iso(), doc["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO notificaciones (documento_id, last_state, last_expiry, last_notified_state, last_notified_5_expiry, last_checked_at)
                    VALUES (?, ?, ?, NULL, NULL, ?)
                    """,
                    (doc["id"], state, doc["fecha_vencimiento"], now_iso()),
                )

    conn.commit()

    if not pending:
        print("No hay recordatorios pendientes.")
        conn.close()
        return 0

    if dry_run:
        print(f"Recordatorios pendientes: {len(pending)} (dry-run, no se envía correo)")
        for _, subj, body, *_ in pending[:20]:
            print("\n---")
            print(subj)
            print(body)
        conn.close()
        return 0

    messages = [(subj, body) for _, subj, body, *_ in pending]
    sent = send_messages(messages)

    # Mark notified
    for doc_id, _, _, state, expiry_s, want_state_notify, want_5_notify in pending:
        if want_state_notify:
            conn.execute("UPDATE notificaciones SET last_notified_state=? WHERE documento_id=?", (state, doc_id))
        if want_5_notify:
            conn.execute("UPDATE notificaciones SET last_notified_5_expiry=? WHERE documento_id=?", (expiry_s, doc_id))

    conn.commit()
    conn.close()
    print(f"Correos enviados: {sent}")
    return 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Envío de recordatorios de documentos (Flota).")
    p.add_argument("--dry-run", action="store_true", help="No envía correos; solo imprime.")
    args = p.parse_args()
    raise SystemExit(main(dry_run=args.dry_run))

