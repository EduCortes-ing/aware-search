// =====================================================================
// app.js - Logica del frontend (login, sesion y buscador)
// Se usa en login.html y en index.html; cada pagina dice quien es con
// el atributo data-pagina del <body>.
// Seguridad: el frontend NO protege nada por si solo; la proteccion real
// es que la API rechace peticiones sin token valido. Aqui solo evitamos
// errores comunes (XSS, guardar secretos, sesiones eternas).
// =====================================================================

"use strict";                                         // modo estricto: detecta errores comunes de JavaScript

const CLAVE_TOKEN = "aware_token";                    // nombre con el que se guarda el token en el navegador
const CLAVE_USUARIO = "aware_usuario";                // nombre con el que se guarda el usuario
const API = "/api";                                   // Nginx reenvia /api al contenedor de la API

// ---------------------------------------------------------------------
// Sesion: el token vive en sessionStorage, que se borra al cerrar la pestaña
// ---------------------------------------------------------------------
function leerToken() {                                // devuelve el token guardado o null
  try { return sessionStorage.getItem(CLAVE_TOKEN); } // lee el token
  catch { return null; }                              // si el navegador bloquea el almacenamiento, no hay sesion
}

function guardarSesion(datos) {                       // guarda token y usuario tras un login correcto
  try {
    sessionStorage.setItem(CLAVE_TOKEN, datos.access_token);  // el token firmado por auth-service
    sessionStorage.setItem(CLAVE_USUARIO, datos.username);    // el nombre para el saludo
  } catch { /* sin almacenamiento: la sesion dura solo esta pagina */ }
}

function cerrarSesion(motivo) {                       // borra la sesion y regresa al login
  try { sessionStorage.clear(); } catch { /* nada que borrar */ }  // elimina token y usuario
  const destino = motivo ? `login.html?motivo=${encodeURIComponent(motivo)}` : "login.html";  // explica por que
  window.location.replace(destino);                   // replace: el boton "atras" no regresa al buscador
}

// ---------------------------------------------------------------------
// Llamadas a la API
// ---------------------------------------------------------------------
function textoDeError(cuerpo, estado) {               // convierte la respuesta de error en un mensaje legible
  const detalle = cuerpo && cuerpo.detail;            // FastAPI pone el error en "detail"
  if (typeof detalle === "string") return detalle;    // mensaje simple de nuestros servicios
  if (Array.isArray(detalle) && detalle.length) {     // errores de validacion (lista)
    return detalle.map((e) => e.msg).join(". ");      // une los mensajes
  }
  if (estado === 429) return "Demasiados intentos. Espera un momento y vuelve a intentar.";  // limite de Nginx
  if (estado >= 500) return "El servicio no está disponible. Intenta más tarde.";             // falla del servidor
  return "Ocurrió un error inesperado.";              // cualquier otro caso
}

async function pedir(ruta, opciones = {}) {           // hace una peticion a la API y devuelve el JSON
  const cabeceras = { Accept: "application/json" };   // pedimos respuesta en JSON
  if (opciones.cuerpo) cabeceras["Content-Type"] = "application/json";  // enviamos JSON
  const token = leerToken();                          // token actual (si hay)
  if (token) cabeceras.Authorization = `Bearer ${token}`;  // se manda en cada peticion
  let respuesta;                                      // aqui queda la respuesta del servidor
  try {
    respuesta = await fetch(API + ruta, {             // peticion HTTP
      method: opciones.metodo || "GET",               // GET por defecto
      headers: cabeceras,                             // cabeceras preparadas arriba
      body: opciones.cuerpo ? JSON.stringify(opciones.cuerpo) : undefined,  // cuerpo en JSON
      signal: opciones.senal,                         // permite cancelar busquedas viejas
      credentials: "same-origin",                     // nunca envia datos a otros sitios
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;             // cancelacion intencional: se propaga tal cual
    throw new Error("No hay conexión con el servidor."); // sin red o servidor caido
  }
  let cuerpo = null;                                  // el JSON de la respuesta
  try { cuerpo = await respuesta.json(); } catch { /* respuesta sin JSON */ }
  if (respuesta.status === 401 && opciones.exigeSesion) {  // token vencido o invalido en una ruta protegida
    cerrarSesion("expirada");                         // regresa al login con aviso
    throw new Error("Sesión expirada");               // detiene el resto del codigo
  }
  if (!respuesta.ok) {                                // cualquier otro error (400, 422, 429, 502...)
    const error = new Error(textoDeError(cuerpo, respuesta.status));  // mensaje legible
    error.estado = respuesta.status;                  // guarda el codigo por si se necesita
    throw error;                                      // lo maneja quien llamo
  }
  return cuerpo;                                      // datos correctos
}

// ---------------------------------------------------------------------
// Pantalla de login y registro
// ---------------------------------------------------------------------
function iniciarLogin() {
  if (leerToken()) { window.location.replace("index.html"); return; }  // ya hay sesion: directo al buscador

  const formLogin = document.getElementById("form-login");        // formulario de entrar
  const formRegistro = document.getElementById("form-registro");  // formulario de crear cuenta
  const tabLogin = document.getElementById("tab-login");          // pestaña "Entrar"
  const tabRegistro = document.getElementById("tab-registro");    // pestaña "Crear cuenta"
  const mensaje = document.getElementById("mensaje");             // zona de avisos

  function avisar(texto, tipo = "error") {            // muestra un aviso (error u ok)
    mensaje.textContent = texto;                      // textContent: el texto nunca se interpreta como HTML
    mensaje.dataset.tipo = tipo;                      // el CSS lo pinta segun el tipo
  }

  const motivo = new URLSearchParams(location.search).get("motivo");  // por que llegamos al login
  if (motivo === "expirada") avisar("Tu sesión expiró. Inicia sesión de nuevo.", "info");
  if (motivo === "salida") avisar("Cerraste sesión correctamente.", "ok");

  function mostrar(cual) {                            // cambia entre las dos pestañas
    const esLogin = cual === "login";                 // true si se eligio "Entrar"
    formLogin.hidden = !esLogin;                      // muestra u oculta cada formulario
    formRegistro.hidden = esLogin;
    tabLogin.classList.toggle("activa", esLogin);     // resalta la pestaña elegida
    tabRegistro.classList.toggle("activa", !esLogin);
    tabLogin.setAttribute("aria-selected", String(esLogin));   // accesibilidad
    tabRegistro.setAttribute("aria-selected", String(!esLogin));
    avisar("", "info");                               // limpia avisos anteriores
    (esLogin ? formLogin : formRegistro).querySelector("input").focus();  // cursor en el primer campo
  }
  tabLogin.addEventListener("click", () => mostrar("login"));       // clic en "Entrar"
  tabRegistro.addEventListener("click", () => mostrar("registro")); // clic en "Crear cuenta"

  async function entrar(usuario, clave) {             // pide el token y pasa al buscador
    const datos = await pedir("/login", { metodo: "POST", cuerpo: { username: usuario, password: clave } });
    guardarSesion(datos);                             // guarda token y usuario
    window.location.replace("index.html");            // abre el buscador
  }

  function bloquear(form, ocupado) {                  // desactiva el boton mientras se espera respuesta
    const boton = form.querySelector("button[type=submit]");  // boton de enviar
    boton.disabled = ocupado;                         // evita dobles envios
    boton.classList.toggle("cargando", ocupado);      // muestra que esta trabajando
  }

  formLogin.addEventListener("submit", async (evento) => {   // al enviar el login
    evento.preventDefault();                          // evita que la pagina se recargue
    const usuario = formLogin.username.value.trim();  // usuario sin espacios
    const clave = formLogin.password.value;           // contrasena tal cual
    if (!usuario || !clave) { avisar("Escribe tu usuario y contraseña."); return; }  // campos vacios
    bloquear(formLogin, true);                        // boton ocupado
    try { await entrar(usuario, clave); }             // intenta entrar
    catch (e) { avisar(e.message); formLogin.password.value = ""; formLogin.password.focus(); }  // muestra el error y limpia la clave
    finally { bloquear(formLogin, false); }           // libera el boton
  });

  formRegistro.addEventListener("submit", async (evento) => {  // al enviar el registro
    evento.preventDefault();                          // evita recargar la pagina
    const codigo = formRegistro.invitation_code.value.trim();  // codigo de invitacion
    const usuario = formRegistro.username.value.trim().toLowerCase();  // usuario en minusculas
    const clave = formRegistro.password.value;        // contrasena
    if (!codigo || !usuario || !clave) { avisar("Completa los tres campos."); return; }  // faltan datos
    bloquear(formRegistro, true);                     // boton ocupado
    try {
      await pedir("/register", { metodo: "POST", cuerpo: { username: usuario, password: clave, invitation_code: codigo } });
      await entrar(usuario, clave);                   // si se creo la cuenta, entra de una vez
    } catch (e) { avisar(e.message); }                // muestra por que no se pudo
    finally { bloquear(formRegistro, false); }        // libera el boton
  });
}

// ---------------------------------------------------------------------
// Pantalla del buscador
// ---------------------------------------------------------------------
const ETIQUETAS = {                                   // texto en español de cada categoria
  "Access": "Access",
  "Watch": "Watch",
  "Reserve": "Reserve",
  "Not recommended": "No recomendado",
};

function claseCss(categoria) {                        // "Not recommended" -> "not-recommended"
  return categoria.toLowerCase().replace(/\s+/g, "-");  // minusculas y guiones
}

async function iniciarBuscador() {
  if (!leerToken()) { cerrarSesion(); return; }       // sin token no se muestra nada

  const entrada = document.getElementById("q");               // caja de texto
  const estado = document.getElementById("estado");           // linea de estado
  const lista = document.getElementById("resultados");        // lista de tarjetas
  const plantilla = document.getElementById("plantilla-tarjeta");  // molde de cada tarjeta
  let controlador = null;                             // permite cancelar la busqueda anterior
  let espera = null;                                  // temporizador para no buscar en cada tecla

  try {                                               // confirma con la API que el token sigue valido
    const yo = await pedir("/me", { exigeSesion: true });       // si no es valido, cerrarSesion() redirige
    document.getElementById("saludo").textContent = `Hola, ${yo.username}`;  // saludo seguro (textContent)
  } catch { return; }                                 // ya se redirigio al login
  document.body.classList.remove("oculto-sin-sesion");  // ahora si se muestra la pagina

  document.getElementById("salir").addEventListener("click", () => cerrarSesion("salida"));  // boton salir

  function pintar(datos) {                            // dibuja las tarjetas de resultados
    lista.replaceChildren();                          // borra los resultados anteriores
    if (datos.count === 0) {                          // sin coincidencias
      estado.textContent = `Sin coincidencias para “${datos.query}”. Revisa la ortografía o prueba con el nombre en inglés.`;
      return;
    }
    estado.textContent = datos.count === 1 ? "1 resultado" : `${datos.count} resultados`;  // cuantos hay
    for (const a of datos.results) {                  // una tarjeta por antibiotico
      const t = plantilla.content.firstElementChild.cloneNode(true);  // copia del molde
      t.querySelector(".nombre").textContent = a.name;              // nombre (texto, no HTML)
      const chip = t.querySelector(".categoria");                   // etiqueta de color
      chip.textContent = ETIQUETAS[a.category] || a.category;       // texto de la categoria
      chip.classList.add(claseCss(a.category));                     // color segun la categoria
      t.classList.add(claseCss(a.category));                        // borde de color en la tarjeta
      t.querySelector(".clase").textContent = a.drug_class || "—";  // clase o guion si no hay
      t.querySelector(".atc").textContent = a.atc_code || "—";      // codigo ATC o guion
      t.querySelector(".eml").textContent = a.listed_eml ? "Sí" : "No";  // en la lista esencial
      if (a.notes) {                                  // nota de la OMS (alternativas terapeuticas)
        const nota = t.querySelector(".nota");
        nota.textContent = a.notes.replace(/^\*+\s*/, "");          // quita los asteriscos del Excel
        nota.hidden = false;                          // la muestra
      }
      lista.appendChild(t);                           // agrega la tarjeta a la lista
    }
  }

  async function buscar(texto) {                      // consulta la API con el texto escrito
    if (controlador) controlador.abort();             // cancela la busqueda anterior si seguia en curso
    controlador = new AbortController();              // nuevo controlador para esta busqueda
    estado.textContent = "Buscando…";                 // aviso mientras llega la respuesta
    try {
      const params = new URLSearchParams({ q: texto, limit: "25" });  // parametros codificados de forma segura
      const datos = await pedir(`/search?${params}`, { senal: controlador.signal, exigeSesion: true });
      pintar(datos);                                  // dibuja los resultados
    } catch (e) {
      if (e.name === "AbortError") return;            // se cancelo porque el usuario siguio escribiendo
      lista.replaceChildren();                        // limpia resultados viejos
      estado.textContent = e.message;                 // muestra el error
    }
  }

  entrada.addEventListener("input", () => {           // cada vez que cambia el texto
    clearTimeout(espera);                             // reinicia la espera
    const texto = entrada.value.trim();               // texto sin espacios en los extremos
    if (texto.length < 2) {                           // muy corto: no se busca
      if (controlador) controlador.abort();           // cancela cualquier busqueda pendiente
      lista.replaceChildren();                        // limpia la lista
      estado.textContent = texto ? "Escribe al menos 2 letras." : "";  // ayuda
      return;
    }
    espera = setTimeout(() => buscar(texto), 300);    // busca 0.3 s despues de la ultima tecla
  });

  document.getElementById("form-busqueda").addEventListener("submit", (evento) => {  // tecla Enter
    evento.preventDefault();                          // evita recargar la pagina
    clearTimeout(espera);                             // cancela la espera
    const texto = entrada.value.trim();               // texto actual
    if (texto.length >= 2) buscar(texto);             // busca de inmediato
  });
}

// ---------------------------------------------------------------------
// Arranque: elige que pantalla iniciar segun el <body data-pagina="...">
// ---------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => { // espera a que el HTML este listo
  const pagina = document.body.dataset.pagina;        // "login" o "buscador"
  if (pagina === "login") iniciarLogin();             // pantalla de acceso
  else if (pagina === "buscador") iniciarBuscador();  // pantalla de busqueda
});
