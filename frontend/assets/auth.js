// Separación por tipo de usuario (conductor / admin).
//
// IMPORTANTE: esto es un selector del LADO DEL CLIENTE, no un login real.
// Decide qué pantallas mostrar, pero no protege nada: el backend hoy no
// valida quién llama a cada endpoint. Es una separación de experiencia de
// usuario (UX), no un control de seguridad. Si se quiere seguridad real más
// adelante, hay que agregar una tabla Usuario en la base, un endpoint de
// login que devuelva un token, y que el backend exija y valide ese token
// en los endpoints de admin.

const ROLES = { CONDUCTOR: "conductor", ADMIN: "admin" };

function getRole() {
  try {
    return localStorage.getItem("accesoseguro_rol");
  } catch (e) {
    return null;
  }
}

function setRole(rol) {
  try {
    localStorage.setItem("accesoseguro_rol", rol);
  } catch (e) {
    /* si el navegador bloquea localStorage, seguimos sin persistir rol */
  }
}

function logout() {
  try {
    localStorage.removeItem("accesoseguro_rol");
  } catch (e) {}
  window.location.href = "login.html";
}

// Redirige a login si todavía no se eligió rol, o a index si el rol
// elegido no tiene permitido ver esta pantalla.
function requireRole(rolesPermitidos) {
  const rol = getRole();
  if (!rol) {
    window.location.href = "login.html";
    return null;
  }
  if (!rolesPermitidos.includes(rol)) {
    window.location.href = "index.html";
    return null;
  }
  return rol;
}

const NAV_CONDUCTOR = [
  { href: "nueva-solicitud.html", label: "Nueva solicitud" },
  { href: "mis-solicitudes.html", label: "Mis solicitudes" },
];

const NAV_ADMIN = [
  { href: "panel-aprobacion.html", label: "Aprobación" },
  { href: "solicitudes.html", label: "Todas" },
  { href: "verificacion.html", label: "Verificación" },
  { href: "reporte.html", label: "Reporte" },
  { href: "auditoria.html", label: "Auditoría" },
];

function renderNav() {
  const nav = document.getElementById("nav-links");
  if (!nav) return;
  const rol = getRole();
  const links = rol === ROLES.ADMIN ? NAV_ADMIN : NAV_CONDUCTOR;
  const linksHtml = links.map((l) => `<a href="${l.href}">${l.label}</a>`).join("");
  const salirHtml = `<a href="#" id="link-salir">Salir${rol ? ` (${rol})` : ""}</a>`;
  nav.innerHTML = linksHtml + salirHtml;
  document.getElementById("link-salir").addEventListener("click", (e) => {
    e.preventDefault();
    logout();
  });
}
