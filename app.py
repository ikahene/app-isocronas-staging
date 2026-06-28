import streamlit as st
import openrouteservice
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from pathlib import Path
import gdown

# --- CONFIGURACIÓN ---
API_KEY = st.secrets["ORS_KEY"]
client = openrouteservice.Client(key=API_KEY)

# Ruta al parquet de manzanas — AJUSTA si tu estructura es diferente
RUTA_MANZANAS = Path(__file__).parent / "Cartografía_censo2024_R13" / "Cartografía_censo2024_R13_Manzanas.parquet"

st.set_page_config(layout="wide", page_title="Análisis territorial")


# ── Columnas de conteo que se ponderan por fracción de área ──
COLS_CONTEO = [
    # Demografía
    "n_per", "n_hombres", "n_mujeres",
    "n_edad_0_5", "n_edad_6_14", "n_edad_15_24",
    "n_edad_25_44", "n_edad_45_59", "n_edad_60_mas",
    "n_inmigrantes",
    # Educación CINE
    "n_cine_nunca_curso_primera_infancia",
    "n_cine_primaria", "n_cine_secundaria",
    "n_cine_terciaria_maestria_doctorado",
    # Empleo
    "n_ocupado", "n_desocupado", "n_fuera_fuerza_trabajo",
    # Vivienda
    "n_vp", "n_tipo_viv_casa", "n_tipo_viv_depto",
    "n_tipo_viv_pieza", "n_tipo_viv_mediagua",
    "n_tipo_viv_movil", "n_tipo_viv_otro",
    # Hogares y tenencia
    "n_hog",
    "n_tenencia_propia_pagada", "n_tenencia_propia_pagandose",
    "n_tenencia_arrendada_contrato", "n_tenencia_arrendada_sin_contrato",
    "n_tenencia_cedida_trabajo", "n_tenencia_cedida_familiar",
    "n_tenencia_otro",
]

FRACCION_MINIMA = 0.01  

# --- CARGA DE DATOS CENSALES (una sola vez, cacheado) ---
@st.cache_data
def cargar_manzanas():
    # Si el archivo no existe en el servidor de Streamlit, lo descarga de Drive
    if not RUTA_MANZANAS.exists():
        RUTA_MANZANAS.parent.mkdir(parents=True, exist_ok=True)
        
        id_drive = "1nM1USy_lmB-tbgvy6JUzS6r23PUZExoj"
        
        gdown.download(id=id_drive, output=str(RUTA_MANZANAS), quiet=False)

    gdf = gpd.read_parquet(RUTA_MANZANAS)
    gdf["MANZENT"] = gdf["MANZENT"].apply(lambda x: str(int(x)) if pd.notna(x) else None)
    gdf = gdf.to_crs(epsg=4326)
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf

@st.cache_data
def cargar_manzanas_con_datos():
    gdf = cargar_manzanas()
    return gdf[gdf["MZ_BASE_CENSO"] == 1].copy()

def cruzar_isocrona_con_manzanas(isocrona_geojson, gdf_manzanas):
    # 1. Extraer polígono de la isócrona
    poligono_iso = shape(isocrona_geojson["features"][0]["geometry"])

    # 2. Filtro rápido: solo manzanas que intersectan la isócrona
    candidatas = gdf_manzanas[gdf_manzanas.intersects(poligono_iso)].copy()

    if candidatas.empty:
        return candidatas

    # 3. Reproyectar a UTM zona 19S para cálculos de área en metros
    candidatas_utm = candidatas.to_crs(epsg=32719)
    gdf_iso = gpd.GeoDataFrame(geometry=[poligono_iso], crs="EPSG:4326")
    iso_utm = gdf_iso.to_crs(epsg=32719)
    poligono_iso_utm = iso_utm.geometry.iloc[0]

    # 4. Calcular área total de cada manzana
    candidatas_utm["area_manzana_m2"] = candidatas_utm.geometry.area

    # 5. Calcular intersección geométrica y su área
    candidatas_utm["geom_interseccion"] = candidatas_utm.geometry.intersection(poligono_iso_utm)
    candidatas_utm["area_interseccion_m2"] = candidatas_utm["geom_interseccion"].area

    # 6. Calcular fracción de área
    candidatas_utm["fraccion_area"] = (
        candidatas_utm["area_interseccion_m2"] / candidatas_utm["area_manzana_m2"]
    )

    # 7. Filtrar slivers
    resultado = candidatas_utm[candidatas_utm["fraccion_area"] >= FRACCION_MINIMA].copy()

    if resultado.empty:
        return gpd.GeoDataFrame()

    # 8. Clasificar método
    resultado["metodo"] = resultado["fraccion_area"].apply(
        lambda f: "completa" if f >= 0.99 else "parcial"
    )

    # 9. Ponderar columnas de conteo por fracción de área
    cols_presentes = [c for c in COLS_CONTEO if c in resultado.columns]
    for col in cols_presentes:
        resultado[f"{col}_original"] = resultado[col]
        resultado[col] = (resultado[col] * resultado["fraccion_area"]).round(1)

    # 10. Limpiar y volver a EPSG:4326
    resultado = resultado.drop(columns=["geom_interseccion"])
    resultado = resultado.to_crs(epsg=4326)

    return resultado


def calcular_metricas(manzanas_dentro):

    if manzanas_dentro.empty:
        return {
            "area_m2": 0, "area_km2": 0, "poblacion": 0,
            "hogares": 0, "viviendas": 0, "prom_escolaridad": 0,
            "n_manzanas": 0, "n_parciales": 0, "n_completas": 0,
        }

    # Área efectiva: suma de las intersecciones reales
    if "area_interseccion_m2" in manzanas_dentro.columns:
        area_m2 = manzanas_dentro["area_interseccion_m2"].sum()
    else:
        mz_utm = manzanas_dentro.to_crs(epsg=32719)
        area_m2 = mz_utm.geometry.area.sum()

    poblacion = manzanas_dentro["n_per"].sum()
    hogares = manzanas_dentro["n_hog"].sum()
    viviendas = manzanas_dentro["n_vp"].sum()

    if "prom_escolaridad18" in manzanas_dentro.columns and "n_per" in manzanas_dentro.columns:
        mask = manzanas_dentro["prom_escolaridad18"].notna() & (manzanas_dentro["n_per"] > 0)
        sub = manzanas_dentro[mask]
        if len(sub) > 0:
            prom_escolaridad = (
                (sub["prom_escolaridad18"] * sub["n_per"]).sum() / sub["n_per"].sum()
            )
        else:
            prom_escolaridad = 0
    else:
        prom_escolaridad = 0

    n_manzanas = len(manzanas_dentro)
    n_parciales = int((manzanas_dentro["metodo"] == "parcial").sum()) if "metodo" in manzanas_dentro.columns else 0
    n_completas = n_manzanas - n_parciales

    return {
        "area_m2": area_m2,
        "area_km2": area_m2 / 1_000_000,
        "poblacion": int(round(poblacion)) if pd.notna(poblacion) else 0,
        "hogares": int(round(hogares)) if pd.notna(hogares) else 0,
        "viviendas": int(round(viviendas)) if pd.notna(viviendas) else 0,
        "prom_escolaridad": round(prom_escolaridad, 1) if pd.notna(prom_escolaridad) else 0,
        "n_manzanas": n_manzanas,
        "n_parciales": n_parciales,
        "n_completas": n_completas,
    }


def generar_resumen_comunal(manzanas_dentro):
    resumen = manzanas_dentro.groupby("COMUNA").agg(
        poblacion=("n_per", "sum"),
        hogares=("n_hog", "sum"),
        manzanas=("MANZENT", "count"),
        prom_escolaridad=("prom_escolaridad18", "mean"),
        ocupados=("n_ocupado", "sum"),
        inmigrantes=("n_inmigrantes", "sum"),
    ).round(1).sort_values("poblacion", ascending=False).reset_index()

    for col in ["poblacion", "hogares", "ocupados", "inmigrantes"]:
        resumen[col] = resumen[col].round(0).astype(int)

    resumen.columns = ["Comuna", "Población", "Hogares", "Manzanas",
                       "Escolaridad prom.", "Ocupados", "Inmigrantes"]
    return resumen


def generar_resumen_educacion(manzanas_dentro):
    cols_edu = {
        "n_cine_nunca_curso_primera_infancia": "Sin educación formal",
        "n_cine_primaria": "Primaria (CINE)",
        "n_cine_secundaria": "Secundaria (CINE)",
        "n_cine_terciaria_maestria_doctorado": "Superior / Postgrado (CINE)",
    }
    datos = {}
    for col, label in cols_edu.items():
        if col in manzanas_dentro.columns:
            datos[label] = int(round(manzanas_dentro[col].sum()))

    df = pd.DataFrame(list(datos.items()), columns=["Nivel educativo", "Personas"])
    total = df["Personas"].sum()
    df["% del total"] = (df["Personas"] / total * 100).round(1) if total > 0 else 0
    return df


def generar_resumen_vivienda(manzanas_dentro):
    cols_viv = {
        "n_tipo_viv_casa": "Casa",
        "n_tipo_viv_depto": "Departamento",
        "n_tipo_viv_pieza": "Pieza",
        "n_tipo_viv_mediagua": "Mediagua",
        "n_tipo_viv_movil": "Vivienda móvil",
        "n_tipo_viv_otro": "Otro",
    }
    datos = {}
    for col, label in cols_viv.items():
        if col in manzanas_dentro.columns:
            datos[label] = int(round(manzanas_dentro[col].sum()))

    df = pd.DataFrame(list(datos.items()), columns=["Tipo vivienda", "Cantidad"])
    total = df["Cantidad"].sum()
    df["% del total"] = (df["Cantidad"] / total * 100).round(1) if total > 0 else 0
    return df


# --- 1. GESTIÓN DE ESTADO ---
if 'calc_lat' not in st.session_state:
    st.session_state.calc_lat = -33.4489
if 'calc_lon' not in st.session_state:
    st.session_state.calc_lon = -70.6693
if 'isocrona_data' not in st.session_state:
    st.session_state.isocrona_data = None
if 'direccion' not in st.session_state:
    st.session_state.direccion = "Avenida Libertador Bernardo O'Higgins, Santiago"
if 'estilo' not in st.session_state:
    st.session_state.estilo = 'Profesional'
if 'input_raw' not in st.session_state:
    st.session_state.input_raw = "-33.4489, -70.6693"
if 'metricas' not in st.session_state:
    st.session_state.metricas = None
if 'manzanas_resultado' not in st.session_state:
    st.session_state.manzanas_resultado = None


# --- 2. FUNCIONES AUXILIARES ---
@st.cache_data
def obtener_direccion_calle(lat, lon):
    try:
        geolocator = Nominatim(user_agent="app_isocronas_ingenieria")
        location = geolocator.reverse((lat, lon), timeout=3)
        return location.address if location else "Dirección no identificada"
    except Exception:
        return "No se pudo conectar al servidor de calles"


def extraer_lat_lon(texto_google):
    try:
        texto_limpio = texto_google.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
        partes = texto_limpio.split(",")
        lat = float(partes[0].strip())
        lon = float(partes[1].strip())
        return lat, lon
    except Exception:
        return None, None


def get_style(feature):
    value = feature['properties'].get('value', 0)
    estilo = st.session_state.estilo

    if estilo == "Profesional":
        if value <= 600:
            return {'fillColor': '#a8d8ea', 'color': '#2b6c8f', 'weight': 2, 'fillOpacity': 0.35}
        elif value <= 1200:
            return {'fillColor': '#6cb4d9', 'color': '#1a4a6b', 'weight': 2, 'fillOpacity': 0.35}
        else:
            return {'fillColor': '#2b8cbe', 'color': '#0d2b47', 'weight': 2, 'fillOpacity': 0.35}
    elif estilo == "Mapa de calor":
        if value <= 600:
            return {'fillColor': '#e74c3c', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.5}
        elif value <= 1200:
            return {'fillColor': '#e67e22', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.35}
        else:
            return {'fillColor': '#f1c40f', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.2}
    else:
        return {'fillColor': '#ff0000', 'color': '#000000', 'weight': 3, 'fillOpacity': 0.3}


mapa_modos = {"Auto": "driving-car", "A pie": "foot-walking", "Bicicleta": "cycling-regular"}


# --- 3. CARGA INICIAL DE MANZANAS ---
try:
    gdf_manzanas = cargar_manzanas_con_datos()
    datos_cargados = True
except FileNotFoundError:
    datos_cargados = False
    st.warning(
        f"No se encontró el archivo de manzanas en:\n`{RUTA_MANZANAS}`\n\n"
        "Ajusta la variable `RUTA_MANZANAS` al inicio del script."
    )


# --- 4. LAYOUT ---
st.markdown("<h1 style='text-align: center;'>Generador de Isocronas</h1>", unsafe_allow_html=True)
st.divider()

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("Configuración")
    st.info("""
    **Ingresa la ubicación**
    1. Busca el lugar en **Google Maps**.
    2. Haz **clic derecho** sobre el punto exacto.
    3. Haz clic en los números para copiarlos.
    4. Pégalos aquí:
    """)

    texto_ingresado = st.text_input(
        "Coordenadas GPS",
        value=st.session_state.input_raw,
        placeholder="-33.453263, -70.745348"
    )

    tiempo = st.slider("Tiempo de viaje (minutos)", 5, 30, 10)
    opcion_usuario = st.selectbox("Medio de transporte", list(mapa_modos.keys()))
    modo_tecnico = mapa_modos[opcion_usuario]

    if st.button("Calcular Isocrona", type="primary", use_container_width=True):
        lat_parseada, lon_parseada = extraer_lat_lon(texto_ingresado)

        if lat_parseada is not None and lon_parseada is not None:
            with st.spinner(':shimmer[Mapeando territorio...]'):
                try:
                    st.session_state.calc_lat = lat_parseada
                    st.session_state.calc_lon = lon_parseada
                    st.session_state.input_raw = texto_ingresado

                    # 1. Obtener isócrona de ORS
                    st.session_state.isocrona_data = client.isochrones(
                        locations=[(lon_parseada, lat_parseada)],
                        profile=modo_tecnico,
                        range=[tiempo * 60]
                    )
                    st.session_state.direccion = obtener_direccion_calle(lat_parseada, lon_parseada)

                    # 2. Cruzar con manzanas censales (interpolación areal)
                    if datos_cargados:
                        manzanas_dentro = cruzar_isocrona_con_manzanas(
                            st.session_state.isocrona_data, gdf_manzanas
                        )
                        st.session_state.metricas = calcular_metricas(manzanas_dentro)
                        st.session_state.manzanas_resultado = manzanas_dentro

                except Exception as e:
                    st.error(f"Error: {e}")
        else:
            st.error("Formato de texto no reconocido. Pega las coordenadas separadas por una coma.")

    # Indicador de estado de datos
    if datos_cargados:
        st.success(f"Datos censales: {len(gdf_manzanas):,} manzanas RM cargadas")
    else:
        st.error("Sin datos censales — métricas no disponibles")

with col2:
    c_titulo, c_selector, c_vacio = st.columns([3, 1.5, 1])

    with c_titulo:
        st.subheader("Isocrona generada")
    with c_selector:
        st.session_state.estilo = st.selectbox(
            "Visualización de capa:",
            ["Profesional", "Mapa de calor", "Alto contraste"],
            index=["Profesional", "Mapa de calor", "Alto contraste"].index(st.session_state.estilo),
            label_visibility="collapsed"
        )

    m = folium.Map(
        location=[st.session_state.calc_lat, st.session_state.calc_lon],
        zoom_start=14,
        tiles='CartoDB positron'
    )

    folium.Marker(
        [st.session_state.calc_lat, st.session_state.calc_lon],
        popup="Punto de Origen",
        icon=folium.Icon(color='blue', icon='info-sign')
    ).add_to(m)

    if st.session_state.isocrona_data:
        folium.GeoJson(st.session_state.isocrona_data, style_function=get_style).add_to(m)

    st_folium(m, width=900, height=520)

    st.markdown("**Ubicación analizada:**")
    st.caption(f"**{st.session_state.direccion}**")

st.divider()


# --- 5. INFORMACIÓN TERRITORIAL (datos reales del Censo) ---
st.subheader("Información territorial")

met = st.session_state.metricas
mz_res = st.session_state.manzanas_resultado

if met is not None:
    # ── Tab de Resumen y Detalle ──
    tab1, tab2 = st.tabs(["Resumen", "INE"])

    with tab1:
        a, b = st.columns(2)
        a.metric(label="Habitantes", value=f"{met['poblacion']:,}", border=True)
        b.metric(label="Viviendas", value=f"{met['viviendas']:,}", border=True)

        e, f = st.columns(2)
        e.metric(label="Área (km²)", value=f"{met['area_km2']:.2f}", border=True)
        f.metric(label="Gasto total", value=0, border=True)

        # Indicador de interpolación areal
        st.caption(
            f"🔍 {met['n_manzanas']} manzanas analizadas: "
            f"{met['n_completas']} completas + {met['n_parciales']} parciales (interpolación areal)"
        )

        # Tabla por comuna
        st.subheader("Desglose por comuna")
        df_comunas = generar_resumen_comunal(mz_res)
        st.dataframe(df_comunas, use_container_width=True, hide_index=True)

    with tab2:
        options = ["Educación", "Empleo", "Tipo de vivienda", "Propiedad vivienda"]
        info = st.selectbox("¿Qué infomación deseas ver?", options)

        st.divider()

        if info == "Educación":
            st.markdown("**Nivel educativo (CINE)**")
            df_edu = generar_resumen_educacion(mz_res)
            st.dataframe(df_edu, use_container_width=True, hide_index=True)

        elif info == "Empleo":
            st.markdown("**Situación laboral**")
            ocupados = int(round(mz_res["n_ocupado"].sum())) if "n_ocupado" in mz_res.columns else 0
            desocupados = int(round(mz_res["n_desocupado"].sum())) if "n_desocupado" in mz_res.columns else 0
            fuera = int(round(mz_res["n_fuera_fuerza_trabajo"].sum())) if "n_fuera_fuerza_trabajo" in mz_res.columns else 0

            df_emp = pd.DataFrame({
                "Categoría": ["Ocupados", "Desocupados", "Fuera fuerza de trabajo"],
                "Personas": [ocupados, desocupados, fuera],
            })
            total_emp = df_emp["Personas"].sum()
            df_emp["% del total"] = (df_emp["Personas"] / total_emp * 100).round(1) if total_emp > 0 else 0
            st.dataframe(df_emp, use_container_width=True, hide_index=True)

        elif info == "Tipo de vivienda":
            st.markdown("**Tipo de vivienda**")
            df_viv = generar_resumen_vivienda(mz_res)
            st.dataframe(df_viv, use_container_width=True, hide_index=True)

        else:
            st.markdown("**Tenencia de vivienda**")
            cols_ten = {
                "n_tenencia_propia_pagada": "Propia pagada",
                "n_tenencia_propia_pagandose": "Propia pagándose",
                "n_tenencia_arrendada_contrato": "Arrendada con contrato",
                "n_tenencia_arrendada_sin_contrato": "Arrendada sin contrato",
                "n_tenencia_cedida_trabajo": "Cedida por trabajo",
                "n_tenencia_cedida_familiar": "Cedida por familiar",
                "n_tenencia_otro": "Otro",
            }
            datos_ten = {}
            for col, label in cols_ten.items():
                if col in mz_res.columns:
                    datos_ten[label] = int(round(mz_res[col].sum()))
            df_ten = pd.DataFrame(list(datos_ten.items()), columns=["Tenencia", "Hogares"])
            total_ten = df_ten["Hogares"].sum()
            df_ten["% del total"] = (df_ten["Hogares"] / total_ten * 100).round(1) if total_ten > 0 else 0
            st.dataframe(df_ten, use_container_width=True, hide_index=True)

else:
    # Estado inicial antes de calcular
    tab1, tab2 = st.tabs(["", ""])
    with tab1:
        st.caption("Calcula una isócrona para ver los datos censales del territorio.")
    with tab2:
        st.caption("Los datos se cargarán automáticamente al calcular una isócrona.")