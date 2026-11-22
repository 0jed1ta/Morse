importScripts("lib/workbox-v7.4.1/workbox-sw.js");
workbox.setConfig({ modulePathPrefix: "lib/workbox-v7.4.1/", debug: false });

const { precacheAndRoute, cleanupOutdatedCaches } = workbox.precaching;
const { registerRoute } = workbox.routing;
const { CacheFirst, NetworkFirst } = workbox.strategies;
const { ExpirationPlugin } = workbox.expiration;
const URL_BASE = new URL(self.registration.scope);
const rutaEnScope = (ruta) => new URL(ruta, URL_BASE).pathname;
const RUTA_ICONOS = rutaEnScope("icons/");
const RUTA_API = rutaEnScope("api/");
const URL_INICIO = new URL("index.html", URL_BASE).href;

self.skipWaiting();
workbox.core.clientsClaim();
cleanupOutdatedCaches();
precacheAndRoute([{"revision":"4f645e4101068851a207837eab92bafc","url":"index.html"},{"revision":"4b8bd1e576949d4f2c3a5096dfcca2de","url":"manifest.json"},{"revision":"87b9d2694b9e02a2e26fcd590bf4d0c3","url":"css/estilos.css"},{"revision":"48969afc22fb08ced992128ea41e4d89","url":"tareas.js"},{"revision":"c91622f90d228c0aa50cb4cc9fcaa377","url":"sync.js"},{"revision":"f401e0d081efc5a304a8253260784a63","url":"sensores.js"},{"revision":"69cd065bca79ac14ea1641e95f89b97b","url":"notificaciones.js"},{"revision":"dc281b70aea49e5525e2343f8269306f","url":"dispositivo.js"},{"revision":"cdc81d730bd8024bee54cafda4a537b8","url":"db.js"},{"revision":"200128abd7e14f28b5147f2a71e59527","url":"crypto.js"},{"revision":"94b6c88a22baa4888edbe35c84192149","url":"app.js"},{"revision":"916675198be1f0b31c9535700364d050","url":"icons/icon-512.png"},{"revision":"238df3f8103dfc9c36e481b23510eb84","url":"icons/icon-512-maskable.png"},{"revision":"30dad524c42638418c4249e28bda4dbe","url":"icons/icon-192.png"}]);

registerRoute(
  ({ url }) => url.origin === self.location.origin && url.pathname.startsWith(RUTA_ICONOS),
  new CacheFirst({
    cacheName: "imagenes",
    plugins: [new ExpirationPlugin({ maxEntries: 20, maxAgeSeconds: 2592000 })],
  })
);

registerRoute(
  ({ url }) => url.origin === self.location.origin && url.pathname.startsWith(RUTA_API),
  new NetworkFirst({ cacheName: "api-tareas" })
);

self.addEventListener("push", (evento) => {
  let datos = { titulo: "Gestor de Tareas", cuerpo: "Tienes novedades.", url: URL_INICIO };
  try {
    if (evento.data) datos = { ...datos, ...evento.data.json() };
  } catch {
    if (evento.data) datos.cuerpo = evento.data.text();
  }
  evento.waitUntil(
    self.registration.showNotification(datos.titulo, {
      body: datos.cuerpo,
      icon: new URL("icons/icon-192.png", URL_BASE).href,
      badge: new URL("icons/icon-192.png", URL_BASE).href,
      tag: datos.tag ?? "gestor-aviso",
      data: { url: datos.url },
    })
  );
});

self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  const destino = new URL(evento.notification.data?.url ?? URL_INICIO, URL_BASE).href;
  evento.waitUntil(
    (async () => {
      const ventanas = await clients.matchAll({ type: "window", includeUncontrolled: true });
      const abierta = ventanas.find((ventana) => new URL(ventana.url).origin === self.location.origin);
      if (!abierta) return clients.openWindow(destino);
      if (abierta.url !== destino && "navigate" in abierta) await abierta.navigate(destino);
      return abierta.focus();
    })()
  );
});

function abrirBaseDeDatos() {
  return new Promise((resolve, reject) => {
    const peticion = indexedDB.open("gestor-tareas-db");
    peticion.onsuccess = () => resolve(peticion.result);
    peticion.onerror = () => reject(peticion.error);
  });
}

function resolverPeticion(peticion) {
  return new Promise((resolve, reject) => {
    peticion.onsuccess = () => resolve(peticion.result);
    peticion.onerror = () => reject(peticion.error);
  });
}

async function vaciarBandeja() {
  const db = await abrirBaseDeDatos();
  const pendientes = await resolverPeticion(db.transaction("outbox").objectStore("outbox").getAll());
  let enviados = 0;
  for (const cambio of pendientes) {
    const respuesta = await fetch(rutaEnScope("api/tareas"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cambio),
    });
    if (!respuesta.ok) throw new Error(`El servidor respondió ${respuesta.status}`);
    await resolverPeticion(db.transaction("outbox", "readwrite").objectStore("outbox").delete(cambio.idCambio));
    enviados += 1;
  }
  db.close();
  return enviados;
}

async function avisarDeSincronizacion(enviados) {
  if (enviados === 0) return;
  const ventanas = await clients.matchAll({ type: "window" });
  ventanas.forEach((ventana) => ventana.postMessage({ tipo: "sync-completo", enviados }));
  if (!ventanas.some((ventana) => ventana.visibilityState === "visible")) {
    await self.registration.showNotification("Tareas sincronizadas", {
      body: enviados === 1 ? "1 tarea llegó al servidor." : `${enviados} tareas llegaron al servidor.`,
      icon: new URL("icons/icon-192.png", URL_BASE).href,
      tag: "gestor-sync",
      data: { url: URL_INICIO },
    });
  }
}

self.addEventListener("sync", (evento) => {
  if (evento.tag === "sync-tareas") {
    evento.waitUntil(vaciarBandeja().then(avisarDeSincronizacion));
  }
});
