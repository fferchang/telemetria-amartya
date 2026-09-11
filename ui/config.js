/* config.js — la configuración de esta instalación.
   =========================================================================

   Este archivo SÍ va al repo, a diferencia de lo que suele hacerse con un
   config. La razón es que no tiene un solo secreto adentro y el costo de que
   falte es alto: sin él la página no arranca, así que un repo recién clonado
   no se podría mostrar sin un paso manual previo. Versionado, se clona y anda.

   La contra es que un `git pull` pisa los valores de la instalación. Con un
   tanque es manejable; cuando sean varios, esto pasa a ser una respuesta del
   backend (`GET /config`) y deja de ser un archivo.

   Por qué no hay secretos acá y no puede haberlos: es un sitio estático, todo
   lo que se escriba en este archivo queda visible para cualquiera que abra la
   página. Si alguna vez hace falta una credencial, va del lado del servidor
   (como el proxy de nginx del proyecto hermano Casa Rosada, que inyecta el
   token de InfluxDB sin que llegue nunca al navegador).
   ========================================================================= */

window.AMARTYA_CONFIG = {
  /* Cómo se llama este tanque para quien lo mira. Va en el encabezado. */
  sitio: "Cisterna principal",

  /* De dónde salen los datos.

       "simulado" = inventados por datos.js, sin backend ni hardware.
       "http"     = del backend real (ver `apiUrl` abajo).

     QUEDA EN "simulado" EN EL REPO aunque el backend ya exista y funcione, y
     es una decisión, no un olvido: así un clon recién hecho se abre y se ve
     andando sin token, sin servidor y sin base. Para mostrar la pantalla eso
     vale más que mostrar el stack completo, porque no hay nada que pueda
     fallar en el momento.

     Lo que hace que esto no sea peligroso es que la página lo dice: la píldora
     del encabezado muestra "Datos simulados" en verde y bien visible, y el
     detalle del nodo dice "Simulado" en el origen. Una demo que no avisa que
     es una demo es una demo que alguien va a confundir con el sistema andando.

     Para usar el backend de verdad: poner "http" acá y subir el `?v=` de
     config.js en index.html (si no, el navegador sigue con el archivo viejo).
     Los pasos completos están en el README. */
  origen: "simulado",

  /* Base de la API cuando `origen` es "http". Relativa si el frontend y el
     backend los sirve el mismo nginx (que es el plan), absoluta si no. */
  apiUrl: "/api",

  /* --- Geometría del tanque ---

     Las dos distancias se miden DESDE EL SENSOR, que va fijo en la tapa
     mirando hacia abajo. Son las dos únicas medidas que hay que tomar en la
     instalación, y de ellas sale todo lo demás.

       distanciaLlenoCm  |  sensor  |   <- 0 cm
                         |    |     |
                         |    v     |
                         |~~~~~~~~~~|   <- espejo de agua con el tanque LLENO
                         |          |
                         |          |
       distanciaFondoCm  |__________|   <- fondo del tanque

     OJO con `distanciaLlenoCm`: no es cero. El sensor ultrasónico tiene una
     zona muerta de unos 20-25 cm en la que no mide nada (el eco vuelve antes
     de que el receptor termine de escuchar el pulso emitido), así que el
     sensor va montado por encima del nivel de rebalse y esa separación es este
     número. Si se pone 0, el tanque lleno va a leer "error" en vez de 100%. */
  tanque: {
    distanciaFondoCm: 180,
    distanciaLlenoCm: 25,

    /* Capacidad útil en litros, o sea el volumen entre el fondo y el nivel de
       lleno. De acá salen los litros que muestra la pantalla.

       SUPUESTO IMPORTANTE: el cálculo asume sección constante (cilindro
       parado o prisma). Para un tanque esférico, cónico o acostado, los litros
       NO son proporcionales a la altura y este número solo, sin una tabla de
       conversión, da mal en los extremos. El porcentaje de ALTURA sigue siendo
       correcto en cualquier forma; los litros no. */
    capacidadLitros: 5000,
  },

  /* --- Umbrales ---
     Van acá y no en el firmware: son una decisión de cada instalación, no una
     constante física del sensor. Cambiarlos no tiene que implicar reflashear
     un ESP32 que está arriba de un tanque. */
  umbrales: {
    /* Debajo de esto la pantalla avisa. Elegir pensando en cuánto tarda en
       llegar la reposición: si el camión tarda dos días, el umbral tiene que
       dejar más de dos días de agua. */
    avisoPct: 25,
    criticoPct: 10,
  },

  /* Cada cuánto la página vuelve a pedir el estado. */
  refrescoSegundos: 30,

  /* Cada cuánto publica el nodo. De acá sale cuándo marcar "sin datos": la
     página espera unos pocos ciclos antes de desconfiar, para no gritar por un
     mensaje perdido. Tiene que coincidir con el `CYCLE_INTERVAL_SEC` del
     firmware — si cambia allá, hay que cambiarlo acá. */
  cicloNodoSegundos: 900,

  /* --- Solo para `origen: "simulado"` ---
     Sirve para mostrar cada estado de la interfaz en una demo sin tener que
     desenchufar nada. */
  simulacion: {
    /* Nivel que muestra la pantalla en este instante. Cambiarlo es la forma de
       elegir qué escenario mostrar: 62 se ve sano, 18 dispara el aviso, 6
       dispara el crítico. */
    pctAhora: 62,

    /* "normal" | "sin-datos" | "sensor-roto" | "reloj-roto"
       Los tres últimos son fallas del NODO, independientes del nivel de agua:
       sirven para mostrar que la interfaz distingue "no sé cuánta agua hay" de
       "hay poca agua". */
    escenario: "normal",

    /* Forma del ciclo de consumo y llenado del tanque simulado. */
    consumoLitrosPorDia: 340,
    llenoPct: 96,
    vaciaHastaPct: 14,
  },
};
