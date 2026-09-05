from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///accesoseguro.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
db = SQLAlchemy(app)

# --- Modelos (tablas) ---

class Sede(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)

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
    hora_ingreso = db.Column(db.DateTime)   # NUEVO
    hora_egreso = db.Column(db.DateTime)    # NUEVO

    sede = db.relationship("Sede")

class LogAuditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    accion = db.Column(db.String(100), nullable=False)       # ej: "Aprobación", "Registro de ingreso"
    detalle = db.Column(db.String(300))                       # ej: "Solicitud #4 aprobada"
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

# --- Rutas ---

@app.route("/")
def home():
    return '<a href="/nueva-solicitud">Nueva solicitud de acceso</a> | <a href="/panel-aprobacion">Panel de aprobación</a> | <a href="/verificacion">Verificación en portería</a> | <a href="/solicitudes">Ver todas</a>'

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
        return render_template("nueva_solicitud.html", sedes=sedes, mensaje="Solicitud enviada correctamente. Queda pendiente de aprobación.")

    return render_template("nueva_solicitud.html", sedes=sedes, mensaje=None)

@app.route("/panel-aprobacion")
def panel_aprobacion():
    pendientes = Solicitud.query.filter_by(estado="pendiente").all()
    return render_template("panel_aprobacion.html", solicitudes=pendientes)

@app.route("/resolver/<int:id>", methods=["POST"])
def resolver_solicitud(id):
    solicitud = Solicitud.query.get_or_404(id)
    accion = request.form["accion"]
    solicitud.estado = accion

    db.session.add(LogAuditoria(
        accion="Aprobación" if accion == "aprobada" else "Rechazo",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante}) - {accion}"
    ))

    db.session.commit()
    return redirect(url_for("panel_aprobacion"))

@app.route("/solicitudes")
def ver_solicitudes():
    solicitudes = Solicitud.query.all()
    html = "<h1>Todas las solicitudes</h1><ul>"
    for s in solicitudes:
        html += f"<li>{s.nombre_solicitante} - DNI {s.dni} - Estado: {s.estado} - Sede: {s.sede.nombre}</li>"
    html += "</ul>"
    return html

@app.route("/verificacion")
def verificacion():
    dni_buscado = request.args.get("dni")
    resultado = None
    buscado = False

    if dni_buscado:
        buscado = True
        resultado = Solicitud.query.filter_by(dni=dni_buscado, estado="aprobada").first()

    return render_template("verificacion.html", dni_buscado=dni_buscado, resultado=resultado, buscado=buscado)

@app.route("/registrar-ingreso/<int:id>", methods=["POST"])
def registrar_ingreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_ingreso = datetime.utcnow()

    db.session.add(LogAuditoria(
        accion="Registro de ingreso",
        detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})"
    ))

    db.session.commit()
    return redirect(url_for("verificacion", dni=solicitud.dni))

@app.route("/registrar-egreso/<int:id>", methods=["POST"])
def registrar_egreso(id):
    solicitud = Solicitud.query.get_or_404(id)
    solicitud.hora_egreso = datetime.utcnow()

    db.session.add(LogAuditoria(
            accion="Registro de egreso",
            detalle=f"Solicitud #{solicitud.id} ({solicitud.nombre_solicitante})"
        ))
    
    db.session.commit()
    return redirect(url_for("verificacion", dni=solicitud.dni))

@app.route("/auditoria")
def ver_auditoria():
    logs = LogAuditoria.query.order_by(LogAuditoria.fecha.desc()).all()
    html = "<h1>Log de auditoría</h1><ul>"
    for l in logs:
        html += f"<li>{l.fecha} — {l.accion}: {l.detalle}</li>"
    html += "</ul>"
    return html

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not Sede.query.first():  # si no hay ninguna sede todavía, creá una
            db.session.add(Sede(nombre="Planta Central"))
            db.session.add(Sede(nombre="Deposito Norte"))
            db.session.add(Sede(nombre="Deposito Sur"))
            db.session.commit()
    app.run(host="0.0.0.0", debug=True, port=5000)