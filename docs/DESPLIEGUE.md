# De esto que corre en la notebook a un servicio de verdad

Este documento existe porque la pregunta "¿cómo hago para migrar todo lo que
tengo local a un servidor real?" no tiene una respuesta de un renglón, y la
mayoría de lo que se encuentra buscando asume que ya sabés qué estás eligiendo.

Está escrito para el caso de Amartya: **una cisterna, un nodo**. Nada de lo de
acá abajo asume que exista todavía el backend.

---

## 1. Primero, el mapa

Hoy hay una sola pieza hecha (la interfaz) y funciona con datos inventados.
El servicio completo son cuatro:

```
  [ESP32 + ultrasónico]          el nodo, arriba del tanque
           |
           |  HTTP POST cada 15 min, por WiFi
           v
  [API]                          recibe la lectura y la guarda
           |
           v
  [Base de datos]                la serie histórica
           |
           v
  [Interfaz]  <-- HECHA          pide GET /estado y GET /historico
```

Las dos del medio son lo que hay que escribir y lo que hay que hostear. El
contrato entre la API y la interfaz **ya está definido y comentado** en
`ui/datos.js`: dos endpoints de solo lectura, más uno de escritura para el nodo.

```
GET  /api/estado              -> la última lectura
GET  /api/historico?horas=N   -> la serie
POST /api/lectura             -> lo que manda el nodo (falta especificar auth)
```

Que el contrato esté cerrado antes que el backend no es un detalle de
prolijidad: significa que quien escriba la API puede hacerlo sin tocar nada del
frontend, y que se puede cambiar la base de datos más adelante sin que la
pantalla se entere.

---

## 2. "¿No podemos hacer un container nuevo con lo que ya tenemos?"

Sí, y es el camino correcto. Pero conviene ser preciso sobre qué se reutiliza,
porque Casa Rosada resolvió un problema más grande que este.

**Lo que sirve tal cual del proyecto hermano:**

- La forma del `docker-compose.yml` (un servicio por pieza, `restart:
  unless-stopped` en todos, volúmenes nombrados, secretos por `.env`).
- El patrón de nginx sirviendo los archivos estáticos **y** haciendo de proxy
  hacia la API en `/api`. Eso resuelve dos cosas de una: el navegador ve un solo
  origen (no hay CORS) y cualquier credencial se queda del lado del servidor.
- El patrón `ISensor`/`IPublisher` y toda la librería `lib/Telemetria/` para el
  firmware.
- La disciplina de configuración como código: si algo se configura por la UI de
  una herramienta, en la próxima migración se pierde.

**Lo que NO conviene copiar:**

- **El broker MQTT.** En Casa Rosada se eligió MQTT por una razón concreta: con
  14-28 nodos, el broker retiene el último valor por tópico y el dashboard se
  suscribe a `#` y tiene todo al instante, sin 28 consultas a la base. Con **un**
  nodo eso no compra nada y agrega un servicio más para mantener, con sus
  usuarios y sus ACLs. Amartya ya venía publicando por HTTP directo; mantener
  eso.
- **Grafana.** Está bueno y es gratis, pero es una herramienta de operador, no
  algo para mostrarle al usuario de una cisterna. Si hace falta alerting, ver el
  punto 6.

---

## 3. La decisión que hay que tomar: dónde guardar la serie

Es la única elección de fondo que queda, y conviene tomarla a conciencia porque
arrastra el resto.

| | InfluxDB | SQLite |
|---|---|---|
| Pensada para | series temporales a gran escala | todo lo demás |
| Servicio aparte | sí, un contenedor más | no, es un archivo |
| RAM en reposo | ~200-400 MB | prácticamente nada |
| Backup | `influx backup` + `restore` | copiar un archivo |
| Consistente con la familia | sí (Casa Rosada la usa) | no |

**Recomendación: SQLite.** El argumento es el volumen real. Un nodo publicando
cada 15 minutos son 96 lecturas por día, unas **35.000 por año**. Eso es una
tabla chica: una consulta de 7 días toca ~700 filas y responde en
microsegundos. InfluxDB empieza a ganar en el orden de millones de puntos —
que es exactamente el caso de Casa Rosada (28 nodos cada 2 minutos, ~7 millones
al año) y exactamente no el de acá.

Elegir SQLite baja el hosting de "un servidor con varios contenedores" a "un
proceso y un archivo", que es la diferencia entre necesitar un VPS y poder
correr en casi cualquier lado.

**Y si esto resulta equivocado, se cambia sin drama**, porque la interfaz habla
con la API y no con la base. Cambiar de motor es reescribir el backend, no el
frontend ni el firmware.

---

## 4. Cómo se hace la migración, en concreto

Lo que hace que "mover a un servidor real" se sienta difícil es que suelen ser
tres cosas distintas mezcladas. Separadas, son manejables:

### (a) Mover el código

Es lo fácil, y es lo que casi todo el mundo cree que es "el" problema. Con el
proyecto en Docker: `git clone` en el servidor, `docker compose up -d`. Con la
imagen publicada en un registry, ni siquiera eso.

### (b) Mover los datos

Los datos NO viajan con el código. Hay que sacarlos de un lado y meterlos en el
otro, y esto **se ensaya antes**, no el día de la mudanza.

- Con SQLite: parar el servicio, copiar el archivo `.db`, levantarlo del otro
  lado. Ojo con copiar el archivo *con el servicio andando* — hay que usar
  `sqlite3 origen.db ".backup destino.db"`, que es consistente, y no `cp`.
- Con InfluxDB: `influx backup` / `influx restore`, nunca copiar el volumen
  crudo. Casa Rosada ya tiene los scripts hechos y **ensayados destruyendo el
  volumen entero**; de ese ensayo salieron dos cosas que no están en la
  documentación oficial: el backup exige un token *operator* (el `all-access`
  falla con un `401` que no dice nada útil) y el restore es `--full`, o sea que
  reemplaza la instancia completa.

La regla que vale para las dos: **un backup que nunca se restauró no es un
backup**, es un archivo del que se asume algo.

### (c) Cambiar a dónde apunta todo

Es lo que más se olvida y lo que más rompe:

- El **nodo** tiene la dirección del servidor grabada en su configuración. Si
  cambia, hay que reflashearlo o dejarlo configurable de antemano. Vale la pena
  que apunte a un **nombre de dominio** y no a una IP, justamente para no tener
  que volver a subirse al tanque.
- La **interfaz** apunta a `/api`, relativo, así que no hay nada que cambiar
  mientras el mismo nginx sirva las dos cosas. Esa es la razón de que sea
  relativo.
- Si aparece HTTPS, el firmware necesita el certificado raíz. Un ESP32 no tiene
  un almacén de certificados como un navegador: hay que compilarle el CA
  adentro, y **los certificados vencen**. Es la falla que aparece tres meses
  después, cuando ya nadie se acuerda.

---

## 5. Qué preguntarle a quien vaya a hostear

Si hay un grupo o una empresa que se va a encargar del hosteo, esta es la lista
corta. Sirve tanto para hablar con ellos como para darse cuenta de si lo que
ofrecen alcanza.

1. **¿Qué me dan exactamente?** No es lo mismo un VPS (una máquina, uno la
   administra), un PaaS tipo Railway o Fly.io (se sube el contenedor y ellos lo
   corren) o un hosting compartido (probablemente no sirva: hace falta correr un
   proceso propio, no solo servir archivos).
2. **¿Puedo correr Docker?** Si la respuesta es no, cambia bastante el plan.
3. **¿El almacenamiento es persistente?** Pregunta clave y fácil de pasar por
   alto: varios PaaS tienen disco **efímero** — se borra en cada deploy. Con
   SQLite eso significa perder el histórico entero sin que nadie avise. Si el
   disco es efímero, hace falta un volumen aparte o una base gestionada.
4. **¿Quién hace los backups, cada cuánto, y se probó restaurar uno?**
5. **¿El servidor es alcanzable desde afuera por el nodo?** El ESP32 tiene que
   poder llegar a una dirección pública y estable.
6. **¿Me dan HTTPS y un dominio?** (ver el punto del certificado, arriba).
7. **¿Cuánto sale y quién lo paga?** Conviene tenerlo por escrito antes de
   depender del servicio.

Para dimensionar: esto necesita muy poco. **1 vCPU y 512 MB–1 GB de RAM**
sobran para un nodo con SQLite. Entra en el escalón más barato de casi
cualquier proveedor, y en varios entra en el plan gratuito.

---

## 6. Lo mínimo de seguridad, antes de exponerlo

Nada de esto es opcional cuando el servicio queda con IP pública.

- **El `POST /api/lectura` necesita autenticación.** Sin eso, cualquiera que
  encuentre la URL puede inyectar lecturas falsas — un nivel de 95% inventado es
  peor que no tener el sistema, porque nadie va a ir a mirar el tanque. Un token
  compartido en un header alcanza para un nodo; que sea distinto del de
  cualquier otra cosa.
- **Los GET pueden quedar públicos**, pero conviene saber que lo son: quien
  tenga la URL ve el nivel de agua de la casa, que es un dato de presencia
  (tanque quieto varios días = no hay nadie). Si eso importa, va detrás de una
  contraseña.
- **Nada de secretos en `ui/`.** Es un sitio estático; todo lo que se ponga ahí
  lo ve cualquiera que abra la página. Por eso la interfaz habla con `/api` y no
  con la base directamente.
- **Rate limit en la API.** Un nodo publica cada 15 minutos; cualquier cosa por
  encima de unas pocas por minuto es un error o un abuso.

---

## 7. El orden que propongo

1. Mostrar la interfaz como está. Ya anda y ya se puede mostrar.
2. Escribir la API contra el contrato de `ui/datos.js`, con SQLite, y probarla
   contra la interfaz cambiando `origen` a `"http"`. Sigue sin hacer falta
   hardware: se puede cargar la base con lecturas de prueba.
3. Recién ahí el firmware, adaptando Casa Rosada.
4. Medir el tanque real y cargar la geometría.
5. Hosting, con la lista del punto 5 ya respondida.

El orden importa: cada paso deja algo que se puede mostrar, y ninguno queda
bloqueado esperando al hardware.
