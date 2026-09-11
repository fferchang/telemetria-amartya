"""Carga la base con lecturas de prueba, publicandolas contra la API real.

    python tools/sembrar.py --dias 7 --nivel-ahora 62

POR QUE EXISTE: para poder probar el backend y la interfaz juntos antes de que
exista el hardware. Es lo que permite que el orden de trabajo del proyecto sea
"backend, despues firmware" y no al reves — ver docs/DESPLIEGUE.md, punto 7.

POR QUE PUBLICA POR HTTP Y NO ESCRIBE LA BASE DIRECTO, que seria mas rapido:
escribiendo directo se saltearia justamente todo lo que hay que probar — la
autenticacion, la validacion del cuerpo, la normalizacion del reloj y el
INSERT OR REPLACE que evita los duplicados. Sembrar por la API es, de hecho,
la prueba de extremo a extremo mas parecida a lo que va a hacer el nodo.

OJO CON EL LIMITE DE TASA: la API acepta 6 POST por minuto por IP, y esto manda
cientos. Hay que levantar el servidor con el limite subido solo para sembrar:

    AMARTYA_MAX_POST_POR_MINUTO=100000 python backend/app.py

y volver a bajarlo despues. Que haya que hacer eso a mano es a proposito: si el
sembrador pudiera saltearse el limite por su cuenta, el limite no seria un
limite.
"""

import argparse
import math
import os
import random
import sys
import time

import requests

# Geometria del tanque. Tiene que coincidir con ui/config.js, porque el nivel
# que se ve en pantalla sale de aplicarle esa geometria a la distancia que se
# siembra aca. Si no coinciden, la demo muestra otro numero del que se pidio.
DISTANCIA_FONDO_CM = 180.0
DISTANCIA_LLENO_CM = 25.0
CAPACIDAD_LITROS = 5000.0

# Forma del ciclo de consumo y llenado, igual que el simulador de ui/datos.js.
CONSUMO_LITROS_POR_DIA = 340.0
LLENO_PCT = 96.0
VACIA_HASTA_PCT = 14.0

SEGUNDOS_POR_DIA = 86400.0


def pct_en(t, ahora, nivel_ahora):
    """Nivel (0-100) en el instante `t`, anclado a `nivel_ahora` en `ahora`.

    Es el mismo modelo que `pctSimulado()` en ui/datos.js: se vacia a ritmo
    constante y se llena de golpe al tocar fondo, que es como funciona de
    verdad — llega el camion o arranca la bomba y el tanque pasa de casi vacio
    a casi lleno en poco tiempo.

    Los extremos del ciclo se corren un punto mas alla de `nivel_ahora` por la
    misma razon que alla: si el piso del ciclo queda EXACTAMENTE en el nivel
    pedido, la fase vale justo una vuelta entera, el modulo la manda a cero y
    cero significa "recien llenado". El instante en que el tanque toca fondo es
    el mismo en que se llena, asi que es ambiguo por construccion.
    """
    lleno = max(LLENO_PCT, nivel_ahora + 1)
    vacia_hasta = min(VACIA_HASTA_PCT, nivel_ahora - 1)

    consumo_pct_por_dia = (CONSUMO_LITROS_POR_DIA / CAPACIDAD_LITROS) * 100
    rango = lleno - vacia_hasta

    fase_ahora = lleno - nivel_ahora
    dias = (t - ahora) / SEGUNDOS_POR_DIA
    fase = (fase_ahora + consumo_pct_por_dia * dias) % rango

    pct = lleno - fase

    # Variacion diaria: se consume mas de mañana y de tarde que de madrugada.
    # Sin esto la curva de 24 h es una recta perfecta y se nota inventada.
    hora = (t % SEGUNDOS_POR_DIA) / 3600.0
    pct -= 0.6 * math.sin(((hora - 4) / 24) * 2 * math.pi)

    return max(0.0, min(100.0, pct))


def distancia_para(pct):
    """Convierte un nivel a la distancia que habria medido el sensor.

    Se siembra la medicion CRUDA y no el porcentaje, igual que hace el
    simulador del frontend: asi la prueba ejercita la misma cadena de calculo
    que va a correr en produccion (distancia -> columna -> porcentaje -> litros).
    """
    altura_util = DISTANCIA_FONDO_CM - DISTANCIA_LLENO_CM
    columna = (pct / 100.0) * altura_util
    # Ruido del ultrasonico: ~+/-0.8 cm sobre agua quieta. No es adorno — es lo
    # que hace que el suavizado del calculo de consumo tenga algo que suavizar.
    return DISTANCIA_FONDO_CM - columna + random.uniform(-0.8, 0.8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # 127.0.0.1 y NO localhost, y la diferencia no es cosmetica: medido, la
    # misma siembra tarda 110 ms por lectura contra 127.0.0.1 y 2160 ms contra
    # localhost. Veinte veces mas.
    #
    # Por que: en Windows, `localhost` resuelve primero a ::1 (IPv6), y el
    # servidor de desarrollo de Flask escucha solo en IPv4. El intento contra
    # ::1 falla, pero el sistema tarda ~2 segundos en darlo por perdido antes de
    # reintentar contra 127.0.0.1. Sembrar 673 lecturas pasaba de 80 segundos a
    # 24 minutos por esto solo.
    #
    # En produccion no existe el problema (nginx escucha en los dos stacks),
    # pero en la notebook se siente muchisimo.
    parser.add_argument("--api", default="http://127.0.0.1:8000/api",
                        help="base de la API (default: %(default)s)")
    parser.add_argument("--dias", type=float, default=7.0,
                        help="cuantos dias de historia sembrar")
    parser.add_argument("--cada", type=int, default=900,
                        help="segundos entre lecturas (default: %(default)s)")
    parser.add_argument("--nivel-ahora", type=float, default=62.0,
                        help="nivel que tiene que quedar al final, en %%")
    parser.add_argument("--nodo", default="cisterna01")
    parser.add_argument("--token", default=os.environ.get("AMARTYA_TOKEN_NODO"),
                        help="por default sale de AMARTYA_TOKEN_NODO")
    argumentos = parser.parse_args()

    if not argumentos.token:
        print("Falta el token. Pasalo con --token o poné AMARTYA_TOKEN_NODO.",
              file=sys.stderr)
        return 1

    ahora = int(time.time())
    desde = ahora - int(argumentos.dias * SEGUNDOS_POR_DIA)
    marcas = list(range(desde, ahora + 1, argumentos.cada))

    print("Sembrando %d lecturas (%.1f dias, una cada %d s) en %s"
          % (len(marcas), argumentos.dias, argumentos.cada, argumentos.api))

    sesion = requests.Session()
    sesion.headers["Authorization"] = "Bearer " + argumentos.token

    enviadas = 0
    for t in marcas:
        cuerpo = {
            "node_id": argumentos.nodo,
            "timestamp_unix": t,
            "valid": True,
            "distance_cm": round(distancia_para(pct_en(t, ahora, argumentos.nivel_ahora)), 1),
            "rssi_dbm": random.randint(-72, -55),
        }

        respuesta = sesion.post(argumentos.api + "/lectura", json=cuerpo, timeout=10)

        if respuesta.status_code == 429:
            print("\nLa API corto por limite de tasa. Levanta el servidor con\n"
                  "  AMARTYA_MAX_POST_POR_MINUTO=100000\n"
                  "solo para sembrar, y volvelo a bajar despues.", file=sys.stderr)
            return 1

        if respuesta.status_code != 201:
            print("\nLa API respondio %d: %s"
                  % (respuesta.status_code, respuesta.text[:200]), file=sys.stderr)
            return 1

        enviadas += 1
        if enviadas % 100 == 0:
            print("  %d/%d" % (enviadas, len(marcas)))

    salud = sesion.get(argumentos.api + "/salud", timeout=10).json()
    print("Listo: %d lecturas enviadas, %d en la base." % (enviadas, salud["lecturas"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
