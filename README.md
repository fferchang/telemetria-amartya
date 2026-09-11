# Amartya — nivel de cisterna

Monitoreo del nivel de agua de una cisterna con un ESP32 y un sensor
ultrasónico montado en la tapa. Parte de la familia GridWire (junto con
GridWire industrial y Casa Rosada), con el mismo patrón `ISensor`/`IPublisher`.

**Estado actual: solo la interfaz.** Anda con datos simulados, sin backend y sin
hardware. Es lo que se puede mostrar hoy.

---

## Verlo andando

No hay que instalar nada. Desde la raíz del repo:

```bash
python -m http.server 8137 --directory ui
```

y abrir <http://localhost:8137>.

Se puede abrir `ui/index.html` con doble clic también, pero conviene el
servidor: algunos navegadores tratan `file://` de forma rara y, sobre todo, así
se prueba igual que como va a estar servido de verdad.

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

## Verificaciones

Las dos corren sin instalar dependencias.

```bash
python tools/contraste.py
```

Mide los 44 pares de color de la interfaz contra WCAG 2.1 AA, en tema claro y
oscuro, leyendo los tokens directamente de `ui/style.css`. Hoy pasan todos.

Para la lógica del simulador y las cuentas de nivel, abrir
<http://localhost:8137/test.html> con el servidor levantado. Corre en el
navegador justamente para no necesitar Node.

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
ui/
  index.html     La pantalla
  style.css      Tokens y layout (mobile-first, claro + oscuro)
  app.js         Cálculo, clasificación de estados, pintado y gráfico
  datos.js       De dónde salen los datos: simulador o backend HTTP
  config.js      Geometría del tanque, umbrales y escenario de demo
  test.html      Verificaciones, se abren en el navegador
  vendor/        Chart.js
tools/
  contraste.py   Auditoría WCAG de los colores
docs/
  DESPLIEGUE.md  Cómo pasar de esto a un servicio andando
```

Sin build, sin `npm install`, sin framework. Son archivos estáticos que se
sirven tal cual, lo mismo que hace `dashboard/` en Casa Rosada.

---

## Lo que falta

- [ ] El backend. El contrato ya está escrito y comentado en `ui/datos.js`
      (`GET /estado` y `GET /historico?horas=N`); falta implementarlo.
- [ ] El firmware del nodo. Se adapta de Casa Rosada: cambia el driver del
      sensor y el publisher (HTTP en vez de MQTT), el resto de
      `lib/Telemetria/` sirve igual.
- [ ] Medir el tanque real y cargar `distanciaFondoCm`, `distanciaLlenoCm` y
      `capacidadLitros` en `ui/config.js`.
- [ ] Confirmar la forma del tanque. Los litros de hoy suponen sección
      constante (cilindro parado o prisma); para uno esférico o acostado hace
      falta una tabla de conversión. El porcentaje de altura es correcto en
      cualquier forma.
- [ ] Decidir el hosting (ver `docs/DESPLIEGUE.md`).
