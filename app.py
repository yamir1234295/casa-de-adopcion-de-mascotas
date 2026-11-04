from flask import (Flask, render_template, request, redirect,
                   url_for, abort, jsonify, flash, session)
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
import os
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "huellitas-clave-local-cambiar")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(app.static_folder, "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
EXTENSIONES_IMAGEN = {"png", "jpg", "jpeg", "gif", "webp"}

def guardar_imagen(archivo):
    if not archivo or not archivo.filename:
        return None
    nombre_seguro = secure_filename(archivo.filename)
    extension = nombre_seguro.rsplit(".", 1)[-1].lower() if "." in nombre_seguro else ""
    if extension not in EXTENSIONES_IMAGEN:
        abort(400, description="La imagen debe ser JPG, PNG, GIF o WEBP.")
    nombre_final = f"{uuid4().hex}.{extension}"
    archivo.save(os.path.join(app.config["UPLOAD_FOLDER"], nombre_final))
    return nombre_final

def borrar_imagen(nombre):
    if nombre:
        ruta = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(nombre))
        if os.path.isfile(ruta):
            os.remove(ruta)
mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"
cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000, tz_aware=True)
base_datos = cliente["casa_adopcion_de_mascotas"]
alumnos = base_datos["perros"]
usuarios = base_datos["usuarios"]
reservas = base_datos["reservas"]

DURACION_RESERVA_MINUTOS = 5


def ahora_utc():
    return datetime.now(timezone.utc)


def obtener_reserva_id():
    if not session.get("reserva_id"):
        session["reserva_id"] = uuid.uuid4().hex

    return session["reserva_id"]

@app.route("/reservar/<id>", methods=["POST"])
def reservar_mascota(id):
    liberar_reservas_vencidas()
    mascota = buscar_alumno(id)
    visitante = obtener_reserva_id()
    ahora = ahora_utc()
    vence = ahora + timedelta(minutes=DURACION_RESERVA_MINUTOS)

    recurso = alumnos.find_one_and_update(
        {
            "_id": mascota["_id"],
            "estado": "Disponible",
            "$or": [
                {"reserva": {"$exists": False}},
                {"reserva.vence_en": {"$lte": ahora}},
                {"reserva.reserva_id": visitante}
            ]
        },
        {"$set": {"reserva": {"reserva_id": visitante, "vence_en": vence}}},
        return_document=ReturnDocument.AFTER
    )
    if recurso is None:
        flash("Esta mascota ya está apartada o no está disponible.", "error")
        return redirect(url_for("ver_mascota", id=id))

    reservas.update_one(
        {"recurso_id": mascota["_id"], "reserva_id": visitante, "estado": "activa"},
        {"$set": {"vence_en": vence, "estado": "activa"}, "$setOnInsert": {
            "recurso_id": mascota["_id"], "reserva_id": visitante,
            "cantidad": 1, "creada_en": ahora
        }},
        upsert=True
    )
    flash("Mascota apartada durante 5 minutos. El contador ya está activo.", "success")
    return redirect(url_for("ver_mascota", id=id))


@app.route("/cancelar-reserva/<id>", methods=["POST"])
def cancelar_reserva(id):
    visitante = obtener_reserva_id()
    mascota_id = ObjectId(id) if ObjectId.is_valid(id) else None
    if mascota_id is None:
        abort(404, description="La mascota no es válida.")
    resultado = alumnos.update_one(
        {"_id": mascota_id, "reserva.reserva_id": visitante},
        {"$unset": {"reserva": ""}}
    )
    reservas.update_many(
        {"reserva_id": visitante, "recurso_id": mascota_id,
         "$or": [{"estado": "activa"}, {"estado": {"$exists": False}}]},
        {"$set": {"estado": "cancelada", "cerrada_en": ahora_utc()}}
    )
    if resultado.modified_count:
        flash("Reserva cancelada. La mascota vuelve a estar disponible.", "success")
    else:
        flash("No tienes una reserva activa de esta mascota.", "error")
    destino = request.form.get("next", "")
    if destino.startswith("/") and not destino.startswith("//"):
        return redirect(destino)
    return redirect(url_for("ver_mascota", id=id))


def liberar_reservas_vencidas():
    """Libera apartados vencidos usando como fuente la reserva de cada mascota."""
    ahora = ahora_utc()
    vencidas = alumnos.find(
        {"reserva.vence_en": {"$lte": ahora}},
        {"reserva.reserva_id": 1, "reserva.vence_en": 1}
    )
    for mascota in vencidas:
        reserva = mascota.get("reserva") or {}
        reserva_id = reserva.get("reserva_id")
        if not reserva_id or not reserva.get("vence_en"):
            continue
        liberada = alumnos.update_one(
            {"_id": mascota["_id"], "reserva.reserva_id": reserva_id,
             "reserva.vence_en": {"$lte": ahora}},
            {"$unset": {"reserva": ""}}
        )
        if liberada.modified_count:
            reservas.update_many(
                {"recurso_id": mascota["_id"], "reserva_id": reserva_id,
                 "$or": [{"estado": "activa"}, {"estado": {"$exists": False}}]},
                {"$set": {"estado": "vencida", "cerrada_en": ahora}}
            )


@app.route("/api/reserva/<id>")
def api_estado_reserva(id):
    liberar_reservas_vencidas()
    mascota = buscar_alumno(id)
    visitante = obtener_reserva_id()
    reserva = mascota.get("reserva")
    if not reserva or reserva.get("reserva_id") != visitante:
        return jsonify({"activa": False})
    vence = reserva.get("vence_en")
    if not vence:
        return jsonify({"activa": False})
    if vence.tzinfo is None:
        vence = vence.replace(tzinfo=timezone.utc)
    return jsonify({"activa": True, "vence_en": vence.isoformat()})


@app.route("/api/mis-reservas")
def api_mis_reservas():
    """Apartados activos del visitante para mostrar contador y cancelar desde cualquier página."""
    liberar_reservas_vencidas()
    visitante = obtener_reserva_id()
    ahora = ahora_utc()
    mascotas = alumnos.find({
        "reserva.reserva_id": visitante,
        "reserva.vence_en": {"$gt": ahora}
    }, {"nombre": 1, "reserva.vence_en": 1})
    resultado = []
    for mascota in mascotas:
        vence = (mascota.get("reserva") or {}).get("vence_en")
        if not vence:
            continue
        if vence.tzinfo is None:
            vence = vence.replace(tzinfo=timezone.utc)
        resultado.append({
            "id": str(mascota["_id"]),
            "nombre": mascota.get("nombre", "Mascota"),
            "vence_en": vence.isoformat(),
            "cancel_url": url_for("cancelar_reserva", id=str(mascota["_id"]))
        })
    return jsonify({"reservas": resultado})


def leer_formulario(datos=None):
    if datos is None:
        datos = request.form
    campos = ("nombre", "categoria", "estado")
    if any(not isinstance(datos.get(c, ""), str) for c in campos):
        abort(400, description="Los campos deben contener texto.")
    nombre = datos.get("nombre", "").strip()
    categoria = datos.get("categoria", "").strip()
    estado = datos.get("estado", "").strip()
    if not nombre or not categoria or not estado:
        abort(400, description="Completa nombre, especie y estado.")
    if len(nombre) > 80 or len(categoria) > 20:
        abort(400, description="Máximo: nombre 80 y especie 20.")
    if estado not in ["Disponible", "En proceso", "Adoptado"]:
        abort(400, description="Selecciona un estado válido.")
    return {"nombre": nombre, "categoria": categoria, "estado": estado}


def buscar_alumno(id):
    if not ObjectId.is_valid(id):
        abort(404, description="La clave de la mascota no es válida")
    alumno = alumnos.find_one({"_id": ObjectId(id)})
    if alumno is None:
        abort(404, description="Esta mascota ya no existe")
    return alumno


@app.route("/")
def inicio():
    liberar_reservas_vencidas()
    lista = list(alumnos.find().sort("nombre", 1))
    return render_template("index.html", alumnos=lista)


@app.route("/agregar", methods=["POST"])
def agregar():
    datos = leer_formulario()
    imagen = guardar_imagen(request.files.get("imagen"))
    if imagen:
        datos["imagen"] = imagen
    alumnos.insert_one(datos)
    flash("Mascota agregada correctamente.", "success")
    return redirect(url_for("inicio"))


@app.route("/mascota/<id>")
def ver_mascota(id):
    liberar_reservas_vencidas()
    alumno = buscar_alumno(id)
    return render_template("ver_mascota.html", alumno=alumno)


@app.route("/editar/<id>", methods=["GET", "POST"])
def editar(id):
    alumno = buscar_alumno(id)
    if request.method == "POST":
        datos = leer_formulario()
        archivo = request.files.get("imagen")
        imagen_nueva = guardar_imagen(archivo)
        if imagen_nueva:
            borrar_imagen(alumno.get("imagen"))
            datos["imagen"] = imagen_nueva
        elif alumno.get("imagen"):
            datos["imagen"] = alumno["imagen"]
        alumnos.update_one({"_id": alumno["_id"]}, {"$set": datos})
        flash("Cambios guardados correctamente.", "success")
        return redirect(url_for("ver_mascota", id=id))
    return render_template("editar.html", alumno=alumno)


@app.route("/eliminar/<id>", methods=["POST"])
def eliminar(id):
    alumno = buscar_alumno(id)
    borrar_imagen(alumno.get("imagen"))
    alumnos.delete_one({"_id": alumno["_id"]})
    flash("Mascota eliminada.", "success")
    return redirect(url_for("inicio"))


@app.errorhandler(PyMongoError)
def error_mongo(error):
    mensaje = "No fue posible conectar con la base de datos."
    if request.path.startswith("/api/"):
        return jsonify({"error": mensaje}), 503
    return render_template("error.html", mensaje=mensaje), 503


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
def error_peticion(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description}), error.code
    return render_template(
        "error.html", mensaje=error.description
    ), error.code


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        usuario = usuarios.find_one({"email": email}) if email else None
        if usuario and check_password_hash(usuario.get("password_hash", ""), password):
            session.clear()
            session["usuario_id"] = str(usuario["_id"])
            session["usuario_nombre"] = usuario.get("nombre", "Usuario")
            session["usuario_email"] = usuario["email"]
            flash("Iniciaste sesión correctamente.", "success")
            return redirect(url_for("inicio"))
        flash("Correo o contraseña incorrectos. Si aún no tienes cuenta, créala en esta página.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["POST"])
def registro():
    nombre = request.form.get("nombre", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if len(nombre) < 2 or len(nombre) > 100:
        flash("Escribe un nombre de entre 2 y 100 caracteres.", "error")
        return redirect(url_for("login"))
    if not email or "@" not in email or len(email) > 150:
        flash("Escribe un correo válido.", "error")
        return redirect(url_for("login"))
    if len(password) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "error")
        return redirect(url_for("login"))
    if usuarios.find_one({"email": email}):
        flash("Ya existe una cuenta con ese correo. Inicia sesión.", "error")
        return redirect(url_for("login"))
    usuario = {
        "nombre": nombre,
        "email": email,
        "password_hash": generate_password_hash(password),
        "fecha_registro": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    resultado = usuarios.insert_one(usuario)
    session.clear()
    session["usuario_id"] = str(resultado.inserted_id)
    session["usuario_nombre"] = nombre
    session["usuario_email"] = email
    flash("Tu cuenta se creó correctamente.", "success")
    return redirect(url_for("inicio"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Cerraste sesión.", "success")
    return redirect(url_for("inicio"))


@app.route("/carrito")
def carrito():
    
    return render_template("carrito.html")


@app.route("/ayuda")
def ayuda():
    fecha = datetime.now().strftime("%d/%m/%Y")
    return render_template("ayuda.html", fecha=fecha)


def producto_json(producto):
    return {
        "id": str(producto["_id"]),
        "nombre": producto["nombre"],
        "categoria": producto["categoria"],
        "estado": producto["estado"]
    }


def leer_json():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400, description="Envía un objeto JSON válido.")
    return leer_formulario(datos)


@app.route("/api/mascotas", methods=["GET", "POST"])
def api_mascotas():
    liberar_reservas_vencidas()
    if request.method == "POST":
        datos = leer_json()
        resultado = alumnos.insert_one(datos)
        mascota = buscar_alumno(str(resultado.inserted_id))
        return jsonify(producto_json(mascota)), 201
    lista = alumnos.find().sort("nombre", 1)
    return jsonify([producto_json(m) for m in lista])

solicitudes = base_datos["solicitudes"]

@app.route("/solicitudes")
def solicitudes_lista():
    liberar_reservas_vencidas()
    lista = list(solicitudes.find().sort("_id", -1))
    return render_template("solicitudes.html", solicitudes=lista)


@app.route("/solicitudes/agregar", methods=["POST"])
def agregar_solicitud():
    import json
    liberar_reservas_vencidas()
    nombre = request.form.get("nombre_solicitante", "").strip()
    contacto = request.form.get("contacto", "").strip()
    mensaje = request.form.get("mensaje", "").strip()
    try:
        mascotas_carrito = json.loads(request.form.get("mascotas_json", "[]"))
    except (ValueError, TypeError):
        mascotas_carrito = []
    if not nombre or not contacto or not isinstance(mascotas_carrito, list) or not mascotas_carrito:
        abort(400, description="Completa tu nombre, contacto y selecciona al menos una mascota.")

    visitante = obtener_reserva_id()
    mascotas_elegidas = []
    ids_confirmados = []
    for item in mascotas_carrito:
        if not isinstance(item, dict) or not ObjectId.is_valid(str(item.get("id", ""))):
            continue
        mascota_id = ObjectId(str(item["id"]))
        mascota = alumnos.find_one({
            "_id": mascota_id, "estado": "Disponible",
            "reserva.reserva_id": visitante,
            "reserva.vence_en": {"$gt": ahora_utc()}
        })
        if mascota:
            mascotas_elegidas.append({
                "id": str(mascota["_id"]), "nombre": mascota.get("nombre", ""),
                "categoria": mascota.get("categoria", ""), "imagen": mascota.get("imagen", "")
            })
            ids_confirmados.append(mascota_id)

    if not mascotas_elegidas:
        abort(400, description="Primero aparta durante 5 minutos cada mascota de tu lista. Si el tiempo terminó, vuelve a apartarla.")
    if len(mascotas_elegidas) != len(mascotas_carrito):
        abort(400, description="Todas las mascotas de tu lista deben tener una reserva activa. Revisa cuáles necesitas apartar.")

    ahora = ahora_utc()
    solicitud = {
        "nombre_solicitante": nombre, "contacto": contacto, "mensaje": mensaje,
        "mascotas": mascotas_elegidas, "estado": "Pendiente",
        "fecha": ahora.strftime("%d/%m/%Y %H:%M")
    }
    resultado = solicitudes.insert_one(solicitud)

    # Confirmar cierra cada reserva y bloquea la mascota mientras se revisa la solicitud.
    for mascota_id in ids_confirmados:
        confirmada = alumnos.update_one(
            {"_id": mascota_id, "estado": "Disponible", "reserva.reserva_id": visitante,
             "reserva.vence_en": {"$gt": ahora}},
            {"$set": {"estado": "En proceso"}, "$unset": {"reserva": ""}}
        )
        if confirmada.modified_count:
            reservas.update_many(
                {"recurso_id": mascota_id, "reserva_id": visitante, "estado": "activa"},
                {"$set": {"estado": "confirmada", "solicitud_id": resultado.inserted_id,
                          "cerrada_en": ahora}}
            )

    session["carrito_confirmado_ids"] = [str(x) for x in ids_confirmados]
    flash("Solicitud enviada. Las mascotas seleccionadas salieron de tu lista y quedaron en revisión.", "success")
    return redirect(url_for("ver_solicitud", id=str(resultado.inserted_id)))


def buscar_solicitud(id):
    if not ObjectId.is_valid(id):
        abort(404, description="La solicitud no es válida.")
    solicitud = solicitudes.find_one({"_id": ObjectId(id)})
    if solicitud is None:
        abort(404, description="No se encontró la solicitud.")
    return solicitud


@app.route("/solicitudes/<id>")
def ver_solicitud(id):
    solicitud = buscar_solicitud(id)
    return render_template("ver_solicitud.html", solicitud=solicitud)


@app.route("/solicitudes/<id>/editar", methods=["GET", "POST"])
def editar_solicitud(id):
    solicitud = buscar_solicitud(id)
    if request.method == "POST":
        nombre = request.form.get("nombre_solicitante", "").strip()
        contacto = request.form.get("contacto", "").strip()
        mensaje = request.form.get("mensaje", "").strip()
        estado = request.form.get("estado", "Pendiente")
        if not nombre or not contacto:
            abort(400, description="El nombre y contacto son obligatorios.")
        if estado not in ["Pendiente", "En revisión", "Aprobada", "Rechazada"]:
            abort(400, description="Selecciona un estado válido.")
        solicitudes.update_one({"_id": solicitud["_id"]}, {"$set": {
            "nombre_solicitante": nombre, "contacto": contacto,
            "mensaje": mensaje, "estado": estado
        }})
        flash("Solicitud actualizada.", "success")
        return redirect(url_for("ver_solicitud", id=id))
    return render_template("editar_solicitud.html", solicitud=solicitud)


@app.route("/solicitudes/<id>/eliminar", methods=["POST"])
def eliminar_solicitud(id):
    solicitud = buscar_solicitud(id)
    solicitudes.delete_one({"_id": solicitud["_id"]})
    flash("Solicitud eliminada.", "success")
    return redirect(url_for("solicitudes_lista"))

@app.route("/api/mascotas/<id>", methods=["GET", "PUT", "DELETE"])
def api_mascota(id):
    mascota = buscar_alumno(id)
    if request.method == "PUT":
        datos = leer_json()
        alumnos.update_one({"_id": mascota["_id"]}, {"$set": datos})
        return jsonify(producto_json(buscar_alumno(id)))
    if request.method == "DELETE":
        alumnos.delete_one({"_id": mascota["_id"]})
        return "", 204
    return jsonify(producto_json(mascota))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)