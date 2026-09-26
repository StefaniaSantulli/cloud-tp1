from flask import Flask, request, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import csv
import io
import boto3

SEGUNDOS_VENCIMIENTO = 10

s3 = boto3.client("s3", region_name="us-east-1")
# Antes estaba hardcodeado en el código. Ahora se puede pisar con una variable
# de entorno en el Launch Template sin tener que rehornear la AMI.
BUCKET_NAME = os.environ.get("BUCKET_NAME", "accesoseguro-documentos-2026")

app = Flask(__name__)

# El frontend estático (S3/CloudFront) vive en un ORIGEN DISTINTO a esta API
# (distinto dominio/puerto), así que el navegador exige CORS para permitir
# que el JS del frontend llame a estos endpoints.
# En producción conviene reemplazar "*" por el dominio real del frontend,
# por ejemplo "https://d111111abcdef8.cloudfront.net".
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": FRONTEND_ORIGIN}})

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///accesoseguro.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
db = SQLAlchemy(app)

# --- Modelos (sin cambios respecto a la versión anterior) ---


class Sede(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)

    def to_dict(self):
        return {"id": self.id, "nombre": self.nombre}


class LogAuditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    accion = db.Column(db.String(100), nullable=False)
    detalle = db.Column(db.String(300))
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "accion": self.accion,
            "detalle": self.detalle,
            "fecha": self.fecha.isoformat() if self.fecha else None,
        }


class Solicitud(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre_solicitante = db.Column(db.String(100), nullable=False)
    dni = db.Column(db.String(20), nullable=False)
    patente = db.Column(db.String(20))
    empresa = db.Column(db.String(100))
    motivo = db.Column(db.String(200))
    sede_id = db.Column(db.Integer, db.ForeignKey("sede.id"), nullable=False)
    estado = db.Column(db.String(20), default="pendiente")
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_aprobacion = db.Column(db.DateTime)
    hora_ingreso = db.Column(db.DateTime)
    hora_egreso = db.Column(db.DateTime)
    documento_s3_key = db.Column(db.String(300))
    motivo_decision = db.Column(db.String(300))

    sede = db.relationship("Sede")

    def to_dict(self, incluir_url_documento=False):
        data = {
            "id": self.id,
            "nombre_solicitante": self.nombre_solicitante,
            "dni": self.dni,
            "patente": self.patente,
            "empresa": self.empresa,
            "motivo": self.motivo,
            "sede": self.sede.to_dict() if self.sede else None,
            "estado": self.estado,
            "fecha_creacion": self.fecha_creacion.isoformat() if self.fecha_creacion else None,
            "fecha_aprobacion": self.fecha_aprobacion.isoformat() if self.fecha_aprobacion else None,
            "hora_ingreso": self.hora_ingreso.isoformat() if self.hora_ingreso else None,
            "hora_egreso": self.hora_egreso.isoformat() if self.hora_egreso else None,
            "motivo_decision": self.motivo_decision,
            "tiene_documento": bool(self.documento_s3_key),
        }
        if incluir_url_documento:
            data["url_documento"] = generar_url_documento(self.documento_s3_key)
        return data


def generar_url_documento(key, expiracion=3600):
    if not key:
        return None
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=expiracion,
    )


# --- Health check ---
# El Target Group del ALB apunta acá (path "/"). Antes esto devolvía el
# home.html renderizado; ahora el home vive en el frontend estático, así
# que "/" pasa a ser pura señal de salud del backend.
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "accesoseguro-api"})


# --- Sedes (para el combo del formulario y del reporte) ---
@app.route("/api/sedes")
def listar_sedes():
    sedes = Sede.query.all()
    return jsonify([s.to_dict() for s in sedes])


# --- Rol conductor: nueva solicitud ---
@app.route("/api/solicitudes", methods=["POST"])
def crear_solicitud():
    nueva = Solicitud(
        nombre_solicitante=request.form["nombre_solicitante"],
        dni=request.form["dni"],
        patente=request.form.get("patente"),
        empresa=request.form.get("empresa"),
        motivo=request.form.get("motivo"),
        sede_id=request.form["sede_id"],
    )
    db.session.add(nueva)
    db.session.commit()

    archivo = request.files.get("documento")
    if archivo and archivo.filename:
        try:
            key = f"solicitudes/{nueva.id}_{archivo.filename}"
            s3.upload_fileobj(archivo, BUCKET_NAME, key)
            nueva.documento_s3_key = key

            db.session.add(LogAuditoria(
                accion="Carga de documentación",
                detalle=f"Solicitud #{nueva.id} ({nueva.nombre_solicitante}) - archivo: {archivo.filename}",
            ))
            db.session.commit()
        except Exception as e:
            print(f"No se pudo subir el archivo a S3: {e}")

    return jsonify(nueva.to_dict()), 201


# --- Rol conductor: consultar mis solicitudes por DNI (solo lectura) ---
@app.route("/api/mis-solicitudes")
def mis_solicitudes():
    dni = (request.args.get("dni") or "").strip()
    if not dni:
        return jsonify({"error": "Falta el parámetro dni"}), 400

    solicitudes = (
        Solicitud.query.filter_by(dni=dni)
        .order_by(Solicitud.fecha_creacion.desc())
        .all()
    )
    return jsonify([s.to_dict() for s in solicitudes])


# --- Rol admin: panel de aprobación ---
@app.route("/api/solicitudes/pendientes")
def solicitudes_pendientes():
    pendientes = Solicitud.query.filter_by(estado="pendiente").all()
    return jsonify([s.to_dict(incluir_url_documento=True) for s in pendientes])


@app.route("/api/solicitudes/<int:id>/resolver", methods=["POST"])
def resolver_solicitud(id):
    solicitud = Solicitud.query.get_or_404(id)
    data = request.get_json(silent=True) or request.form
    accion = data.get("accion")
    motivo = data.get("motivo_decision", "")

    if accion not in ("aprobada", "rechazada"):
        return jsonify({"error": "accion inválida"}), 400

    solicitud.estado = accion
    solicitud.motivo_decision = motivo

    if accion == "aprobada":
        solicitud.fecha_aprobacion = datetime.utcnow()

    db.session.add(LogAuditoria(
        accion="Aprobación" if accion == "aprobada" else "Rechazo",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante}) - {accion}"
        + (f" - Motivo: {motivo}" if motivo else ""),
    ))
    db.session.commit()
    return jsonify(solicitud.to_dict())


# --- Rol admin: todas las solicitudes ---
@app.route("/api/solicitudes")
def listar_solicitudes():
    solicitudes = Solicitud.query.order_by(Solicitud.fecha_creacion.desc()).all()
    return jsonify([s.to_dict() for s in solicitudes])


# --- Rol admin: verificación en portería ---
@app.route("/api/verificacion")
def verificacion():
    busqueda = (request.args.get("busqueda") or "").strip()
    if not busqueda:
        return jsonify({"error": "Falta el parámetro busqueda"}), 400

    resultado = Solicitud.query.filter(
        Solicitud.estado == "aprobada",
        (Solicitud.dni == busqueda) | (Solicitud.patente == busqueda),
    ).first()

    if not resultado:
        return jsonify({"autorizado": False})

    return jsonify({"autorizado": True, "solicitud": resultado.to_dict()})


@app.route("/api/solicitudes/<int:id>/ingreso", methods=["POST"])
def registrar_ingreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_ingreso = datetime.utcnow()
    db.session.add(LogAuditoria(
        accion="Registro de ingreso",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})",
    ))
    db.session.commit()
    return jsonify(solicitud.to_dict())


@app.route("/api/solicitudes/<int:id>/egreso", methods=["POST"])
def registrar_egreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_egreso = datetime.utcnow()
    db.session.add(LogAuditoria(
        accion="Registro de egreso",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})",
    ))
    db.session.commit()
    return jsonify(solicitud.to_dict())


# --- Rol admin: auditoría ---
@app.route("/api/auditoria")
def ver_auditoria():
    logs = LogAuditoria.query.order_by(LogAuditoria.fecha.desc()).all()
    return jsonify([l.to_dict() for l in logs])


# --- Rol admin: reporte histórico (descarga directa de CSV, no necesita JSON) ---
@app.route("/api/reporte/exportar")
def exportar_reporte():
    sede_id = request.args.get("sede_id")
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")

    query = Solicitud.query
    if sede_id:
        query = query.filter_by(sede_id=sede_id)
    if desde:
        query = query.filter(Solicitud.fecha_creacion >= desde)
    if hasta:
        query = query.filter(Solicitud.fecha_creacion <= hasta + " 23:59:59")

    solicitudes = query.order_by(Solicitud.fecha_creacion).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nombre", "DNI", "Patente", "Empresa", "Sede", "Estado", "Fecha solicitud", "Hora ingreso", "Hora egreso"])
    for s in solicitudes:
        writer.writerow([
            s.id, s.nombre_solicitante, s.dni, s.patente, s.empresa,
            s.sede.nombre, s.estado, s.fecha_creacion,
            s.hora_ingreso or "", s.hora_egreso or "",
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_accesos.csv"},
    )


# --- Tarea de expiración automática ---
@app.route("/api/expirar-vencidas")
def expirar_vencidas():
    limite = datetime.utcnow() - timedelta(seconds=SEGUNDOS_VENCIMIENTO)
    vencidas = Solicitud.query.filter(
        Solicitud.estado == "aprobada",
        Solicitud.hora_ingreso.is_(None),
        Solicitud.fecha_aprobacion.isnot(None),
        Solicitud.fecha_aprobacion < limite,
    ).all()

    for s in vencidas:
        s.estado = "vencida"
        db.session.add(LogAuditoria(
            accion="Expiración automática",
            detalle=f"Solicitud #{s.id} ({s.nombre_solicitante}) - vencida (no se presentó dentro de {SEGUNDOS_VENCIMIENTO}seg)",
        ))
    db.session.commit()

    return jsonify({"vencidas": len(vencidas), "ids": [s.id for s in vencidas]})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not Sede.query.first():
            db.session.add(Sede(nombre="Planta Central"))
            db.session.add(Sede(nombre="Deposito Norte"))
            db.session.add(Sede(nombre="Deposito Sur"))
            db.session.commit()
    app.run(host="0.0.0.0", debug=True, port=5000)
