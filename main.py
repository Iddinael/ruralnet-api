"""
RuralNet Colombia · Backend v5 · Señal (dBm) + Ancho de banda (Mbps) + leads
===========================================================================
Se despliega gratis en Render.com (ver GUIA-ACTUALIZACION.md).

Endpoints:
  GET  /                -> Prueba rápida: {"estado": "RuralNet API funcionando", ...}
  GET  /calcular        -> Estudio (parámetros lat, lon): dBm y Mbps estimados por operador
  POST /registrar-lead  -> Guarda Nombre + WhatsApp y devuelve el mismo estudio
                           (es el que usa el formulario de WordPress)

Variable de entorno opcional (Render > Environment):
  LEADS_WEBHOOK_URL  URL del Google Apps Script que anota cada lead en Google Sheets.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ruralnet")

LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL", "").strip()
HORA_COLOMBIA = timezone(timedelta(hours=-5))

app = FastAPI(title="RuralNet Colombia API", version="5.0.0")

# CORS global ('*') para que WordPress (o cualquier dominio) pueda consultar la API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ===========================================================================
# 1. PARÁMETROS DE RADIOFRECUENCIA (RF)
# ===========================================================================
POTENCIA_TX_DBM = 45.0         # Potencia de transmisión de la torre rural (≈ 32 W)
GANANCIA_ANTENAS_DB = 12.0     # Ganancia combinada antena torre + antena receptora
DBM_MAX = -50.0                # Señal perfecta (techo físico razonable en el receptor)
DBM_MIN = -120.0               # Zona ciega profunda

# --- Correcciones de entorno rural (sobre el modelo FSPL) -------------------
# El espacio libre (FSPL) supone línea de vista perfecta, sin cerros, árboles ni paredes.
# Con FSPL puro un predio a 30 km recibiría ~-62 dBm (4 barras): irreal en Cundinamarca y Boyacá.
# Estas dos correcciones acercan el resultado a lo que mide un técnico en campo:
#
#   EXPONENTE_RURAL: el FSPL pierde 20 dB por década de distancia (exponente 2).
#       En zona rural con vegetación y relieve la pérdida crece más rápido (2.7 a 4).
#       Se suma 10·(n−2)·log10(d) a partir de 1 km (modelo log-distancia).
#   PERDIDAS_ENTORNO_DB: pérdidas fijas que el FSPL no ve:
#       vegetación/relieve ≈ 12 dB + penetración en vivienda rural ≈ 12 dB + margen de desvanecimiento ≈ 8 dB.
#
# Para usar FSPL puro (sin correcciones): EXPONENTE_RURAL = 2.0 y PERDIDAS_ENTORNO_DB = 0.0
EXPONENTE_RURAL = 3.5
PERDIDAS_ENTORNO_DB = 32.0

# Umbrales de diagnóstico (dBm) -> barras de señal
UMBRAL_4_BARRAS = -75.0        # > -75 dBm  : 4 barras · Excelente
UMBRAL_3_BARRAS = -90.0        # > -90 dBm  : 3 barras · Regular
UMBRAL_2_BARRAS = -105.0       # > -105 dBm : 2 barras · Crítica   |   ≤ -105 dBm: 1 barra · Crítica
UMBRAL_ZONA_CIEGA = -115.0     # ≤ -115 dBm : un amplificador ya no logra una conexión estable


# ===========================================================================
# 2. BASE DE DATOS SIMULADA
# ===========================================================================
# Frecuencia asignada a cada operador
OPERADORES = {
    "Claro":    {"banda": 28, "frecuencia_mhz": 700},
    "Tigo":     {"banda": 4,  "frecuencia_mhz": 2100},
    "Movistar": {"banda": 5,  "frecuencia_mhz": 850},
}

# Kit RuralNet Pro por banda (precios base ESTIMADOS en COP, IVA incluido)
AMPLIFICADORES = {
    28: "Amplificador de señal 4G Banda 28 (700 MHz) · ganancia 70 dB",
    4:  "Amplificador de señal 4G Banda 4 (AWS 2100 MHz) · ganancia 70 dB",
    5:  "Amplificador de señal 4G Banda 5 (850 MHz) · ganancia 70 dB",
}
ROUTER = "Router 5G CPE Wi-Fi 6 con puertos de antena externa"
INSTALACION = "Instalación, alineación y configuración profesional"
ANTENA_YAGI = "Antena exterior direccional Yagi de alta ganancia + 15 m de cable LMR-400"
ANTENA_PANEL = "Antena exterior tipo panel MIMO + 10 m de cable LMR-400"

PRECIO_BASE_KIT = {28: 2_090_000, 5: 2_090_000, 4: 2_290_000}   # amplificador + router + instalación
PRECIO_ANTENA = {"yagi": 320_000, "panel": 220_000}

# Antenas SIMULADAS (ubicaciones ilustrativas, NO son datos reales de los operadores)
TORRES = [
    {"operador": "Claro",    "lat": 4.8640, "lon": -74.0330, "municipio": "Chía"},
    {"operador": "Tigo",     "lat": 4.8590, "lon": -74.0590, "municipio": "Cajicá"},
    {"operador": "Movistar", "lat": 4.8705, "lon": -74.0450, "municipio": "Chía"},
    {"operador": "Claro",    "lat": 5.0255, "lon": -74.0050, "municipio": "Zipaquirá"},
    {"operador": "Tigo",     "lat": 5.0310, "lon": -73.9930, "municipio": "Zipaquirá"},
    {"operador": "Movistar", "lat": 5.0600, "lon": -73.9790, "municipio": "Cogua"},
    {"operador": "Claro",    "lat": 5.3130, "lon": -73.8150, "municipio": "Ubaté"},
    {"operador": "Tigo",     "lat": 5.3070, "lon": -73.8250, "municipio": "Ubaté"},
    {"operador": "Movistar", "lat": 5.2470, "lon": -73.8530, "municipio": "Sutatausa"},
    {"operador": "Claro",    "lat": 5.6180, "lon": -73.8170, "municipio": "Chiquinquirá"},
    {"operador": "Tigo",     "lat": 5.6220, "lon": -73.8230, "municipio": "Chiquinquirá"},
    {"operador": "Movistar", "lat": 5.6150, "lon": -73.8100, "municipio": "Chiquinquirá"},
    {"operador": "Claro",    "lat": 5.6340, "lon": -73.5240, "municipio": "Villa de Leyva"},
    {"operador": "Tigo",     "lat": 5.6000, "lon": -73.4900, "municipio": "Sáchica"},
    {"operador": "Claro",    "lat": 5.5350, "lon": -73.3670, "municipio": "Tunja"},
    {"operador": "Tigo",     "lat": 5.5440, "lon": -73.3570, "municipio": "Tunja"},
    {"operador": "Movistar", "lat": 5.5300, "lon": -73.3610, "municipio": "Tunja"},
    {"operador": "Claro",    "lat": 4.3370, "lon": -74.3640, "municipio": "Fusagasugá"},
    {"operador": "Tigo",     "lat": 4.3420, "lon": -74.3700, "municipio": "Fusagasugá"},
    {"operador": "Movistar", "lat": 4.4050, "lon": -74.3870, "municipio": "Silvania"},
    {"operador": "Claro",    "lat": 4.6310, "lon": -74.4620, "municipio": "La Mesa"},
    {"operador": "Tigo",     "lat": 4.5530, "lon": -74.5360, "municipio": "Anapoima"},
    {"operador": "Claro",    "lat": 5.0060, "lon": -73.4720, "municipio": "Guateque"},
    {"operador": "Movistar", "lat": 5.0820, "lon": -73.3640, "municipio": "Garagoa"},
    {"operador": "Claro",    "lat": 7.1190, "lon": -73.1220, "municipio": "Bucaramanga"},
    {"operador": "Tigo",     "lat": 6.2440, "lon": -75.5810, "municipio": "Medellín"},
    {"operador": "Movistar", "lat": 3.4370, "lon": -76.5220, "municipio": "Cali"},
]


# ===========================================================================
# 3. MATEMÁTICA: GEODESIA + PROPAGACIÓN RF
# ===========================================================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica (gran círculo) en km entre dos puntos. Radio medio terrestre 6371 km."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def calcular_intensidad_dbm(distancia_km: float, frecuencia_mhz: float) -> float:
    """Potencia recibida estimada (dBm) en el predio.

    Modelo base: Pérdida de Trayectoria en Espacio Libre (FSPL), con d en km y f en MHz:
        FSPL(dB) = 20·log10(d) + 20·log10(f) + 32.44

    Presupuesto de enlace:
        P_rx = P_tx (45 dBm) + G_antenas (12 dB) − FSPL − Exceso_rural − Pérdidas_entorno
        Exceso_rural = 10·(n − 2)·log10(d)      (sólo para d > 1 km; 0 si n = 2)

    El resultado se acota a [-120, -50] dBm.
    """
    if frecuencia_mhz <= 0:
        raise ValueError("La frecuencia debe ser mayor que 0 MHz.")
    d = max(distancia_km, 0.05)                      # Evita log10(0) junto a la torre (mín. 50 m)

    fspl_db = 20 * math.log10(d) + 20 * math.log10(frecuencia_mhz) + 32.44
    exceso_rural_db = 10 * (EXPONENTE_RURAL - 2) * math.log10(d) if d > 1 else 0.0

    p_rx = POTENCIA_TX_DBM + GANANCIA_ANTENAS_DB - fspl_db - exceso_rural_db - PERDIDAS_ENTORNO_DB
    return round(max(DBM_MIN, min(DBM_MAX, p_rx)), 1)


def diagnostico(dbm: float) -> dict:
    """Barras de señal + etiqueta comercial + explicación del hardware necesario."""
    valor = f"{dbm:.0f} dBm"
    if dbm > UMBRAL_4_BARRAS:
        return {"barras": 4, "etiqueta": "Excelente", "color": "verde",
                "explicacion": (f"Intensidad excelente de {valor}. La señal llega fuerte: el amplificador "
                                "y el router 5G la distribuyen con velocidad a toda la casa y el predio.")}
    if dbm > UMBRAL_3_BARRAS:
        return {"barras": 3, "etiqueta": "Regular", "color": "amarillo",
                "explicacion": (f"Intensidad regular de {valor}. Dentro de la vivienda la conexión se corta: "
                                "la antena exterior del Kit capta la señal en el techo y el amplificador la estabiliza.")}
    if dbm > UMBRAL_ZONA_CIEGA:
        barras = 2 if dbm > UMBRAL_2_BARRAS else 1
        return {"barras": barras, "etiqueta": "Crítica", "color": "rojo",
                "explicacion": (f"Intensidad crítica de {valor}. El bloqueo por vegetación o distancia requiere "
                                "acoplar la antena Yagi externa de nuestro Kit, apuntada hacia la torre.")}
    return {"barras": 1, "etiqueta": "Crítica", "color": "rojo",
            "explicacion": (f"Zona ciega de {valor}. Un amplificador no puede crear señal donde no llega: "
                            "se requiere un estudio técnico en sitio para evaluar otras alternativas.")}


# Tramos comerciales de ancho de banda: (dBm límite inferior, Mbps mín, Mbps máx)
TRAMOS_MBPS = [
    (-75.0, 60, 90),     # > -75 dBm
    (-90.0, 30, 60),     # -76 a -90 dBm
    (-105.0, 10, 30),    # -91 a -105 dBm
    (-120.0, 1, 9),      # peor que -105 dBm: menos de 10 Mbps
]


def estimar_ancho_banda(dbm: float) -> dict:
    """Ancho de banda comercial estimado (Mbps) que llegará a los equipos del predio.

    Dentro de cada tramo se interpola linealmente: una señal en el borde superior del
    tramo obtiene el máximo y una en el borde inferior, el mínimo.
    """
    techo = DBM_MAX
    for i, (piso, mbps_min, mbps_max) in enumerate(TRAMOS_MBPS):
        # Primer tramo: estrictamente mejor que -75. Los siguientes incluyen su límite (-90, -105).
        dentro = dbm > piso if i == 0 else dbm >= piso
        if dentro or i == len(TRAMOS_MBPS) - 1:
            fraccion = max(0.0, min(1.0, (dbm - piso) / (techo - piso)))
            mbps = round(mbps_min + (mbps_max - mbps_min) * fraccion)
            break
        techo = piso

    if mbps >= 60:
        uso = "¡Ancho de banda ideal para conectar computadoras, Smart TVs y teletrabajo sin caídas gracias a nuestro sistema de antenas MIMO!"
    elif mbps >= 30:
        uso = "Velocidad suficiente para videollamadas, clases virtuales y streaming HD en varios equipos a la vez con la antena MIMO del Kit."
    elif mbps >= 10:
        uso = "Alcanza para WhatsApp, correo, redes sociales y videollamadas; la antena Yagi del Kit estabiliza la conexión para que no se caiga."
    else:
        uso = "Menos de 10 Mbps: esta señal requiere hardware de alta potencia (antena Yagi + amplificador) para estabilizar la conexión."

    return {
        "mbps_estimado": mbps,
        "rango_mbps": f"{mbps_min}-{mbps_max}",
        "rango_texto": "Menos de 10 Mbps" if mbps_max < 10 else f"{mbps_min} a {mbps_max} Mbps",
        "requiere_alta_potencia": mbps_max < 10,
        "mensaje": uso,
    }


def recomendar_kit(mejor: dict) -> dict:
    """Arma el Kit RuralNet Pro según la banda y los dBm del mejor operador."""
    if mejor["dbm"] <= UMBRAL_ZONA_CIEGA:
        return {
            "disponible": False,
            "nombre": "Estudio técnico personalizado en sitio",
            "componentes": [],
            "precio_base": 0,
            "mensaje": ("Ningún operador supera los -115 dBm en tu predio. Un asesor de RuralNet "
                        "revisará tu caso y te propondrá una solución a la medida."),
        }
    tipo_antena = "yagi" if mejor["dbm"] <= UMBRAL_3_BARRAS else "panel"
    banda = mejor["banda"]
    return {
        "disponible": True,
        "nombre": f"Kit RuralNet Pro {mejor['frecuencia_mhz']} MHz" + (" + Yagi" if tipo_antena == "yagi" else ""),
        "componentes": [AMPLIFICADORES[banda], ROUTER,
                        ANTENA_YAGI if tipo_antena == "yagi" else ANTENA_PANEL, INSTALACION],
        "precio_base": PRECIO_BASE_KIT[banda] + PRECIO_ANTENA[tipo_antena],
        "mensaje": (f"Tu mejor señal es {mejor['operador']} con {mejor['dbm_texto']} en "
                    f"Banda {banda} ({mejor['frecuencia_mhz']} MHz), a {mejor['distancia_km']} km: "
                    f"≈ {mejor['ancho_banda']['mbps_estimado']} Mbps estimados para tu predio."),
    }


def estudio_senal(lat: float, lon: float) -> dict:
    """Torre más cercana por operador, dBm estimados, diagnóstico y Kit recomendado."""
    operadores = []
    for nombre, rf in OPERADORES.items():
        torre = min((t for t in TORRES if t["operador"] == nombre),
                    key=lambda t: haversine_km(lat, lon, t["lat"], t["lon"]))
        d = haversine_km(lat, lon, torre["lat"], torre["lon"])
        dbm = calcular_intensidad_dbm(d, rf["frecuencia_mhz"])
        operadores.append({
            "operador": nombre,
            "banda": rf["banda"],
            "frecuencia_mhz": rf["frecuencia_mhz"],
            "banda_texto": f"Banda {rf['banda']} · {rf['frecuencia_mhz']} MHz",
            "distancia_km": round(d, 2),
            "torre_referencia": torre["municipio"],
            "dbm": dbm,
            "dbm_texto": f"{dbm:.0f} dBm",
            "diagnostico": diagnostico(dbm),
            "ancho_banda": estimar_ancho_banda(dbm),
        })

    # Mejor operador = mayor potencia recibida (dBm menos negativo)
    mejor = max(operadores, key=lambda o: o["dbm"])
    return {
        "ubicacion": {"lat": round(lat, 6), "lon": round(lon, 6)},
        "operadores": operadores,
        "mejor_opcion": mejor,
        "kit_recomendado": recomendar_kit(mejor),
        "modelo_rf": {
            "base": "FSPL (espacio libre) + corrección rural log-distancia",
            "potencia_tx_dbm": POTENCIA_TX_DBM,
            "ganancia_antenas_db": GANANCIA_ANTENAS_DB,
            "exponente_rural": EXPONENTE_RURAL,
            "perdidas_entorno_db": PERDIDAS_ENTORNO_DB,
            "ancho_banda": "Tramos comerciales por dBm con interpolación lineal (estimación, no garantizada)",
        },
        "moneda": "COP",
        "aviso": ("Señal y velocidad estimadas por modelo de propagación con antenas de referencia; no son una "
                  "garantía de servicio. La velocidad real depende del relieve, la congestión de la red y tu plan "
                  "de datos. Un técnico de RuralNet mide la señal real antes de instalar."),
    }


def validar_colombia(lat: float, lon: float) -> None:
    if not (-4.3 <= lat <= 13.6 and -82.0 <= lon <= -66.8):
        raise HTTPException(status_code=422, detail="La ubicación seleccionada está fuera de Colombia.")


# ===========================================================================
# 4. ENDPOINTS
# ===========================================================================
@app.get("/")
def inicio():
    return {"estado": "RuralNet API funcionando", "version": app.version, "documentacion": "/docs"}


@app.get("/calcular")
def calcular(
    lat: float = Query(..., ge=-90, le=90, description="Latitud del predio"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud del predio"),
):
    validar_colombia(lat, lon)
    try:
        return estudio_senal(lat, lon)
    except Exception as e:  # noqa: BLE001
        log.exception("Error calculando el estudio")
        raise HTTPException(status_code=500, detail="No pudimos calcular el estudio. Intenta de nuevo.") from e


class Lead(BaseModel):
    nombre: str = Field(..., max_length=120)
    whatsapp: str = Field(..., max_length=20)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    acepta_datos: bool          # Autorización de tratamiento de datos (Ley 1581 de 2012)
    sitio_web: str = ""         # Campo trampa anti-bots: una persona real lo deja vacío

    @field_validator("nombre")
    @classmethod
    def limpiar_nombre(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .-]{3,80}", v):
            raise ValueError("Escribe tu nombre completo (sólo letras).")
        return v.title()

    @field_validator("whatsapp")
    @classmethod
    def limpiar_whatsapp(cls, v: str) -> str:
        digitos = re.sub(r"\D", "", v)
        if digitos.startswith("57") and len(digitos) == 12:
            digitos = digitos[2:]
        if not re.fullmatch(r"3\d{9}", digitos):
            raise ValueError("Escribe un celular colombiano de 10 dígitos que empiece por 3.")
        return "57" + digitos


def enviar_a_google_sheets(fila: dict) -> None:
    """Tarea en segundo plano: envía el lead a Google Sheets sin demorar la respuesta al cliente."""
    if not LEADS_WEBHOOK_URL:
        return
    try:
        req = urllib.request.Request(
            LEADS_WEBHOOK_URL,
            data=json.dumps(fila).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            log.info("Lead guardado en Google Sheets (HTTP %s)", r.status)
    except Exception as e:  # noqa: BLE001 - un fallo aquí nunca debe afectar al cliente
        log.error("No se pudo enviar el lead a Google Sheets: %s", e)


@app.post("/registrar-lead")
def registrar_lead(lead: Lead, tareas: BackgroundTasks):
    if lead.sitio_web:
        raise HTTPException(status_code=400, detail="Solicitud no válida.")
    if not lead.acepta_datos:
        raise HTTPException(status_code=422, detail="Debes autorizar el tratamiento de tus datos.")
    validar_colombia(lead.lat, lead.lon)

    try:
        estudio = estudio_senal(lead.lat, lead.lon)
    except Exception as e:  # noqa: BLE001
        log.exception("Error calculando el estudio del lead")
        raise HTTPException(status_code=500, detail="No pudimos calcular el estudio. Intenta de nuevo.") from e

    ops = {o["operador"]: o["dbm_texto"] for o in estudio["operadores"]}
    mejor, kit = estudio["mejor_opcion"], estudio["kit_recomendado"]
    fila = {
        "fecha": datetime.now(HORA_COLOMBIA).strftime("%Y-%m-%d %H:%M"),
        "nombre": lead.nombre,
        "whatsapp": lead.whatsapp,
        "lat": round(lead.lat, 6),
        "lon": round(lead.lon, 6),
        "mapa": f"https://www.openstreetmap.org/?mlat={lead.lat:.6f}&mlon={lead.lon:.6f}#map=15/{lead.lat:.6f}/{lead.lon:.6f}",
        "claro_dbm": ops.get("Claro", ""),
        "tigo_dbm": ops.get("Tigo", ""),
        "movistar_dbm": ops.get("Movistar", ""),
        "operador": mejor["operador"],
        "senal": mejor["diagnostico"]["etiqueta"],
        "mbps_estimado": mejor["ancho_banda"]["mbps_estimado"],
        "kit": kit["nombre"],
        "precio": kit["precio_base"],
    }
    log.info("NUEVO LEAD: %s", json.dumps(fila, ensure_ascii=False))
    tareas.add_task(enviar_a_google_sheets, fila)

    estudio["cliente"] = {"nombre": lead.nombre}
    return estudio
