/* datos.js — de dónde salen los números de la pantalla.
   =========================================================================

   QUÉ HACE: expone dos funciones (`Datos.estado()` y `Datos.historico()`) y
   esconde detrás de ellas si los datos vienen de un backend real o de un
   simulador.

   POR QUÉ EXISTE: la UI tiene que poder mostrarse la semana que viene sin que
   exista todavía ni el nodo ni el servidor. Si `app.js` hablara directo con
   `fetch()`, para hacer la demo habría que comentar código y después
   acordarse de descomentarlo — que es exactamente cómo se rompen las demos.
   Acá el origen es un valor de configuración (`origen: "simulado" | "http"`) y
   el resto del programa no se entera de cuál está activo.

   Las dos implementaciones devuelven la MISMA forma, así que cuando el backend
   exista no hay que tocar `app.js`. Esa forma es, de hecho, el contrato que el
   backend va a tener que cumplir — está documentado abajo en `estadoHttp()`.

   Nota sobre qué devuelve y qué NO: acá adentro no se calcula ningún
   porcentaje ni ningún litro. Esta capa entrega la medición CRUDA del sensor
   (distancia en cm) y los cálculos viven en `app.js`, contra la geometría del
   tanque. Se separa así porque la geometría es configuración de cada
   instalación, no un dato que viaje por la red: si mañana se cambia el tanque,
   se toca el config y no el backend.
   ========================================================================= */

(function (global) {
  "use strict";

  const MS_POR_DIA = 86400000;

  /* Rango de plausibilidad del reloj del nodo.

     El ESP32 no tiene RTC con batería: si el NTP no sincronizó, su
     `timestamp_unix` es "segundos desde que arrancó", o sea un número chico
     que cae en 1970. Sin esta validación, una lectura recién tomada se
     mostraría como "hace 56 años" y el gráfico se iría al principio de los
     tiempos. Es el mismo chequeo que hace el bridge del proyecto hermano
     (`bridge/timestamps.py` en Casa Rosada), y se repite acá porque la UI
     también recibe datos que podrían no haber pasado por el bridge. */
  const EPOCH_MINIMO_MS = Date.UTC(2020, 0, 1);
  /* Un día de margen hacia adelante: un reloj adelantado unos minutos es
     normal, uno adelantado años es un reloj roto. */
  const MARGEN_FUTURO_MS = MS_POR_DIA;

  /* Decide si el `timestamp_unix` que mandó el nodo se puede creer.
     Devuelve true si la lectura está fechada en un momento posible. */
  function relojPlausible(timestampMs) {
    if (!Number.isFinite(timestampMs)) return false;
    return timestampMs > EPOCH_MINIMO_MS && timestampMs < Date.now() + MARGEN_FUTURO_MS;
  }

  /* Convierte a número, o devuelve null si no hay valor.

     Existe por una trampa de JavaScript que acá era peligrosa de verdad:
     `Number(null)` no es `NaN`, es **0**. La primera versión hacía
     `Number.isFinite(Number(x)) ? Number(x) : null`, así que un
     `"distance_cm": null` —que es exactamente lo que manda el nodo cuando el
     sensor no contesta— se convertía en 0 cm. Cero centímetros de distancia al
     agua significa que el agua está tocando el sensor, o sea el TANQUE LLENO:
     un sensor roto se mostraba como la mejor noticia posible.

     El `== null` de la primera línea agarra `null` y `undefined` a la vez, que
     es el único caso donde la comparación laxa es la que se quiere. */
  function numeroONulo(valor) {
    if (valor == null) return null;
    const n = Number(valor);
    return Number.isFinite(n) ? n : null;
  }

  /* Normaliza una lectura cruda (venga del backend o del simulador) a la forma
     que consume `app.js`. Existe para que las dos implementaciones no puedan
     divergir en detalles tontos como el nombre de un campo. */
  function normalizarLectura(cruda) {
    const timestampMs = Number(cruda.timestamp_unix) * 1000;
    return {
      // Hora de MEDICIÓN, no de recepción. La diferencia importa: si se mide
      // por hora de llegada, un dato viejo que recién se descarga de un buffer
      // se muestra como recién nacido.
      timestampMs: timestampMs,
      relojConfiable: relojPlausible(timestampMs),
      // `valid: false` = el nodo está vivo y publicando, pero el sensor no
      // contestó. Es un estado distinto de "el nodo no publica", y se muestra
      // distinto (ver app.js).
      valid: cruda.valid !== false,
      distanciaCm: numeroONulo(cruda.distance_cm),
      rssiDbm: numeroONulo(cruda.rssi_dbm),
      nodeId: String(cruda.node_id || "—"),
    };
  }

  /* =======================================================================
     Implementación HTTP — el backend real
     ======================================================================= */

  /* Pide la última lectura al backend.

     CONTRATO que el backend tiene que cumplir (todavía no existe; esto es la
     especificación de lo que hay que escribir):

       GET {apiUrl}/estado  ->  200
       {
         "node_id": "cisterna01",
         "timestamp_unix": 1757600000,   // epoch UTC, en SEGUNDOS
         "valid": true,                  // ¿el sensor contestó?
         "distance_cm": 62.4,            // del sensor al espejo de agua
         "rssi_dbm": -67                 // opcional
       }

     Se eligió que el backend devuelva la distancia cruda y no el porcentaje
     por la misma razón de arriba: el porcentaje depende de la geometría del
     tanque, que es config del frontend. Si el backend mandara el porcentaje,
     cambiar la altura del tanque obligaría a redeployar el backend. */
  async function estadoHttp(cfg) {
    const resp = await fetch(cfg.apiUrl + "/estado", { cache: "no-store" });
    if (!resp.ok) throw new Error("El backend respondió " + resp.status);
    return normalizarLectura(await resp.json());
  }

  /* Pide la serie histórica.

       GET {apiUrl}/historico?horas=24  ->  200
       { "puntos": [ { "timestamp_unix": ..., "distance_cm": ..., "valid": true }, ... ] }

     Los puntos vienen ordenados de más viejo a más nuevo. El backend decide el
     downsampling (no tiene sentido mandar 7 días de lecturas crudas al
     teléfono de alguien con señal de campo). */
  async function historicoHttp(cfg, horas) {
    const resp = await fetch(cfg.apiUrl + "/historico?horas=" + horas, { cache: "no-store" });
    if (!resp.ok) throw new Error("El backend respondió " + resp.status);
    const datos = await resp.json();
    return (datos.puntos || []).map(normalizarLectura);
  }

  /* =======================================================================
     Implementación simulada — para la demo y para desarrollar sin hardware
     ======================================================================= */

  /* Modelo del tanque en el tiempo.

     Devuelve el porcentaje de llenado en el instante `tMs`, construido para
     que se parezca a una cisterna de verdad y no a una recta:

     - Se vacía a ritmo constante (el consumo diario de la config).
     - Se llena de golpe cuando toca fondo — que es como funciona: llega el
       camión o arranca la bomba, y el tanque pasa de casi vacío a casi lleno
       en poco tiempo. Ese escalón en el gráfico es la forma que hay que poder
       reconocer.
     - Tiene variación diaria: se consume más a la mañana y a la tarde que de
       madrugada.

     El truco de la fase: en vez de simular hacia adelante desde una fecha
     inventada, se ancla el ciclo para que en ESTE momento el nivel sea
     exactamente `simulacion.pctAhora`. Así la demo es reproducible —se abre la
     página y siempre se ve el escenario que se eligió mostrar— pero el
     histórico igual sale coherente hacia atrás. */
  function pctSimulado(tMs, ahoraMs, cfg) {
    const sim = cfg.simulacion;
    const consumoPctPorDia = (sim.consumoLitrosPorDia / cfg.tanque.capacidadLitros) * 100;

    /* Los extremos del ciclo se estiran para que `pctAhora` SIEMPRE quede
       ESTRICTAMENTE adentro.

       Sin esto hay una trampa que se comió dos intentos. Pidiendo
       `pctAhora: 6` con un ciclo que va de 96% a 14%, la fase pedida (90) se
       pasa del rango del ciclo (82), el módulo la envuelve y la pantalla
       termina mostrando 88%: la perilla para elegir qué escenario mostrar
       mostraba OTRO escenario, en silencio y sin ningún error.

       El primer arreglo fue estirar los extremos hasta `pctAhora`, y seguía
       fallando por una razón más fina: si el piso del ciclo queda EXACTAMENTE
       en `pctAhora`, entonces la fase vale justo una vuelta entera, el módulo
       la manda a cero y cero significa "recién llenado". Es ambiguo por
       construcción — el instante en que el tanque toca fondo es el mismo en
       que se llena.

       Por eso los extremos se corren un punto más allá: así la fase queda en
       [0, rango) sin tocar ningún borde, y `pctAhora` manda siempre. */
    const llenoPct = Math.max(sim.llenoPct, sim.pctAhora + 1);
    const vaciaHastaPct = Math.min(sim.vaciaHastaPct, sim.pctAhora - 1);

    // Cuánto se vacía entre un llenado y el siguiente.
    const rangoPct = llenoPct - vaciaHastaPct;

    // Cuánto lleva bajado el tanque AHORA desde el último llenado.
    const faseAhora = llenoPct - sim.pctAhora;

    // Días transcurridos entre `tMs` y ahora (negativo si tMs es pasado).
    const dias = (tMs - ahoraMs) / MS_POR_DIA;

    // En el pasado el tanque estaba más lleno, o sea con menos fase.
    let fase = faseAhora + consumoPctPorDia * dias;

    // Envolver dentro de [0, rangoPct): cada vuelta completa es un llenado.
    // El doble módulo es para que funcione con fase negativa (JS devuelve
    // negativo para `-5 % 75`, que acá daría un nivel arriba del 100%).
    fase = ((fase % rangoPct) + rangoPct) % rangoPct;

    let pct = llenoPct - fase;

    // Variación diaria: una onda de amplitud chica con mínimo de madrugada.
    // Es cosmética pero importa para la demo, porque sin esto la curva de 24 h
    // es una recta perfecta y se nota que es inventada.
    const horaDelDia = new Date(tMs).getHours() + new Date(tMs).getMinutes() / 60;
    pct -= 0.6 * Math.sin(((horaDelDia - 4) / 24) * 2 * Math.PI);

    return Math.max(0, Math.min(100, pct));
  }

  /* Convierte un porcentaje a la distancia que habría medido el sensor.

     El simulador genera la medición CRUDA y no el porcentaje directamente, a
     propósito: así la demo ejercita la misma cadena de cálculo que va a correr
     en producción (distancia -> columna -> porcentaje -> litros). Si el
     simulador entregara el porcentaje ya hecho, un error en esa cuenta no
     aparecería hasta que llegara el hardware. */
  function pctADistancia(pct, cfg, conRuido) {
    const t = cfg.tanque;
    const alturaUtilCm = t.distanciaFondoCm - t.distanciaLlenoCm;
    const columnaCm = (pct / 100) * alturaUtilCm;
    let distancia = t.distanciaFondoCm - columnaCm;

    // Ruido del ultrasónico. No es adorno: los HC-SR04 / JSN-SR04T tienen
    // dispersión de aproximadamente +/-1 cm sobre una superficie de agua quieta
    // (peor si hay ondas), y esa dispersión es la razón por la que el número
    // grande de la pantalla se redondea a entero en vez de mostrar decimales.
    if (conRuido) distancia += (Math.random() - 0.5) * 1.6;

    return distancia;
  }

  /* Arma una lectura simulada para un instante dado, aplicando el escenario
     elegido en la config. Los escenarios existen para poder MOSTRAR cada
     estado de la interfaz en una demo sin desenchufar nada. */
  function lecturaSimulada(tMs, ahoraMs, cfg, esLaUltima) {
    const escenario = cfg.simulacion.escenario;
    const pct = pctSimulado(tMs, ahoraMs, cfg);

    const base = {
      node_id: "cisterna01",
      timestamp_unix: Math.round(tMs / 1000),
      valid: true,
      distance_cm: pctADistancia(pct, cfg, true),
      rssi_dbm: -58 - Math.round(Math.random() * 14),
    };

    // Los escenarios de falla solo afectan a la ÚLTIMA lectura: el histórico
    // tiene que seguir siendo normal, porque así es como se ve un problema que
    // acaba de empezar. Un histórico entero roto no se parece a nada real.
    if (esLaUltima) {
      if (escenario === "sensor-roto") {
        // El nodo publica, pero el sensor no contestó. Se publica igual, con
        // valid=false y sin valor: un sensor caído es información, no algo
        // que haya que descartar en silencio.
        base.valid = false;
        base.distance_cm = null;
      } else if (escenario === "reloj-roto") {
        // Lo que manda un ESP32 al que no le sincronizó el NTP: segundos desde
        // el arranque, que caen en 1970.
        base.timestamp_unix = 842;
      }
    }

    return normalizarLectura(base);
  }

  /* Última lectura simulada. El escenario "sin-datos" se implementa fechando
     la lectura varias horas atrás: no hay que inventar ningún estado nuevo,
     porque "sin datos" en la interfaz de verdad también se deduce de la
     antigüedad y no de un flag. */
  async function estadoSimulado(cfg) {
    const ahora = Date.now();
    const desfase = cfg.simulacion.escenario === "sin-datos" ? 6 * 3600 * 1000 : 0;
    return lecturaSimulada(ahora - desfase, ahora, cfg, true);
  }

  /* Serie histórica simulada. El paso se elige para dar del orden de 150-200
     puntos en cualquier ventana: suficiente para que la curva se vea suave, y
     poco como para que Chart.js no tenga que dibujar miles de vértices en un
     teléfono. */
  async function historicoSimulado(cfg, horas) {
    const ahora = Date.now();
    const pasoMs = Math.max(5, Math.round((horas * 60) / 180)) * 60000;
    const puntos = [];

    for (let t = ahora - horas * 3600000; t <= ahora; t += pasoMs) {
      puntos.push(lecturaSimulada(t, ahora, cfg, false));
    }

    return puntos;
  }

  /* =======================================================================
     La interfaz pública
     ======================================================================= */

  const Datos = {
    /* ¿Estamos mirando datos inventados? `app.js` lo usa para avisarlo en
       pantalla. Una demo que no dice que es una demo es una demo que en algún
       momento alguien va a confundir con el sistema andando. */
    esSimulado: function () {
      return (global.AMARTYA_CONFIG || {}).origen !== "http";
    },

    estado: function () {
      const cfg = global.AMARTYA_CONFIG;
      return Datos.esSimulado() ? estadoSimulado(cfg) : estadoHttp(cfg);
    },

    historico: function (horas) {
      const cfg = global.AMARTYA_CONFIG;
      return Datos.esSimulado() ? historicoSimulado(cfg, horas) : historicoHttp(cfg, horas);
    },
  };

  global.Datos = Datos;
})(window);
