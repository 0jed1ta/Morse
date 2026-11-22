export function estadoNotificaciones() {
  return "Notification" in window ? Notification.permission : "no-soportado";
}

export async function pedirPermisoNotificaciones() {
  if (!("Notification" in window)) {
    return "no-soportado";
  }
  return Notification.requestPermission();
}

const urlAplicacion = (ruta = "index.html") => new URL(ruta, import.meta.url).href;
const urlApi = (ruta) => new URL(`api/${ruta}`, import.meta.url).pathname;

export async function mostrarAvisoLocal(titulo, cuerpo, url = urlAplicacion()) {
  const registro = await navigator.serviceWorker.ready;
  await registro.showNotification(titulo, {
    body: cuerpo,
    icon: urlAplicacion("icons/icon-192.png"),
    tag: "gestor-aviso",
    data: { url },
  });
}

export function urlBase64ABytes(base64url) {
  const relleno = "=".repeat((4 - (base64url.length % 4)) % 4);
  const base64 = (base64url + relleno).replace(/-/g, "+").replace(/_/g, "/");
  const binario = atob(base64);
  return Uint8Array.from(binario, (caracter) => caracter.charCodeAt(0));
}

export async function suscribirAPush() {
  const registro = await navigator.serviceWorker.ready;
  if (!("pushManager" in registro)) {
    throw new Error("push-no-soportado");
  }
  const respuestaClave = await fetch(urlApi("clave-publica"));
  if (!respuestaClave.ok) {
    throw new Error(`No se obtuvo la clave pública (${respuestaClave.status})`);
  }
  const { clave } = await respuestaClave.json();
  const suscripcion = await registro.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ABytes(clave),
  });
  const respuesta = await fetch(urlApi("suscribir"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(suscripcion.toJSON()),
  });
  if (!respuesta.ok) {
    throw new Error(`El servidor no guardó la suscripción (${respuesta.status})`);
  }
  return suscripcion;
}

export async function suscripcionActual() {
  const registro = await navigator.serviceWorker.ready;
  if (!("pushManager" in registro)) {
    return null;
  }
  return registro.pushManager.getSubscription();
}

export async function cancelarSuscripcion() {
  const suscripcion = await suscripcionActual();
  if (!suscripcion) {
    return false;
  }
  const respuesta = await fetch(urlApi("desuscribir"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: suscripcion.endpoint }),
  });
  if (!respuesta.ok) {
    throw new Error(`El servidor no eliminó la suscripción (${respuesta.status})`);
  }
  return suscripcion.unsubscribe();
}

export function describirErrorPush(error) {
  if (error?.name === "NotAllowedError") {
    return "El permiso de notificaciones fue denegado. Puedes cambiarlo en la configuración del sitio.";
  }
  if (error?.name === "AbortError") {
    return "El servicio de mensajes del navegador no respondió.";
  }
  if (error?.message === "push-no-soportado") {
    return "Este navegador no soporta mensajes push.";
  }
  if (error?.name === "InvalidStateError") {
    return "Se necesita un Service Worker activo. Recarga la página e inténtalo de nuevo.";
  }
  return `No se pudo completar la suscripción: ${error?.message ?? error}`;
}
