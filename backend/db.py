"""Acceso a la base de datos.

QUE HACE: guarda lecturas y las vuelve a sacar. Nada mas — no valida, no decide
estados, no calcula niveles. Todo eso pasa en app.py o en la interfaz.

POR QUE SQLITE Y NO INFLUXDB (que es lo que usa el proyecto hermano): un nodo
publicando cada 15 minutos son 96 lecturas por dia, ~35.000 al ano. Una consulta
de 7 dias toca ~700 filas. InfluxDB empieza a ganar en el orden de millones de
puntos, que es exactamente el caso de Casa Rosada (28 nodos cada 2 minutos) y
exactamente no el de aca. El razonamiento largo esta en docs/DESPLIEGUE.md.

La contra honesta: si esto crece a muchos tanques y consultas por rango con
agregaciones, SQLite se queda corto. Se cambia sin tocar ni la interfaz ni el
firmware, porque los dos hablan con la API y no con la base.
"""

import os
import sqlite3

# El esquema completo. Se ejecuta en cada arranque: los CREATE llevan IF NOT
# EXISTS, asi que es idempotente y no hace falta un sistema de migraciones para
# una sola tabla.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS lecturas (
    -- Que nodo la mando. Hoy hay uno solo, pero la columna esta desde el
    -- principio: agregarla despues obligaria a migrar la tabla, y no cuesta
    -- nada tenerla.
    node_id              TEXT    NOT NULL,

    -- Hora de MEDICION, epoch UTC en segundos. Si el reloj del nodo no era
    -- creible, aca queda la hora de recepcion (ver tiempo.normalizar).
    timestamp_unix       INTEGER NOT NULL,

    -- Lo que el nodo dijo que era la hora, sin tocar. Se guarda aunque sea un
    -- disparate: es lo que permite darse cuenta despues de que el nodo estuvo
    -- semanas sin NTP, en vez de tener solo un flag sin contexto.
    timestamp_reportado  INTEGER,

    -- Cuando llego al servidor. Sirve para medir cuanto tarda el nodo en
    -- entregar y para detectar descargas de buffer.
    recibido_unix        INTEGER NOT NULL,

    -- 1/0: ¿el sensor contesto? SQLite no tiene booleano propio.
    valid                INTEGER NOT NULL,

    -- NULL cuando el sensor no midio. Es la medicion CRUDA: el porcentaje y
    -- los litros los calcula la interfaz contra la geometria del tanque.
    distance_cm          REAL,

    rssi_dbm             INTEGER,

    -- 1 si el timestamp reportado era implausible.
    reloj_dudoso         INTEGER NOT NULL DEFAULT 0,

    -- La clave primaria es (nodo, hora de medicion), y esa eleccion tiene un
    -- motivo concreto: el nodo reintenta. Si se cae la red, guarda la lectura
    -- en su buffer y la vuelve a mandar despues, asi que el MISMO instante
    -- puede llegar dos veces. Con esta clave, el reintento cae encima del
    -- original en vez de duplicar el punto y torcer el grafico.
    --
    -- Es el mismo razonamiento por el que en Casa Rosada se acepta que MQTT con
    -- QoS 1 duplique mensajes: InfluxDB sobrescribe el punto que tenga el mismo
    -- measurement+tags+timestamp. Aca lo hace el INSERT OR REPLACE.
    PRIMARY KEY (node_id, timestamp_unix)
);

-- Todas las consultas de la API filtran por rango de tiempo sobre todos los
-- nodos. Sin este indice, cada una recorre la tabla entera.
CREATE INDEX IF NOT EXISTS idx_lecturas_ts ON lecturas (timestamp_unix);
"""


def inicializar(ruta):
    """Crea la base y el esquema. Se llama UNA VEZ, al arrancar la app.

    Esto estaba adentro de `conectar()`, que corre en cada request, y era un
    problema de verdad: sembrar 672 lecturas de prueba tardaba varios minutos
    porque cada POST reabria la base, reejecutaba el script del esquema entero y
    volvia a negociar el modo WAL. Medido despues de separarlo, la misma siembra
    baja a segundos.

    Es idempotente (todos los CREATE llevan IF NOT EXISTS), asi que llamarla en
    cada arranque no rompe nada y evita necesitar un sistema de migraciones para
    una sola tabla.
    """
    # Crear el directorio si hace falta. En Docker la ruta apunta a un volumen
    # montado que ya existe, pero en la notebook es una carpeta suelta y sin
    # esto el primer arranque falla con un "unable to open database file" que
    # no dice que el problema era el directorio.
    carpeta = os.path.dirname(os.path.abspath(ruta))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    conexion = sqlite3.connect(ruta)
    try:
        # WAL (write-ahead logging): permite leer mientras se escribe. Sin esto,
        # un POST del nodo y un GET de la interfaz al mismo tiempo se bloquean
        # entre si. Es una propiedad del ARCHIVO y queda guardada, asi que
        # alcanza con ponerla aca una vez y no en cada conexion.
        conexion.execute("PRAGMA journal_mode=WAL")
        conexion.executescript(ESQUEMA)
        conexion.commit()
    finally:
        conexion.close()


def conectar(ruta):
    """Abre una conexion a una base que YA fue inicializada.

    Barata a proposito: se llama una vez por request, asi que lo unico que hace
    es abrir y configurar la conexion. Si la base no existe todavia, SQLite
    crearia un archivo vacio sin la tabla y las consultas fallarian — por eso
    `inicializar()` corre antes, al crear la app.
    """
    conexion = sqlite3.connect(ruta)

    # Filas como diccionarios en vez de tuplas: `fila["distance_cm"]` en lugar
    # de `fila[5]`, que se rompe en silencio la primera vez que alguien agrega
    # una columna en el medio.
    conexion.row_factory = sqlite3.Row

    # NORMAL en vez de FULL: no espera el fsync del disco en cada escritura.
    # A diferencia de journal_mode, este pragma es POR CONEXION, asi que va aca
    # y no en inicializar(). El riesgo real que agrega es perder las ultimas
    # transacciones ante un corte de luz del servidor — o sea, como mucho una
    # lectura de nivel de agua. A esta escala conviene el intercambio.
    conexion.execute("PRAGMA synchronous=NORMAL")

    return conexion


def guardar_lectura(conexion, lectura):
    """Inserta una lectura, pisando la que tenga el mismo (nodo, timestamp).

    `lectura` es el diccionario ya validado que arma app.py.
    """
    conexion.execute(
        """
        INSERT OR REPLACE INTO lecturas
            (node_id, timestamp_unix, timestamp_reportado, recibido_unix,
             valid, distance_cm, rssi_dbm, reloj_dudoso)
        VALUES (:node_id, :timestamp_unix, :timestamp_reportado, :recibido_unix,
                :valid, :distance_cm, :rssi_dbm, :reloj_dudoso)
        """,
        lectura,
    )
    conexion.commit()


def ultima_lectura(conexion):
    """La lectura mas reciente por hora de MEDICION, o None si no hay ninguna.

    Se ordena por `timestamp_unix` y no por `recibido_unix` a proposito: si un
    nodo descarga su buffer, la ultima en LLEGAR es la mas vieja de la tanda, y
    ordenar por llegada mostraria esa como el estado actual del tanque.
    """
    fila = conexion.execute(
        "SELECT * FROM lecturas ORDER BY timestamp_unix DESC LIMIT 1"
    ).fetchone()
    return dict(fila) if fila else None


def lecturas_desde(conexion, desde_unix):
    """Todas las lecturas con hora de medicion >= `desde_unix`, de vieja a nueva.

    El orden ascendente es parte del contrato con la interfaz: el grafico dibuja
    los puntos en el orden en que vienen, asi que una serie desordenada sale
    como un garabato.
    """
    filas = conexion.execute(
        "SELECT * FROM lecturas WHERE timestamp_unix >= ? ORDER BY timestamp_unix ASC",
        (desde_unix,),
    ).fetchall()
    return [dict(f) for f in filas]


def contar(conexion):
    """Cuantas lecturas hay en total. Lo usa el endpoint de salud."""
    return conexion.execute("SELECT COUNT(*) AS n FROM lecturas").fetchone()["n"]
