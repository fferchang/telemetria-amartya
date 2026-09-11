"""Configuracion del backend, leida del entorno.

QUE HACE: junta en un solo lugar todo lo que cambia entre la notebook y el
servidor, y se planta si falta algo que no puede tener default.

POR QUE DEL ENTORNO Y NO DE UN ARCHIVO: el token del nodo es un secreto, y un
secreto en un archivo del repo termina en GitHub tarde o temprano. Variables de
entorno es ademas lo que entienden todos los servicios de hosting sin que haya
que explicarles nada (ver docs/DESPLIEGUE.md).
"""

import os


class ConfigInvalida(Exception):
    """Falta algo sin lo cual arrancar seria peor que no arrancar."""


# Rango fisico aceptable para la medicion del ultrasonico, en centimetros.
#
# Es un chequeo GRUESO a proposito. El backend NO sabe que forma tiene el tanque
# —esa geometria vive en ui/config.js, porque es configuracion de cada
# instalacion— asi que no puede decir si 84 cm es un nivel razonable o un
# disparate. Lo unico que puede descartar es lo que ningun sensor ultrasonico
# puede haber medido: los HC-SR04 llegan a ~400 cm y los JSN-SR04T a ~450, asi
# que arriba de 1000 cm o en cero hay una falla, no una lectura.
DISTANCIA_MINIMA_CM = 0.5
DISTANCIA_MAXIMA_CM = 1000.0


def _entero(entorno, clave, default):
    """Lee un entero del entorno, con un error entendible si no lo es.

    Sin esto, un AMARTYA_MAX_PUNTOS="muchos" revienta con un ValueError pelado
    en medio del arranque y hay que ir a leer el traceback para entender que
    pasaba.
    """
    crudo = entorno.get(clave)
    if crudo is None or crudo.strip() == "":
        return default
    try:
        return int(crudo)
    except ValueError:
        raise ConfigInvalida(
            "%s tiene que ser un numero entero, y vale %r" % (clave, crudo)
        )


def cargar(entorno=None):
    """Arma la configuracion. `entorno` se puede inyectar para los tests."""
    entorno = os.environ if entorno is None else entorno

    token = (entorno.get("AMARTYA_TOKEN_NODO") or "").strip()

    # Sin token no se arranca, y esto NO tiene default a proposito.
    #
    # Un default (aunque sea "cambiame") es peor que no tener nada: el servicio
    # levanta, todo parece andar, y queda un endpoint de escritura abierto que
    # nadie se acuerda de cerrar. Cualquiera que encuentre la URL puede inyectar
    # un nivel de 95% inventado, y un numero falso es peor que ningun numero
    # porque nadie va a ir a mirar el tanque.
    if not token:
        raise ConfigInvalida(
            "Falta la variable de entorno AMARTYA_TOKEN_NODO, que es la que\n"
            "autentica al nodo cuando publica una lectura.\n\n"
            "Genera una con:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n\n"
            "y despues, segun donde estes:\n"
            "    PowerShell:  $env:AMARTYA_TOKEN_NODO = 'lo-que-salio'\n"
            "    bash:        export AMARTYA_TOKEN_NODO='lo-que-salio'\n"
            "    Docker:      ponela en el archivo .env"
        )

    if len(token) < 16:
        raise ConfigInvalida(
            "AMARTYA_TOKEN_NODO tiene %d caracteres y es demasiado corto para\n"
            "resistir que lo prueben por fuerza bruta. Usa al menos 16; el\n"
            "comando de arriba genera uno de 43." % len(token)
        )

    return {
        "token_nodo": token,

        # Ruta del archivo SQLite. En Docker apunta a un volumen montado; si
        # apuntara adentro del contenedor, cada deploy borraria el historico.
        "base_datos": entorno.get("AMARTYA_DB") or "datos/amartya.db",

        # Cuantos POST por minuto se aceptan antes de devolver 429. El nodo
        # publica cada 15 minutos, asi que 6 por minuto ya es sesenta veces mas
        # de lo normal: cualquier cosa por encima es un bucle de reintentos mal
        # hecho o alguien probando.
        #
        # OJO CON EL NUMERO EFECTIVO EN PRODUCCION: el contador vive en memoria
        # y cada worker de gunicorn lleva el suyo, asi que el limite real es
        # este valor POR LA CANTIDAD DE WORKERS. Medido contra el contenedor con
        # los 2 workers del Dockerfile: con esto en 6, la API acepto 12 por
        # minuto antes de empezar a devolver 429.
        #
        # Se deja asi y no se compensa dividiendo, porque el numero exacto no
        # importa a esta escala —12 sigue siendo 180 veces el ritmo del nodo— y
        # compartir el contador entre workers pediria Redis o una tabla, que es
        # mucha maquinaria para un tope que es una red de seguridad. La defensa
        # de verdad contra el abuso es el token.
        "max_post_por_minuto": _entero(entorno, "AMARTYA_MAX_POST_POR_MINUTO", 6),

        # Tope de puntos que devuelve /historico. Por encima de esto se
        # promedia en cubetas (ver app.py). 700 esta elegido para que la
        # ventana mas grande que usa la interfaz —7 dias a una lectura cada 15
        # minutos, o sea 672 puntos— pase entera y sin tocar; el tope existe
        # para que un `?horas=8760` no intente mandar un ano de lecturas.
        "max_puntos_historico": _entero(entorno, "AMARTYA_MAX_PUNTOS", 700),

        # Ventana maxima que se puede pedir, en horas. Un ano.
        "max_horas_historico": _entero(entorno, "AMARTYA_MAX_HORAS", 8760),

        # ¿Hay un proxy adelante (nginx) que setea X-Real-IP?
        #
        # Importa para el limitador de tasa. Detras de un proxy, todas las
        # requests llegan con la IP del proxy como origen, asi que el limitador
        # cuenta a todo el mundo en una sola bolsa: un solo cliente haciendo
        # ruido deja afuera al nodo de verdad.
        #
        # POR QUE ES UN FLAG Y NO SE DETECTA SOLO: el header lo puede mandar
        # cualquiera. Si la API se creyera siempre lo que dice X-Real-IP,
        # alguien que le pegue directo podria cambiarlo en cada request y
        # saltearse el limite entero. Confiar en el header solo esta bien cuando
        # se sabe que hay un proxy adelante que lo SOBRESCRIBE y que la API no
        # es alcanzable por otro lado — que es exactamente el caso del
        # docker-compose, donde el servicio `api` no publica ningun puerto.
        "detras_de_proxy": (entorno.get("AMARTYA_DETRAS_DE_PROXY") or "").strip().lower()
                           in ("1", "true", "si", "sí", "yes"),
    }
