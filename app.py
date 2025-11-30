import streamlit as st
from datetime import datetime, date
import sqlite3
from pathlib import Path
import html

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="Flota de vehículos – Documentos", page_icon="logo.png", layout="wide")

DB_FILE = "vehiculos.db"

# -----------------------------
# CSS personalizado
# -----------------------------
st.markdown("""
<style>
/* Página blanca */
.main {
  background-color: #FFFFFF !important;
}

/* Encabezado */
.top-bar {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 10px 0;
  border-bottom: 1px solid #E6E9F2;
}
.top-bar img {
  height: 110px; /* logo más grande */
  object-fit: contain;
  box-shadow: 0 3px 8px rgba(10,26,61,0.06);
  border-radius: 6px;
  padding: 6px;
  background: white;
}
.top-bar-title {
  font-size: 34px;
  font-weight: 800;
  color: #0D1A4A;
}

/* Sidebar */
section[data-testid="stSidebar"] {
  background-color: #F3F7FF !important;
}

/* Botones */
.stButton>button {
  background-color: #0D1A4A !important;
  color: white !important;
  border-radius: 6px;
}

/* Tabla HTML */
.table-container {
  width: 100%;
  margin-top: 16px;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
}
.table th, .table td {
  padding: 10px 12px;
  border: 1px solid #e9eef8;
  text-align: left;
}
.table th {
  background: #f7f9fe;
  color: #0d1a4a;
  font-weight: 700;
}

/* Semáforo: colores para la fecha de expiración */
.badge-green {
  background-color: #DFF3E6;
  color: #0A682B;
  padding: 6px 10px;
  border-radius: 6px;
  display: inline-block;
  font-weight: 600;
}
.badge-yellow {
  background-color: #FFF7D6;
  color: #6B5500;
  padding: 6px 10px;
  border-radius: 6px;
  display: inline-block;
  font-weight: 600;
}
.badge-red {
  background-color: #FDE8E8;
  color: #8B0000;
  padding: 6px 10px;
  border-radius: 6px;
  display: inline-block;
  font-weight: 600;
}

/* pequeña nota */
.small-note {
  color: #6b7280;
  font-size: 13px;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# DB helpers
# -----------------------------
def get_conn():
    Path(DB_FILE).touch(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS vehiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descripcion TEXT,
            ano INTEGER,
            color TEXT,
            patente TEXT UNIQUE
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehiculo_id INTEGER,
            tipo TEXT,
            fecha_vencimiento TEXT,
            notes TEXT,
            FOREIGN KEY (vehiculo_id) REFERENCES vehiculos(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

init_db()

# -----------------------------
# UTIL - estado / dias
# -----------------------------
def days_left_from_iso(iso_date_str):
    try:
        d = datetime.fromisoformat(iso_date_str).date()
    except Exception:
        # if stored differently, try fallback
        d = datetime.strptime(iso_date_str, "%Y-%m-%d").date()
    return (d - date.today()).days, d

def state_from_days(days):
    if days <= 30:
        return "Rojo", "badge-red"
    elif days <= 60:
        return "Amarillo", "badge-yellow"
    else:
        return "Verde", "badge-green"

def human_days_label(days):
    if days < 0:
        d = abs(days)
        if d == 1:
            return "Vencido hace 1 día"
        else:
            return f"Vencido hace {d} días"
    else:
        if days == 0:
            return "Hoy expira"
        elif days == 1:
            return "1 día para expirar"
        else:
            return f"{days} días para expirar"

# -----------------------------
# HEADER
# -----------------------------
# --- ENCABEZADO (usa st.image para evitar problemas de path/HTML) ---
col1, col2 = st.columns([1, 6])
with col1:
    try:
        # ajusta el width si quieres el logo más o menos grande
        st.image("logo.png", width=290)
    except Exception as e:
        # si no se encuentra el logo, no romper la app
        st.write("")  # o st.text("Logo (logo.png no encontrado)")

with col2:
    st.markdown("""
        <div style="display:flex;flex-direction:column;justify-content:center;">
            <div style="font-size:34px;font-weight:800;color:#0D1A4A">Flota de vehículos – Documentos</div>
            <div style="color:#6b7280;margin-top:6px">Control documental con sistema de semáforo automático.</div>
        </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# -----------------------------
# SIDEBAR - navegación
# -----------------------------
st.sidebar.header("Panel de gestión")
modo = st.sidebar.radio("Acciones:", ["Dashboard general", "Añadir vehículo", "Administrar documentos", "Eliminar vehículo"])

# -----------------------------
# Función para render tabla HTML
# -----------------------------
def render_documents_table(rows):
    html_rows = ""
    for r in rows:
        desc = html.escape(r["descripcion"] or "")
        ano = r.get("ano") or ""
        color = html.escape(r.get("color") or "")
        col0 = f"{desc} — {ano} — {color}"

        patente = html.escape(r.get("patente") or "")
        tipo = html.escape(r.get("tipo") or "")

        days = r["days_left"]
        days_label = human_days_label(days)

        state_text, badge_class = state_from_days(days)
        fecha_display = r["fecha_display"]
        fecha_html = f'<span class="{badge_class}">{fecha_display}</span>'

        html_rows += f"""
<tr>
<td>{col0}</td>
<td>{patente}</td>
<td>{tipo}</td>
<td>{fecha_html}</td>
<td>{days_label}</td>
</tr>
"""

    table_html = f"""
<div class="table-container">
<table class="table" role="table">
<thead>
<tr>
<th>Descripción</th>
<th>Patente</th>
<th>Documento</th>
<th>Fecha de expiración</th>
<th>Días a expirar</th>
</tr>
</thead>
<tbody>
{html_rows}
</tbody>
</table>
</div>
"""

    st.markdown(table_html, unsafe_allow_html=True)


# -----------------------------
# DASHBOARD general
# -----------------------------
if modo == "Dashboard general":
    st.subheader("Dashboard – Documentación de toda la flota")

    # fetch vehicles and docs
    conn = get_conn()
    cur = conn.cursor()
    docs = cur.execute("""
        SELECT d.id, d.vehiculo_id, d.tipo, d.fecha_vencimiento, v.descripcion, v.ano, v.color, v.patente
        FROM documentos d
        JOIN vehiculos v ON d.vehiculo_id = v.id
        ORDER BY d.fecha_vencimiento ASC
    """).fetchall()

    if not docs:
        st.info("Aún no hay documentos registrados.")
    else:
        # build rows, compute days
        rows = []
        for row in docs:
            iso = row["fecha_vencimiento"]
            try:
                days, dt = days_left_from_iso(iso)
            except Exception:
                # if invalid format, skip
                continue
            rows.append({
                "descripcion": row["descripcion"],
                "ano": row["ano"],
                "color": row["color"],
                "patente": row["patente"],
                "tipo": row["tipo"],
                "fecha_display": dt.isoformat(),
                "days_left": days,
                "vehiculo_id": row["vehiculo_id"],
                "doc_id": row["id"]
            })

        # Filters
        st.markdown("### Filtros")
        cols = st.columns([1,1,1])
        with cols[0]:
            placas = ["Todos"] + sorted(list({r["patente"] for r in rows if r.get("patente")}))
            placa_sel = st.selectbox("Filtrar por vehículo:", placas)
        with cols[1]:
            tipos = ["Todos"] + sorted(list({r["tipo"] for r in rows}))
            tipo_sel = st.selectbox("Filtrar por tipo:", tipos)
        with cols[2]:
            estado_sel = st.selectbox("Filtrar por estado:", ["Todos", "Verde", "Amarillo", "Rojo"])

        # apply filters
        filtered = []
        for r in rows:
            if placa_sel != "Todos" and r["patente"] != placa_sel:
                continue
            if tipo_sel != "Todos" and r["tipo"] != tipo_sel:
                continue
            estado_name, _ = state_from_days(r["days_left"])
            if estado_sel != "Todos" and estado_sel != estado_name:
                continue
            filtered.append(r)

        # render
        render_documents_table(filtered)

# -----------------------------
# Añadir vehículo
# -----------------------------
elif modo == "Añadir vehículo":
    st.subheader("Agregar nuevo vehículo")
    descripcion = st.text_input("Descripción / Modelo")
    ano = st.number_input("Año", min_value=1950, max_value=date.today().year+1, value=date.today().year)
    color = st.text_input("Color")
    patente = st.text_input("Patente")

    if st.button("Guardar vehículo"):
        if not descripcion or not patente:
            st.error("Descripción y patente son obligatorios.")
        else:
            conn = get_conn()
            try:
                conn.execute("INSERT INTO vehiculos (descripcion, ano, color, patente) VALUES (?, ?, ?, ?)",
                             (descripcion, int(ano), color, patente.upper()))
                conn.commit()
                st.success("Vehículo agregado correctamente.")
            except sqlite3.IntegrityError:
                st.error("La patente ya existe.")

# -----------------------------
# Administrar documentos
# -----------------------------
elif modo == "Administrar documentos":
    st.subheader("Gestión de documentos por vehículo")
    conn = get_conn()
    vehs = conn.execute("SELECT id, descripcion, patente FROM vehiculos ORDER BY descripcion").fetchall()
    if not vehs:
        st.warning("Primero agrega vehículos.")
    else:
        veh_map = {f"{v['descripcion']} — {v['patente']}": v["id"] for v in vehs}
        sel = st.selectbox("Selecciona vehículo", [""] + list(veh_map.keys()))
        tipos_docs = ["Permiso de Circulación", "SOAP", "Revisión Técnica"]

        if sel:
            vid = veh_map[sel]
            # show existing docs for that vehicle
            docs = conn.execute("SELECT id, tipo, fecha_vencimiento, notes FROM documentos WHERE vehiculo_id = ?", (vid,)).fetchall()
            st.markdown("### Documentos actuales")
            if not docs:
                st.info("No hay documentos para este vehículo.")
            else:
                # prepare rows for display
                rows = []
                for d in docs:
                    days, dt = days_left_from_iso(d["fecha_vencimiento"])
                    rows.append({
                        "doc_id": d["id"],
                        "tipo": d["tipo"],
                        "fecha": dt.isoformat(),
                        "days": days,
                        "notes": d["notes"] or ""
                    })
                # render as small table with edit buttons
                for r in rows:
                    estado_name, badge_class = state_from_days(r["days"])
                    st.markdown(f"""
                        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f1f5fb">
                          <div>
                            <b>{html.escape(r['tipo'])}</b><br/>
                            <small class="small-note">Vence: <span class="{badge_class}">{r['fecha']}</span> — {human_days_label(r['days'])}</small>
                          </div>
                          <div>
                            <form>
                            </form>
                          </div>
                        </div>
                    """, unsafe_allow_html=True)

            st.markdown("### Agregar / actualizar documento")
            tipo = st.selectbox("Tipo de documento", tipos_docs)
            fecha = st.date_input("Fecha de vencimiento", value=date.today())
            notes = st.text_area("Notas (opcional)")

            if st.button("Guardar documento"):
                # if exists for vehicle+tipo -> update, else insert
                cur = conn.cursor()
                cur.execute("SELECT id FROM documentos WHERE vehiculo_id = ? AND tipo = ?", (vid, tipo))
                exists = cur.fetchone()
                if exists:
                    conn.execute("UPDATE documentos SET fecha_vencimiento = ?, notes = ? WHERE id = ?",
                                 (fecha.isoformat(), notes, exists["id"]))
                    conn.commit()
                    st.success("Documento actualizado.")
                else:
                    conn.execute("INSERT INTO documentos (vehiculo_id, tipo, fecha_vencimiento, notes) VALUES (?, ?, ?, ?)",
                                 (vid, tipo, fecha.isoformat(), notes))
                    conn.commit()
                    st.success("Documento agregado.")

# -----------------------------
# Eliminar vehículo
# -----------------------------
elif modo == "Eliminar vehículo":
    st.subheader("Eliminar vehículo")
    conn = get_conn()
    vehs = conn.execute("SELECT id, descripcion, patente FROM vehiculos ORDER BY descripcion").fetchall()
    if not vehs:
        st.info("No hay vehículos.")
    else:
        sel = st.selectbox("Selecciona vehículo a eliminar", [f"{v['descripcion']} — {v['patente']}" for v in vehs])
        if st.button("Eliminar"):
            # find id
            parts = sel.split("—")
            patente_sel = parts[-1].strip()
            vrow = conn.execute("SELECT id FROM vehiculos WHERE patente = ?", (patente_sel,)).fetchone()
            if vrow:
                conn.execute("DELETE FROM documentos WHERE vehiculo_id = ?", (vrow["id"],))
                conn.execute("DELETE FROM vehiculos WHERE id = ?", (vrow["id"],))
                conn.commit()
                st.success("Vehículo y documentos eliminados.")
            else:
                st.error("No se encontró el vehículo.")

# -----------------------------
# Footer
# -----------------------------
st.markdown("---")
st.caption("Sistema de documentación – SERCOAMB 🚚")
