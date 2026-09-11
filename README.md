# Amartya — nivel de cisterna

Monitoreo del nivel de agua de una cisterna con un ESP32 y un sensor
ultrasónico montado en la tapa. Parte de la familia GridWire (junto con
GridWire industrial y Casa Rosada), con el mismo patrón `ISensor`/`IPublisher`.

**Estado actual: interfaz, backend y empaquetado.** Las tres piezas andan y
están probadas juntas, incluido el stack de Docker. Falta el firmware del nodo y
el hardware.

---

## Verlo andando — la interfaz sola

No hay que instalar nada. Desde la raíz del repo:

```bash
python -m http.server 8137 --directory ui
```

y abrir <http://localhost:8137>. Viene con `origen: "simulado"`, así que muestra
datos inventados y avisa que lo son (la píldora verde del encabezado).

Se puede abrir `ui/index.html` con doble clic también, pero conviene el
servidor: algunos navegadores tratan `file://` de forma rara y, sobre todo, así
se prueba igual que como va a estar servido de verdad.

## Verlo andando — con el backend de verdad

```bash
python -m venv .venv
.venv\Scripts\pip install -r backend/requirements-dev.txt
```

Generar un token y ponerlo en el entorno:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
set AMARTYA_TOKEN_NODO=lo-que-salio-arriba
```

Levantar la API (sirve también la interfaz, así que es un solo proceso y no hay
CORS que configurar):

```bash
.venv\Scripts\python backend/app.py
```

Cargar lecturas de prueba — hace falta subir el límite de tasa solo para esto,
porque la API acepta 6 POST por minuto y el sembrador manda cientos:

```bash
set AMARTYA_MAX_POST_POR_MINUTO=100000
```

```bash
.venv\Scripts\python tools/sembrar.py --dias 7 --nivel-ahora 62
```

Y por último, en `ui/config.js` poner `origen: "http"` y subirle el `?v=` a
`config.js` en `ui/index.html`. Abrir <http://127.0.0.1:8000>.

> **Usá `127.0.0.1` y no `localhost`.** En Windows, `localhost` resuelve primero
> a `::1` (IPv6) y el servidor de desarrollo escucha solo en IPv4, así que cada
> pedido espera ~2 segundos a que falle el intento por IPv6 antes de reintentar.
> Medido: 110 ms por request contra `127.0.0.1`, 2160 ms contra `localhost` —
> sembrar 673 lecturas pasa de 80 segundos a 24 minutos. En producción no pasa,
> porque nginx escucha en los dos stacks.

## Elegir qué escenario mostrar

Todo sale de `ui/config.js`, en el bloque `simulacion`. **Al cambiarlo hay que
subir el `?v=` de `config.js` en `ui/index.html`**, si no el navegador sigue
sirviendo el archivo viejo de su caché y parece que el cambio no hizo nada.

| Qué mostrar | Cómo |
|---|---|
| Todo bien | `pctAhora: 62`, `escenario: "normal"` |
| Nivel bajo (aviso) | `pctAhora: 18` |
| Nivel crítico | `pctAhora: 6` |
| El nodo dejó de publicar | `escenario: "sin-datos"` |
| El sensor no responde | `escenario: "sensor-roto"` |
| El nodo perdió la hora | `escenario: "reloj-roto"` |

Los últimos tres son fallas del **nodo**, no del agua, y se muestran distinto a
propósito — ver más abajo.

## Con Docker

Es la forma en que va a correr en el servidor: la API en un contenedor y nginx
sirviendo la interfaz y haciendo de proxy hacia `/api`.

```bash
docker compose up -d --build
```

Queda en <http://127.0.0.1:8090>. Necesita el `.env` con `AMARTYA_TOKEN_NODO`
(ver `.env.example`); el compose se niega a levantar si falta.

**Verificado de punta a punta**: imagen construida, API *healthy*, la interfaz
servida por nginx, el proxy de `/api` llegando a Flask con el prefijo entero, la
autenticación funcionando a través del proxy (401 sin token, 201 con), el
histórico comprimido de 42 KB a 4,4 KB por gzip, y la base sobreviviendo tanto a
un recreate del contenedor como a un `docker compose down`.

> **El puerto es 8090 y no 8080 a propósito.** Casa Rosada corre en la misma
> máquina y su compose ya publica el dashboard en 8080 (además de 3000, 8086,
> 1883 y 9001). Con los dos stacks arriba, el segundo falla con un *"port is
> already allocated"* que no dice de quién es el puerto. Si 8090 también está
> ocupado, se cambia con `AMARTYA_PUERTO_WEB` en el `.env`, sin tocar el compose.

**El límite de tasa real es el configurado por la cantidad de workers.** El
contador vive en memoria y cada worker de gunicorn lleva el suyo: medido contra
el contenedor, con el límite en 6 la API aceptó 12 por minuto antes de empezar a
devolver 429. Se deja así porque 12 sigue siendo 180 veces el ritmo del nodo, y
compartir el contador entre workers pediría Redis o una tabla — mucha maquinaria
para una red de seguridad cuya defensa de verdad es el token.

## Verificaciones

**Backend — 34 tests.** Necesita el venv:

```bash
.venv\Scripts\python -m pytest backend/ -v
```

**Colores — 44 pares contra WCAG 2.1 AA**, en tema claro y oscuro, leyendo los
tokens directamente de `ui/style.css` (no una copia, que quedaría
desincronizada). Sin dependencias:

```bash
python tools/contraste.py
```

**Simulador y cuentas de nivel — 22 checks.** Se abren en el navegador, en
`/test.html`, justamente para no necesitar Node.

Hoy pasan las tres.

---

## La API

El contrato está escrito y comentado en [`ui/datos.js`](ui/datos.js), y esa
documentación es anterior al backend: se definió primero para poder escribir las
dos mitades sin que ninguna esperara a la otra.

| | | |
|---|---|---|
| `POST` | `/api/lectura` | lo que manda el nodo — **necesita token** |
| `GET` | `/api/estado` | la última lectura |
| `GET` | `/api/historico?horas=N` | la serie |
| `GET` | `/api/salud` | healthcheck; toca la base a propósito |

El nodo se autentica con `Authorization: Bearer <token>`. Los `GET` son públicos
(ver *Seguridad*, abajo).

### Lo que la API deliberadamente no hace

- **No calcula porcentajes ni litros.** Eso sale de la geometría del tanque, que
  es configuración de cada instalación y vive en `ui/config.js`. Si el backend
  devolviera el porcentaje, cambiar la altura del tanque obligaría a redeployar
  el backend en vez de editar un archivo.
- **No evalúa umbrales ni dispara alertas.** La interfaz decide qué es "bajo"
  contra su propia config. Es la misma decisión que se tomó en Casa Rosada,
  donde un motor de alertas en Python se escribió y se descartó.

### Por qué SQLite

Un nodo publicando cada 15 minutos son ~35.000 lecturas al año, y una consulta
de 7 días toca ~700 filas. InfluxDB empieza a ganar en el orden de millones de
puntos — que es exactamente el caso de Casa Rosada (28 nodos cada 2 minutos) y
exactamente no el de acá. El razonamiento largo está en
[docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).

Si resulta equivocado se cambia sin drama: la interfaz habla con la API y no con
la base, así que cambiar de motor es reescribir el backend, no el frontend ni el
firmware.

### Detalles que no son obvios

- **La clave primaria es `(node_id, timestamp_unix)`.** El nodo bufferiza y
  reintenta, así que el mismo instante puede llegar dos veces; con esta clave el
  reintento cae encima del original en vez de duplicar el punto y torcer el
  gráfico. Es el mismo razonamiento por el que Casa Rosada acepta que MQTT con
  QoS 1 duplique mensajes.
- **"Última lectura" se ordena por hora de MEDICIÓN, no de llegada.** Cuando el
  nodo descarga su buffer, la última en llegar es la más vieja de la tanda.
- **Un nodo sin NTP se guarda con la hora de recepción y marcado.** El dato no
  se descarta —el agua igual se midió bien— pero el flag `reloj_dudoso` viaja
  hasta la pantalla, que lo muestra como un estado propio.
- **Ese flag lo manda `/estado` pero no `/historico`**, y es a propósito: en la
  serie esos puntos ya tienen una hora usable, y si el flag viajara, la interfaz
  filtraría del gráfico un tramo entero de historia real.
- **Una distancia fuera del rango físico de cualquier ultrasónico se marca
  inválida pero se guarda con su valor crudo.** Una lectura inválida es
  información sobre el equipo, no basura.
- **Base vacía devuelve `200 {"sin_lecturas": true}`, no un 404.** Un error HTTP
  haría que la interfaz muestre "sin conexión", que es falso: mandaría a revisar
  la red cuando el que no publicó es el nodo.

### Seguridad

- **El `POST` necesita token**, sin default posible: la API se niega a arrancar
  si falta. Un default (aunque sea "cambiame") deja el endpoint de escritura
  abierto mientras todo parece andar bien, y un nivel de 95% inventado es peor
  que no tener el sistema porque nadie va a ir a mirar el tanque.
- **Los `GET` son públicos**, pero conviene saber que lo son: quien tenga la URL
  ve el nivel de agua de la casa, que es un dato de presencia (tanque quieto
  varios días = no hay nadie). Si eso importa, va detrás de una contraseña.
- **Límite de tasa** de 6 POST por minuto por IP.
- El token se compara con `hmac.compare_digest` y no con `==`, para que el
  tiempo que tarda no filtre cuántos caracteres se acertaron.

---

## Qué muestra la pantalla, y por qué así

### Está pensada para el celular, al sol

El contexto real es alguien parado al lado del tanque, en el campo. De ahí salen
dos decisiones que se notan en todo el código: las reglas base del CSS son las
del teléfono (la pantalla grande es el caso raro, no el default), y el tema es
**claro** por default — al revés que el dashboard de Casa Rosada, que vive en un
sótano. Un fondo oscuro al sol es un espejo.

### Dos capas

1. **La respuesta.** Una frase que dice qué pasa ("Alcanza para unos 9 días"),
   el porcentaje grande, los litros, y la fecha estimada en que se quedaría sin
   agua. Sin jerga y sin ninguna unidad que haya que interpretar.
2. **El detalle del nodo.** La distancia cruda del sensor, el estado del reloj,
   la señal WiFi, la geometría configurada. Quien vino a saber si le queda agua
   no lo necesita; quien vino a arreglar el nodo lo tiene todo junto.

El gráfico queda entre las dos: "¿viene bajando rápido?" se la pregunta
cualquiera, no solo el técnico.

### En el celular se apila; en la PC entra todo junto

En el teléfono las tres secciones van una abajo de la otra y el detalle del nodo
arranca plegado, porque ahí el espacio es el recurso escaso.

En una pantalla de PC pasa lo contrario —sobra ancho y falta alto—, así que a
partir de **1152×768** el layout pasa a dos columnas, el detalle se abre solo y
**todo entra en una ventana sin scrollear**. Verificado midiendo el borde real
del contenido: sobran 40px en 1152×768 y en 1366×768, 138px en 1440×900 y 271px
en 1920×1080. Con el sensor caído, que es el caso de textos más largos, sobran
25px en el peor tamaño.

Por debajo de ese umbral vuelve a apilarse a propósito: una ventana ancha pero
baja recibiría un layout pensado para no scrollear que igual no entra, y el
resultado sería peor que la columna — todo apretado **y** con scroll.

El orden del DOM es el mismo en los dos casos (es un `grid`, no un reordenamiento
del HTML), así que el recorrido por teclado y por lector de pantalla no cambia.

### El estado tiene DOS ejes, no uno

Es la decisión de diseño más importante del proyecto.

| Eje | Estados | Color |
|---|---|---|
| **Agua** | en rango / bajo / crítico | verde / ámbar / rojo |
| **Nodo** | sin datos | gris |
| | sensor sin responder, reloj sin sincronizar | violeta |

Los problemas de nodo **ganan siempre**, no porque sean más graves sino porque
son anteriores: si el dato no se puede creer, el nivel que muestre no es una
afirmación que la pantalla pueda sostener. Un tanque lleno con el sensor roto no
es "todo bien" ni es "poca agua" — es "no sé", y se arregla llamando a otra
persona.

El violeta está deliberadamente fuera de la escala verde/ámbar/rojo: un ámbar
ahí haría pensar que el agua está baja, que es justo lo que no se sabe.

> Esto es, a propósito, lo que le falta hoy al dashboard de Casa Rosada, donde
> "sensor caído" y "nodo mudo" caen en el mismo estado visual y el cartel de
> arriba termina diciendo algo falso para uno de los dos casos. Acá está
> resuelto desde el arranque.

### Otras cosas que no son obvias

- **La antigüedad se mide por hora de MEDICIÓN, no de recepción.** Un dato viejo
  que recién sale de un buffer no es un dato nuevo.
- **La página valida el reloj del nodo por su cuenta.** El ESP32 no tiene RTC con
  batería: sin NTP manda "segundos desde el arranque" y las lecturas quedan
  fechadas en 1970. Eso se muestra como un estado propio, distinto de "sin
  datos", porque se arregla distinto.
- **Sin medición no se inventa un número.** Se muestra un guión, se esconden el
  "%" y la línea de litros (una unidad sin número no informa nada) y el tanque
  se dibuja vacío. Un default optimista sería mentir.
- **"Medido" y "Reportado" no son sinónimos.** Con el sensor caído el nodo
  reportó recién, pero no midió nada.
- **La estimación de consumo no cuenta los llenados** y suaviza la serie antes de
  calcular. Sin suavizar, el ruido de ±1 cm del ultrasónico se suma como si
  fuera consumo y la estimación sale inflada.
- **Los días se redondean para abajo.** Prometer de más es el único error que
  deja a alguien sin agua.
- **El dato viejo baja de jerarquía con un cambio de color, no con `opacity`.**
  Atenuar con opacidad es el truco obvio y rompe el contraste: medido, el 0.45
  original dejaba la línea de litros en 2.10:1, muy por debajo del 4.5:1 de AA.

---

## Estructura

```
ui/                  La pantalla. Sin build, sin npm, sin framework:
  index.html           archivos estáticos que se sirven tal cual, igual
  style.css            que dashboard/ en Casa Rosada.
  app.js             Cálculo, clasificación de estados, pintado, gráfico
  datos.js           De dónde salen los datos: simulador o backend HTTP
  config.js          Geometría del tanque, umbrales y escenario de demo
  test.html          22 verificaciones, se abren en el navegador
  vendor/            Chart.js

backend/             La API
  app.py             Rutas, validación, autenticación, submuestreo
  db.py              SQLite
  tiempo.py          Validación del reloj del nodo
  config.py          Configuración por variables de entorno
  test_backend.py    34 tests
  Dockerfile

tools/
  contraste.py       Auditoría WCAG de los colores
  sembrar.py         Carga lecturas de prueba contra la API real

nginx/               Sirve la interfaz y hace de proxy hacia /api
docs/DESPLIEGUE.md   Cómo pasar de esto a un servicio andando
docker-compose.yml   API + nginx
```

---

## Lo que falta

- [ ] El firmware del nodo. Se adapta de Casa Rosada: cambia el driver del
      sensor y el publisher (HTTP en vez de MQTT), el resto de
      `lib/Telemetria/` sirve igual. El token va en `include/config.local.h`.
- [ ] Medir el tanque real y cargar `distanciaFondoCm`, `distanciaLlenoCm` y
      `capacidadLitros` en `ui/config.js`.
- [ ] Confirmar la forma del tanque. Los litros de hoy suponen sección
      constante (cilindro parado o prisma); para uno esférico o acostado hace
      falta una tabla de conversión. El porcentaje de altura es correcto en
      cualquier forma.
- [ ] Decidir el hosting (ver `docs/DESPLIEGUE.md`, punto 5, para la lista de
      preguntas).
- [ ] Backups de la base. Con SQLite es copiar un archivo, pero **con el
      servicio parado o con `sqlite3 origen.db ".backup destino.db"`** — un `cp`
      con la base en uso puede salir inconsistente. Y un backup que nunca se
      restauró no es un backup.
