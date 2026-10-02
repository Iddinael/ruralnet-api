"""
RuralNet · Prepara torres.csv a partir del archivo de OpenCellID de Colombia (MCC 732)
=====================================================================================
Uso (en tu computador, con Python 3):
    python preparar_torres.py 732.csv          # o 732.csv.gz
Genera 'torres.csv' en esta misma carpeta. Súbelo a GitHub junto a main.py.

Qué hace:
  1. Toma sólo Claro (MNC 101), Tigo (103/111) y Movistar (123).
  2. Usa celdas LTE, NR (5G) y UMTS. Los sitios 3G casi siempre tienen 4G en el mismo mástil;
     si quieres sólo 4G/5G, deja TECNOLOGIAS = {"LTE", "NR"}.
  3. Agrupa las celdas (sectores) del mismo operador que caen a menos de ~330 m en un solo sitio,
     ponderando la posición por el número de mediciones.

Datos: OpenCellID (https://opencellid.org), licencia CC BY-SA 4.0. Hay que citar la fuente en la web.
Las posiciones son ESTIMADAS a partir de mediciones de celulares, no la ubicación oficial del mástil.
"""
from __future__ import annotations

import csv
import gzip
import sys
from collections import defaultdict
from datetime import datetime, timezone

MNC_OPERADOR = {"101": "Claro", "103": "Tigo", "111": "Tigo", "123": "Movistar"}
TECNOLOGIAS = {"LTE", "NR", "UMTS"}
PRIORIDAD = {"NR": 3, "LTE": 2, "UMTS": 1}
CELDA_GRADOS = 0.003          # ≈ 330 m: celdas del mismo operador dentro de esta rejilla = un sitio


def main(ruta: str, salida: str = "torres.csv") -> None:
    abrir = gzip.open if ruta.endswith(".gz") else open
    sitios: dict[tuple, dict] = defaultdict(lambda: {"sw": 0.0, "slat": 0.0, "slon": 0.0,
                                                    "celdas": 0, "tec": "UMTS", "upd": 0})
    leidas = usadas = 0
    with abrir(ruta, "rt", encoding="utf-8", newline="") as f:
        for fila in csv.reader(f):
            leidas += 1
            try:
                radio, mcc, mnc = fila[0].upper(), fila[1], fila[2]
                lon, lat = float(fila[6]), float(fila[7])
                muestras = max(int(fila[9] or 1), 1)
                actualizado = int(fila[12] or 0)
            except (ValueError, IndexError):
                continue                                   # encabezado o fila dañada
            if mcc != "732" or mnc not in MNC_OPERADOR or radio not in TECNOLOGIAS:
                continue
            if not (-4.3 <= lat <= 13.6 and -82.0 <= lon <= -66.8):
                continue
            usadas += 1
            op = MNC_OPERADOR[mnc]
            clave = (op, round(lat / CELDA_GRADOS), round(lon / CELDA_GRADOS))
            s = sitios[clave]
            s["sw"] += muestras
            s["slat"] += lat * muestras
            s["slon"] += lon * muestras
            s["celdas"] += 1
            s["upd"] = max(s["upd"], actualizado)
            if PRIORIDAD[radio] > PRIORIDAD[s["tec"]]:
                s["tec"] = radio

    with open(salida, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["operador", "lat", "lon", "tecnologia", "celdas", "muestras", "actualizado"])
        for (op, _, _), s in sorted(sitios.items()):
            fecha = datetime.fromtimestamp(s["upd"], tz=timezone.utc).date().isoformat() if s["upd"] else ""
            w.writerow([op, round(s["slat"] / s["sw"], 5), round(s["slon"] / s["sw"], 5),
                        s["tec"], s["celdas"], int(s["sw"]), fecha])

    por_op = defaultdict(int)
    for (op, _, _) in sitios:
        por_op[op] += 1
    print(f"Filas leídas: {leidas} · celdas usadas: {usadas} · sitios: {len(sitios)} {dict(por_op)}")
    print(f"Archivo generado: {salida}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Uso: python preparar_torres.py 732.csv")
    main(sys.argv[1])
