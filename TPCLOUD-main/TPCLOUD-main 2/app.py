from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
import csv
import io
from flask import Response
import boto3

SEGUNDOS_VENCIMIENTO = 10

s3 = boto3.client("s3", region_name="us-east-1")
BUCKET_NAME = "accesoseguro-documentos-2026"
app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///accesoseguro.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
db = SQLAlchemy(app)

# --- Modelos (tablas) ---

class Sede(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)


class LogAuditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    accion = db.Column(db.String(100), nullable=False)
    detalle = db.Column(db.String(300))
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

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
    fecha_aprobacion = db.Column(db.DateTime)   # NUEVO: cuándo se aprobó (para calcular el vencimiento)
    hora_ingreso = db.Column(db.DateTime)
    hora_egreso = db.Column(db.DateTime)
    documento_s3_key = db.Column(db.String(300))
    motivo_decision = db.Column(db.String(300)) 

    sede = db.relationship("Sede")

# --- Rutas ---
def generar_url_documento(key, expiracion=3600):
    if not key:
        return None
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=expiracion
    )

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/nueva-solicitud", methods=["GET", "POST"])
def nueva_solicitud():
    sedes = Sede.query.all()

    if request.method == "POST":
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
                detalle=f"Solicitud #{nueva.id} ({nueva.nombre_solicitante}) - archivo: {archivo.filename}"
            ))

            db.session.commit()
        except Exception as e:
            print(f"No se pudo subir el archivo a S3: {e}")

        return render_template("nueva_solicitud.html", sedes=sedes, mensaje="Solicitud enviada correctamente. Queda pendiente de aprobación.")

    return render_template("nueva_solicitud.html", sedes=sedes, mensaje=None)

@app.route("/panel-aprobacion")
def panel_aprobacion():
    pendientes = Solicitud.query.filter_by(estado="pendiente").all()
    urls_documentos = {
        s.id: generar_url_documento(s.documento_s3_key) for s in pendientes
    }
    return render_template("panel_aprobacion.html", solicitudes=pendientes, urls_documentos=urls_documentos)

@app.route("/resolver/<int:id>", methods=["POST"])
def resolver_solicitud(id):
    solicitud = Solicitud.query.get_or_404(id)
    accion = request.form["accion"]
    motivo = request.form.get("motivo_decision", "")
    solicitud.estado = accion
    solicitud.motivo_decision = motivo

    if accion == "aprobada":
        solicitud.fecha_aprobacion = datetime.utcnow()

    db.session.add(LogAuditoria(
        accion="Aprobación" if accion == "aprobada" else "Rechazo",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante}) - {accion}" + (f" - Motivo: {motivo}" if motivo else "")
    ))

    db.session.commit()
    return redirect(url_for("panel_aprobacion"))

@app.route("/solicitudes")
def ver_solicitudes():
    solicitudes = Solicitud.query.all()
    return render_template("solicitudes.html", solicitudes=solicitudes)

@app.route("/verificacion")
def verificacion():
    busqueda = request.args.get("busqueda")
    resultado = None
    buscado = False

    if busqueda:
        buscado = True
        resultado = Solicitud.query.filter(
            Solicitud.estado == "aprobada",
            (Solicitud.dni == busqueda) | (Solicitud.patente == busqueda)
        ).first()

    return render_template("verificacion.html", busqueda=busqueda, resultado=resultado, buscado=buscado)

@app.route("/registrar-ingreso/<int:id>", methods=["POST"])
def registrar_ingreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_ingreso = datetime.utcnow()

    db.session.add(LogAuditoria(
        accion="Registro de ingreso",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})"
    ))

    db.session.commit()
    return redirect(url_for("verificacion", busqueda=solicitud.dni))

@app.route("/registrar-egreso/<int:id>", methods=["POST"])
def registrar_egreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_egreso = datetime.utcnow()

    db.session.add(LogAuditoria(
            accion="Registro de egreso",
            detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})"
        ))

    db.session.commit()
    return redirect(url_for("verificacion",busqueda=solicitud.dni))

@app.route("/auditoria")
def ver_auditoria():
    logs = LogAuditoria.query.order_by(LogAuditoria.fecha.desc()).all()
    return render_template("auditoria.html", logs=logs)

@app.route("/reporte")
def reporte():
    sedes = Sede.query.all()
    return render_template("reporte.html", sedes=sedes)

@app.route("/reporte/exportar")
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
            s.hora_ingreso or "", s.hora_egreso or ""
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_accesos.csv"}
    )

@app.route("/api/expirar-vencidas")
def expirar_vencidas():
    limite = datetime.utcnow() - timedelta(seconds=SEGUNDOS_VENCIMIENTO)

    vencidas = Solicitud.query.filter(
        Solicitud.estado == "aprobada",
        Solicitud.hora_ingreso.is_(None),
        Solicitud.fecha_aprobacion.isnot(None),
        Solicitud.fecha_aprobacion < limite
    ).all()

    for s in vencidas:
        s.estado = "vencida"
        db.session.add(LogAuditoria(
            accion="Expiración automática",
            detalle=f"Solicitud #{s.id} ({s.nombre_solicitante}) - vencida (no se presentó dentro de {SEGUNDOS_VENCIMIENTO}seg)"
        ))

    db.session.commit()

    return {"vencidas": len(vencidas), "ids": [s.id for s in vencidas]}

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not Sede.query.first():
            db.session.add(Sede(nombre="Planta Central"))
            db.session.add(Sede(nombre="Deposito Norte"))
            db.session.add(Sede(nombre="Deposito Sur"))
            db.session.commit()
    app.run(host="0.0.0.0", debug=True, port=5000)