"""Validacion del reloj del nodo.

QUE HACE: decide si el `timestamp_unix` que mando el nodo se puede creer, y
cuando no, elige uno usable sin borrar la evidencia de que estaba mal.

POR QUE EXISTE: el ESP32 no tiene RTC con bateria. Si el NTP no sincronizo
antes del primer publish, su reloj cuenta "segundos desde que arranco", asi que
manda un numero chico —842, por ejemplo— que como epoch cae en enero de 1970.

Sin este modulo pasan dos cosas, y las dos son feas:

  1. El grafico se estira hasta 1970 y la serie real se aplasta contra el borde
     derecho, o sea que no se ve nada.
  2. La clave primaria de la tabla es (node_id, timestamp_unix), asi que dos
     arranques del nodo generarian la misma clave y una lectura pisaria a la
     otra.

Es el mismo chequeo que hace `bridge/timestamps.py` en el proyecto hermano Casa
Rosada. Se repite acá porque son dos backends distintos, no porque se haya
copiado sin pensar.
"""

import calendar

# Piso de plausibilidad: nada de este proyecto existia antes de 2020, asi que
# un timestamp anterior es un reloj sin sincronizar y no una lectura vieja.
EPOCH_MINIMO = calendar.timegm((2020, 1, 1, 0, 0, 0))

# Techo: un dia hacia adelante. Un reloj adelantado unos minutos es normal
# (deriva, latencia del NTP); uno adelantado meses esta roto.
MARGEN_FUTURO_SEG = 86400


def es_plausible(timestamp, ahora):
    """True si `timestamp` (epoch UTC en segundos) puede ser una hora real."""
    if timestamp is None:
        return False
    return EPOCH_MINIMO < timestamp < ahora + MARGEN_FUTURO_SEG


def normalizar(timestamp_reportado, recibido_unix):
    """Elige con que hora guardar la lectura.

    Devuelve (timestamp_usable, reloj_dudoso).

    Si el reloj del nodo es creible, se usa tal cual: la hora de MEDICION es el
    dato correcto, y no la de recepcion. La diferencia importa de verdad cuando
    el nodo estuvo sin red y descarga su buffer: esas lecturas llegan todas
    juntas ahora, pero se midieron hace horas, y fecharlas por llegada las
    amontonaria todas en el mismo instante.

    Si no es creible, se guarda la hora de RECEPCION —que es aproximadamente
    correcta para una lectura recien tomada— y se marca `reloj_dudoso`. No se
    descarta la lectura: un nodo sin NTP igual esta midiendo bien el agua, y
    tirar el dato seria perder informacion por un problema que es de otra cosa.

    Lo que NO se hace es esconder el problema. `reloj_dudoso` viaja hasta la
    interfaz, que lo muestra como un estado propio ("el nodo no tiene la hora
    sincronizada"), distinto de "sin datos", porque se arregla distinto. La hora
    de recepcion es un parche para que el grafico funcione, no una correccion:
    una lectura bufferizada fechada asi perdio su hora real para siempre.
    """
    if es_plausible(timestamp_reportado, recibido_unix):
        return timestamp_reportado, False

    return recibido_unix, True
