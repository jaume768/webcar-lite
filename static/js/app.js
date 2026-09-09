/*
 * Pegamento de interfaz: avisos, confirmaciones y errores de HTMX.
 *
 * Se carga antes que Alpine (defer conserva el orden) y registra los stores en
 * el evento alpine:init.
 */
(function () {
  "use strict";

  const ICONO = (d, color) =>
    `<svg class="size-5 ${color}" fill="none" viewBox="0 0 24 24" stroke="currentColor"` +
    ` stroke-width="1.8" aria-hidden="true"><path stroke-linecap="round"` +
    ` stroke-linejoin="round" d="${d}"/></svg>`;

  const DURACION_POR_DEFECTO = 5000;

  document.addEventListener("alpine:init", () => {
    Alpine.store("toasts", {
      lista: [],
      siguienteId: 1,

      estilos: {
        success: "border-emerald-200 text-emerald-900",
        info: "border-sky-200 text-sky-900",
        warning: "border-amber-200 text-amber-900",
        error: "border-rose-200 text-rose-900",
        debug: "border-slate-200 text-slate-700",
      },

      iconos: {
        success: ICONO("M4.5 12.75l6 6 9-13.5", "text-emerald-600"),
        info: ICONO(
          "M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z",
          "text-sky-600"
        ),
        warning: ICONO(
          "M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z",
          "text-amber-600"
        ),
        error: ICONO("M6 18L18 6M6 6l12 12", "text-rose-600"),
        debug: ICONO("M12 6.75v10.5", "text-slate-500"),
      },

      añadir(mensaje, nivel = "info", duracion = DURACION_POR_DEFECTO) {
        if (!mensaje) return;
        const id = this.siguienteId++;
        this.lista.push({ id, message: mensaje, level: this.normalizar(nivel), visible: true });
        if (duracion > 0) window.setTimeout(() => this.cerrar(id), duracion);
        return id;
      },

      cerrar(id) {
        const toast = this.lista.find((t) => t.id === id);
        if (!toast) return;
        toast.visible = false;
        window.setTimeout(() => {
          this.lista = this.lista.filter((t) => t.id !== id);
        }, 250);
      },

      /* django.contrib.messages usa error/warning/success/info/debug. */
      normalizar(nivel) {
        return this.estilos[nivel] ? nivel : "info";
      },

      /* Recoge los mensajes serializados por el servidor en la carga inicial. */
      desdeMensajes(contenedor) {
        contenedor.querySelectorAll("script[data-toast]").forEach((nodo) => {
          try {
            const datos = JSON.parse(nodo.textContent);
            this.añadir(datos.message, datos.level);
          } catch (error) {
            console.error("Aviso mal formado", error);
          }
          nodo.remove();
        });
      },
    });

    Alpine.store("confirmar", {
      abierto: false,
      titulo: "",
      mensaje: "",
      etiquetaAceptar: "Confirmar",
      _resolver: null,

      /* Devuelve una promesa: true si el usuario confirma. */
      pedir({ titulo, mensaje, etiquetaAceptar } = {}) {
        this.titulo = titulo || "¿Confirmas la acción?";
        this.mensaje = mensaje || "Esta acción no se puede deshacer.";
        this.etiquetaAceptar = etiquetaAceptar || "Confirmar";
        this.abierto = true;
        return new Promise((resolve) => {
          this._resolver = resolve;
        });
      },

      _cerrar(valor) {
        this.abierto = false;
        const resolver = this._resolver;
        this._resolver = null;
        if (resolver) resolver(valor);
      },

      aceptar() {
        this._cerrar(true);
      },
      cancelar() {
        this._cerrar(false);
      },
    });

    /* Modal generico: ver ui/_modal.html. */
    Alpine.data("modal", () => ({
      abierto: true,
      cerrar() {
        this.abierto = false;
        window.setTimeout(() => this.$root.remove(), 200);
      },
    }));

    /* Select con busqueda: ver ui/_select_search.html. */
    Alpine.data("selectBuscador", () => ({
      abierto: false,
      activa: -1,

      opciones() {
        return Array.from(this.$refs.opciones.querySelectorAll('li[role="option"]'));
      },

      abrir() {
        this.abierto = true;
        this.activa = -1;
        this.pintar();
      },

      cerrar() {
        this.abierto = false;
        this.activa = -1;
      },

      mover(paso) {
        const opciones = this.opciones();
        if (!opciones.length) return;
        this.abierto = true;
        this.activa = (this.activa + paso + opciones.length) % opciones.length;
        this.pintar();
        opciones[this.activa].scrollIntoView({ block: "nearest" });
      },

      pintar() {
        this.opciones().forEach((opcion, indice) => {
          opcion.setAttribute("aria-selected", indice === this.activa ? "true" : "false");
        });
      },

      elegirActiva() {
        const opciones = this.opciones();
        if (this.activa >= 0 && opciones[this.activa]) this.elegir(opciones[this.activa]);
      },

      elegirDesdeClic(evento) {
        const opcion = evento.target.closest('li[role="option"]');
        if (opcion) this.elegir(opcion);
      },

      elegir(opcion) {
        this.$refs.valor.value = opcion.dataset.value;
        this.$refs.buscador.value = opcion.dataset.label;
        this.$refs.valor.dispatchEvent(new Event("change", { bubbles: true }));
        this.cerrar();
      },
    }));
  });

  const avisar = (mensaje, nivel) => {
    if (window.Alpine && Alpine.store("toasts")) Alpine.store("toasts").añadir(mensaje, nivel);
    else console.warn(mensaje);
  };

  /* Avisos disparados por el servidor: HX-Trigger: {"toast": {...}} */
  document.body.addEventListener("toast", (evento) => {
    const datos = evento.detail || {};
    avisar(datos.message, datos.level || "info");
  });

  /* Confirmacion de acciones destructivas: sustituye al window.confirm de HTMX
     por el dialogo propio, sin que las vistas tengan que saberlo. */
  document.body.addEventListener("htmx:confirm", (evento) => {
    const pregunta = evento.detail.question;
    if (!pregunta || !window.Alpine) return;
    evento.preventDefault();
    Alpine.store("confirmar")
      .pedir({
        mensaje: pregunta,
        titulo: evento.detail.elt.dataset.confirmTitulo,
        etiquetaAceptar: evento.detail.elt.dataset.confirmAceptar,
      })
      .then((aceptado) => {
        if (aceptado) evento.detail.issueRequest(true);
      });
  });

  /* Errores de HTMX como aviso: el usuario no puede quedarse mirando una
     pantalla que no cambia. */
  const MENSAJES_HTTP = {
    403: "No tienes permiso para hacer esto.",
    404: "No se ha encontrado el recurso.",
    409: "Alguien ha modificado estos datos antes que tú. Vuelve a cargar.",
    422: "Revisa los datos del formulario.",
  };

  document.body.addEventListener("htmx:responseError", (evento) => {
    const codigo = evento.detail.xhr.status;
    avisar(
      MENSAJES_HTTP[codigo] || `Error del servidor (${codigo}). Inténtalo de nuevo.`,
      codigo >= 500 ? "error" : "warning"
    );
  });

  document.body.addEventListener("htmx:sendError", () => {
    avisar("Sin conexión con el servidor. Comprueba la red.", "error");
  });

  document.body.addEventListener("htmx:timeout", () => {
    avisar("El servidor ha tardado demasiado en responder.", "warning");
  });
})();
