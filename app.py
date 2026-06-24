import streamlit as st
import openrouteservice
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim 

# --- CONFIGURACIÓN ---
API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImQ5MWI5ZjY4ZmYwZTQ1ZWFhZTliYmJmNjdjNzg5MjRmIiwiaCI6Im11cm11cjY0In0='
client = openrouteservice.Client(key=API_KEY)

st.set_page_config(layout="wide", page_title="Análisis territorial")

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
        
        # Separamos por la coma
        partes = texto_limpio.split(",")
        
        # .strip() borra los espacios en blanco invisibles que queden a los lados
        lat = float(partes[0].strip())
        lon = float(partes[1].strip())
        return lat, lon
    except Exception:
        return None, None

def get_style(feature):
    value = feature['properties'].get('value', 0)
    estilo = st.session_state.estilo

    if estilo == "Profesional":
        if value <= 600: return {'fillColor': '#a8d8ea', 'color': '#2b6c8f', 'weight': 2, 'fillOpacity': 0.35}
        elif value <= 1200: return {'fillColor': '#6cb4d9', 'color': '#1a4a6b', 'weight': 2, 'fillOpacity': 0.35}
        else: return {'fillColor': '#2b8cbe', 'color': '#0d2b47', 'weight': 2, 'fillOpacity': 0.35}

    elif estilo == "Mapa de calor":
        if value <= 600: return {'fillColor': '#e74c3c', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.5}
        elif value <= 1200: return {'fillColor': '#e67e22', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.35}
        else: return {'fillColor': '#f1c40f', 'color': '#2c3e50', 'weight': 1.5, 'fillOpacity': 0.2}

    else:
        return {'fillColor': '#ff0000', 'color': '#000000', 'weight': 3, 'fillOpacity': 0.3}

mapa_modos = {"Auto": "driving-car", "A pie": "foot-walking", "Bicicleta": "cycling-regular"}

# --- 3. LAYOUT ---
col1, col2 = st.columns([1, 3])

with col1:
    st.header("Configuración")
    st.markdown("""
    **Ingresa la ubicación**
    1. Busca el lugar en **Google Maps**.
    2. Haz **clic derecho** sobre el punto exacto.
    3. Haz clic en los números para copiarlos.
    4. Pégalos aquí:
    """)
    
    # LA NUEVA CASILLA ÚNICA DE COPY-PASTE
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
            with st.spinner('Mapeando territorio...'):
                try:
                    # Guardamos oficialmente en la memoria
                    st.session_state.calc_lat = lat_parseada
                    st.session_state.calc_lon = lon_parseada
                    st.session_state.input_raw = texto_ingresado
                    
                    st.session_state.isocrona_data = client.isochrones(
                        locations=[(lon_parseada, lat_parseada)],
                        profile=modo_tecnico,
                        range=[tiempo * 60]
                    )
                    st.session_state.direccion = obtener_direccion_calle(lat_parseada, lon_parseada)
                except Exception as e:
                    st.error(f"Error de API: {e}")
        else:
            st.error("Formato de texto no reconocido. Pega las coordenadas separadas por una coma.")

with col2:
    top_col1, top_col2 = st.columns([3, 1])
    
    with top_col1:
        st.header("Isocrona generada")
        
    with top_col2:
        # Movemos el selectbox aquí arriba
        st.session_state.estilo = st.selectbox(
            "Visualización de capa:", 
            ["Profesional", "Mapa de calor", "Alto contraste"],
            index=["Profesional", "Mapa de calor", "Alto contraste"].index(st.session_state.estilo),
            label_visibility="collapsed" 
        )

    m = folium.Map(location=[st.session_state.calc_lat, st.session_state.calc_lon], zoom_start=14, tiles='CartoDB positron')

    folium.Marker(
        [st.session_state.calc_lat, st.session_state.calc_lon],
        popup="Punto de Origen",
        icon=folium.Icon(color='blue', icon='info-sign')
    ).add_to(m)

    if st.session_state.isocrona_data:
        folium.GeoJson(st.session_state.isocrona_data, style_function=get_style).add_to(m)

    st_folium(m, width=900, height=520)

    st.markdown("""**Ubicación analizada:**""")
    st.caption(f"**{st.session_state.direccion}**")