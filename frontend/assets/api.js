// Configuración de la API backend.
// Si se abre desde localhost (corriendo el backend en tu máquina para
// pruebas), usa localhost:5000. En cualquier otro caso (subido a S3),
// usa el DNS real del ALB delante del Auto Scaling Group en AWS.
// TODO: si más adelante se agrega un dominio propio (Route 53) o CloudFront
// delante de la API, actualizar la segunda URL.
const API_BASE_URL =
  (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
    ? "http://localhost:5000"
    : "http://accesoseguro-alb-542552161.us-east-1.elb.amazonaws.com";

async function apiGet(path) {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) throw new Error(`Error ${res.status} al consultar ${path}`);
  return res.json();
}

async function apiPostJSON(path, data) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Error ${res.status} al enviar a ${path}`);
  return res.json();
}

async function apiPostForm(path, formData) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error(`Error ${res.status} al enviar a ${path}`);
  return res.json();
}
