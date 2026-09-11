/* app.js — la lógica de la pantalla.
   =========================================================================

   QUÉ HACE: pide los datos (a `Datos`), los convierte en las cifras que se
   muestran, decide en qué estado está el sistema y pinta todo.

   CÓMO ESTÁ ORGANIZADO, de arriba hacia abajo:

     1. Config y constantes
     2. Cálculo    — de la distancia cruda a porcentaje, litros y autonomía
     3. Estado     — clasificar qué está pasando (y es la parte con más
                     decisiones de diseño adentro)
     4. Formato    — números y tiempos en castellano
     5. Pintado    — escribir en el DOM
     6. Gráfico    — Chart.js
     7. Arranque   — el ciclo de refresco

   LA DECISIÓN DE DISEÑO MÁS IMPORTANTE DE ESTE ARCHIVO está en la sección 3:
   el estado NO es una sola escala. Hay dos ejes independientes —cuánta agua
   hay, y si el nodo funciona— y mezclarlos en una lista de severidad es un
   error que se paga caro. Un tanque lleno con el sensor roto no es "todo
   bien", y tampoco es "poca agua": es "no sé". Son tres cosas distintas, y
   cada una se arregla llamando a una persona distinta.
   ========================================================================= */

(function () {
  "use strict";

  /* =======================================================================
     1. Config y constantes
     ======================================================================= */

  const cfg = window.AMARTYA_CONFIG;

  // Fallar fuerte y temprano si falta la config, con un mensaje que diga qué
  // hacer. El modo silencioso sería una página con guiones por todos lados y
  // media hora de buscar por qué.
  if (!cfg) {
    document.body.innerHTML =
      '<p style="padding:2rem;font-family:system-ui">Falta <code>ui/config.js</code>, ' +
      'que define la geometría del tanque y los umbrales. Recuperalo del repo ' +
      '(<code>git checkout ui/config.js</code>) y recargá.</p>';
    return;
  }

  const MS_POR_DIA = 86400000;

  /* Cuántos ciclos del nodo se esperan antes de decir "sin datos".

     Tres y no uno: con un solo ciclo de margen, un mensaje perdido —cosa
     normal con WiFi de campo— pondría la pantalla en rojo cada tanto sin que
     pase nada. Una alarma que se dispara sola es una alarma que se aprende a
     ignorar. */
  const CICLOS_DE_GRACIA = 3;
  const SIN_DATOS_MS = cfg.cicloNodoSegundos * 1000 * CICLOS_DE_GRACIA;

  /* Subidas mayores a esto entre dos puntos se leen como un llenado y no como
     ruido. Se usa para no contar el llenado como "consumo negativo" al estimar
     cuánto dura el agua. */
  const SALTO_DE_LLENADO_PCT = 2;

  const $ = (id) => document.getElementById(id);

  // Estado en memoria: lo último que se supo. Se guarda para poder repintar
  // (por ejemplo cuando cambia la ventana del gráfico) sin volver a pedir.
  let ultimaLectura = null;
  let historicoActual = [];
  let horasVentana = 24;
  let grafico = null;
  let consumoEstimado = null; // litros/día, o null si no alcanzan los datos

  /* =======================================================================
     2. Cálculo
     ======================================================================= */

  /* Convierte la distancia que midió el sensor en nivel de agua.

     La cuenta es una resta y una regla de tres, pero tiene dos sutilezas:

     - El sensor mide desde ARRIBA, así que más distancia es MENOS agua. Es la
       fuente de error más fácil de cometer en este proyecto y por eso la resta
       está escrita explícita en vez de metida dentro de otra expresión.

     - El resultado se recorta a 0-100. Fuera de rango no significa que el
       tanque tenga 103% de agua: significa que la geometría configurada no
       coincide con la realidad, o que el sensor leyó cualquier cosa. Mostrar
       "103%" haría dudar del número correcto de al lado; recortarlo mantiene
       la pantalla coherente, y el dato crudo queda visible en el detalle del
       nodo para quien tenga que investigar.

     Devuelve null si no hay medición (sensor caído). */
  function derivarNivel(lectura) {
    if (lectura.distanciaCm === null) return null;

    const t = cfg.tanque;
    const alturaUtilCm = t.distanciaFondoCm - t.distanciaLlenoCm;
    if (alturaUtilCm <= 0) return null; // config mal cargada

    const columnaCm = t.distanciaFondoCm - lectura.distanciaCm;
    const pctCrudo = (columnaCm / alturaUtilCm) * 100;
    const pct = Math.max(0, Math.min(100, pctCrudo));

    return {
      pct: pct,
      // Los litros salen del porcentaje ya recortado, no del crudo, para que
      // los dos números de la pantalla no puedan contradecirse.
      litros: (pct / 100) * t.capacidadLitros,
      columnaCm: Math.max(0, columnaCm),
      fueraDeRango: pctCrudo < -1 || pctCrudo > 101,
    };
  }

  /* Suaviza la serie con un promedio móvil antes de estimar el consumo.

     POR QUÉ HACE FALTA: el ultrasónico tiene una dispersión de aproximadamente
     +/-1 cm. Al estimar el consumo sumando todas las bajadas punto a punto, ese
     ruido se suma como si fuera consumo real —cada zigzag aporta su mitad
     negativa— y el resultado sale sistemáticamente inflado. No es un error
     chico: con lecturas cada 15 minutos, el ruido puede aportar más "consumo"
     que el consumo. El promedio móvil lo cancela porque el ruido es simétrico
     y el consumo no. */
  function suavizar(valores, ventana) {
    if (valores.length < ventana) return valores.slice();

    const medio = Math.floor(ventana / 2);
    return valores.map((_, i) => {
      const desde = Math.max(0, i - medio);
      const hasta = Math.min(valores.length, i + medio + 1);
      let suma = 0;
      for (let j = desde; j < hasta; j++) suma += valores[j];
      return suma / (hasta - desde);
    });
  }

  /* Estima cuántos litros por día se están consumiendo.

     Cómo: recorre la serie sumando solo las BAJADAS y el tiempo en que
     ocurrieron, y saltea los tramos donde el nivel sube de golpe (un llenado).
     Si no se saltearan, un camión de agua se contaría como consumo negativo y
     la estimación diría que el tanque dura para siempre.

     Una meseta (el nivel no se mueve) sí cuenta: es tiempo real en el que no
     se consumió nada, y descartarla haría creer que se consume todo el día.

     Devuelve null si no hay suficiente historia — es preferible no mostrar el
     dato a mostrar una extrapolación hecha con cuatro puntos. */
  function estimarConsumoLitrosPorDia(puntos) {
    const utiles = puntos.filter((p) => p.valid && p.distanciaCm !== null);
    if (utiles.length < 8) return null;

    const pcts = suavizar(
      utiles.map((p) => {
        const n = derivarNivel(p);
        return n ? n.pct : 0;
      }),
      5
    );

    let caidaPct = 0;
    let tiempoMs = 0;

    for (let i = 1; i < utiles.length; i++) {
      const dt = utiles[i].timestampMs - utiles[i - 1].timestampMs;
      if (dt <= 0) continue;

      const dPct = pcts[i] - pcts[i - 1];

      // Llenado: ni el salto ni el tiempo entran en la cuenta.
      if (dPct > SALTO_DE_LLENADO_PCT) continue;

      caidaPct += Math.max(0, -dPct);
      tiempoMs += dt;
    }

    // Menos de medio día de observación útil no alcanza para extrapolar.
    if (tiempoMs < MS_POR_DIA / 2 || caidaPct <= 0) return null;

    const pctPorDia = caidaPct / (tiempoMs / MS_POR_DIA);
    return (pctPorDia / 100) * cfg.tanque.capacidadLitros;
  }

  /* =======================================================================
     3. Estado — los dos ejes
     ======================================================================= */

  /* Decide qué está pasando.

     Devuelve { clase, titulo, detalle }, donde `clase` es uno de:

       AGUA (el nodo anda bien, lo que se informa es el nivel)
         ok       — hay agua de sobra
         bajo     — por debajo del umbral de aviso
         critico  — por debajo del umbral crítico

       NODO (no se puede afirmar nada sobre el nivel)
         mudo     — el nodo no publica hace rato
         sensor   — el nodo publica, pero el sensor no contesta
         reloj    — el nodo publica, pero sin hora sincronizada

     EL ORDEN IMPORTA y es la regla central de la pantalla: los problemas de
     nodo se evalúan PRIMERO y ganan siempre. No porque sean más graves, sino
     porque son anteriores: si el dato no se puede creer, el nivel que sea que
     muestre no es una afirmación que la pantalla pueda sostener. Decir "todo
     bien, 82%" con el sensor roto es la peor falla posible en este producto.

     (Esta separación es, a propósito, la que le falta al dashboard del
     proyecto hermano Casa Rosada, donde "sensor caído" y "nodo mudo" caen en
     el mismo estado visual y el cartel termina diciendo algo falso para uno de
     los dos casos. Acá está resuelta desde el arranque.) */
  function clasificar(lectura, nivel) {
    const antiguedadMs = Date.now() - lectura.timestampMs;

    // --- Eje NODO ---

    if (!lectura.relojConfiable) {
      return {
        clase: "reloj",
        titulo: "El nodo no tiene la hora sincronizada",
        detalle:
          "Está publicando, pero sus lecturas llegan sin fecha válida, así que " +
          "no se puede saber si este nivel es de recién o de la semana pasada.",
      };
    }

    if (!lectura.valid || lectura.distanciaCm === null) {
      return {
        clase: "sensor",
        titulo: "El sensor no está respondiendo",
        detalle:
          "El nodo está conectado y publicando, pero no logra medir. " +
          "Suele ser el cable del sensor o el sensor mismo, no la red.",
      };
    }

    if (antiguedadMs > SIN_DATOS_MS) {
      return {
        clase: "mudo",
        titulo: "Sin datos desde hace " + formatearAntiguedad(antiguedadMs, false),
        detalle:
          "El último nivel conocido era " +
          Math.round(nivel ? nivel.pct : 0) +
          "%. Suele ser falta de energía o de señal en el nodo.",
      };
    }

    // --- Eje AGUA ---
    // Recién acá se puede hablar del agua, porque recién acá el dato es
    // confiable: es de ahora, el sensor contestó y la hora es creíble.

    const pct = nivel.pct;
    const dias = consumoEstimado ? nivel.litros / consumoEstimado : null;

    if (pct <= cfg.umbrales.criticoPct) {
      return {
        clase: "critico",
        titulo: "Queda muy poca agua",
        detalle: dias
          ? "Al ritmo actual alcanza " + frasearDias(dias) + ". Hay que reponer."
          : "Hay que reponer.",
      };
    }

    if (pct <= cfg.umbrales.avisoPct) {
      return {
        clase: "bajo",
        titulo: dias ? "Queda agua para " + frasearDias(dias) : "El nivel está bajo",
        detalle: "Conviene ir pidiendo la reposición.",
      };
    }

    return {
      clase: "ok",
      titulo: dias ? "Alcanza para " + frasearDias(dias) : "El nivel está bien",
      detalle: "",
    };
  }

  /* =======================================================================
     4. Formato
     ======================================================================= */

  /* Días en palabras.

     Se redondea para ABAJO a propósito. Este número se usa para decidir cuándo
     pedir agua: prometer de más es el único error que deja a alguien sin agua,
     así que la estimación se inclina siempre para el lado seguro. */
  function frasearDias(dias) {
    if (dias < 1) return "menos de un día";
    if (dias < 2) return "un día";
    if (dias < 14) return "unos " + Math.floor(dias) + " días";
    return "más de dos semanas";
  }

  /* La fecha en la que el tanque llegaría a cero, al ritmo actual.

     Es la misma información que "alcanza para 9 días", pero en la forma en que
     se usa: para agendar el camión hace falta un día de la semana, y
     convertir "9 días" a "el jueves" es una cuenta que no tiene por qué hacer
     quien mira la pantalla.

     Se muestra el día de la semana además del número porque a menos de dos
     semanas de distancia "el jueves" ubica más rápido que "19/9". Más allá de
     ese plazo la estimación no vale la precisión de un día, así que no se da
     una fecha: se dice que falta mucho. */
  function fechaDeVaciado(dias) {
    if (dias >= 14) return "en más de 2 semanas";

    const fecha = new Date(Date.now() + dias * MS_POR_DIA);
    return fecha.toLocaleDateString("es-AR", {
      weekday: "short",
      day: "numeric",
      month: "short",
    });
  }

  /* Antigüedad en palabras. `corto` es para el renglón chico debajo del
     número; la versión larga se usa dentro de una frase. */
  function formatearAntiguedad(ms, corto) {
    const min = Math.floor(ms / 60000);
    if (min < 1) return corto ? "recién" : "menos de un minuto";
    if (min < 60) return (corto ? "hace " : "") + min + " min";

    const horas = Math.floor(min / 60);
    if (horas < 48) return (corto ? "hace " : "") + horas + " h";

    return (corto ? "hace " : "") + Math.floor(horas / 24) + " días";
  }

  /* Litros con separador de miles en formato local (3.400 y no 3,400). */
  function formatearLitros(litros) {
    return Math.round(litros).toLocaleString("es-AR");
  }

  /* =======================================================================
     5. Pintado
     ======================================================================= */

  /* Geometría del interior del tanque dibujado, en unidades del viewBox del
     SVG. Está duplicada entre el HTML (el `rect` del clipPath), el CSS (el
     translateY inicial) y acá. No hay forma linda de evitarlo sin leer el DOM
     en cada frame, así que al menos está en un solo lugar de este archivo y
     anotada en los otros dos: si se cambia el dibujo, se cambian los tres. */
  const TOPE_INTERIOR = 31;
  const ALTO_INTERIOR = 126;

  /* Mueve el agua del dibujo al nivel que corresponde. El grupo se traslada
     hacia abajo tanto como le falte para estar lleno: 0% => translateY(154)
     y 100% => translateY(0). */
  function pintarTanque(pct) {
    const desplazamiento = ALTO_INTERIOR * (1 - pct / 100);
    $("agua").style.transform = "translateY(" + desplazamiento.toFixed(1) + "px)";
  }

  /* Ubica el triángulo del umbral contra la pared del tanque. Se calcula acá y
     no se deja fijo en el HTML porque el umbral es configurable: si alguien lo
     cambia a 40%, la marca tiene que moverse con él o pasa a mentir. */
  function pintarUmbral() {
    const y = TOPE_INTERIOR + ALTO_INTERIOR * (1 - cfg.umbrales.avisoPct / 100);

    // Triángulo apuntando a la pared: dos vértices a la izquierda y la punta
    // tocando el tanque en x=20.
    $("marca-umbral").setAttribute(
      "points",
      "7," + (y - 4).toFixed(1) + " 7," + (y + 4).toFixed(1) + " 17," + y.toFixed(1)
    );

    $("nota-umbral").textContent = "Aviso: " + cfg.umbrales.avisoPct + "%";
  }

  /* Aplica una clase de estado a un elemento, sacando primero cualquier otra
     del mismo grupo. Sin esto, un nodo que pasa de "crítico" a "ok" se queda
     con las dos clases y gana la que esté después en la hoja de estilos. */
  function ponerClaseEstado(elemento, prefijo, clase) {
    const todas = ["ok", "bajo", "critico", "mudo", "sensor", "reloj", "nodo"];
    todas.forEach((c) => elemento.classList.remove(prefijo + "--" + c));

    // "sensor" y "reloj" comparten el color de "problema del nodo": las dos
    // dicen lo mismo a nivel de confianza en el dato, y darles dos colores
    // distintos obligaría a aprender una paleta más grande sin ganar nada.
    const claseVisual = clase === "sensor" || clase === "reloj" ? "nodo" : clase;
    elemento.classList.add(prefijo + "--" + claseVisual);
  }

  /* Pinta todo lo que depende de la última lectura. */
  function pintarEstado(lectura) {
    const nivel = derivarNivel(lectura);
    const estado = clasificar(lectura, nivel);
    const antiguedadMs = Date.now() - lectura.timestampMs;
    const datoDudoso = estado.clase === "mudo" || estado.clase === "reloj";

    // --- Veredicto ---
    const veredicto = $("veredicto");
    $("veredicto-titulo").textContent = estado.titulo;
    ponerClaseEstado(veredicto, "veredicto", estado.clase);

    // La segunda línea aparece solo si hay algo que decir. El estado "ok" no
    // trae detalle a propósito: un renglón diciendo "no hay nada que hacer" es
    // un renglón que hay que leer para enterarse de que no había nada que leer.
    const detalle = $("veredicto-detalle");
    detalle.textContent = estado.detalle;
    detalle.hidden = !estado.detalle;

    // --- Números grandes ---
    const lecturaCaja = document.querySelector(".lectura");

    if (nivel) {
      $("nivel-pct").textContent = Math.round(nivel.pct);
      $("nivel-pct").classList.remove("lectura__numero--vacio");
      $("nivel-litros").textContent = formatearLitros(nivel.litros);
      $("nivel-unidad").hidden = false;
      $("linea-litros").hidden = false;
      pintarTanque(nivel.pct);
    } else {
      // Sin medición no se inventa un número ni se deja el anterior: se
      // muestra un guión y el tanque se vacía. Dejar el último valor pintado
      // como si fuera actual es lo mismo que mentir.
      //
      // Se esconden también el "%" y la línea de litros: "—%" y "— litros" son
      // unidades sin número, que no informan y dejan la pantalla con cara de
      // formulario a medio llenar.
      $("nivel-pct").textContent = "—";
      $("nivel-pct").classList.add("lectura__numero--vacio");
      $("nivel-unidad").hidden = true;
      $("linea-litros").hidden = true;
      pintarTanque(0);
    }

    // Atenuar cuando el dato existe pero no se puede fechar o es viejo. El
    // número no desaparece —sigue siendo lo último que se supo— pero deja de
    // mostrarse con la misma confianza visual que un dato fresco.
    lecturaCaja.classList.toggle("lectura--dudosa", datoDudoso);

    // "Medido" y "Reportado" no son sinónimos y la diferencia importa justo en
    // el caso del sensor caído: el nodo mandó algo hace un minuto, pero no
    // midió nada. Decir "Medido recién" al lado de un guión es una
    // contradicción chica que hace dudar de toda la pantalla.
    $("antiguedad").textContent = !lectura.relojConfiable
      ? "Sin hora confiable"
      : (nivel ? "Medido " : "Reportado ") + formatearAntiguedad(antiguedadMs, true);

    // --- Derivados ---
    if (consumoEstimado && nivel && !datoDudoso) {
      $("fecha-vacio").textContent = fechaDeVaciado(nivel.litros / consumoEstimado);
      $("consumo").textContent = formatearLitros(consumoEstimado) + " l";
    } else {
      $("fecha-vacio").textContent = "—";
      $("consumo").textContent = "—";
    }

    // La salvedad cambia según si hay estimación o no: un texto fijo que
    // explica una cuenta que no se hizo es ruido.
    $("salvedad").textContent = consumoEstimado
      ? "La estimación usa el consumo medido en los últimos días y no cuenta los llenados. Un cambio de uso la corre."
      : "Todavía no hay suficiente historial para estimar cuánto dura el agua.";

    // --- Ficha técnica (capa 2) ---
    pintarFicha(lectura, nivel, estado, antiguedadMs);
  }

  function pintarFicha(lectura, nivel, estado, antiguedadMs) {
    const problemaDeNodo =
      estado.clase === "mudo" || estado.clase === "sensor" || estado.clase === "reloj";

    // La pista del encabezado cerrado. Si el nodo está sano no dice nada
    // llamativo; si no lo está, lo grita desde afuera para que nadie tenga que
    // abrir la sección para enterarse.
    const pista = $("detalle-pista");
    pista.textContent = problemaDeNodo ? "Revisar" : "Todo en orden";
    pista.className = "detalle__pista";
    pista.classList.add(
      "detalle__pista--" + (estado.clase === "mudo" ? "mudo" : problemaDeNodo ? "nodo" : "ok")
    );

    $("ficha-estado").textContent = problemaDeNodo ? estado.titulo : "Publicando normalmente";

    // hour12: false explícito. Sin eso, `toLocaleString("es-AR")` devolvía
    // "04:27:16" para las 16:27 — el formato de 12 horas SIN el AM/PM, que es
    // lo peor de los dos mundos: no solo ambiguo, directamente equivocado a
    // simple vista. En una ficha de diagnóstico, donde el dato sirve para
    // cruzar con los logs del nodo, una hora corrida doce horas manda a
    // investigar el momento equivocado.
    // La fecha y hora exactas, y NADA MÁS. Antes esto agregaba la antigüedad
    // relativa entre paréntesis —"11/9/2026, 16:42 (recién)"— que ya está
    // escrita arriba, abajo del número grande. Repetirla acá no informaba nada
    // y hacía partir el valor en dos renglones, que en la columna derecha del
    // layout de escritorio es lo que decide si aparece el scroll.
    //
    // Sin segundos, además: el nodo publica cada 15 minutos, así que el segundo
    // exacto no distingue nada.
    $("ficha-ultima").textContent = lectura.relojConfiable
      ? new Date(lectura.timestampMs).toLocaleString("es-AR", {
          hour12: false,
          day: "numeric",
          month: "numeric",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "Fecha no confiable";

    $("ficha-distancia").textContent =
      lectura.distanciaCm === null ? "Sin lectura" : lectura.distanciaCm.toFixed(1) + " cm";

    $("ficha-columna").textContent = nivel ? nivel.columnaCm.toFixed(1) + " cm" : "—";

    $("ficha-reloj").textContent = lectura.relojConfiable
      ? "Sincronizado"
      : "SIN sincronizar (NTP)";

    $("ficha-senal").textContent =
      lectura.rssiDbm === null ? "—" : lectura.rssiDbm + " dBm";

    const t = cfg.tanque;
    $("ficha-geometria").textContent =
      "fondo " + t.distanciaFondoCm + " cm · lleno " + t.distanciaLlenoCm +
      " cm · " + formatearLitros(t.capacidadLitros) + " l";

    // "Simulado" a secas. El "(sin backend)" que decía antes era una aclaración
    // de más: la píldora del encabezado ya avisa "Datos simulados" en grande y
    // en verde, que es donde tiene que estar el aviso para que nadie confunda
    // una demo con el sistema andando. Acá alcanza con nombrar el origen.
    $("ficha-origen").textContent = window.Datos.esSimulado()
      ? "Simulado"
      : "Backend " + cfg.apiUrl;
  }

  /* Píldora de conexión. Habla del contacto entre la PÁGINA y el BACKEND, que
     es una cosa distinta de si el NODO está publicando: se puede tener el
     backend perfecto y el nodo muerto, y son dos problemas de dos personas
     distintas. Por eso son dos indicadores y no uno. */
  function pintarConexion(ok, mensaje) {
    const el = $("conexion");
    el.classList.remove("conexion--ok", "conexion--caida", "conexion--conectando");
    el.classList.add(ok ? "conexion--ok" : "conexion--caida");
    el.textContent = mensaje;
  }

  /* =======================================================================
     6. Gráfico
     ======================================================================= */

  /* Los colores salen de las custom properties del CSS y no están escritos
     acá. Si estuvieran duplicados, el gráfico se quedaría con el azul viejo la
     próxima vez que alguien cambie la paleta, y peor: no seguiría el cambio a
     tema oscuro. */
  function tomarColor(nombre) {
    return getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();
  }

  /* Dibuja la línea del umbral de aviso sobre el área del gráfico.

     Es un plugin de 10 líneas en vez del plugin oficial de anotaciones porque
     agregar una dependencia entera (y un archivo más en vendor/) para una
     línea recta no se justifica. */
  const pluginUmbral = {
    id: "umbralAviso",
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      const y = scales.y.getPixelForValue(cfg.umbrales.avisoPct);
      if (!Number.isFinite(y)) return;

      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = tomarColor("--texto-tenue");
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.restore();
    },
  };

  /* Etiqueta del eje X. Con ventana de 24 h interesa la hora; con 7 días, el
     día. Mostrar "11/09 14:30" en las dos hace que las etiquetas se pisen en
     un teléfono y no agrega nada. */
  function etiquetaTiempo(ms, horas) {
    const d = new Date(ms);
    const dosDigitos = (n) => String(n).padStart(2, "0");

    if (horas <= 24) return dosDigitos(d.getHours()) + ":" + dosDigitos(d.getMinutes());
    return dosDigitos(d.getDate()) + "/" + dosDigitos(d.getMonth() + 1);
  }

  function pintarGrafico(puntos) {
    const utiles = puntos.filter((p) => p.valid && p.distanciaCm !== null && p.relojConfiable);
    const vacio = $("grafico-vacio");

    // Menos de dos puntos no es una serie: es un punto. Mostrar el cartel en
    // vez de un canvas con ejes inventados.
    if (utiles.length < 2) {
      vacio.hidden = false;
      if (grafico) { grafico.destroy(); grafico = null; }
      return;
    }
    vacio.hidden = true;

    const etiquetas = utiles.map((p) => etiquetaTiempo(p.timestampMs, horasVentana));
    const valores = utiles.map((p) => {
      const n = derivarNivel(p);
      return n ? Number(n.pct.toFixed(1)) : null;
    });

    // Respetar prefers-reduced-motion también en el gráfico: Chart.js anima
    // 1000 ms por default, que para alguien que pidió menos movimiento en su
    // sistema es justo lo que no quiere.
    const sinMovimiento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Si el gráfico ya existe, se le cambian los datos en vez de destruirlo y
    // recrearlo: recrear en cada refresco reinicia la animación y hace
    // parpadear la pantalla cada 30 segundos.
    if (grafico) {
      grafico.data.labels = etiquetas;
      grafico.data.datasets[0].data = valores;
      grafico.update();
      return;
    }

    grafico = new Chart($("grafico").getContext("2d"), {
      type: "line",
      data: {
        labels: etiquetas,
        datasets: [
          {
            label: "Nivel",
            data: valores,
            borderColor: tomarColor("--agua"),
            // Área rellena: para un nivel de tanque, el área bajo la curva ES
            // el volumen. Una línea sola sería correcta pero desaprovecha que
            // la forma del relleno se lee como "cuánto hay".
            backgroundColor: tomarColor("--agua-tenue"),
            fill: true,
            borderWidth: 2,
            // Sin puntos: con ~180 muestras, dibujarlos convierte la curva en
            // una tira de manchas. Reaparecen al pasar el mouse.
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: sinMovimiento ? false : { duration: 400 },
        interaction: { mode: "index", intersect: false },
        scales: {
          y: {
            // Fijo de 0 a 100 y no automático: con escala automática, una
            // variación de tres puntos llena la pantalla y parece que el
            // tanque se vació. El eje tiene que mostrar el rango real de la
            // magnitud para que la pendiente signifique algo.
            min: 0,
            max: 100,
            ticks: {
              stepSize: 25,
              color: tomarColor("--texto-tenue"),
              callback: (v) => v + "%",
            },
            grid: { color: tomarColor("--borde") },
          },
          x: {
            ticks: {
              color: tomarColor("--texto-tenue"),
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 6,
            },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false }, // una sola serie: la leyenda sobra
          tooltip: {
            callbacks: {
              label: (item) => item.parsed.y + "% del tanque",
            },
          },
        },
      },
      plugins: [pluginUmbral],
    });
  }

  /* =======================================================================
     7. Arranque y ciclo de refresco
     ======================================================================= */

  async function refrescar() {
    try {
      const lectura = await window.Datos.estado();
      ultimaLectura = lectura;

      pintarConexion(
        true,
        window.Datos.esSimulado() ? "Datos simulados" : "Conectado"
      );
      pintarEstado(lectura);
    } catch (error) {
      // Falla de red o backend caído: se avisa arriba y se DEJA lo último que
      // se supo en pantalla, atenuado. Borrar los números sería perder
      // información que sigue siendo la mejor disponible.
      console.error("No se pudo leer el estado:", error);
      pintarConexion(false, "Sin conexión");
      document.querySelector(".lectura").classList.add("lectura--dudosa");
    }
  }

  async function recargarHistorico() {
    try {
      historicoActual = await window.Datos.historico(horasVentana);

      // El consumo se estima SIEMPRE sobre 7 días aunque el gráfico muestre
      // 24 h: con una ventana de un día, un día atípico (se llenó la pileta,
      // no hubo nadie) se convierte en "el" consumo y la autonomía salta de 3
      // a 20 días entre un refresco y el siguiente.
      const paraConsumo =
        horasVentana >= 168 ? historicoActual : await window.Datos.historico(168);
      consumoEstimado = estimarConsumoLitrosPorDia(paraConsumo);

      pintarGrafico(historicoActual);

      // Repintar el estado: la autonomía que se muestra arriba depende de esta
      // estimación, así que hasta acá estaba en "—".
      if (ultimaLectura) pintarEstado(ultimaLectura);
    } catch (error) {
      console.error("No se pudo leer el histórico:", error);
      $("grafico-vacio").hidden = false;
    }
  }

  /* Botones de ventana del gráfico. aria-pressed se actualiza junto con la
     clase: sin eso, un lector de pantalla anuncia los dos botones igual y no
     hay forma de saber cuál está activo. */
  document.querySelectorAll(".rango__boton").forEach((boton) => {
    boton.addEventListener("click", () => {
      horasVentana = Number(boton.dataset.horas);

      document.querySelectorAll(".rango__boton").forEach((b) => {
        b.setAttribute("aria-pressed", String(b === boton));
      });

      // Destruir el gráfico fuerza a recrearlo con las etiquetas del eje X en
      // el formato de la ventana nueva (horas vs. días).
      if (grafico) { grafico.destroy(); grafico = null; }
      recargarHistorico();
    });
  });

  /* Si el usuario cambia el tema del sistema, los colores del gráfico quedan
     con la paleta vieja: Chart.js los copió a su config cuando se creó, así que
     el resto de la página cambia y el gráfico se queda oscuro adentro de una
     tarjeta clara. Se recrea.

     El requestAnimationFrame no es decorativo: el evento de matchMedia puede
     llegar antes de que el navegador termine de recalcular los custom
     properties, y `tomarColor()` leería la paleta que se está yendo. Esperar un
     frame garantiza que los estilos ya están aplicados cuando se consultan. */
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    requestAnimationFrame(() => {
      if (grafico) { grafico.destroy(); grafico = null; }
      pintarGrafico(historicoActual);
    });
  });

  /* En escritorio el detalle del nodo arranca ABIERTO.

     El <details> está plegado en el teléfono porque ahí el espacio es el
     recurso escaso y quien mira quiere una sola respuesta. En una pantalla
     grande pasa lo contrario: sobra ancho, la ficha entra al lado del gráfico
     sin empujar nada, y dejarla cerrada obliga a un click para ver información
     que ya tenía lugar donde mostrarse.

     La condición es la MISMA del @media de escritorio de style.css (72rem de
     ancho y 48rem de alto), y están acopladas a propósito: si esto se abriera
     sin que el grid de dos columnas esté activo, la ficha empujaría todo hacia
     abajo y aparecería justo el scroll que ese layout evita. Si se cambia una,
     hay que cambiar la otra.

     `tocadoAMano` respeta al usuario: si abrió o cerró la ficha a propósito, un
     cambio de tamaño de ventana no se la vuelve a mover en la cara.

     Se escucha el CLICK sobre el <summary> y no el evento `toggle` del
     <details>, que era la primera versión y estaba mal: abrirlo por código
     también dispara `toggle`, así que la propia sincronización se marcaba como
     si la hubiera hecho el usuario y la perilla no servía para nada. El click
     sobre el summary, en cambio, solo ocurre si alguien lo activó — y cubre
     también el teclado, porque activar un <summary> con Enter o Espacio
     dispara un click sintético. */
  const esEscritorio = window.matchMedia("(min-width: 72rem) and (min-height: 48rem)");
  let tocadoAMano = false;

  document
    .querySelector(".detalle__resumen")
    .addEventListener("click", () => { tocadoAMano = true; });

  function sincronizarDetalle() {
    if (!tocadoAMano) $("detalle").open = esEscritorio.matches;
  }

  esEscritorio.addEventListener("change", sincronizarDetalle);

  // --- Puesta en marcha ---
  $("nombre-sitio").textContent = cfg.sitio;
  sincronizarDetalle();
  pintarUmbral();
  refrescar();
  recargarHistorico();

  setInterval(refrescar, cfg.refrescoSegundos * 1000);
  // El histórico se recarga más espaciado que el estado: es mucho más pesado
  // y un punto nuevo cada 15 minutos no cambia una curva de 24 horas.
  setInterval(recargarHistorico, Math.max(5, cfg.refrescoSegundos / 60) * 60000);
})();
