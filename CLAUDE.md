# Amartya — Nivel de cisterna

## Contexto del proyecto

Parte de la familia GridWire (junto con GridWire industrial y Casa Rosada),
todos comparten el patrón `ISensor`/`IPublisher`. Es el más simple de los tres:
**una cisterna, un nodo**.

**Objetivo:** saber cuánta agua queda en la cisterna y hasta cuándo alcanza, sin
subirse al tanque a mirar.

**Quién lo mira:** dos personas distintas y eso manda el diseño. El usuario
final, desde el celular, al aire libre, que solo quiere saber si le queda agua;
y quien mantiene el equipo, que necesita saber si el nodo está vivo. La pantalla
está en dos capas por eso.

**Estado al 11/09/2026:** interfaz, backend y empaquetado de Docker, los tres
probados de punta a punta. Falta el firmware y el hardware.

## Decisiones de arquitectura ya cerradas

- **HTTP directo, no MQTT** (a diferencia de Casa Rosada). Allá el broker se
  justifica porque con 14-28 nodos retiene el último valor por tópico y el
  dashboard se suscribe a `#` en vez de hacer 28 consultas. Con un nodo eso no
  compra nada y agrega un servicio con sus usuarios y ACLs.
- **El contrato entre la interfaz y el backend está cerrado antes que el
  backend**, y documentado en `ui/datos.js`: `GET /estado`, `GET /historico?horas=N`
  y `POST /lectura`. Eso permite escribir la API sin tocar el frontend y cambiar
  la base de datos sin que la pantalla se entere.
- **El backend devuelve la distancia CRUDA en cm, no el porcentaje.** El
  porcentaje depende de la geometría del tanque, que es configuración de cada
  instalación y vive en el frontend. Si el backend mandara el porcentaje, cambiar
  la altura del tanque obligaría a redeployar el backend.
- **El origen de datos es intercambiable por configuración**
  (`origen: "simulado" | "http"` en `ui/config.js`). El simulador no es código de
  descarte: es lo que permite desarrollar y demostrar sin hardware, y lo que hace
  que no haya que comentar y descomentar código para una demo — que es
  exactamente cómo se rompen las demos.
- **SQLite y no InfluxDB** para la serie histórica. Un nodo cada 15 minutos son
  ~35.000 lecturas al año; InfluxDB empieza a ganar en el orden de millones (el
  caso de Casa Rosada, ~7 millones al año). Ver `docs/DESPLIEGUE.md`. Sigue
  siendo reversible sin tocar frontend ni firmware, porque los dos hablan con la
  API y no con la base.
- **Flask y no la biblioteca estándar** para la API. `http.server` evitaría la
  dependencia, pero el ruteo, el parseo del cuerpo, los códigos de error y el
  manejo de excepciones a mano son ~200 líneas que hay que mantener y que no son
  el problema de este proyecto. Flask además está documentada en todos lados,
  que importa para poder buscar cuando algo falla.
- **La clave primaria de `lecturas` es `(node_id, timestamp_unix)`.** El nodo
  bufferiza y reintenta, así que el mismo instante puede llegar dos veces; con
  esta clave el reintento cae encima del original (`INSERT OR REPLACE`) en vez
  de duplicar el punto. Mismo razonamiento que la aceptación de duplicados de
  MQTT QoS 1 en Casa Rosada.
- **`reloj_dudoso` lo manda `/estado` pero NO `/historico`.** Una lectura con el
  reloj roto se guarda con la hora de recepción, que es perfectamente usable
  para ubicarla en el gráfico. El flag sirve para el dato ACTUAL, donde la
  pregunta es "¿de cuándo es este número?". Si viajara en el histórico, la
  interfaz filtraría del gráfico un tramo entero de historia real.
- **Base vacía es `200 {"sin_lecturas": true}`, no un 404.** Un error HTTP haría
  que la interfaz muestre "sin conexión", que es falso y manda a revisar la red
  cuando el que no publicó es el nodo. La interfaz lo muestra como un estado
  propio ("todavía no llegó ninguna lectura"), distinto de "sin datos": ahí hubo
  lecturas y se cortaron, acá nunca hubo.
- **El backend sirve también la interfaz, pero solo en desarrollo**
  (`crear_app(servir_ui=True)`, que es el default; el Dockerfile lo apaga). Es
  para que en local sea un proceso en vez de dos y no haya CORS. En producción
  lo hace nginx, que para archivos estáticos es mucho mejor que Python.
- **Sin build, sin npm, sin framework.** Archivos estáticos servidos tal cual,
  igual que `dashboard/` en Casa Rosada. Las verificaciones corren en el
  navegador (`ui/test.html`) o en Python (`tools/contraste.py`) justamente para
  no necesitar Node, que no está instalado en la máquina de desarrollo.
- **Tema claro por default, oscuro como variante** — al revés que Casa Rosada, y
  a propósito: aquel vive en un sótano y en una pantalla de pared, este se mira
  con sol de frente, donde un fondo oscuro es un espejo.
- **En escritorio, todo entra en una ventana sin scrollear.** A partir de
  **1152×768** (`72rem × 48rem`) el layout pasa a dos columnas y el detalle del
  nodo se abre solo. Por debajo de ese umbral se apila a propósito: una ventana
  ancha pero baja recibiría un layout pensado para no scrollear que igual no
  entra, y quedaría todo apretado **y** con scroll.

  **El umbral está escrito en dos lugares y están acoplados**: el `@media` de
  `ui/style.css` y el `matchMedia` de `ui/app.js` que abre el `<details>`. Si se
  cambia uno hay que cambiar el otro, o la ficha se abre sin el grid de dos
  columnas y empuja todo hacia abajo.

  El alto del gráfico es `clamp(190px, 26vh, 300px)` y es la pieza elástica que
  hace que esto funcione en distintos tamaños: medida la pantalla, la columna
  derecha (gráfico + ficha) es la más alta, y de todo lo que hay ahí el gráfico
  es lo único que puede ceder alto sin perder información.

  Margen verificado midiendo el borde real del contenido (`.pie.bottom +
  padding-bottom`, no `scrollHeight`, que se satura en el alto de la ventana):
  40px en 1152×768 y 1366×768, 138px en 1440×900, 271px en 1920×1080; 25px en el
  peor caso, que es sensor caído a 1152×768. **Si se agrega una fila a la ficha
  o un renglón al veredicto, hay que volver a medir.**
- **`ui/config.js` SÍ se versiona.** No tiene secretos (es un sitio estático, no
  hay dónde esconder nada) y sin él la página no arranca, así que versionarlo es
  lo que hace que el repo recién clonado se abra y se vea andando. La contra es
  que un `git pull` pisa los valores de la instalación; cuando haya más de un
  tanque, esto pasa a ser un `GET /config` y deja de ser un archivo.
- **Cada `<script>` y el `<link>` del CSS llevan `?v=N`.** No es manía de
  desarrollo: un `config.js` cacheado hace que la página calcule el nivel con la
  geometría vieja del tanque y muestre un número equivocado con total confianza.
  **Al cambiar cualquiera de esos archivos hay que subir su número.**

## La decisión de diseño central: el estado tiene dos ejes

No es una escala de severidad. Son dos preguntas independientes:

| Eje | Estados | Color |
|---|---|---|
| **Agua** | ok / bajo / crítico | verde / ámbar / rojo |
| **Nodo** | mudo (no publica) | gris |
| | sensor sin responder, reloj sin sincronizar | violeta |

Los del eje NODO se evalúan **primero y ganan siempre**. No porque sean más
graves, sino porque son anteriores: si el dato no se puede creer, el nivel que
muestre no es una afirmación que la pantalla pueda sostener. Un tanque lleno con
el sensor roto no es "todo bien" ni "poca agua" — es "no sé", y se arregla
llamando a otra persona.

El violeta está deliberadamente fuera de la escala verde/ámbar/rojo: un ámbar
ahí haría pensar que el agua está baja, que es justo lo que no se sabe.

**Esto es lo que le falta hoy al dashboard de Casa Rosada**, donde `classify()`
mete "sensor caído" y "nodo mudo" en el mismo estado y el cartel de arriba
termina diciendo algo falso para uno de los dos. Está en su lista de pendientes.
Acá se hizo bien desde el arranque; si allá se arregla, el criterio es este.

## Bugs encontrados y arreglados (no volver a introducirlos)

- **`Number(null)` es `0`, no `NaN`.** La normalización de lecturas hacía
  `Number.isFinite(Number(x)) ? Number(x) : null`, así que un `"distance_cm":
  null` —exactamente lo que manda el nodo con el sensor caído— se convertía en 0
  cm. Cero de distancia al agua significa tanque LLENO: un sensor roto se
  mostraba como la mejor noticia posible. Resuelto con `numeroONulo()` en
  `ui/datos.js`.
- **La perilla del simulador mostraba otro escenario.** Pidiendo `pctAhora: 6`,
  la pantalla mostraba 88%, en silencio y sin error, porque la fase del ciclo de
  llenado se envolvía por módulo. El primer arreglo no alcanzó: si el piso del
  ciclo queda exactamente en `pctAhora`, la fase vale una vuelta entera y el
  módulo la manda a cero, que significa "recién llenado". Los extremos del ciclo
  se corren un punto más allá (ver `pctSimulado()`).
- **Atenuar con `opacity` rompía WCAG.** El `0.45` original dejaba la línea de
  litros en 2.10:1, muy por debajo del 4.5:1 de AA, y no había valor que
  sirviera: ni 0.80 llegaba, y a esa altura ya no se notaba atenuado. Se
  reemplazó por un cambio a `--texto-tenue`, que ya está verificado.
- **`toLocaleString("es-AR")` devolvía "04:27" para las 16:27** — formato de 12
  horas sin el AM/PM. En una ficha de diagnóstico eso manda a buscar en los logs
  el momento equivocado. Va con `{ hour12: false }` explícito.
- **`localhost` contra `127.0.0.1` en Windows: 20× de diferencia.** Medido, 2160
  ms por request contra `localhost` y 110 ms contra `127.0.0.1`. `localhost`
  resuelve primero a `::1` (IPv6), el servidor de desarrollo escucha solo en
  IPv4, y Windows tarda ~2 segundos en dar por perdido ese intento antes de
  reintentar. Sembrar 673 lecturas pasaba de 80 segundos a **24 minutos**. Por
  eso `tools/sembrar.py` usa `127.0.0.1` por default y el mensaje de arranque
  del backend imprime esa dirección. En producción no pasa (nginx escucha en los
  dos stacks).
- **El esquema de la base se creaba en CADA request.** `db.conectar()` corría
  `executescript(ESQUEMA)` y renegociaba el modo WAL cada vez, y como la
  conexión es por request, eso multiplicaba el costo de cada POST. Se separó en
  `db.inicializar()`, que corre una sola vez al crear la app.
- **La ventana de suavizado del consumo estaba en 5 y sobreestimaba 15%.**
  Medido contra datos sembrados a un consumo real conocido de 340 l/día:
  sin suavizar daba 992 (¡el triple!), con ventana 5 daba 392, con 15 daba 341.
  El ruido del ultrasónico entra en la cuenta de forma asimétrica —se suman solo
  las bajadas, así que cada zigzag aporta su mitad negativa como si fuera
  consumo— y por eso no se cancela solo. Ahora la ventana se calcula por TIEMPO
  (3.5 h) y no en cantidad de puntos, así se ajusta sola si cambia el ciclo de
  publicación del nodo.
- **`VOLUME` en el Dockerfile antes del `chown` tiraba el `chown`.** Docker
  descarta cualquier cambio hecho a una ruta DESPUÉS de declararla como volumen,
  así que `/datos` quedaba de root y el proceso (uid 10001) no podía escribir su
  propia base. Se sacó la instrucción `VOLUME` —el volumen lo declara el compose,
  que es donde se ve— y el `mkdir`+`chown` quedó antes de cambiar de usuario.
- **El puerto 8080 choca con Casa Rosada.** Su compose ya publica el dashboard
  ahí (además de 3000, 8086, 1883 y 9001), y los dos proyectos viven en la misma
  máquina. Amartya usa **8090**, configurable con `AMARTYA_PUERTO_WEB`. El error
  de Docker (*"port is already allocated"*) no dice de quién es el puerto.
- **nginx mandaba `X-Real-IP` y Flask nunca lo leía.** El comentario del nginx
  decía que servía para el límite de tasa, pero `request.remote_addr` detrás de
  un proxy es la IP del proxy, así que el limitador contaba a todos en una sola
  bolsa: un cliente haciendo ruido dejaba afuera al nodo. Ahora se lee, pero
  **solo si `AMARTYA_DETRAS_DE_PROXY` está en true** — el header lo puede
  falsificar cualquiera que le pegue directo a la API, y creerle siempre volvería
  al limitador decorativo. Es seguro creerle en el compose porque el servicio
  `api` no publica ningún puerto.
- **El límite de tasa efectivo es el configurado × la cantidad de workers.** El
  contador es en memoria y cada worker de gunicorn lleva el suyo. Medido contra
  el contenedor: con el límite en 6, la API aceptó 12 por minuto. Se deja así (12
  sigue siendo 180× el ritmo del nodo); compartirlo pediría Redis.
- **La fixture de los tests armaba un diccionario de config paralelo.** Agregar
  una clave nueva con su default rompió 17 tests con un `KeyError` que no tenía
  nada que ver con lo que probaban. Ahora `config_de_prueba()` llama al
  `cargar()` real con un entorno falso, así cualquier clave nueva llega sola.
- **El dibujo del tanque se leía como una PILA.** Un rectángulo vertical angosto
  con una tapita centrada arriba es el icono de una batería, con el agravante de
  que una pila al 62% significa lo mismo que un tanque al 62%, así que el error
  no se notaba — solo dejaba la pantalla sintiéndose genérica. Se arregló
  haciéndolo más ancho que alto, con el módulo del sensor apaisado sobre la tapa
  y una salida abajo.
- **El umbral dibujado adentro del tanque desaparecía con el tanque lleno**
  (gris sobre azul), justo en el caso en que hay que ver cuánto falta para
  llegar a él. Ahora es un triángulo apoyado en la pared **por fuera**.

## Accesibilidad

`python tools/contraste.py` mide los 44 pares de color contra WCAG 2.1 AA en los
dos temas, leyendo los tokens directamente de `ui/style.css` (no una copia, que
quedaría desincronizada). **Hoy pasan todos.** Correrlo después de tocar
cualquier color.

Dos cosas que ese script decide y conviene no revertir sin pensarlo:

- El **borde de las tarjetas** no se mide contra 3:1. WCAG 1.4.11 cubre gráficos
  necesarios para entender el contenido y límites necesarios para identificar un
  control; el borde de una tarjeta no es ninguna de las dos cosas.
- La **pared del tanque** sí se mide, por eso existe el token `--trazo-grafico`
  separado de `--borde-fuerte`: sin pared, el agua es una mancha azul sin
  referencia contra la cual leer el nivel.

## Pendiente

- [x] Empaquetado de Docker **verificado**: imagen construida, API *healthy*,
      nginx sirviendo la interfaz, el proxy de `/api` llegando a Flask con el
      prefijo entero, la autenticación funcionando a través del proxy, gzip
      bajando el histórico de 42 KB a 4,4 KB, y la base sobreviviendo a un
      recreate y a un `docker compose down`. Corre en **8090**, no en 8080.
- [ ] Firmware, adaptando `lib/Telemetria/` de Casa Rosada: cambia el driver del
      sensor y el publisher (HTTP en vez de MQTT). El token va en
      `include/config.local.h` y viaja como `Authorization: Bearer <token>`.
- [ ] Backups de la base. Con SQLite es copiar un archivo, pero con el servicio
      parado o vía `sqlite3 origen.db ".backup destino.db"` — un `cp` con la
      base en uso puede salir inconsistente. Y un backup que nunca se restauró
      no es un backup: en Casa Rosada eso se ensayó destruyendo el volumen
      entero, y de ahí salieron dos gotchas que la documentación no decía.
- [x] Backend contra el contrato de `ui/datos.js`, con SQLite. Hecho: 34 tests,
      y probado de punta a punta contra la interfaz con 673 lecturas sembradas.
- [x] Autenticación del `POST /lectura` con token, más límite de tasa. La API se
      niega a arrancar si falta el token, sin default posible.
- [ ] Medir el tanque real y cargar `distanciaFondoCm`, `distanciaLlenoCm` y
      `capacidadLitros`.
- [ ] **Confirmar la forma del tanque.** Los litros suponen sección constante
      (cilindro parado o prisma). Para uno esférico, cónico o acostado, los
      litros NO son proporcionales a la altura y hace falta una tabla de
      conversión. El porcentaje de ALTURA es correcto en cualquier forma.
- [ ] Confirmar `cicloNodoSegundos` (hoy 900) contra el `CYCLE_INTERVAL_SEC` real
      del firmware. Están acoplados: de ahí sale cuándo marcar "sin datos".
- [ ] Hosting. Ver `docs/DESPLIEGUE.md`, punto 5, para la lista de preguntas.
- [ ] Sin verificar: el gráfico se recrea al cambiar el tema del sistema, pero
      el pane de preview no dispara el evento de `matchMedia`, así que solo se
      probó el render inicial en cada tema. Probar en un navegador de verdad
      cambiando el tema del sistema con la página abierta.

## Estilo de comentarios (IMPORTANTE)

Estoy aprendiendo — priorizá que yo entienda el código por sobre la velocidad de
entrega. Aplica a TODO el código del proyecto.

- Cada función pública y cada clase: comentario arriba explicando QUÉ hace y POR
  QUÉ existe.
- Dentro de cada función, comentar cada bloque lógico de forma técnica — no un
  comentario por línea, sino por bloque, para poder seguir el flujo sin
  decodificar línea por línea.
- Cualquier línea con algo no obvio (timing, quirks de una librería o del
  hardware, por qué ese valor y no otro, un edge case no evidente) necesita
  comentario con el razonamiento.
- No comentes lo obvio (`i++; // incrementa i`) — eso es ruido.
- Al terminar un archivo nuevo o un cambio no trivial, dame en el chat un
  resumen de 3-4 líneas de las decisiones de diseño que tomaste y por qué.
- Si vas a usar un patrón o librería que no usamos hasta ahora, explicá en una
  línea por qué ese y no otro.

## Referencias de diseño

Las mismas cuatro lentes que se usaron en el pase de diseño de Casa Rosada. La
idea no es imitar el estilo visual de ninguna, sino usarlas como cuatro
revisores distintos: lo que solo una señala es cuestión de gusto, lo que señalan
tres o cuatro es un problema real.

- **Apple** — claridad y deferencia: la jerarquía la hace la tipografía, no el
  cromo; no mostrar más precisión de la que hay.
- **Google (Material)** — sistema: tokens en vez de valores sueltos, estados
  explícitos, contraste medido, mínimos táctiles.
- **Airbnb** — confianza: lenguaje humano, mostrar en qué se apoya lo que se
  afirma.
- **Spotify** — densidad legible y que nunca haya un momento en que la interfaz
  no diga en qué estado está.
