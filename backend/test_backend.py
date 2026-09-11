"""Verificaciones del backend.

Se corre con:   .venv/Scripts/python -m pytest backend/ -v
(o `python -m pytest backend/ -v` si el venv ya esta activado)

QUE SE VERIFICA ACA Y POR QUE: casi todos estos casos son fallas que no se ven
como fallas. Un sensor caido que se guarda como tanque lleno, una lectura
duplicada que tuerce el grafico, un nodo sin NTP que manda toda la serie a
1970. Ninguna de esas tira un error: dejan la pantalla andando y mostrando otra
cosa, que es la unica categoria de bug que importa de verdad en un sistema que
la gente mira para decidir si pedir agua.
"""

import time

import pytest

import app as app_mod
import config as config_mod
import tiempo


# ===========================================================================
# Andamiaje
# ===========================================================================

TOKEN = "token-de-prueba-largo-y-aburrido"


def config_de_prueba(tmp_path, **extra):
    """Config para los tests, armada con el `cargar()` de verdad.

    Podria ser un diccionario escrito a mano, y esa fue la primera version — el
    problema es que queda como una copia paralela de la config real que hay que
    acordarse de actualizar. Agregar una clave nueva con su default (paso
    exactamente eso con `detras_de_proxy`) rompia diecisiete tests con un
    `KeyError` que no tenia nada que ver con lo que estaban probando.

    Pasando un entorno falso a `cargar()`, cualquier clave nueva llega sola con
    su default, y de paso los tests ejercitan el mismo camino de configuracion
    que corre en produccion.
    """
    entorno = {"AMARTYA_TOKEN_NODO": TOKEN}
    entorno.update(extra)

    cfg = config_mod.cargar(entorno)
    # La base no sale del entorno: cada test necesita la suya.
    cfg["base_datos"] = str(tmp_path / "prueba.db")
    return cfg


@pytest.fixture
def cliente(tmp_path):
    """Una app con base propia y token conocido.

    `tmp_path` da un directorio nuevo por test, asi que ninguna prueba ve los
    datos de la anterior. Sin eso, el test de "base vacia" pasaria o fallaria
    segun el orden en que corran.
    """
    aplicacion = app_mod.crear_app(config_de_prueba(tmp_path), servir_ui=False)
    aplicacion.config["TESTING"] = True
    return aplicacion.test_client()


def publicar(cliente, token=TOKEN, **campos):
    """Manda un POST /api/lectura con valores por default razonables."""
    cuerpo = {
        "node_id": "cisterna01",
        "timestamp_unix": int(time.time()),
        "valid": True,
        "distance_cm": 84.3,
        "rssi_dbm": -61,
    }
    cuerpo.update(campos)
    return cliente.post(
        "/api/lectura",
        json=cuerpo,
        headers={"Authorization": "Bearer " + token},
    )


# ===========================================================================
# Configuracion
# ===========================================================================

def test_sin_token_no_arranca():
    """Arrancar sin token dejaria el endpoint de escritura abierto."""
    with pytest.raises(config_mod.ConfigInvalida) as error:
        config_mod.cargar({})
    assert "AMARTYA_TOKEN_NODO" in str(error.value)


def test_token_corto_no_arranca():
    with pytest.raises(config_mod.ConfigInvalida):
        config_mod.cargar({"AMARTYA_TOKEN_NODO": "corto"})


def test_numero_invalido_en_el_entorno_da_error_entendible():
    with pytest.raises(config_mod.ConfigInvalida) as error:
        config_mod.cargar({
            "AMARTYA_TOKEN_NODO": TOKEN,
            "AMARTYA_MAX_POST_POR_MINUTO": "muchos",
        })
    assert "AMARTYA_MAX_POST_POR_MINUTO" in str(error.value)


# ===========================================================================
# Reloj
# ===========================================================================

def test_reloj_creible_se_respeta():
    """La hora de MEDICION es el dato correcto cuando se puede creer."""
    ahora = int(time.time())
    medido = ahora - 3600
    usable, dudoso = tiempo.normalizar(medido, ahora)
    assert usable == medido
    assert dudoso is False


def test_reloj_sin_ntp_cae_en_1970_y_se_reemplaza():
    """Lo que manda un ESP32 sin NTP: segundos desde el arranque."""
    ahora = int(time.time())
    usable, dudoso = tiempo.normalizar(842, ahora)
    assert usable == ahora
    assert dudoso is True


def test_reloj_muy_adelantado_tambien_es_dudoso():
    ahora = int(time.time())
    usable, dudoso = tiempo.normalizar(ahora + 400 * 86400, ahora)
    assert usable == ahora
    assert dudoso is True


# ===========================================================================
# Autenticacion y limite de tasa
# ===========================================================================

def test_publicar_sin_token_da_401(cliente):
    respuesta = cliente.post("/api/lectura", json={"node_id": "x", "timestamp_unix": 1})
    assert respuesta.status_code == 401


def test_publicar_con_token_equivocado_da_401(cliente):
    assert publicar(cliente, token="token-equivocado-pero-largo").status_code == 401


def test_leer_no_necesita_token(cliente):
    """Los GET son publicos a proposito; los que se cierran son los POST."""
    assert cliente.get("/api/estado").status_code == 200
    assert cliente.get("/api/historico").status_code == 200


def test_demasiados_posts_seguidos_dan_429(cliente):
    ahora = int(time.time())
    # El limite de la fixture son 6 por minuto.
    for i in range(6):
        assert publicar(cliente, timestamp_unix=ahora - i).status_code == 201
    assert publicar(cliente, timestamp_unix=ahora - 99).status_code == 429


def _publicar_desde(cliente, ip, ts):
    """POST fingiendo venir de `ip` via el header que pone nginx."""
    return cliente.post(
        "/api/lectura",
        json={"node_id": "cisterna01", "timestamp_unix": ts,
              "valid": True, "distance_cm": 84.0},
        headers={"Authorization": "Bearer " + TOKEN, "X-Real-IP": ip},
    )


def test_detras_de_proxy_el_limite_es_por_cliente_real(tmp_path):
    """Con nginx adelante, el limite tiene que contar por cliente y no en bolsa.

    Sin esto, todas las requests llegan con la IP del proxy y el limitador mete
    a todo el mundo en un solo contador: un cliente haciendo ruido deja afuera
    al nodo de verdad, que es justo al unico que no hay que dejar afuera.
    """
    cfg = config_de_prueba(tmp_path, AMARTYA_MAX_POST_POR_MINUTO="3",
                           AMARTYA_DETRAS_DE_PROXY="true")
    c = app_mod.crear_app(cfg, servir_ui=False).test_client()
    ahora = int(time.time())

    # Un cliente quema su cuota entera...
    for i in range(3):
        assert _publicar_desde(c, "10.0.0.9", ahora - i).status_code == 201
    assert _publicar_desde(c, "10.0.0.9", ahora - 50).status_code == 429

    # ...y otro sigue pudiendo publicar.
    assert _publicar_desde(c, "10.0.0.7", ahora - 60).status_code == 201


def test_sin_proxy_el_header_no_se_cree(tmp_path):
    """X-Real-IP lo puede mandar cualquiera.

    Si la API se lo creyera siempre, alguien que le pegue directo cambiaria el
    header en cada request y se saltearia el limite entero. Solo se mira cuando
    la config dice que hay un proxy adelante que lo sobrescribe.
    """
    cfg = config_de_prueba(tmp_path, AMARTYA_MAX_POST_POR_MINUTO="3")
    c = app_mod.crear_app(cfg, servir_ui=False).test_client()
    ahora = int(time.time())

    # Cambiando el header en cada request, el limite tiene que frenarlo igual.
    for i in range(3):
        assert _publicar_desde(c, "10.0.0.%d" % i, ahora - i).status_code == 201
    assert _publicar_desde(c, "10.0.0.99", ahora - 50).status_code == 429


def test_la_config_lee_el_flag_de_proxy():
    base = {"AMARTYA_TOKEN_NODO": TOKEN}
    assert config_mod.cargar(base)["detras_de_proxy"] is False
    assert config_mod.cargar({**base, "AMARTYA_DETRAS_DE_PROXY": "true"})["detras_de_proxy"] is True
    assert config_mod.cargar({**base, "AMARTYA_DETRAS_DE_PROXY": "1"})["detras_de_proxy"] is True
    assert config_mod.cargar({**base, "AMARTYA_DETRAS_DE_PROXY": "no"})["detras_de_proxy"] is False


# ===========================================================================
# Guardar y leer
# ===========================================================================

def test_base_vacia_no_es_un_error(cliente):
    """Recien instalado, sin lecturas todavia.

    Devolver 404 o 500 haria que la interfaz muestre "sin conexion", que es
    falso: manda a revisar la red cuando el que no publico es el nodo.
    """
    respuesta = cliente.get("/api/estado")
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"sin_lecturas": True}


def test_una_lectura_va_y_vuelve_entera(cliente):
    ahora = int(time.time())
    assert publicar(cliente, timestamp_unix=ahora, distance_cm=84.3).status_code == 201

    datos = cliente.get("/api/estado").get_json()
    assert datos["node_id"] == "cisterna01"
    assert datos["timestamp_unix"] == ahora
    assert datos["valid"] is True
    assert datos["distance_cm"] == pytest.approx(84.3)
    assert datos["rssi_dbm"] == -61
    assert datos["reloj_dudoso"] is False


def test_el_reintento_del_nodo_no_duplica_el_punto(cliente):
    """El nodo bufferiza y reenvia: el mismo instante puede llegar dos veces.

    Si se duplicara, el grafico tendria dos puntos en el mismo x y el consumo
    estimado contaria ese tramo dos veces.
    """
    ahora = int(time.time())
    publicar(cliente, timestamp_unix=ahora, distance_cm=84.0)
    publicar(cliente, timestamp_unix=ahora, distance_cm=84.0)

    puntos = cliente.get("/api/historico?horas=1").get_json()["puntos"]
    assert len(puntos) == 1


def test_descargar_el_buffer_no_cambia_cual_es_la_ultima(cliente):
    """Llega DESPUES una lectura que se midio ANTES.

    Pasa siempre que el nodo estuvo sin red y vacia su buffer. Si `ultima
    lectura` se ordenara por hora de llegada, la pantalla mostraria como estado
    actual del tanque un dato de hace horas.
    """
    ahora = int(time.time())
    publicar(cliente, timestamp_unix=ahora, distance_cm=80.0)
    publicar(cliente, timestamp_unix=ahora - 7200, distance_cm=120.0)

    datos = cliente.get("/api/estado").get_json()
    assert datos["timestamp_unix"] == ahora
    assert datos["distance_cm"] == pytest.approx(80.0)


def test_sensor_caido_llega_sin_medicion_y_asi_se_guarda(cliente):
    """`distance_cm: null` NO se puede convertir en 0.

    Cero centimetros de distancia al agua significa que el agua toca el sensor,
    o sea TANQUE LLENO. Es el bug que ya aparecio del lado de la interfaz
    (`Number(null)` es 0, no NaN) y que aca seria exactamente igual de grave.
    """
    assert publicar(cliente, valid=False, distance_cm=None).status_code == 201

    datos = cliente.get("/api/estado").get_json()
    assert datos["valid"] is False
    assert datos["distance_cm"] is None


def test_distancia_imposible_se_marca_invalida_pero_se_guarda(cliente):
    """Fuera del rango de cualquier ultrasonico.

    Se marca invalida y SE GUARDA con su valor crudo: una lectura invalida es
    informacion sobre el equipo, no basura. Quien tenga que arreglar el nodo
    necesita ver que numero estaba devolviendo.
    """
    assert publicar(cliente, distance_cm=5000.0).status_code == 201
    datos = cliente.get("/api/estado").get_json()
    assert datos["valid"] is False
    assert datos["distance_cm"] == pytest.approx(5000.0)


def test_nodo_sin_ntp_se_guarda_con_hora_de_recepcion_y_marcado(cliente):
    antes = int(time.time())
    respuesta = publicar(cliente, timestamp_unix=842)
    assert respuesta.status_code == 201
    assert respuesta.get_json()["reloj_dudoso"] is True

    datos = cliente.get("/api/estado").get_json()
    assert datos["reloj_dudoso"] is True
    # Fechada al recibirla, no en 1970.
    assert datos["timestamp_unix"] >= antes


def test_el_historico_no_manda_el_flag_de_reloj(cliente):
    """A proposito: en la serie, esos puntos tienen una hora usable.

    Si el flag viajara, la interfaz filtraria del grafico un tramo entero de
    historia real por un problema que es de otra cosa.
    """
    publicar(cliente, timestamp_unix=842)
    puntos = cliente.get("/api/historico?horas=1").get_json()["puntos"]
    assert len(puntos) == 1
    assert "reloj_dudoso" not in puntos[0]


# ===========================================================================
# Validacion de entrada
# ===========================================================================

@pytest.mark.parametrize("campos,motivo", [
    ({"node_id": ""}, "node_id vacio"),
    ({"node_id": "x" * 65}, "node_id larguisimo"),
    ({"timestamp_unix": "1757600000"}, "timestamp como string"),
    ({"timestamp_unix": None}, "sin timestamp"),
    ({"valid": "si"}, "valid que no es booleano"),
])
def test_cuerpos_invalidos_dan_400(cliente, campos, motivo):
    assert publicar(cliente, **campos).status_code == 400, motivo


def test_cuerpo_que_no_es_json_da_400(cliente):
    respuesta = cliente.post(
        "/api/lectura",
        data="esto no es json",
        content_type="text/plain",
        headers={"Authorization": "Bearer " + TOKEN},
    )
    assert respuesta.status_code == 400


# ===========================================================================
# Historico
# ===========================================================================

def test_el_historico_respeta_la_ventana(cliente):
    ahora = int(time.time())
    publicar(cliente, timestamp_unix=ahora - 1800)        # hace 30 min
    publicar(cliente, timestamp_unix=ahora - 3 * 3600)    # hace 3 horas

    assert len(cliente.get("/api/historico?horas=1").get_json()["puntos"]) == 1
    assert len(cliente.get("/api/historico?horas=24").get_json()["puntos"]) == 2


def test_el_historico_viene_de_vieja_a_nueva(cliente):
    """Es parte del contrato: el grafico dibuja en el orden en que llegan."""
    ahora = int(time.time())
    for offset in [0, 7200, 3600]:  # desordenados a proposito
        publicar(cliente, timestamp_unix=ahora - offset)

    puntos = cliente.get("/api/historico?horas=24").get_json()["puntos"]
    tiempos = [p["timestamp_unix"] for p in puntos]
    assert tiempos == sorted(tiempos)


@pytest.mark.parametrize("horas", ["cero", "-5", "0"])
def test_horas_invalidas_dan_400(cliente, horas):
    assert cliente.get("/api/historico?horas=" + horas).status_code == 400


def test_siete_dias_pasan_sin_submuestrear(cliente):
    """672 puntos (7 dias cada 15 min) contra un tope de 700: no se toca nada.

    Es el caso real de la interfaz, y vale la pena que este verificado: si
    alguien baja el tope sin pensar, el escalon del llenado se empieza a
    suavizar en el grafico que mira el usuario.
    """
    ahora = int(time.time())
    puntos = [
        {"timestamp_unix": ahora - i * 900, "distance_cm": 80.0, "valid": 1}
        for i in range(672)
    ]
    puntos.reverse()
    assert app_mod.submuestrear(puntos, 700, ahora - 7 * 86400, ahora) is puntos


def test_el_submuestreo_respeta_el_tope(cliente):
    ahora = int(time.time())
    desde = ahora - 365 * 86400
    puntos = [
        {"timestamp_unix": desde + i * 3600, "distance_cm": 80.0, "valid": 1}
        for i in range(8760)
    ]
    resultado = app_mod.submuestrear(puntos, 700, desde, ahora)
    assert len(resultado) <= 700
    assert len(resultado) > 100  # no se comio la serie


def test_el_submuestreo_conserva_el_hueco_del_nodo_caido():
    """Agrupar por TIEMPO y no de a N puntos.

    Agrupando de a N, los puntos de antes y de despues de un hueco caen en la
    misma cubeta, se promedian, y el bajon se dibuja como una rampa suave: el
    hueco desaparece del grafico.
    """
    desde = 1_700_000_000
    hasta = desde + 1000 * 60
    # Dos bloques de lecturas con 500 minutos de silencio en el medio.
    puntos = [{"timestamp_unix": desde + i * 60, "distance_cm": 80.0, "valid": 1}
              for i in range(250)]
    puntos += [{"timestamp_unix": desde + (750 + i) * 60, "distance_cm": 60.0, "valid": 1}
               for i in range(250)]

    resultado = app_mod.submuestrear(puntos, 50, desde, hasta)

    # Tiene que quedar un salto grande de tiempo entre dos puntos consecutivos:
    # eso es el hueco sobreviviendo al submuestreo.
    saltos = [
        resultado[i + 1]["timestamp_unix"] - resultado[i]["timestamp_unix"]
        for i in range(len(resultado) - 1)
    ]
    assert max(saltos) > 400 * 60


def test_el_submuestreo_no_inventa_datos_donde_el_sensor_estaba_caido():
    """Una cubeta sin ninguna medicion util se emite invalida, no se saltea.

    Si se salteara, un tramo con el sensor caido se dibujaria como una linea
    recta entre los dos extremos sanos, o sea inventada.
    """
    desde = 1_700_000_000
    hasta = desde + 600 * 60
    puntos = []
    for i in range(600):
        roto = 200 <= i < 400
        puntos.append({
            "timestamp_unix": desde + i * 60,
            "distance_cm": None if roto else 80.0,
            "valid": 0 if roto else 1,
        })

    resultado = app_mod.submuestrear(puntos, 60, desde, hasta)
    invalidos = [p for p in resultado if not p["valid"]]
    assert invalidos, "el tramo con el sensor caido desaparecio"
    assert all(p["distance_cm"] is None for p in invalidos)


# ===========================================================================
# Salud
# ===========================================================================

def test_salud_cuenta_las_lecturas(cliente):
    assert cliente.get("/api/salud").get_json() == {"ok": True, "lecturas": 0}
    publicar(cliente)
    assert cliente.get("/api/salud").get_json()["lecturas"] == 1
