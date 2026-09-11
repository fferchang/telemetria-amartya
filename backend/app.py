"""La API de Amartya.

QUE HACE: recibe las lecturas del nodo, las guarda, y se las sirve a la
interfaz. Tres endpoints de datos y uno de salud, nada mas.

    POST /api/lectura              lo que manda el nodo   (necesita token)
    GET  /api/estado               la ultima lectura      (publico)
    GET  /api/historico?horas=N    la serie               (publico)
    GET  /api/salud                para el healthcheck    (publico)

LO QUE ESTE ARCHIVO DELIBERADAMENTE NO HACE:

  - No calcula porcentajes ni litros. Eso sale de la geometria del tanque, que
    es configuracion de cada instalacion y vive en ui/config.js. Si el backend
    devolviera el porcentaje, cambiar la altura del tanque obligaria a
    redeployar el backend en vez de editar un archivo.
  - No evalua umbrales ni dispara alertas. La interfaz decide que es "bajo"
    contra su propia config. Es la misma decision que se tomo en Casa Rosada,
    donde un motor de alertas en Python se escribio y se descarto.

POR QUE FLASK Y NO http.server DE LA BIBLIOTECA ESTANDAR: por una sola cosa que
no se ve hasta que duele — el ruteo, el parseo del cuerpo, los codigos de error
y el manejo de excepciones escritos a mano son como 200 lineas que hay que
mantener y que no son el problema de este proyecto. Flask es una dependencia,
esta documentada en todos lados (que importa para poder buscar cuando algo
falla) y cualquier hosting sabe correrla.
"""

import hmac
import os
import sqlite3
import sys
import time
from collections import defaultdict, deque
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory

import config as config_mod
import db
import tiempo

# Carpeta con la interfaz, un nivel arriba de esta. La sirve el propio Flask
# cuando se corre en local (ver el comentario de la ruta estatica, abajo).
CARPETA_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")


# ===========================================================================
# Validacion de lo que manda el nodo
# ===========================================================================

def _numero_o_nulo(valor):
    """Convierte a float, o None si no hay valor o no es numero.

    El `is None` explicito de la primera linea es importante y no es defensivo
    porque si: en la interfaz, el equivalente de esta funcion tenia un bug que
    convertia `null` en 0, y cero centimetros de distancia al agua significa
    tanque LLENO. Un sensor roto se mostraba como la mejor noticia posible. Aca
    la trampa es la misma (`float(None)` revienta, pero `float(False)` da 0.0).
    """
    if valor is None or isinstance(valor, bool):
        return None
    if not isinstance(valor, (int, float)):
        return None
    return float(valor)


def validar_lectura(cuerpo, recibido_unix):
    """Convierte el JSON del nodo en la fila que espera la base.

    Devuelve (lectura, None) si esta bien, o (None, "motivo") si no.

    Se valida con la puerta bien cerrada porque este es el unico endpoint que
    ESCRIBE, y lo que entre aca se va a mostrar despues como si fuera verdad.
    """
    if not isinstance(cuerpo, dict):
        return None, "el cuerpo tiene que ser un objeto JSON"

    # --- node_id ---
    node_id = cuerpo.get("node_id")
    if not isinstance(node_id, str) or not node_id.strip():
        return None, "falta node_id"
    node_id = node_id.strip()
    if len(node_id) > 64:
        return None, "node_id es demasiado largo (maximo 64 caracteres)"

    # --- timestamp ---
    # Se acepta un numero y se trunca a entero: algunos clientes mandan el
    # epoch como float. Lo que NO se acepta es un string con un numero adentro,
    # para que un nodo mal programado falle ruidosamente y no en silencio.
    ts_crudo = cuerpo.get("timestamp_unix")
    if isinstance(ts_crudo, bool) or not isinstance(ts_crudo, (int, float)):
        return None, "falta timestamp_unix, o no es un numero"
    timestamp_reportado = int(ts_crudo)

    # --- valid ---
    valid = cuerpo.get("valid", True)
    if not isinstance(valid, bool):
        return None, "valid tiene que ser true o false"

    # --- distancia ---
    distancia = _numero_o_nulo(cuerpo.get("distance_cm"))

    # Red de seguridad sobre el rango fisico del sensor.
    #
    # Si la distancia esta fuera de lo que cualquier ultrasonico puede medir, la
    # lectura se marca invalida PERO SE GUARDA IGUAL, con su valor crudo. Es la
    # misma regla que en Casa Rosada: una lectura invalida es informacion sobre
    # el equipo, no basura. Tirarla dejaria a quien tiene que arreglar el nodo
    # sin el dato que necesita para entender que estaba pasando.
    if distancia is not None:
        fuera_de_rango = not (
            config_mod.DISTANCIA_MINIMA_CM <= distancia <= config_mod.DISTANCIA_MAXIMA_CM
        )
        if fuera_de_rango:
            valid = False

    rssi = _numero_o_nulo(cuerpo.get("rssi_dbm"))

    timestamp_usable, reloj_dudoso = tiempo.normalizar(timestamp_reportado, recibido_unix)

    return {
        "node_id": node_id,
        "timestamp_unix": timestamp_usable,
        "timestamp_reportado": timestamp_reportado,
        "recibido_unix": recibido_unix,
        "valid": 1 if valid else 0,
        "distance_cm": distancia,
        "rssi_dbm": int(rssi) if rssi is not None else None,
        "reloj_dudoso": 1 if reloj_dudoso else 0,
    }, None


# ===========================================================================
# Submuestreo del historico
# ===========================================================================

def submuestrear(puntos, maximo, desde_unix, hasta_unix):
    """Baja la cantidad de puntos a `maximo` promediando por cubetas de tiempo.

    Con las ventanas que usa la interfaz esto no se activa nunca: 7 dias a una
    lectura cada 15 minutos son 672 puntos y el tope es 700. Existe para que un
    `?horas=8760` no intente mandar un ano entero de lecturas al telefono de
    alguien con senal de campo.

    SE AGRUPA POR TIEMPO Y NO CADA N PUNTOS, que es lo primero que uno escribe.
    La diferencia aparece cuando el nodo estuvo caido: agrupando de a N, los
    puntos de antes y de despues del hueco caen en la misma cubeta y se
    promedian entre si, o sea que el bajon se dibuja como una rampa suave y el
    hueco desaparece. Agrupando por tiempo, las cubetas del hueco quedan vacias
    y se saltean, asi que el hueco se sigue viendo.

    La contra de promediar: suaviza un poco el escalon del llenado. A las
    escalas de este proyecto la cubeta es de dos o tres lecturas, asi que el
    escalon se corre unos minutos y nada mas. A cambio, promediar cancela el
    ruido de +/-1 cm del ultrasonico.
    """
    if len(puntos) <= maximo:
        return puntos

    ancho = (hasta_unix - desde_unix) / float(maximo)
    if ancho <= 0:
        return puntos[-maximo:]

    cubetas = defaultdict(list)
    for p in puntos:
        indice = int((p["timestamp_unix"] - desde_unix) / ancho)
        cubetas[indice].append(p)

    resultado = []
    for indice in sorted(cubetas):
        grupo = cubetas[indice]
        medibles = [p for p in grupo if p["valid"] and p["distance_cm"] is not None]

        if medibles:
            resultado.append({
                "timestamp_unix": int(
                    sum(p["timestamp_unix"] for p in medibles) / len(medibles)
                ),
                "distance_cm": sum(p["distance_cm"] for p in medibles) / len(medibles),
                "valid": 1,
            })
        else:
            # Cubeta sin ninguna medicion util: se emite igual, invalida. Si se
            # salteara, un tramo con el sensor caido se veria como una linea
            # recta entre los dos extremos sanos — o sea, inventada.
            resultado.append({
                "timestamp_unix": grupo[-1]["timestamp_unix"],
                "distance_cm": None,
                "valid": 0,
            })

    return resultado


# ===========================================================================
# La aplicacion
# ===========================================================================

def crear_app(cfg=None, servir_ui=True):
    """Arma la app.

    Es una factory y no un `app = Flask(...)` suelto para que los tests puedan
    crear una instancia con su propia base y su propio token, sin variables de
    entorno y sin que una prueba se lleve puesta a la siguiente.
    """
    app = Flask(__name__, static_folder=None)
    app.config["AMARTYA"] = cfg if cfg is not None else config_mod.cargar()

    # Estado del limitador de tasa, POR INSTANCIA de app y no a nivel de modulo:
    # a nivel de modulo, dos tests seguidos compartirian el contador y el
    # segundo fallaria por lo que hizo el primero.
    app.config["POSTS_RECIENTES"] = defaultdict(deque)

    # El esquema se crea UNA VEZ, aca, y no en cada request. Ver el comentario
    # de db.inicializar(): tenerlo adentro de la conexion por request hacia que
    # cada POST reejecutara el script entero, y eso convertia una siembra de 672
    # lecturas en varios minutos.
    db.inicializar(app.config["AMARTYA"]["base_datos"])

    # -----------------------------------------------------------------------
    # Conexion a la base, una por request
    # -----------------------------------------------------------------------

    def conexion():
        """La conexion de ESTE request, creandola la primera vez que se pide.

        Una por request y no una global porque las conexiones de sqlite3 no se
        pueden compartir entre hilos, y el servidor atiende con varios.
        """
        if "db" not in g:
            g.db = db.conectar(app.config["AMARTYA"]["base_datos"])
        return g.db

    @app.teardown_appcontext
    def cerrar_conexion(_excepcion):
        con = g.pop("db", None)
        if con is not None:
            con.close()

    # -----------------------------------------------------------------------
    # Autenticacion del nodo
    # -----------------------------------------------------------------------

    def requiere_token(vista):
        """Exige el token del nodo en el header Authorization.

        Solo se aplica al POST. Los GET son publicos a proposito (ver el README),
        pero escribir no: sin esto, cualquiera que encuentre la URL puede
        inyectar un nivel de 95% inventado, y un numero falso es peor que no
        tener el sistema porque nadie va a ir a mirar el tanque.
        """
        @wraps(vista)
        def envoltorio(*args, **kwargs):
            encabezado = request.headers.get("Authorization", "")
            prefijo = "Bearer "
            recibido = encabezado[len(prefijo):] if encabezado.startswith(prefijo) else ""

            # compare_digest y no `==`: la comparacion normal de strings corta
            # apenas encuentra el primer caracter distinto, asi que tarda un
            # poquito mas cuanto mas largo sea el prefijo acertado. Midiendo esa
            # diferencia se puede adivinar el token de a un caracter por vez.
            # compare_digest tarda lo mismo siempre.
            if not hmac.compare_digest(recibido, app.config["AMARTYA"]["token_nodo"]):
                # 401 sin pistas: no se aclara si falto el header o si el token
                # estaba mal, porque eso le ahorraria trabajo a quien pruebe.
                return jsonify({"error": "no autorizado"}), 401

            return vista(*args, **kwargs)

        return envoltorio

    def ip_del_cliente():
        """Quien esta haciendo el pedido, para contarlo en el limitador.

        Detras de nginx, `request.remote_addr` es la IP DEL PROXY para todas las
        requests, asi que el limitador contaria a todo el mundo en una sola
        bolsa y un solo cliente haciendo ruido dejaria afuera al nodo de verdad.
        nginx manda la IP real en X-Real-IP.

        Ese header solo se mira si la config dice que hay un proxy adelante (ver
        `detras_de_proxy` en config.py): el header lo puede falsificar cualquiera
        que le pegue directo a la API, asi que creerle siempre convertiria al
        limitador en decorativo.
        """
        if app.config["AMARTYA"]["detras_de_proxy"]:
            real = request.headers.get("X-Real-IP")
            if real:
                return real.strip()
        return request.remote_addr or "desconocido"

    def permitir_post(clave):
        """Limitador de tasa: ¿cuantos POST hizo esta clave en el ultimo minuto?

        Ventana deslizante simple. Es en memoria, asi que con varios workers
        cada uno lleva su propia cuenta y el limite efectivo se multiplica por
        la cantidad de workers. Esta bien para lo que tiene que hacer: frenar
        un bucle de reintentos mal hecho o a alguien tanteando. La defensa de
        verdad contra un abuso es el token, no esto.
        """
        maximo = app.config["AMARTYA"]["max_post_por_minuto"]
        cola = app.config["POSTS_RECIENTES"][clave]
        ahora = time.time()

        while cola and ahora - cola[0] > 60:
            cola.popleft()

        if len(cola) >= maximo:
            return False

        cola.append(ahora)
        return True

    # -----------------------------------------------------------------------
    # Endpoints
    # -----------------------------------------------------------------------

    @app.post("/api/lectura")
    @requiere_token
    def post_lectura():
        """Recibe una lectura del nodo."""
        # El limite se cuenta por IP y no por node_id: el node_id sale del
        # cuerpo, o sea que lo elige quien llama, y alguien que quisiera
        # saturar la base podria mandar uno distinto cada vez.
        if not permitir_post(ip_del_cliente()):
            return jsonify({"error": "demasiadas lecturas seguidas"}), 429

        # silent=True para que un cuerpo que no es JSON devuelva un 400 nuestro
        # y explicado, en vez del 400 generico de Flask.
        cuerpo = request.get_json(silent=True)
        lectura, error = validar_lectura(cuerpo, int(time.time()))
        if error:
            return jsonify({"error": error}), 400

        db.guardar_lectura(conexion(), lectura)

        # 201 Created, con el flag del reloj de vuelta. El nodo no lo usa hoy,
        # pero es informacion que ya tenemos y que le permitiria, si alguna vez
        # se quiere, darse cuenta de que su reloj esta mal.
        return jsonify({"guardado": True, "reloj_dudoso": bool(lectura["reloj_dudoso"])}), 201

    @app.get("/api/estado")
    def get_estado():
        """La ultima lectura conocida."""
        fila = db.ultima_lectura(conexion())

        # Base vacia. Es un estado real —recien instalado, el nodo todavia no
        # publico— y NO un error: devolver 404 o 500 haria que la interfaz
        # muestre "sin conexion", que es falso y manda a revisar la red cuando
        # el problema esta en el nodo. La interfaz lo muestra como "todavia no
        # llego ninguna lectura".
        if fila is None:
            return jsonify({"sin_lecturas": True})

        return jsonify({
            "node_id": fila["node_id"],
            "timestamp_unix": fila["timestamp_unix"],
            "valid": bool(fila["valid"]),
            "distance_cm": fila["distance_cm"],
            "rssi_dbm": fila["rssi_dbm"],
            # Solo /estado manda este flag. Ver el comentario de /historico.
            "reloj_dudoso": bool(fila["reloj_dudoso"]),
        })

    @app.get("/api/historico")
    def get_historico():
        """La serie de las ultimas `horas` horas."""
        cfg_app = app.config["AMARTYA"]

        try:
            horas = float(request.args.get("horas", 24))
        except ValueError:
            return jsonify({"error": "horas tiene que ser un numero"}), 400

        if horas <= 0:
            return jsonify({"error": "horas tiene que ser mayor que cero"}), 400

        # Techo a la ventana. Sin esto, un `?horas=999999999` hace que el
        # servidor intente traer y serializar la tabla entera.
        horas = min(horas, cfg_app["max_horas_historico"])

        hasta = int(time.time())
        desde = hasta - int(horas * 3600)

        crudos = db.lecturas_desde(conexion(), desde)
        puntos = submuestrear(crudos, cfg_app["max_puntos_historico"], desde, hasta)

        # OJO: aca NO va `reloj_dudoso`, y es a proposito.
        #
        # Una lectura con el reloj dudoso quedo guardada con su hora de
        # RECEPCION, que es una hora perfectamente usable para ubicarla en el
        # grafico. El flag sirve para el dato ACTUAL —donde la pregunta es "¿de
        # cuando es este numero?"— y no para la serie. Si viajara aca, la
        # interfaz filtraria esos puntos del grafico y un tramo entero de
        # historia real desapareceria por un problema que es de otra cosa.
        return jsonify({
            "puntos": [
                {
                    "timestamp_unix": p["timestamp_unix"],
                    "distance_cm": p["distance_cm"],
                    "valid": bool(p["valid"]),
                }
                for p in puntos
            ]
        })

    @app.get("/api/salud")
    def get_salud():
        """Healthcheck. Lo usa Docker para saber si el contenedor esta vivo.

        Toca la base a proposito: un proceso que responde HTTP pero no puede
        leer su base esta caido a todos los efectos practicos, y un healthcheck
        que solo devuelve "ok" no se entera.
        """
        try:
            total = db.contar(conexion())
        except sqlite3.Error as error:
            return jsonify({"ok": False, "error": str(error)}), 503

        return jsonify({"ok": True, "lecturas": total})

    # -----------------------------------------------------------------------
    # La interfaz, servida por el mismo proceso (solo para desarrollo local)
    # -----------------------------------------------------------------------
    #
    # En produccion esto lo hace nginx, que para servir archivos estaticos es
    # mucho mejor que Python. Aca existe por una razon concreta de desarrollo:
    # con la interfaz en un puerto y la API en otro, el navegador las considera
    # origenes distintos y bloquea los fetch por CORS. Sirviendo las dos cosas
    # desde el mismo puerto, ese problema no existe — y de paso levantar todo
    # es un comando en vez de dos.
    if servir_ui:
        @app.get("/")
        def index():
            return send_from_directory(CARPETA_UI, "index.html")

        @app.get("/<path:archivo>")
        def estaticos(archivo):
            return send_from_directory(CARPETA_UI, archivo)

    return app


def main():
    """Arranque para desarrollo local."""
    try:
        cfg = config_mod.cargar()
    except config_mod.ConfigInvalida as error:
        # A stderr y con codigo 1, para que si esto corre dentro de un script el
        # script se entere de que fallo.
        print("\n" + str(error) + "\n", file=sys.stderr)
        return 1

    app = crear_app(cfg)

    puerto = int(os.environ.get("PORT", 8000))

    # Las direcciones se imprimen con 127.0.0.1 y no con localhost a proposito.
    #
    # En Windows, `localhost` resuelve primero a ::1 (IPv6) y este servidor
    # escucha solo en IPv4, asi que cada pedido se come ~2 segundos esperando
    # que falle el intento por IPv6 antes de reintentar. Medido: 110 ms por
    # request contra 127.0.0.1, 2160 ms contra localhost. Los navegadores lo
    # disimulan bastante, pero cualquier script (curl, requests) lo sufre
    # entero. En produccion no pasa: nginx escucha en los dos stacks.
    print("Amartya escuchando en http://127.0.0.1:%d" % puerto)
    print("  interfaz  ->  http://127.0.0.1:%d/" % puerto)
    print("  API       ->  http://127.0.0.1:%d/api/estado" % puerto)
    print("  base      ->  %s" % cfg["base_datos"])
    print("\n  (usa 127.0.0.1 y no localhost: en Windows, localhost intenta")
    print("   primero por IPv6 y agrega ~2 segundos a cada pedido)")

    # debug=False incluso en local: con debug=True, un error devuelve una
    # consola interactiva de Python por HTTP. Si el puerto llega a quedar
    # expuesto, eso es ejecucion remota de codigo servida en bandeja.
    app.run(host="0.0.0.0", port=puerto, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
