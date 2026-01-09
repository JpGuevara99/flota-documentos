import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import streamlit as st

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(
    page_title="Flota de vehículos – Documentos",
    page_icon="logo.png",
    layout="wide",
)

DB_FILE = "vehiculos.db"
UPLOADS_DIR = Path("uploads")

DOC_TYPES = [
    "Permiso de Circulación",
    "Cert. Revision Tecnica c/6 meses",
    "Cert. Emision Contaminantes c/6 meses",
    "SOAP (1 vez al año)",
    "Mantención General",
]

# -----------------------------
# CSS
# -----------------------------
st.markdown(
    """
<style>
.main { background-color: #FFFFFF !important; }
section[data-testid="stSidebar"] { background-color: #F3F7FF !important; }
.stButton>button { background-color: #0D1A4A !important; color: white !important; border-radius: 6px; }
.badge-green { background-color: #DFF3E6; color: #0A682B; padding: 6px 10px; border-radius: 6px; display: inline-block; font-weight: 600; }
.badge-yellow { background-color: #FFF7D6; color: #6B5500; padding: 6px 10px; border-radius: 6px; display: inline-block; font-weight: 600; }
.badge-red { background-color: #FDE8E8; color: #8B0000; padding: 6px 10px; border-radius: 6px; display: inline-block; font-weight: 600; }
.small-note { color: #6b7280; font-size: 13px; }
.kpi { padding: 12px; border: 1px solid #E6E9F2; border-radius: 10px; background: #fff; }
</style>
""",
    unsafe_allow_html=True,
)


# -----------------------------
# DB helpers / schema migration
# -----------------------------
def get_conn() -> sqlite3.Connection:
    Path(DB_FILE).touch(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def table_columns(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return {r["name"] for r in rows}


def ensure_schema() -> None:
    conn = get_conn()
    c = conn.cursor()

    # Base tables (create if missing)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS vehiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descripcion TEXT,
            ano INTEGER,
            color TEXT,
            patente TEXT UNIQUE
        );
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehiculo_id INTEGER,
            tipo TEXT,
            fecha_vencimiento TEXT,
            notes TEXT,
            FOREIGN KEY (vehiculo_id) REFERENCES vehiculos(id) ON DELETE CASCADE
        );
        """
    )

    # Migrations: vehiculos fields
    vcols = table_columns(conn, "vehiculos")
    for col, col_type in [
        ("tipo_vehiculo", "TEXT"),
        ("proyecto", "TEXT"),
        ("marca", "TEXT"),
        ("modelo", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ]:
        if col not in vcols:
            c.execute(f"ALTER TABLE vehiculos ADD COLUMN {col} {col_type};")

    # Migrations: documentos fields
    dcols = table_columns(conn, "documentos")
    for col, col_type in [
        ("archivo_path", "TEXT"),
        ("archivo_nombre", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ]:
        if col not in dcols:
            c.execute(f"ALTER TABLE documentos ADD COLUMN {col} {col_type};")

    # Audit / history / notifications
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehiculo_id INTEGER,
            entidad TEXT,
            entidad_id INTEGER,
            accion TEXT,
            detalle TEXT,
            created_at TEXT,
            FOREIGN KEY (vehiculo_id) REFERENCES vehiculos(id) ON DELETE CASCADE
        );
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS documentos_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehiculo_id INTEGER,
            documento_id INTEGER,
            tipo TEXT,
            fecha_vencimiento TEXT,
            notes TEXT,
            archivo_path TEXT,
            archivo_nombre TEXT,
            cambio_tipo TEXT,
            changed_at TEXT,
            FOREIGN KEY (vehiculo_id) REFERENCES vehiculos(id) ON DELETE CASCADE
        );
        """
    )
    c.execute(
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

    # Backfill timestamps (best-effort)
    ts = now_iso()
    c.execute("UPDATE vehiculos SET created_at = COALESCE(created_at, ?)", (ts,))
    c.execute("UPDATE vehiculos SET updated_at = COALESCE(updated_at, ?)", (ts,))
    c.execute("UPDATE documentos SET created_at = COALESCE(created_at, ?)", (ts,))
    c.execute("UPDATE documentos SET updated_at = COALESCE(updated_at, ?)", (ts,))

    conn.commit()
    conn.close()


ensure_schema()


# -----------------------------
# Utilities (dates / status)
# -----------------------------
def parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None


def days_left(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    return (expiry - date.today()).days


def state_from_days(d: Optional[int]) -> Tuple[str, str]:
    """
    Semáforo según lo pedido:
    - Verde: > 30 días
    - Amarillo: 16..30 días (<=30)
    - Rojo: <= 15 días o vencido
    - Si falta documento: Rojo
    """
    if d is None:
        return "Rojo", "badge-red"
    if d <= 15:
        return "Rojo", "badge-red"
    if d <= 30:
        return "Amarillo", "badge-yellow"
    return "Verde", "badge-green"


def human_days_label(d: Optional[int]) -> str:
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


def safe_slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "doc"


def row_get(row: Optional[sqlite3.Row], key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        return default


def log_event(
    conn: sqlite3.Connection,
    vehiculo_id: int,
    entidad: str,
    entidad_id: Optional[int],
    accion: str,
    detalle: str,
) -> None:
    conn.execute(
        """
        INSERT INTO auditoria (vehiculo_id, entidad, entidad_id, accion, detalle, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (vehiculo_id, entidad, entidad_id, accion, detalle, now_iso()),
    )


# -----------------------------
# Data access
# -----------------------------
def fetch_vehicles(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT id, patente, tipo_vehiculo, proyecto, marca, modelo, ano, descripcion, color, created_at, updated_at
        FROM vehiculos
        ORDER BY COALESCE(marca,''), COALESCE(modelo,''), COALESCE(patente,'')
        """
    ).fetchall()


def fetch_vehicle(conn: sqlite3.Connection, vehiculo_id: int):
    return conn.execute(
        """
        SELECT id, patente, tipo_vehiculo, proyecto, marca, modelo, ano, descripcion, color, created_at, updated_at
        FROM vehiculos WHERE id = ?
        """,
        (vehiculo_id,),
    ).fetchone()


def fetch_vehicle_docs(conn: sqlite3.Connection, vehiculo_id: int) -> Dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT id, vehiculo_id, tipo, fecha_vencimiento, notes, archivo_path, archivo_nombre, created_at, updated_at
        FROM documentos
        WHERE vehiculo_id = ?
        """,
        (vehiculo_id,),
    ).fetchall()
    out: Dict[str, sqlite3.Row] = {}
    for r in rows:
        out[r["tipo"]] = r
    return out


def get_doc_status(doc: Optional[sqlite3.Row]) -> Dict[str, Any]:
    if not doc or not row_get(doc, "fecha_vencimiento"):
        return {
            "expiry": None,
            "days": None,
            "state": "Rojo",
            "badge": "badge-red",
            "label": "No registrado",
        }
    expiry = parse_iso_date(row_get(doc, "fecha_vencimiento"))
    d = days_left(expiry)
    state, badge = state_from_days(d)
    return {
        "expiry": expiry,
        "days": d,
        "state": state,
        "badge": badge,
        "label": human_days_label(d),
    }


def get_vehicle_urgency(conn: sqlite3.Connection, vehiculo_id: int) -> Tuple[int, str, Optional[date]]:
    """
    Devuelve: (urgency_score, doc_type, expiry_date)
    urgency_score = menor days_left entre docs; documentos faltantes cuentan como -9999
    """
    docs = fetch_vehicle_docs(conn, vehiculo_id)
    best_days = 10**9
    best_tipo = ""
    best_exp = None

    for tipo in DOC_TYPES:
        doc = docs.get(tipo)
        status = get_doc_status(doc)
        d = status["days"]
        if d is None:
            d_cmp = -9999
        else:
            d_cmp = int(d)
        if d_cmp < best_days:
            best_days = d_cmp
            best_tipo = tipo
            best_exp = status["expiry"]

    return best_days, best_tipo, best_exp


# -----------------------------
# Header
# -----------------------------
col1, col2 = st.columns([1, 6])
with col1:
    try:
        st.image("logo.png", width=260)
    except Exception:
        st.write("")

with col2:
    st.markdown(
        """
        <div style="display:flex;flex-direction:column;justify-content:center;">
          <div style="font-size:34px;font-weight:800;color:#0D1A4A">Flota de vehículos – Mantención</div>
          <div style="color:#6b7280;margin-top:6px">Semáforo documental + historial + PDFs + recordatorios.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")


# -----------------------------
# Navigation
# -----------------------------
def ss_get(key: str, default: Any = None) -> Any:
    # When running without `streamlit run`, session_state may not be available.
    try:
        return st.session_state.get(key, default)
    except Exception:
        return default


def ss_set(key: str, value: Any) -> None:
    try:
        st.session_state[key] = value
    except Exception:
        return


def ss_setdefault(key: str, default: Any) -> Any:
    try:
        if key not in st.session_state:
            st.session_state[key] = default
        return st.session_state[key]
    except Exception:
        return default


ss_setdefault("page", "Dashboard")
ss_setdefault("selected_vehicle_id", None)

st.sidebar.header("Panel de gestión")
pages = ["Dashboard", "Vehículos", "Documentos y PDFs", "Recordatorios"]
current_page = ss_get("page", "Dashboard")
page = st.sidebar.radio("Ir a:", pages, index=(pages.index(current_page) if current_page in pages else 0))
ss_set("page", page)


# -----------------------------
# Dashboard
# -----------------------------
if page == "Dashboard":
    st.subheader("Dashboard – Flota completa")
    conn = get_conn()
    vehicles = fetch_vehicles(conn)

    if not vehicles:
        st.info("Aún no hay vehículos registrados.")
        conn.close()
    else:
        # KPIs
        red = yellow = green = 0
        for v in vehicles:
            urgency, _, _ = get_vehicle_urgency(conn, v["id"])
            state, _ = state_from_days(None if urgency == -9999 else urgency)
            if state == "Rojo":
                red += 1
            elif state == "Amarillo":
                yellow += 1
            else:
                green += 1

        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(f'<div class="kpi"><b>Rojo</b><div style="font-size:26px">{red}</div></div>', unsafe_allow_html=True)
        with k2:
            st.markdown(
                f'<div class="kpi"><b>Amarillo</b><div style="font-size:26px">{yellow}</div></div>',
                unsafe_allow_html=True,
            )
        with k3:
            st.markdown(
                f'<div class="kpi"><b>Verde</b><div style="font-size:26px">{green}</div></div>',
                unsafe_allow_html=True,
            )

        # Filters
        proyectos = sorted({(v["proyecto"] or "").strip() for v in vehicles if (v["proyecto"] or "").strip()})
        colf1, colf2 = st.columns([1, 1])
        with colf1:
            proyecto_sel = st.selectbox("Filtrar por proyecto", ["Todos"] + proyectos)
        with colf2:
            estado_sel = st.selectbox("Filtrar por estado (según documento más urgente)", ["Todos", "Verde", "Amarillo", "Rojo"])

        # Compute urgency list and sort
        items = []
        for v in vehicles:
            if proyecto_sel != "Todos" and (v["proyecto"] or "").strip() != proyecto_sel:
                continue
            urgency, tipo, exp = get_vehicle_urgency(conn, v["id"])
            d_for_state = None if urgency == -9999 else int(urgency)
            state, badge = state_from_days(d_for_state)
            if estado_sel != "Todos" and state != estado_sel:
                continue
            items.append((urgency, state, badge, tipo, exp, v))

        items.sort(key=lambda x: x[0])

        st.markdown("### Vehículos (ordenados por documento más próximo a vencer)")
        for urgency, state, badge, tipo, exp, v in items:
            patente = (v["patente"] or "").upper()
            marca = (v["marca"] or "").strip()
            modelo = (v["modelo"] or "").strip()
            proyecto = (v["proyecto"] or "").strip()
            tipo_veh = (v["tipo_vehiculo"] or "").strip()
            ano = v["ano"] or ""

            if urgency == -9999:
                resumen = f'{tipo}: <span class="{badge}">FALTA</span>'
                days_label = "Documento no registrado"
            else:
                exp_s = exp.isoformat() if exp else "—"
                resumen = f'{tipo}: <span class="{badge}">{exp_s}</span>'
                days_label = human_days_label(urgency)

            header = f"{patente} — {marca} {modelo} ({ano}) — {proyecto or 'Sin proyecto'} — {resumen} — {days_label}"
            with st.expander(header, expanded=False):
                docs = fetch_vehicle_docs(conn, v["id"])
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**Tipo de vehículo:** {tipo_veh or '—'}")
                    st.markdown(f"**Proyecto asignado:** {proyecto or '—'}")
                    st.markdown(f"**Marca / Modelo / Año:** {(marca or '—')} / {(modelo or '—')} / {ano or '—'}")
                with c2:
                    if st.button("Más detalles", key=f"det_{v['id']}"):
                        st.session_state.selected_vehicle_id = v["id"]
                        st.session_state.page = "Documentos y PDFs"
                        st.rerun()

                st.markdown("#### Estado de documentos")
                for dtipo in DOC_TYPES:
                    doc = docs.get(dtipo)
                    status = get_doc_status(doc)
                    exp_s = status["expiry"].isoformat() if status["expiry"] else "—"
                    st.markdown(
                        f'- **{dtipo}**: <span class="{status["badge"]}">{exp_s}</span> <span class="small-note">({status["label"]})</span>',
                        unsafe_allow_html=True,
                    )

        conn.close()


# -----------------------------
# Vehículos (CRUD básico + auditoría)
# -----------------------------
elif page == "Vehículos":
    st.subheader("Vehículos")
    conn = get_conn()
    vehicles = fetch_vehicles(conn)

    colA, colB = st.columns([2, 3])
    with colA:
        st.markdown("### Agregar / actualizar")
        mode = st.radio("Modo", ["Agregar", "Editar"], horizontal=True)

        if mode == "Editar" and vehicles:
            options = {f"{(v['patente'] or '').upper()} — {(v['marca'] or '').strip()} {(v['modelo'] or '').strip()}": v["id"] for v in vehicles}
            sel = st.selectbox("Seleccionar vehículo", [""] + list(options.keys()))
            vid = options.get(sel) if sel else None
        else:
            vid = None

        current = fetch_vehicle(conn, vid) if vid else None

        patente = st.text_input("Patente", value=(current["patente"] if current else "") or "").upper().strip()
        tipo_vehiculo = st.text_input("Tipo de Vehículo", value=(current["tipo_vehiculo"] if current else "") or "")
        proyecto = st.text_input("Proyecto asignado", value=(current["proyecto"] if current else "") or "")
        marca = st.text_input("Marca", value=(current["marca"] if current else "") or "")
        modelo = st.text_input("Modelo", value=(current["modelo"] if current else "") or ((current["descripcion"] if current else "") or ""))
        ano = st.number_input("Año", min_value=1950, max_value=date.today().year + 1, value=int((current["ano"] if current else date.today().year) or date.today().year))

        if st.button("Guardar vehículo"):
            if not patente:
                st.error("La patente es obligatoria.")
            else:
                try:
                    if current:
                        conn.execute(
                            """
                            UPDATE vehiculos
                            SET patente=?, tipo_vehiculo=?, proyecto=?, marca=?, modelo=?, ano=?, descripcion=?, updated_at=?
                            WHERE id=?
                            """,
                            (patente, tipo_vehiculo, proyecto, marca, modelo, int(ano), modelo, now_iso(), current["id"]),
                        )
                        log_event(conn, current["id"], "vehiculo", current["id"], "update", f"Actualización de datos del vehículo {patente}")
                        conn.commit()
                        st.success("Vehículo actualizado.")
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO vehiculos (patente, tipo_vehiculo, proyecto, marca, modelo, ano, descripcion, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (patente, tipo_vehiculo, proyecto, marca, modelo, int(ano), modelo, now_iso(), now_iso()),
                        )
                        new_id = int(cur.lastrowid)
                        log_event(conn, new_id, "vehiculo", new_id, "create", f"Alta de vehículo {patente}")
                        conn.commit()
                        st.success("Vehículo agregado.")
                except sqlite3.IntegrityError:
                    st.error("La patente ya existe.")

    with colB:
        st.markdown("### Lista")
        if not vehicles:
            st.info("No hay vehículos.")
        else:
            for v in vehicles:
                patente = (v["patente"] or "").upper()
                st.write(f"- **{patente}** — {(v['marca'] or '—')} {(v['modelo'] or '')} — {(v['proyecto'] or 'Sin proyecto')}")

        st.markdown("---")
        st.markdown("### Eliminar")
        if vehicles:
            options2 = {f"{(v['patente'] or '').upper()} — {(v['marca'] or '').strip()} {(v['modelo'] or '').strip()}": v["id"] for v in vehicles}
            sel2 = st.selectbox("Seleccionar para eliminar", [""] + list(options2.keys()))
            if sel2:
                vid2 = options2[sel2]
                if st.button("Eliminar definitivamente"):
                    vrow = fetch_vehicle(conn, vid2)
                    conn.execute("DELETE FROM vehiculos WHERE id = ?", (vid2,))
                    conn.commit()
                    st.success(f"Eliminado: {(vrow['patente'] or '').upper() if vrow else 'vehículo'}")

    conn.close()


# -----------------------------
# Documentos + PDFs + historial
# -----------------------------
elif page == "Documentos y PDFs":
    st.subheader("Documentos y PDFs")
    conn = get_conn()
    vehicles = fetch_vehicles(conn)
    if not vehicles:
        st.info("Primero agrega vehículos.")
        conn.close()
    else:
        options = {f"{(v['patente'] or '').upper()} — {(v['marca'] or '').strip()} {(v['modelo'] or '').strip()}": v["id"] for v in vehicles}

        default_key = None
        if st.session_state.selected_vehicle_id:
            for k, vid in options.items():
                if vid == st.session_state.selected_vehicle_id:
                    default_key = k
                    break

        sel = st.selectbox("Selecciona vehículo", [""] + list(options.keys()), index=(0 if not default_key else (1 + list(options.keys()).index(default_key))))
        if not sel:
            st.caption("Selecciona un vehículo para ver/actualizar documentos.")
            conn.close()
        else:
            vid = options[sel]
            st.session_state.selected_vehicle_id = vid
            v = fetch_vehicle(conn, vid)
            docs = fetch_vehicle_docs(conn, vid)

            st.markdown(
                f"**Patente:** {(v['patente'] or '').upper()}  \n"
                f"**Tipo de Vehículo:** {(v['tipo_vehiculo'] or '—')}  \n"
                f"**Proyecto:** {(v['proyecto'] or '—')}  \n"
                f"**Marca / Modelo / Año:** {(v['marca'] or '—')} / {(v['modelo'] or '—')} / {(v['ano'] or '—')}"
            )

            st.markdown("---")
            st.markdown("### Documentos requeridos")
            UPLOADS_DIR.mkdir(exist_ok=True)

            for tipo in DOC_TYPES:
                doc = docs.get(tipo)
                status = get_doc_status(doc)
                exp_s = status["expiry"].isoformat() if status["expiry"] else "—"

                with st.container(border=True):
                    st.markdown(
                        f"#### {tipo}  \n"
                        f'<span class="{status["badge"]}">{exp_s}</span> <span class="small-note">({status["label"]})</span>',
                        unsafe_allow_html=True,
                    )

                    c1, c2, c3 = st.columns([1, 2, 2])
                    with c1:
                        new_exp = st.date_input(
                            "Fecha de vencimiento",
                            value=(status["expiry"] or date.today()),
                            key=f"exp_{vid}_{safe_slug(tipo)}",
                        )
                    with c2:
                        new_notes = st.text_area(
                            "Notas (opcional)",
                            value=(doc["notes"] if doc else "") or "",
                            key=f"notes_{vid}_{safe_slug(tipo)}",
                            height=80,
                        )
                    with c3:
                        up = st.file_uploader("Cargar PDF", type=["pdf"], key=f"pdf_{vid}_{safe_slug(tipo)}")

                    # Existing file
                    if doc and row_get(doc, "archivo_path"):
                        p = Path(str(row_get(doc, "archivo_path")))
                        if p.exists() and p.is_file():
                            try:
                                data = p.read_bytes()
                                st.download_button(
                                    "Descargar PDF actual",
                                    data=data,
                                    file_name=row_get(doc, "archivo_nombre") or p.name,
                                    mime="application/pdf",
                                    key=f"dl_{doc['id']}",
                                )
                            except Exception:
                                st.caption("No se pudo leer el PDF guardado.")
                        else:
                            st.caption("PDF registrado pero no encontrado en disco.")

                    if st.button("Guardar / Renovar", key=f"save_{vid}_{safe_slug(tipo)}"):
                        ts = now_iso()
                        doc_id = doc["id"] if doc else None

                        # Guardar PDF (si lo cargaron)
                        archivo_path = row_get(doc, "archivo_path") if doc else None
                        archivo_nombre = row_get(doc, "archivo_nombre") if doc else None
                        if up is not None:
                            patente = (v["patente"] or "sin_patente").upper()
                            target_dir = UPLOADS_DIR / patente / safe_slug(tipo)
                            target_dir.mkdir(parents=True, exist_ok=True)
                            fname = f"{date.today().isoformat()}_{re.sub(r'[^A-Za-z0-9_.-]+','_', up.name)}"
                            target_path = target_dir / fname
                            target_path.write_bytes(up.getvalue())
                            archivo_path = str(target_path)
                            archivo_nombre = up.name

                        if doc_id:
                            # Historial (antes de update)
                            conn.execute(
                                """
                                INSERT INTO documentos_historial
                                  (vehiculo_id, documento_id, tipo, fecha_vencimiento, notes, archivo_path, archivo_nombre, cambio_tipo, changed_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    vid,
                                    doc_id,
                                    tipo,
                                    row_get(doc, "fecha_vencimiento"),
                                    row_get(doc, "notes"),
                                    row_get(doc, "archivo_path"),
                                    row_get(doc, "archivo_nombre"),
                                    "update",
                                    ts,
                                ),
                            )
                            conn.execute(
                                """
                                UPDATE documentos
                                SET fecha_vencimiento=?, notes=?, archivo_path=?, archivo_nombre=?, updated_at=?
                                WHERE id=?
                                """,
                                (new_exp.isoformat(), new_notes, archivo_path, archivo_nombre, ts, doc_id),
                            )
                            log_event(conn, vid, "documento", doc_id, "update", f"Documento actualizado: {tipo} (vence {new_exp.isoformat()})")
                            conn.commit()
                            st.success("Documento actualizado.")
                        else:
                            cur = conn.execute(
                                """
                                INSERT INTO documentos (vehiculo_id, tipo, fecha_vencimiento, notes, archivo_path, archivo_nombre, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (vid, tipo, new_exp.isoformat(), new_notes, archivo_path, archivo_nombre, ts, ts),
                            )
                            new_doc_id = int(cur.lastrowid)
                            log_event(conn, vid, "documento", new_doc_id, "create", f"Documento registrado: {tipo} (vence {new_exp.isoformat()})")
                            conn.commit()
                            st.success("Documento agregado.")

                        st.rerun()

            st.markdown("---")
            st.markdown("### Historial (auditoría)")
            logs = conn.execute(
                """
                SELECT created_at, entidad, accion, detalle
                FROM auditoria
                WHERE vehiculo_id = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (vid,),
            ).fetchall()
            if not logs:
                st.caption("Sin registros aún.")
            else:
                for r in logs:
                    st.write(f"- **{r['created_at']}** — {r['entidad']} / {r['accion']}: {r['detalle']}")

            st.markdown("### Historial de documentos (renovaciones / cambios)")
            hist = conn.execute(
                """
                SELECT changed_at, tipo, fecha_vencimiento, cambio_tipo, archivo_nombre
                FROM documentos_historial
                WHERE vehiculo_id = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (vid,),
            ).fetchall()
            if not hist:
                st.caption("Sin cambios históricos aún.")
            else:
                for r in hist:
                    st.write(
                        f"- **{r['changed_at']}** — **{r['tipo']}** ({r['cambio_tipo']}): vence {r['fecha_vencimiento'] or '—'}"
                        + (f" — PDF: {r['archivo_nombre']}" if r["archivo_nombre"] else "")
                    )

            conn.close()


# -----------------------------
# Recordatorios (correo)
# -----------------------------
elif page == "Recordatorios":
    st.subheader("Recordatorios por correo")
    st.markdown(
        """
Para habilitar envío por SMTP, define estas variables de entorno en el servidor:
- **SMTP_HOST**, **SMTP_PORT**
- **SMTP_USER**, **SMTP_PASS**
- **SMTP_TLS** = `1` (opcional)
- **EMAIL_FROM**
- **EMAIL_TO** = lista separada por comas

Reglas implementadas:
- Enviar correo cuando un documento pasa de **Verde→Amarillo**, **Amarillo→Rojo**.
- Enviar correo cuando queden **5 días** para expirar y no se haya renovado (por cada fecha de vencimiento).
"""
    )

    st.markdown("---")
    st.markdown("### Ejecutar chequeo ahora (manual)")

    def smtp_config_ok() -> Tuple[bool, str]:
        required = ["SMTP_HOST", "SMTP_PORT", "EMAIL_FROM", "EMAIL_TO"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            return False, "Faltan variables: " + ", ".join(missing)
        return True, "Configuración mínima detectada."

    ok, msg = smtp_config_ok()
    st.caption(msg)

    preview_only = st.checkbox("Solo previsualizar (no enviar)", value=True)

    if st.button("Chequear documentos y generar recordatorios"):
        # Import local helper (reminders.py) if present; otherwise do inline minimal check.
        conn = get_conn()
        vehicles = fetch_vehicles(conn)
        pending_msgs = []

        for v in vehicles:
            docs = fetch_vehicle_docs(conn, v["id"])
            for tipo in DOC_TYPES:
                doc = docs.get(tipo)
                if not doc or not row_get(doc, "fecha_vencimiento"):
                    continue
                expiry = parse_iso_date(row_get(doc, "fecha_vencimiento"))
                d = days_left(expiry)
                state, _ = state_from_days(d)

                # notification state
                n = conn.execute("SELECT * FROM notificaciones WHERE documento_id = ?", (doc["id"],)).fetchone()
                last_state = n["last_state"] if n else None
                last_notified_state = n["last_notified_state"] if n else None
                last_notified_5_expiry = n["last_notified_5_expiry"] if n else None

                # Determine transitions
                want_state_notify = False
                if last_state and last_state != state:
                    if (last_state, state) in [("Verde", "Amarillo"), ("Amarillo", "Rojo")]:
                        want_state_notify = True
                # Determine 5-day notify
                want_5_notify = (d == 5) and (last_notified_5_expiry != row_get(doc, "fecha_vencimiento"))

                if want_state_notify or want_5_notify:
                    patente = (v["patente"] or "").upper()
                    subj = f"[Flota] {patente} – {tipo} – {state} – vence {row_get(doc, 'fecha_vencimiento')}"
                    body = (
                        f"Vehículo: {patente}\n"
                        f"Proyecto: {v['proyecto'] or '-'}\n"
                        f"Documento: {tipo}\n"
                        f"Vencimiento: {row_get(doc, 'fecha_vencimiento')}\n"
                        f"Estado: {state} ({human_days_label(d)})\n"
                    )
                    if want_5_notify:
                        body += "\nAlerta: quedan 5 días para expirar y no se ha renovado.\n"
                    if want_state_notify:
                        body += f"\nCambio de estado: {last_state} → {state}\n"
                    pending_msgs.append((doc["id"], subj, body, state, row_get(doc, "fecha_vencimiento"), want_state_notify, want_5_notify))

                # Upsert tracking row (state snapshot)
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

        if not pending_msgs:
            st.success("No hay recordatorios pendientes.")
            conn.close()
        else:
            st.markdown(f"**Recordatorios pendientes:** {len(pending_msgs)}")
            for _, subj, body, _, _, _, _ in pending_msgs[:20]:
                with st.expander(subj, expanded=False):
                    st.text(body)
            if len(pending_msgs) > 20:
                st.caption("Mostrando solo los primeros 20 en pantalla.")

            if preview_only:
                st.info("Previsualización activa: no se enviaron correos.")
                conn.close()
            else:
                # Send emails (best-effort)
                import smtplib
                from email.message import EmailMessage

                smtp_host = os.getenv("SMTP_HOST", "")
                smtp_port = int(os.getenv("SMTP_PORT", "587"))
                smtp_user = os.getenv("SMTP_USER", "")
                smtp_pass = os.getenv("SMTP_PASS", "")
                smtp_tls = os.getenv("SMTP_TLS", "1") == "1"
                email_from = os.getenv("EMAIL_FROM", "")
                email_to = [x.strip() for x in os.getenv("EMAIL_TO", "").split(",") if x.strip()]

                sent = 0
                with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                    if smtp_tls:
                        server.starttls()
                    if smtp_user and smtp_pass:
                        server.login(smtp_user, smtp_pass)

                    for doc_id, subj, body, state, expiry_s, want_state_notify, want_5_notify in pending_msgs:
                        msg = EmailMessage()
                        msg["From"] = email_from
                        msg["To"] = ", ".join(email_to)
                        msg["Subject"] = subj
                        msg.set_content(body)
                        server.send_message(msg)
                        sent += 1

                        # Mark notified
                        if want_state_notify:
                            conn.execute(
                                "UPDATE notificaciones SET last_notified_state=? WHERE documento_id=?",
                                (state, doc_id),
                            )
                        if want_5_notify:
                            conn.execute(
                                "UPDATE notificaciones SET last_notified_5_expiry=? WHERE documento_id=?",
                                (expiry_s, doc_id),
                            )

                conn.commit()
                conn.close()
                st.success(f"Correos enviados: {sent}")


st.markdown("---")
st.caption("Sistema de documentación – SERCOAMB")
