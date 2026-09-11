"""Mide el contraste de todos los pares de color de la interfaz contra WCAG 2.1.

Se corre con:   python tools/contraste.py

POR QUE EXISTE: la hoja de estilos afirma que los pares de color "estan medidos
contra WCAG AA, no estimados a ojo". Esto es lo que hace que esa frase sea
verdad y no una intencion. Un par que se elige mirando la pantalla puede estar
en 3.9:1 y verse bien en el monitor de quien lo eligio, y ser ilegible al sol
o para alguien con vision reducida.

Lee los tokens directamente de ui/style.css en vez de tener su propia copia: si
tuviera una copia, cambiar un color en el CSS dejaria esta verificacion
midiendo el color viejo y diciendo que todo esta bien.

Umbrales de WCAG 2.1 que se aplican:
  - Texto normal  -> AA pide 4.5:1
  - Texto grande  -> AA pide 3:1   (>=24px normal, o >=18.66px en negrita)
  - Componentes de interfaz y bordes -> 3:1
"""

import re
import sys
from pathlib import Path

RUTA_CSS = Path(__file__).resolve().parent.parent / "ui" / "style.css"


def leer_tokens(texto_css):
    """Extrae los custom properties de los dos bloques :root del CSS.

    Devuelve dos diccionarios, uno por tema. El bloque claro es el primer
    :root del archivo y el oscuro es el que esta adentro del @media de
    prefers-color-scheme; el oscuro hereda del claro todo lo que no redefine,
    que es justamente como lo resuelve el navegador.
    """
    # Cada bloque :root { ... } del archivo, en orden de aparicion.
    bloques = re.findall(r":root\s*\{(.*?)\}", texto_css, re.S)
    if len(bloques) < 2:
        sys.exit("Esperaba dos bloques :root en style.css (claro y oscuro).")

    def tokens_de(bloque):
        return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", bloque))

    claro = tokens_de(bloques[0])
    oscuro = dict(claro)            # el oscuro arranca heredando
    oscuro.update(tokens_de(bloques[1]))
    return claro, oscuro


def a_rgb(hex_color):
    """'#1b1b19' -> (27, 27, 25). Acepta tambien la forma corta de 3 digitos."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminancia(rgb):
    """Luminancia relativa segun la formula de WCAG 2.1.

    El tramo lineal por debajo de 0.03928 no es un detalle decorativo: es lo que
    hace que la formula sea correcta para colores muy oscuros, donde la curva
    gamma pura daria valores demasiado chicos.
    """
    def canal(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(hex_frente, hex_fondo):
    """Relacion de contraste entre dos colores, de 1:1 (iguales) a 21:1."""
    l1, l2 = luminancia(a_rgb(hex_frente)), luminancia(a_rgb(hex_fondo))
    claro, oscuro = max(l1, l2), min(l1, l2)
    return (claro + 0.05) / (oscuro + 0.05)


def mezclar(hex_frente, hex_fondo, alfa):
    """Color resultante de pintar `hex_frente` con opacidad `alfa` sobre el fondo.

    Hace falta porque la interfaz atenua con `opacity` en dos lugares (el dato
    dudoso y la linea de detalle del veredicto). Medir el color declarado en
    esos casos daria un contraste que el usuario nunca ve: lo que llega a la
    pantalla es la mezcla.
    """
    f, b = a_rgb(hex_frente), a_rgb(hex_fondo)
    mezcla = tuple(round(alfa * f[i] + (1 - alfa) * b[i]) for i in range(3))
    return "#%02x%02x%02x" % mezcla


# Cada fila: (descripcion, token de frente, token de fondo, minimo exigido, opacidad)
# El minimo sale del tamano real del texto en la pantalla, no de un default.
PARES = [
    ("Texto principal sobre la tarjeta",        "--texto",        "--superficie", 4.5, 1.0),
    ("Texto principal sobre el fondo",          "--texto",        "--fondo",      4.5, 1.0),
    ("Texto de apoyo sobre la tarjeta",         "--texto-suave",  "--superficie", 4.5, 1.0),
    ("Texto tenue sobre la tarjeta",            "--texto-tenue",  "--superficie", 4.5, 1.0),
    ("Texto tenue sobre el fondo",              "--texto-tenue",  "--fondo",      4.5, 1.0),

    ("Veredicto en rango",                      "--ok-texto",      "--ok-fondo",      4.5, 1.0),
    ("Veredicto nivel bajo",                    "--bajo-texto",    "--bajo-fondo",    4.5, 1.0),
    ("Veredicto nivel critico",                 "--critico-texto", "--critico-fondo", 4.5, 1.0),
    ("Veredicto sin datos",                     "--mudo-texto",    "--mudo-fondo",    4.5, 1.0),
    ("Veredicto problema de nodo",              "--nodo-texto",    "--nodo-fondo",    4.5, 1.0),

    # La segunda linea del veredicto se atenua al 85% sobre el fondo del estado.
    ("Detalle del veredicto (85%) en rango",    "--ok-texto",      "--ok-fondo",      4.5, 0.85),
    ("Detalle del veredicto (85%) bajo",        "--bajo-texto",    "--bajo-fondo",    4.5, 0.85),
    ("Detalle del veredicto (85%) critico",     "--critico-texto", "--critico-fondo", 4.5, 0.85),
    ("Detalle del veredicto (85%) sin datos",   "--mudo-texto",    "--mudo-fondo",    4.5, 0.85),
    ("Detalle del veredicto (85%) nodo",        "--nodo-texto",    "--nodo-fondo",    4.5, 0.85),

    # Graficos y componentes: WCAG 1.4.11 pide 3:1.
    #
    # NO se mide el borde de las tarjetas (--borde sobre --fondo, que da 1.20:1
    # en claro). No es un descuido ni una excepcion que nos concedemos: 1.4.11
    # cubre los graficos "necesarios para entender el contenido" y los limites
    # que hacen falta para IDENTIFICAR un control. El borde de una tarjeta no es
    # ninguna de las dos cosas — es una linea decorativa, el contenido se lee
    # igual sin ella, y forzarla a 3:1 convertiria la pantalla en una grilla de
    # cajas marcadas con gris oscuro. La pared del TANQUE si se mide, porque de
    # ella depende leer el nivel: sin pared, el agua es una mancha azul sin
    # referencia.
    ("Pared y sensor del tanque",               "--trazo-grafico", "--superficie",    3.0, 1.0),
    ("Marca del umbral",                        "--texto-tenue",   "--superficie",    3.0, 1.0),
    ("Agua del tanque",                         "--agua",          "--superficie",    3.0, 1.0),
    ("Anillo de foco",                          "--agua",          "--fondo",         3.0, 1.0),
    ("Serie del grafico",                       "--agua",          "--superficie",    3.0, 1.0),

    # El dato cuando la lectura es vieja o no se puede fechar. Baja a
    # --texto-tenue en vez de atenuarse con opacity (ver el comentario de
    # .lectura--dudosa en style.css), asi que se mide como texto normal: la
    # linea de litros va en 20px, que no llega al umbral de "texto grande".
    ("Dato dudoso: porcentaje y litros",        "--texto-tenue",   "--superficie",    4.5, 1.0),

    # El texto del boton de rango activo va sobre el azul del agua. Es un par
    # de tokens como cualquier otro desde que --sobre-agua existe; antes el
    # blanco estaba escrito dentro de la regla y por eso no se podia medir asi.
    ("Boton de rango activo",                   "--sobre-agua",    "--agua",          4.5, 1.0),
]


def evaluar(tokens, nombre_tema):
    print("\n" + "=" * 72)
    print("TEMA " + nombre_tema.upper())
    print("=" * 72)

    fallas = []
    filas = [(d, tokens.get(f), tokens.get(b), m, a) for d, f, b, m, a in PARES]

    for descripcion, frente, fondo, minimo, alfa in filas:
        if not frente or not fondo:
            print("  ??    %-42s token faltante" % descripcion)
            fallas.append(descripcion)
            continue

        efectivo = mezclar(frente, fondo, alfa) if alfa < 1 else frente
        ratio = contraste(efectivo, fondo)
        pasa = ratio >= minimo

        print("  %s  %-42s %5.2f:1  (pide %.1f)"
              % ("ok  " if pasa else "FALLA", descripcion, ratio, minimo))
        if not pasa:
            fallas.append("%s (%s, %.2f:1 < %.1f)" % (descripcion, nombre_tema, ratio, minimo))

    return fallas


def main():
    css = RUTA_CSS.read_text(encoding="utf-8")
    claro, oscuro = leer_tokens(css)

    fallas = evaluar(claro, "claro") + evaluar(oscuro, "oscuro")

    print("\n" + "=" * 72)
    if fallas:
        print("%d par(es) por debajo del minimo:" % len(fallas))
        for f in fallas:
            print("  - " + f)
        sys.exit(1)

    print("Todos los pares cumplen WCAG 2.1 AA.")
    sys.exit(0)


if __name__ == "__main__":
    main()
