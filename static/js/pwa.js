/*
 * App instalable: registra el service worker y gobierna el boton "Instalar app".
 *
 * - Android / Chrome / Edge: el navegador avisa con `beforeinstallprompt`; se
 *   guarda el evento y los botones [data-pwa-install] lo lanzan.
 * - iPhone / iPad: Safari no tiene ese evento. Se ensena [data-pwa-ios], que
 *   explica el paso por "Compartir > Anadir a pantalla de inicio".
 * - Ya instalada (se abre en modo app): no se ensena nada.
 */
(function () {
  "use strict";

  // Solo en contexto seguro (HTTPS o localhost): fuera de el no hay service worker.
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  var instalada =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  var aviso = null;

  function mostrar(selector, visible) {
    document.querySelectorAll(selector).forEach(function (el) {
      el.hidden = !visible;
    });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    aviso = event;
    mostrar("[data-pwa-install]", true);
  });

  window.addEventListener("appinstalled", function () {
    aviso = null;
    mostrar("[data-pwa-install]", false);
  });

  document.addEventListener("click", function (event) {
    var boton = event.target.closest("[data-pwa-install]");
    if (!boton || !aviso) return;
    aviso.prompt();
    aviso.userChoice.finally(function () {
      aviso = null;
      mostrar("[data-pwa-install]", false);
    });
  });

  document.addEventListener("DOMContentLoaded", function () {
    // iPadOS se presenta como Mac: se distingue por la pantalla tactil.
    var ios =
      /iphone|ipad|ipod/i.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (ios && !instalada) mostrar("[data-pwa-ios]", true);
  });
})();
