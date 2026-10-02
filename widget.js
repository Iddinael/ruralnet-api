/*!
 * RuralNet · Widget de estudio de señal (v5.2)
 * Lo sirve el backend en /widget.js para que WordPress no modifique el JavaScript.
 * La configuración (URL de la API, WhatsApp de ventas, política de datos) se lee de
 * los atributos data-* del elemento <div id="rnx-config"> del bloque HTML.
 */
(function () {
  "use strict";

  /* ---------------- Configuración ---------------- */
  // La configuración viene de los atributos data-* del bloque de WordPress (div#rnx-config)
  var nodoCfg = document.getElementById("rnx-config");
  var CFG = nodoCfg ? {
    API_URL: nodoCfg.getAttribute("data-api"),
    TELEFONO_VENTAS: nodoCfg.getAttribute("data-telefono"),
    URL_POLITICA_DATOS: nodoCfg.getAttribute("data-politica")
  } : (window.RURALNET_CONFIG || {});
  if (!document.getElementById("rnx")) return;   // El bloque no está en esta página
  var API_URL = String(CFG.API_URL || "").replace(/\/+$/, "");
  var TELEFONO = String(CFG.TELEFONO_VENTAS || "").replace(/\D/g, "");
  var TIEMPO_MAX_MS = 70000;              // Render gratis tarda ~50 s en "despertar"
  var COLOMBIA = [4.5709, -74.2973];

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    captura: $("rnx-captura"), form: $("rnx-form"), carga: $("rnx-carga"), cargaTexto: $("rnx-carga-texto"),
    resultado: $("rnx-resultado"), gps: $("rnx-gps"), pista: $("rnx-pista"), error: $("rnx-error"),
    analizar: $("rnx-analizar"), nombre: $("rnx-nombre"), whatsapp: $("rnx-whatsapp"),
    acepta: $("rnx-acepta"), sitio: $("rnx-sitio"), p1: $("rnx-p1"), p2: $("rnx-p2"), p3: $("rnx-p3")
  };
  $("rnx-politica").href = CFG.URL_POLITICA_DATOS || "#";

  var coordenadas = null;                 // Se guardan en secreto, el usuario no las escribe

  // Despierta el servidor de Render mientras el usuario usa el mapa
  if (API_URL) fetch(API_URL + "/", { mode: "cors" }).catch(function () {});

  /* ---------------- Mapa ---------------- */
  if (typeof L === "undefined") {
    el.pista.textContent = "⚠️ No se pudo cargar el mapa. Revisa tu conexión y recarga la página.";
    return;
  }
  var mapa = L.map("rnx-mapa", { scrollWheelZoom: false, zoomControl: true }).setView(COLOMBIA, 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
  }).addTo(mapa);
  mapa.once("focus", function () { mapa.scrollWheelZoom.enable(); });
  setTimeout(function () { mapa.invalidateSize(); }, 400);

  // Pin arrastrable que inicia en el centro del mapa
  var pin = L.marker(mapa.getCenter(), { draggable: true, autoPan: true, title: "Arrastra hasta tu predio" })
    .addTo(mapa).bindPopup("<b>Arrástrame hasta tu predio</b> 🏡").openPopup();

  function marcarPaso(n) {
    [el.p1, el.p2, el.p3].forEach(function (p, i) {
      p.classList.toggle("activo", i + 1 === n);
      p.classList.toggle("hecho", i + 1 < n);
    });
  }

  function ubicacionElegida(latlng) {
    coordenadas = { lat: latlng.lat, lon: latlng.lng };
    el.pista.textContent = "✅ Ubicación de tu predio guardada. Puedes ajustarla cuando quieras.";
    el.pista.classList.add("ok");
    ocultarError();
    // El formulario aparece deslizándose SÓLO tras la primera interacción con el mapa o el GPS
    if (el.form.classList.contains("rnx-oculto")) {
      el.form.classList.remove("rnx-oculto");
      el.form.classList.add("rnx-entra");
      marcarPaso(2);
      setTimeout(function () { el.form.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 250);
    }
  }

  // El aviso de error desaparece en cuanto el usuario corrige algún campo
  [el.nombre, el.whatsapp, el.acepta].forEach(function (c) {
    c.addEventListener(c.type === "checkbox" ? "change" : "input", ocultarError);
  });

  pin.on("dragend", function (e) { ubicacionElegida(e.target.getLatLng()); });
  mapa.on("click", function (e) { pin.setLatLng(e.latlng); ubicacionElegida(e.latlng); });

  /* ---------------- GPS ---------------- */
  el.gps.addEventListener("click", function () {
    if (!("geolocation" in navigator)) {
      el.pista.textContent = "⚠️ Tu navegador no permite el GPS. Arrastra el pin hasta tu predio.";
      return;
    }
    var html = el.gps.innerHTML;
    el.gps.disabled = true;
    el.gps.innerHTML = '<span class="rnx-gps-icono">…</span> Buscando tu ubicación';
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var ll = L.latLng(pos.coords.latitude, pos.coords.longitude);
        pin.setLatLng(ll);
        mapa.flyTo(ll, 15, { duration: 1.2 });
        pin.bindPopup("¡Aquí estás! Si tu predio está en otro punto, mueve el pin.").openPopup();
        ubicacionElegida(ll);
        el.gps.disabled = false; el.gps.innerHTML = html;
      },
      function (err) {
        var msj = {
          1: "⚠️ No diste permiso de ubicación. Actívalo en tu navegador o arrastra el pin.",
          2: "⚠️ No pudimos detectar tu ubicación. Arrastra el pin hasta tu predio.",
          3: "⚠️ El GPS tardó demasiado. Intenta de nuevo o arrastra el pin."
        };
        el.pista.textContent = msj[err.code] || "⚠️ Error con el GPS. Arrastra el pin hasta tu predio.";
        el.pista.classList.remove("ok");
        el.gps.disabled = false; el.gps.innerHTML = html;
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });

  /* ---------------- Validación ---------------- */
  function celularValido(v) {
    var d = String(v).replace(/\D/g, "");
    if (d.length === 12 && d.indexOf("57") === 0) d = d.slice(2);
    return /^3\d{9}$/.test(d) ? d : null;
  }
  function validar() {
    var nombre = el.nombre.value.trim().replace(/\s+/g, " ");
    var cel = celularValido(el.whatsapp.value);
    if (!coordenadas) return { error: "Primero ubica tu predio en el mapa.", foco: el.gps };
    if (nombre.length < 3) return { error: "Escribe tu nombre completo.", foco: el.nombre };
    if (!cel) return { error: "Escribe un WhatsApp válido de 10 dígitos que empiece por 3 (ej: 310 123 4567).", foco: el.whatsapp };
    if (!el.acepta.checked) return { error: "Para generar tu estudio debes autorizar el tratamiento de datos.", foco: el.acepta };
    return { nombre: nombre, whatsapp: cel };
  }

  /* ---------------- Envío y consulta al backend ---------------- */
  var textosCarga = ["Escaneando antenas cercanas…", "Midiendo distancias geodésicas (Haversine)…", "Calculando pérdida de trayectoria (FSPL)…", "Estimando potencia recibida en dBm…", "Proyectando ancho de banda en Mbps…", "Seleccionando tu Kit RuralNet Pro…"];

  el.form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    ocultarError();
    var datos = validar();
    if (datos.error) { mostrarError(datos.error); if (datos.foco) datos.foco.focus(); return; }

    el.analizar.disabled = true;
    el.captura.classList.add("rnx-oculto");
    el.carga.classList.remove("rnx-oculto");
    el.carga.scrollIntoView({ behavior: "smooth", block: "center" });
    var i = 0;
    var rotador = setInterval(function () { el.cargaTexto.textContent = textosCarga[++i % textosCarga.length]; }, 1400);
    var control = new AbortController();
    var reloj = setTimeout(function () { control.abort(); }, TIEMPO_MAX_MS);

    try {
      var resp = await fetch(API_URL + "/registrar-lead", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nombre: datos.nombre, whatsapp: datos.whatsapp,
          lat: coordenadas.lat, lon: coordenadas.lon,
          acepta_datos: true, sitio_web: el.sitio.value
        }),
        signal: control.signal
      });
      var json = null;
      try { json = await resp.json(); } catch (e) { /* respuesta sin JSON */ }
      if (!resp.ok) {
        var det = json && json.detail;
        if (Array.isArray(det)) det = det.map(function (d) { return String(d.msg || "").replace(/^Value error, /, ""); }).join(" ");
        throw new Error(det || "El servidor respondió con un error (" + resp.status + ").");
      }
      renderizar(json, datos.nombre);
    } catch (err) {
      console.error("[RuralNet]", err);
      var msj = err.name === "AbortError"
        ? "El servidor tardó demasiado en responder. Presiona de nuevo el botón."
        : (err instanceof TypeError ? "No pudimos conectarnos. Revisa tu internet e inténtalo otra vez." : err.message);
      el.carga.classList.add("rnx-oculto");
      el.captura.classList.remove("rnx-oculto");
      mapa.invalidateSize();
      mostrarError(msj);
    } finally {
      clearInterval(rotador);
      clearTimeout(reloj);
      el.analizar.disabled = false;
    }
  });

  /* ---------------- Render de resultados ---------------- */
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pesos(n) {
    return new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 }).format(n || 0);
  }
  // Posición del indicador en la escala de -120 dBm (0 %) a -50 dBm (100 %)
  function posicionEscala(dbm) { return Math.max(0, Math.min(100, Math.round((dbm + 120) / 70 * 100))); }

  function enlaceWhatsApp(nombre, d) {
    var kit = d.kit_recomendado;
    var coords = coordenadas.lat.toFixed(6) + ", " + coordenadas.lon.toFixed(6);
    var dbm = {};
    d.operadores.forEach(function (o) { dbm[o.operador] = o.dbm_texto; });
    var senal = "Claro: " + (dbm.Claro || "N/D") + ", Tigo: " + (dbm.Tigo || "N/D") + ", Movistar: " + (dbm.Movistar || "N/D");
    var mbps = d.mejor_opcion.ancho_banda.mbps_estimado;
    var texto = "Hola RuralNet Colombia, mi nombre es " + nombre + ". Acabo de generar mi estudio técnico de frecuencias en la web. " +
                "Mis coordenadas son " + coords + ". ";
    texto += kit.disponible
      ? "El sistema arrojó disponibilidad técnica de ancho de banda (≈ " + mbps + " Mbps estimados) y las siguientes señales: " + senal + ". " +
        "El hardware recomendado es: " + kit.nombre + " (" + pesos(kit.precio_base) + "). " +
        "Deseo agendar asesoría comercial para la compra e instalación inmediata."
      : "El sistema arrojó las siguientes señales: " + senal + " (zona ciega). " +
        "Deseo agendar un estudio técnico personalizado en sitio.";
    return "https://wa.me/" + TELEFONO + "?text=" + encodeURIComponent(texto);
  }

  var ICONO_WA = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2.05 22l5.25-1.38a9.9 9.9 0 0 0 4.74 1.21h.01c5.46 0 9.9-4.45 9.9-9.91A9.86 9.86 0 0 0 12.04 2Zm5.79 14.04c-.24.68-1.41 1.3-1.95 1.35-.5.05-1.13.07-1.82-.11-.42-.13-.96-.31-1.65-.61-2.9-1.25-4.79-4.17-4.94-4.36-.14-.2-1.18-1.57-1.18-3s.75-2.13 1.02-2.42c.26-.29.58-.36.77-.36h.55c.18 0 .42-.07.65.5.24.58.82 2 .89 2.15.07.14.12.31.02.5-.1.2-.14.31-.29.48l-.43.5c-.14.14-.29.3-.13.59.17.29.74 1.22 1.59 1.98 1.09.97 2.01 1.27 2.3 1.42.29.14.46.12.63-.07.17-.2.72-.84.92-1.13.19-.29.38-.24.65-.14.26.1 1.68.79 1.97.94.29.14.48.21.55.33.07.12.07.68-.17 1.36Z"/></svg>';

  function renderizar(d, nombre) {
    var kit = d.kit_recomendado, mejor = d.mejor_opcion;
    var primerNombre = esc(nombre.split(" ")[0]);

    var tarjetas = d.operadores.map(function (o) {
      var dg = o.diagnostico;
      var esMejor = kit.disponible && o.operador === mejor.operador;
      var barras = "";
      for (var b = 1; b <= 4; b++) barras += "<i" + (b <= dg.barras ? ' class="on"' : "") + "></i>";
      return '<article class="rnx-op ' + esc(dg.color) + (esMejor ? " mejor" : "") + '">' +
          '<div class="rnx-op-cab"><div><span class="rnx-op-nombre">' + esc(o.operador) + "</span>" +
            '<span class="rnx-op-banda">' + esc(o.banda_texto) + "</span>" +
            (esMejor ? '<span class="rnx-mejor-sello">★ Recomendado</span>' : "") + "</div>" +
            '<span class="rnx-senal" role="img" aria-label="' + dg.barras + ' de 4 barras de señal">' + barras + "</span></div>" +
          '<div class="rnx-dbm"><b>' + esc(Math.round(o.dbm)) + "</b><span>dBm</span></div>" +
          '<div class="rnx-escala"><i data-pos="' + posicionEscala(o.dbm) + '%"></i></div>' +
          '<div class="rnx-escala-txt"><span>-120</span><span>-50 dBm</span></div>' +
          '<div class="rnx-diag"><span class="rnx-tag">' + esc(dg.etiqueta) + "</span>" +
            '<span class="rnx-km">' + (o.torre_encontrada === false ? "Sin torre a menos de 60 km" : "Torre a " + esc(o.distancia_km.toFixed(1)) + " km") + "</span></div>" +
          '<div class="rnx-mbps-mini"><span>⚡ Velocidad</span><b>≈ ' + esc(o.ancho_banda.mbps_estimado) + "<small>Mbps</small></b></div>" +
          '<p class="rnx-explica">' + esc(dg.explicacion) + "</p>" +
        "</article>";
    }).join("");

    var ab = mejor.ancho_banda;
    var usos = [["💬 WhatsApp y correo", 1], ["🎥 Videollamadas", 10], ["🎓 Clases virtuales", 20], ["📺 Smart TV HD", 30], ["💻 Teletrabajo multi-equipo", 60]];
    var tramos = [10, 30, 60, 90];
    var medidor = tramos.map(function (t, i) {
      var desde = i === 0 ? 0 : tramos[i - 1];
      var lleno = Math.max(0, Math.min(1, (ab.mbps_estimado - desde) / (t - desde)));
      return '<div><i data-w="' + Math.round(lleno * 100) + '%"></i></div>';
    }).join("");
    var abHtml = kit.disponible
      ? '<section class="rnx-ab"><div class="rnx-ab-grid">' +
          '<div><div class="rnx-ab-et">Ancho de banda estimado · ' + esc(mejor.operador) + "</div>" +
            '<div class="rnx-ab-num"><b data-mbps="' + esc(ab.mbps_estimado) + '">0</b><span>Mbps</span></div>' +
            '<div class="rnx-ab-rango">Rango: ' + esc(ab.rango_texto) + " · " + esc(mejor.dbm_texto) + "</div></div>" +
          '<div><div class="rnx-ab-medidor">' + medidor + "</div>" +
            '<div class="rnx-ab-escala"><span>&lt;10</span><span>10-30</span><span>30-60</span><span>60-90 Mbps</span></div></div>' +
        "</div>" +
        '<p class="rnx-ab-msj">' + esc(ab.mensaje) + "</p>" +
        '<div class="rnx-ab-usos">' + usos.map(function (u) {
          return '<span class="' + (ab.mbps_estimado >= u[1] ? "si" : "no") + '">' + u[0] + "</span>";
        }).join("") + "</div></section>"
      : "";

    var kitHtml = kit.disponible
      ? '<div class="rnx-kit"><div class="rnx-kit-cab"><div><div class="rnx-kit-et">Hardware recomendado</div><h4>' + esc(kit.nombre) + "</h4></div>" +
          '<div class="rnx-precio"><small>Precio base · IVA incluido</small><b>' + pesos(kit.precio_base) + "</b></div></div>" +
          "<p>" + esc(kit.mensaje) + "</p>" +
          "<ul>" + kit.componentes.map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("") + "</ul></div>"
      : '<div class="rnx-kit"><div class="rnx-kit-et">Siguiente paso</div><h4>' + esc(kit.nombre) + "</h4><p>" + esc(kit.mensaje) + "</p></div>";

    el.resultado.innerHTML =
      '<div class="rnx-res">' +
        '<div class="rnx-res-top"><div><h3>' + primerNombre + ", este es tu estudio de frecuencias</h3>" +
        "<p>Señal (dBm) y ancho de banda (Mbps) estimados en tu predio por operador, con modelo de propagación RF.</p></div>" +
        '<span class="rnx-coord">' + esc(coordenadas.lat.toFixed(5) + ", " + coordenadas.lon.toFixed(5)) + "</span></div>" +
        '<div class="rnx-ops">' + tarjetas + "</div>" +
        abHtml +
        kitHtml +
        '<a class="rnx-btn rnx-btn-wa" target="_blank" rel="noopener" href="' + enlaceWhatsApp(nombre, d) + '">' + ICONO_WA +
          (kit.disponible ? "Agendar asesoría e instalación por WhatsApp" : "Hablar con un asesor por WhatsApp") + "</a>" +
        '<p class="rnx-aviso">' + esc(d.aviso) + "</p>" +
        '<button type="button" class="rnx-otra" id="rnx-otra">Analizar otra ubicación</button>' +
      "</div>";

    el.carga.classList.add("rnx-oculto");
    el.resultado.classList.remove("rnx-oculto");
    marcarPaso(3);
    requestAnimationFrame(function () {
      setTimeout(function () {
        el.resultado.querySelectorAll(".rnx-escala i").forEach(function (p) { p.style.left = p.getAttribute("data-pos"); });
        el.resultado.querySelectorAll(".rnx-ab-medidor i").forEach(function (b, i) {
          setTimeout(function () { b.style.width = b.getAttribute("data-w"); }, 350 + i * 420);
        });
        var num = el.resultado.querySelector("[data-mbps]");
        if (num) contarHasta(num, parseInt(num.getAttribute("data-mbps"), 10) || 0, 1500);
      }, 120);
    });
    el.resultado.scrollIntoView({ behavior: "smooth", block: "start" });

    $("rnx-otra").addEventListener("click", function () {
      el.resultado.classList.add("rnx-oculto");
      el.captura.classList.remove("rnx-oculto");
      marcarPaso(2);
      mapa.invalidateSize();
      el.captura.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // Contador animado de 0 al valor final
  function contarHasta(nodo, final, ms) {
    var t0 = null;
    function paso(t) {
      if (!t0) t0 = t;
      var k = Math.min(1, (t - t0) / ms);
      nodo.textContent = Math.round(final * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(paso);
    }
    requestAnimationFrame(paso);
  }

  function mostrarError(msj) { el.error.textContent = "⚠️ " + msj; el.error.classList.remove("rnx-oculto"); }
  function ocultarError() { el.error.classList.add("rnx-oculto"); }
})();
