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


# --- CARGA DE DATOS CENSALES (una sola vez, cacheado) ---
@st.cache_data
def cargar_manzanas():
    # Si el archivo no existe en el servidor de Streamlit, lo descarga de Drive
    if not RUTA_MANZANAS.exists():
        RUTA_MANZANAS.parent.mkdir(parents=True, exist_ok=True)
        id_drive = "https://drive.google.com/file/d/1nM1USy_lmB-tbgvy6JUzS6r23PUZExoj/view?usp=sharing"
        url = f"https://drive.google.com/uc?id={id_drive}"
        gdown.download(url, str(RUTA_MANZANAS), quiet=False)

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

    # Extraer el polígono de la isócrona desde el GeoJSON de ORS
    poligono_iso = shape(isocrona_geojson["features"][0]["geometry"])

    # Calcular centroides de las manzanas
    centroides = gdf_manzanas.geometry.centroid

    # Filtrar manzanas cuyo centroide cae dentro de la isócrona
    mascara = centroides.within(poligono_iso)
    manzanas_dentro = gdf_manzanas[mascara].copy()

    return manzanas_dentro


def calcular_metricas(manzanas_dentro):

    mz_utm = manzanas_dentro.to_crs(epsg=32719)
    area_m2 = mz_utm.geometry.area.sum()

    poblacion = manzanas_dentro["n_per"].sum()
    hogares = manzanas_dentro["n_hog"].sum()
    viviendas = manzanas_dentro["n_vp"].sum()
    prom_escolaridad = manzanas_dentro["prom_escolaridad18"].mean()
    n_manzanas = len(manzanas_dentro)

    return {
        "area_m2": area_m2,
        "area_km2": area_m2 / 1_000_000,
        "poblacion": int(poblacion) if pd.notna(poblacion) else 0,
        "hogares": int(hogares) if pd.notna(hogares) else 0,
        "viviendas": int(viviendas) if pd.notna(viviendas) else 0,
        "prom_escolaridad": round(prom_escolaridad, 1) if pd.notna(prom_escolaridad) else 0,
        "n_manzanas": n_manzanas,
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
            datos[label] = int(manzanas_dentro[col].sum())

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
            datos[label] = int(manzanas_dentro[col].sum())

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


# --- 2. FUNCIONES AUXILIARES (originales) ---
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

                    # 2. Cruzar con manzanas censales
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
    tab1, tab2, tab3 = st.tabs(["Resumen", "Educación y empleo", "Vivienda y territorio"])

    with tab1:
        a, b, c, d = st.columns(4)
        a.metric(label="Población", value=f"{met['poblacion']:,}", border=True)
        b.metric(label="Hogares", value=f"{met['hogares']:,}", border=True)
        c.metric(label="Viviendas", value=f"{met['viviendas']:,}", border=True)
        d.metric(label="Manzanas censales", value=f"{met['n_manzanas']:,}", border=True)

        e, f, g, h = st.columns(4)
        e.metric(label="Área (km²)", value=f"{met['area_km2']:.2f}", border=True)
        f.metric(label="Área (m²)", value=f"{met['area_m2']:,.0f}", border=True)
        g.metric(label="Escolaridad prom. (años)", value=f"{met['prom_escolaridad']}", border=True)
        h.metric(label="Fuente", value="Censo 2024", border=True)

        # Tabla por comuna
        st.markdown("**Desglose por comuna**")
        df_comunas = generar_resumen_comunal(mz_res)
        st.dataframe(df_comunas, use_container_width=True, hide_index=True)

    with tab2:
        col_edu, col_emp = st.columns(2)

        with col_edu:
            st.markdown("**Nivel educativo (CINE)**")
            df_edu = generar_resumen_educacion(mz_res)
            st.dataframe(df_edu, use_container_width=True, hide_index=True)

        with col_emp:
            st.markdown("**Situación laboral**")
            ocupados = int(mz_res["n_ocupado"].sum()) if "n_ocupado" in mz_res.columns else 0
            desocupados = int(mz_res["n_desocupado"].sum()) if "n_desocupado" in mz_res.columns else 0
            fuera = int(mz_res["n_fuera_fuerza_trabajo"].sum()) if "n_fuera_fuerza_trabajo" in mz_res.columns else 0

            df_emp = pd.DataFrame({
                "Categoría": ["Ocupados", "Desocupados", "Fuera fuerza de trabajo"],
                "Personas": [ocupados, desocupados, fuera],
            })
            total_emp = df_emp["Personas"].sum()
            df_emp["% del total"] = (df_emp["Personas"] / total_emp * 100).round(1) if total_emp > 0 else 0
            st.dataframe(df_emp, use_container_width=True, hide_index=True)

    with tab3:
        col_viv, col_terr = st.columns(2)

        with col_viv:
            st.markdown("**Tipo de vivienda**")
            df_viv = generar_resumen_vivienda(mz_res)
            st.dataframe(df_viv, use_container_width=True, hide_index=True)

        with col_terr:
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
                    datos_ten[label] = int(mz_res[col].sum())
            df_ten = pd.DataFrame(list(datos_ten.items()), columns=["Tenencia", "Hogares"])
            total_ten = df_ten["Hogares"].sum()
            df_ten["% del total"] = (df_ten["Hogares"] / total_ten * 100).round(1) if total_ten > 0 else 0
            st.dataframe(df_ten, use_container_width=True, hide_index=True)

else:
    # Estado inicial antes de calcular
    tab1, tab2 = st.tabs(["Resumen", "Información detallada"])
    with tab1:
        st.caption("Calcula una isócrona para ver los datos censales del territorio.")
    with tab2:
        st.caption("Los datos se cargarán automáticamente al calcular una isócrona.")