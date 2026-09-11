# Imagen de la API.
#
# NO VERIFICADA TODAVIA: se escribio sin poder construirla, porque en la maquina
# de desarrollo el daemon de Docker no estaba corriendo. La logica de adentro si
# esta probada (34 tests). Antes de confiar en esto para un despliegue, correr
# `docker compose up --build` una vez y ver que el healthcheck pase.

# slim y no alpine: alpine usa musl en vez de glibc, y eso obliga a compilar
# desde fuente varias dependencias de Python que en slim vienen como wheel. La
# imagen termina siendo mas chica pero el build tarda mucho mas y se rompe de
# formas raras. Para una app sin extensiones en C la diferencia de tamaño no
# justifica el dolor de cabeza.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: no escribir .pyc adentro del contenedor, que es de
# solo lectura en la practica y los archivos no sobreviven al reinicio igual.
# PYTHONUNBUFFERED: que los print salgan al instante en vez de quedarse en el
# buffer — sin esto, `docker logs` se ve vacio y parece que la app esta colgada.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Los requirements se copian e instalan ANTES que el codigo, y el orden importa:
# Docker cachea cada paso, asi que mientras requirements.txt no cambie, cambiar
# una linea de app.py no vuelve a bajar e instalar Flask.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# La base va en un volumen montado en /datos, NO adentro de la imagen. Si
# quedara adentro, cada deploy la reemplazaria y se perderia el historico
# entero, en silencio. Es exactamente el riesgo del punto 3 de la lista de
# preguntas de docs/DESPLIEGUE.md.
ENV AMARTYA_DB=/datos/amartya.db
VOLUME ["/datos"]

# No correr como root. Si alguna vez hay una vulnerabilidad que permita ejecutar
# algo dentro del contenedor, que sea con los permisos minimos.
RUN useradd --create-home --uid 10001 amartya \
    && mkdir -p /datos \
    && chown -R amartya:amartya /datos /app
USER amartya

EXPOSE 8000

# Healthcheck que TOCA LA BASE (ver /api/salud en app.py): un proceso que
# responde HTTP pero no puede leer su base esta caido a todos los efectos
# practicos, y un chequeo que solo mira si el puerto contesta no se entera.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/salud', timeout=4).status==200 else 1)"

# `crear_app(servir_ui=False)` — gunicorn acepta llamar a una factory con
# argumentos. servir_ui=False porque aca los archivos estaticos los sirve nginx,
# que para eso es mucho mejor que Python; ademas la carpeta ui/ ni siquiera esta
# en esta imagen, asi que dejarlo en True daria 404 confusos.
#
# Dos workers: uno solo deja la API muda mientras atiende un pedido lento. No
# muchos mas porque el limitador de tasa es en memoria y cada worker lleva su
# propia cuenta (ver app.py), asi que el limite efectivo se multiplica.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:crear_app(servir_ui=False)"]
