import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --------------------
# Config
# --------------------
st.set_page_config(page_title="📦 Punto de Reorden Inteligente", layout="wide")
st.title("📊 Punto de Reorden con Pronóstico e Inventario (sin columnas añadidas)")

# --------------------
# Cargar archivos
# --------------------
st.sidebar.header("📁 Cargar archivos Excel")
inv_file = st.sidebar.file_uploader("Inventario (Excel) — columnas: Producto, Bodega, Inventario_Actual, Stock_Seguridad, Lead_Time", type=["xlsx"])
for_file = st.sidebar.file_uploader("Pronóstico (Excel) — columnas: Fecha, Producto, Bodega, Pronostico_Ventas", type=["xlsx"])

if not (inv_file and for_file):
    st.info("Carga los dos archivos Excel para comenzar (inventario y pronóstico).")
    st.stop()

# Leer y normalizar columnas (minimiza errores por espacios/mayúsculas)
inv = pd.read_excel(inv_file)
forc = pd.read_excel(for_file)

inv.columns = inv.columns.str.strip().str.lower()
forc.columns = forc.columns.str.strip().str.lower()

# Requeridos (en minúsculas)
req_inv = {"producto", "bodega", "inventario_actual", "stock_seguridad", "lead_time"}
req_for = {"fecha", "producto", "bodega", "pronostico_ventas"}

if not req_inv.issubset(set(inv.columns)):
    st.error(f"Archivo de inventario debe contener columnas: {sorted(req_inv)}")
    st.stop()
if not req_for.issubset(set(forc.columns)):
    st.error(f"Archivo de pronóstico debe contener columnas: {sorted(req_for)}")
    st.stop()

# Normalizar tipos
forc["fecha"] = pd.to_datetime(forc["fecha"])
# Agrupar duplicados si existen
inv = inv.groupby(["bodega", "producto"], as_index=False).agg({
    "inventario_actual": "sum",
    "stock_seguridad": "mean",
    "lead_time": "mean"
})
forc = forc.groupby(["bodega", "producto", "fecha"], as_index=False).agg({
    "pronostico_ventas": "sum"
})

# Merge base
base = forc.merge(inv, on=["bodega", "producto"], how="left")
base = base.sort_values(["bodega", "producto", "fecha"]).reset_index(drop=True)

# --------------------
# Session state: historial de compras (lista de dicts)
# --------------------
if "compras" not in st.session_state:
    st.session_state["compras"] = []  # cada elemento: dict con keys: bodega, producto, fecha_compra (Timestamp), fecha_entrega (Timestamp), cantidad

# --------------------
# Helper: reconstruir datos activos aplicando compras (en fecha_entrega)
# --------------------
def build_active(base_df, compras_list):
    df = base_df.copy()
    df["inventario_proyectado"] = np.nan

    # crear dict de entregas por (bodega,producto) -> {fecha_entrega(datetime.date normalized): cantidad_on_that_day}
    entregas = {}
    for c in compras_list:
        key = (c["bodega"], c["producto"])
        fecha_ent = pd.Timestamp(c["fecha_entrega"]).normalize()
        entregas.setdefault(key, {})
        entregas[key][fecha_ent] = entregas[key].get(fecha_ent, 0) + float(c["cantidad"])

    # calcular por grupo sin duplicar entregas (sumar solo lo entregado en la fecha actual)
    for (b, p), grp in df.groupby(["bodega", "producto"]):
        grp = grp.sort_values("fecha").copy()
        inv_temp = float(grp["inventario_actual"].iloc[0]) if not pd.isna(grp["inventario_actual"].iloc[0]) else 0.0
        inv_proj = []
        key = (b, p)
        deliveries_for_key = entregas.get(key, {})

        for idx, row in grp.iterrows():
            fecha_actual = pd.Timestamp(row["fecha"]).normalize()
            recibidos_hoy = deliveries_for_key.get(fecha_actual, 0.0)
            inv_temp += recibidos_hoy
            inv_temp -= float(row["pronostico_ventas"])
            inv_proj.append(inv_temp)

        df.loc[grp.index, "inventario_proyectado"] = inv_proj

    # punto de reorden: stock_seguridad + pronostico_ventas * lead_time (mismo criterio anterior)
    df["punto_reorden"] = df["stock_seguridad"] + df["pronostico_ventas"] * df["lead_time"]
    df["alerta"] = df["inventario_proyectado"] <= df["punto_reorden"]

    return df

# construir datos activos
active = build_active(base, st.session_state["compras"])

# --------------------
# Filtros UI (manteniendo tu interfaz)
# --------------------
st.sidebar.subheader("🔍 Filtros")
bodegas = sorted(active["bodega"].unique())
productos = sorted(active["producto"].unique())

f_bodegas = st.sidebar.multiselect("Selecciona bodegas:", bodegas, default=bodegas)
f_productos = st.sidebar.multiselect("Selecciona productos:", productos, default=productos)

df_filtered = active[active["bodega"].isin(f_bodegas) & active["producto"].isin(f_productos)].copy()
if df_filtered.empty:
    st.warning("No hay datos con los filtros seleccionados.")
    st.stop()

# --------------------
# Registrar compra (para la bodega/producto que elijas)
# --------------------
st.subheader("🛒 Registrar compra (se aplica en fecha de entrega según Lead_Time)")

col1, col2, col3, col4 = st.columns(4)
with col1:
    b_sel = col1.selectbox("Bodega (registro)", sorted(df_filtered["bodega"].unique()))
with col2:
    p_sel = col2.selectbox("Producto (registro)", sorted(df_filtered[df_filtered["bodega"] == b_sel]["producto"].unique()))
with col3:
    fecha_compra = col3.date_input("Fecha de compra", value=df_filtered["fecha"].min().date())
with col4:
    cantidad = col4.number_input("Cantidad a comprar", min_value=1, step=1, value=1)

if st.button("✅ Registrar compra y recalcular"):
    # obtener lead_time desde base (no filtrado)
    lt_mask = (base["bodega"] == b_sel) & (base["producto"] == p_sel)
    if lt_mask.any():
        lt = int(base.loc[lt_mask, "lead_time"].iloc[0])
    else:
        st.error("No se encontró Lead_Time para la combinación seleccionada.")
        st.stop()

    fecha_compra_ts = pd.Timestamp(fecha_compra)
    fecha_entrega = fecha_compra_ts + pd.Timedelta(days=lt)

    # guardar compra
    st.session_state["compras"].append({
        "bodega": b_sel,
        "producto": p_sel,
        "fecha_compra": fecha_compra_ts,
        "fecha_entrega": fecha_entrega,
        "cantidad": float(cantidad)
    })

    # reconstruir datos activos con nueva compra
    active = build_active(base, st.session_state["compras"])
    df_filtered = active[active["bodega"].isin(f_bodegas) & active["producto"].isin(f_productos)].copy()

    st.success(f"Compra registrada: {cantidad} u. de {p_sel} en {b_sel} (entrega {fecha_entrega.date()}).")

# --------------------
# Resumen dinámico (actualiza en base a 'active')
# --------------------
resumen_rows = []
for (b, p), grp in active.groupby(["bodega", "producto"]):
    grp = grp.sort_values("fecha").copy()
    mask_alert = grp["inventario_proyectado"] <= grp["punto_reorden"]
    if mask_alert.any():
        # primera fecha de alerta
        first_idx = mask_alert.idxmax()  # devuelve primer índice donde True
        fecha_reorden = grp.loc[first_idx, "fecha"]
        inv_en_fecha = grp.loc[first_idx, "inventario_proyectado"]
        stock_seg = grp.loc[first_idx, "stock_seguridad"]
        cantidad_sugerida = max(stock_seg * 2 - inv_en_fecha, 0)
    else:
        fecha_reorden = pd.NaT
        cantidad_sugerida = 0.0

    dias = int((pd.to_datetime(fecha_reorden).normalize() - pd.Timestamp.now().normalize()).days) if pd.notna(fecha_reorden) else None
    estado = "🔴 Reorden" if dias is not None and dias <= 0 else ("🟡 Cerca" if dias is not None and dias <= 5 else "🟢 OK")

    resumen_rows.append({
        "Bodega": b,
        "Producto": p,
        "Inventario_Actual": float(grp["inventario_actual"].iloc[0]),
        "Stock_Seguridad": float(grp["stock_seguridad"].iloc[0]),
        "Lead_Time": float(grp["lead_time"].iloc[0]),
        "Fecha_Siguiente_Compra": fecha_reorden if pd.notna(fecha_reorden) else None,
        "Cantidad_Sugerida_Pedir": float(cantidad_sugerida),
        "Días_Hasta_Punto_Reorden": dias,
        "Estado": estado
    })

resumen_df = pd.DataFrame(resumen_rows)

# Mostrar resumen (principal)
st.subheader("📋 Resumen de próximas compras sugeridas (actualizado)")
st.dataframe(resumen_df, use_container_width=True)

# --------------------
# Historial de compras registradas
# --------------------
st.markdown("---")
st.subheader("🧾 Historial de compras simuladas (esta sesión)")
if st.session_state["compras"]:
    compras_df = pd.DataFrame(st.session_state["compras"]).copy()
    # mostrar fechas legibles
    compras_df["fecha_compra"] = pd.to_datetime(compras_df["fecha_compra"]).dt.date
    compras_df["fecha_entrega"] = pd.to_datetime(compras_df["fecha_entrega"]).dt.date
    st.dataframe(compras_df, use_container_width=True)
else:
    st.write("No hay compras registradas en esta sesión.")

# Botón para descargar historial
if st.session_state["compras"]:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        resumen_df.to_excel(writer, index=False, sheet_name="Resumen")
        active.to_excel(writer, index=False, sheet_name="Inventario_Proyectado")
        pd.DataFrame(st.session_state["compras"]).to_excel(writer, index=False, sheet_name="Compras_Simuladas")
    st.download_button(
        label="⬇️ Descargar datos (Resumen + Proyección + Compras)",
        data=buf.getvalue(),
        file_name="resumen_proyeccion_compras.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# --------------------
# Gráfico (usar df_filtered para rendimiento)
# --------------------
st.markdown("---")
st.subheader("📈 Inventario proyectado vs Punto de Reorden (filtrado)")

# Asegurar que df_filtered tenga fechas tipo datetime
df_filtered["fecha"] = pd.to_datetime(df_filtered["fecha"], errors="coerce")

fig = px.line(
    df_filtered,
    x="fecha",
    y="inventario_proyectado",
    color="producto",
    line_shape="spline",
    markers=True,
    facet_col="bodega",
    title="Evolución inventario proyectado (por producto y bodega)"
)

# Añadir líneas de stock por bodega y marcadores de reorden + marcadores de compras entregadas
for bodega in df_filtered["bodega"].unique():
    stock_val = float(df_filtered.loc[df_filtered["bodega"] == bodega, "stock_seguridad"].mean())
    fig.add_hline(y=stock_val, line_dash="dot", line_color="red", annotation_text=f"Stock Seguridad ({bodega})", annotation_position="bottom right")

    # marcar la(s) fecha(s) de reorden por producto en este bodega (si existen en resumen_df)
    filas = resumen_df[resumen_df["Bodega"] == bodega]
    for _, r in filas.iterrows():
        f = r["Fecha_Siguiente_Compra"]
        if f is not None and not (pd.isna(f)):
            # convertir a datetime nativo
            fecha_dt = pd.to_datetime(f)
            fecha_py = fecha_dt.to_pydatetime()
            # tratar de obtener y mostrar valor proyectado en esa fecha (si existe)
            df_match = df_filtered[(df_filtered["bodega"] == bodega) & (df_filtered["producto"] == r["Producto"]) & (df_filtered["fecha"] == fecha_dt)]
            y_val = float(df_match["inventario_proyectado"].iloc[0]) if not df_match.empty else float(r["Stock_Seguridad"])
            # marcador/etiqueta
            fig.add_scatter(x=[fecha_py], y=[y_val], mode="markers+text",
                            marker=dict(color="orange", size=10), text=[f"Reorden {r['Producto']}"],
                            textposition="top center", showlegend=False)

# marcar compras entregadas en gráfico (por filtro)
for c in st.session_state["compras"]:
    # solo mostrar compras para bodegas/productos que están en el filtro
    if c["bodega"] in df_filtered["bodega"].unique() and c["producto"] in df_filtered["producto"].unique():
        fecha_ent = pd.to_datetime(c["fecha_entrega"])
        fecha_py = fecha_ent.to_pydatetime()
        # y_pos: buscar inventario proyectado en esa fecha para mostrar el marcador; si no existe, usar NaN y Plotly lo ignora
        df_m = df_filtered[(df_filtered["bodega"] == c["bodega"]) & (df_filtered["producto"] == c["producto"]) & (df_filtered["fecha"] == fecha_ent)]
        if not df_m.empty:
            y_pos = float(df_m["inventario_proyectado"].iloc[0])
            fig.add_scatter(x=[fecha_py], y=[y_pos], mode="markers+text",
                            marker=dict(color="green", size=10), text=[f"Compra +{int(c['cantidad'])}"],
                            textposition="bottom center", showlegend=False)
        else:
            # si no hay fila exacta, añadir la anotación en la coordenada y = stock para referencia visual
            y_pos = float(df_filtered.loc[df_filtered["bodega"] == c["bodega"], "stock_seguridad"].mean())
            fig.add_scatter(x=[fecha_py], y=[y_pos], mode="markers+text",
                            marker=dict(color="green", size=8), text=[f"Compra +{int(c['cantidad'])}"],
                            textposition="bottom center", showlegend=False)

st.plotly_chart(fig, use_container_width=True)