from flask import (Flask, render_template, request, redirect,
                   url_for, abort, jsonify, flash, session, send_file, Response)
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
import gridfs
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from functools import wraps
import os
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
import uuid
import io
import base64
import qrcode
from PIL import Image, ImageOps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "huellitas-clave-local-cambiar")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
EXTENSIONES_IMAGEN = {"png", "jpg", "jpeg", "gif", "webp"}

mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"
# maxIdleTimeMS evita reutilizar conexiones que Atlas/Render ya cerraron por inactividad.
cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000,
                      maxIdleTimeMS=60000, retryReads=True, retryWrites=True, tz_aware=True)
base_datos = cliente["casa_adopcion_de_mascotas"]
alumnos = base_datos["perros"]
usuarios = base_datos["usuarios"]
reservas = base_datos["reservas"]
solicitudes = base_datos["solicitudes"]
fotos = gridfs.GridFS(base_datos, collection="fotos")

TIPOS_IMAGEN = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def guardar_imagen(archivo):
    """Valida, normaliza (JPEG, máx. 1600 px) y guarda la foto en MongoDB (GridFS).
    El disco de Render se borra en cada despliegue, por eso no se guarda en static/."""
    if not archivo or not archivo.filename:
        return None
    nombre_seguro = secure_filename(archivo.filename)
    extension = nombre_seguro.rsplit(".", 1)[-1].lower() if "." in nombre_seguro else ""
    if extension not in EXTENSIONES_IMAGEN:
        abort(400, description="La imagen debe ser JPG, PNG, GIF o WEBP.")
    try:
        imagen = Image.open(archivo.stream)
        imagen = ImageOps.exif_transpose(imagen)      # respeta la rotación de fotos de celular
        imagen.thumbnail((1600, 1600))
        if imagen.mode in ("RGBA", "LA", "P"):
            imagen = imagen.convert("RGBA")
            fondo = Image.new("RGB", imagen.size, (255, 255, 255))
            fondo.paste(imagen, mask=imagen.split()[-1])
            imagen = fondo
        else:
            imagen = imagen.convert("RGB")
        salida = io.BytesIO()
        imagen.save(salida, format="JPEG", quality=85, optimize=True)
    except Exception:
        abort(400, description="La imagen está dañada o no se pudo leer. Prueba con otra foto.")
    nombre_final = f"{uuid4().hex}.jpg"
    fotos.put(salida.getvalue(), filename=nombre_final, content_type="image/jpeg")
    return nombre_final


def borrar_imagen(nombre):
    if nombre:
        for archivo in fotos.find({"filename": secure_filename(nombre)}):
            fotos.delete(archivo._id)


@app.route("/foto/<nombre>")
def foto(nombre):
    archivo = fotos.find_one({"filename": secure_filename(nombre)})
    if not archivo:
        # La foto ya no existe (p. ej. se perdió del disco antes de migrar): mostrar un recuadro en vez de imagen rota.
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"><rect width="400" height="300" fill="#e4eddd"/>'
               '<text x="200" y="175" font-size="96" text-anchor="middle">🐾</text></svg>')
        return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-cache"})
    respuesta = Response(archivo.read(), mimetype=archivo.content_type or "application/octet-stream")
    respuesta.headers["Cache-Control"] = "public, max-age=86400"
    return respuesta



def asegurar_admin_configurado():
    """Crea/promueve la cuenta indicada por variables de entorno, nunca desde el registro público."""
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or len(password) < 8:
        return
    usuarios.update_one(
        {"email": email},
        {
            "$set": {"rol": "admin"},
            "$setOnInsert": {
                "nombre": os.environ.get("ADMIN_NOMBRE", "Administrador"),
                "email": email,
                "password_hash": generate_password_hash(password),
                "fecha_registro": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
        },
        upsert=True
    )


def usuario_es_administrador():
    """Comprueba el rol directamente en MongoDB; no confía solo en la sesión."""
    usuario_id = session.get("usuario_id", "")
    if not ObjectId.is_valid(usuario_id):
        return False
    usuario_actual = usuarios.find_one({"_id": ObjectId(usuario_id)}, {"rol": 1})
    return bool(usuario_actual and usuario_actual.get("rol") == "admin")


def usuario_requerido(vista):
    @wraps(vista)
    def protegida(*args, **kwargs):
        usuario_id = session.get("usuario_id", "")
        if not ObjectId.is_valid(usuario_id) or not usuarios.find_one({"_id": ObjectId(usuario_id)}, {"_id": 1}):
            flash("Inicia sesión para gestionar la información de las mascotas.", "error")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)
    return protegida


def administrador_requerido(vista):
    @wraps(vista)
    @usuario_requerido
    def protegida(*args, **kwargs):
        if not usuario_es_administrador():
            abort(403, description="Solo el administrador puede gestionar esta sección.")
        return vista(*args, **kwargs)
    return protegida


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


def leer_formulario(datos=None, estado_actual="Disponible", permitir_estado=None):
    """Valida datos editables. Solo el administrador puede enviar/cambiar estado."""
    if datos is None:
        datos = request.form
    campos = ("nombre", "categoria")
    if any(not isinstance(datos.get(c, ""), str) for c in campos):
        abort(400, description="Los campos deben contener texto.")
    nombre = datos.get("nombre", "").strip()
    categoria = datos.get("categoria", "").strip()
    if not nombre or not categoria:
        abort(400, description="Completa el nombre y la especie de la mascota.")
    if len(nombre) > 80 or len(categoria) > 20:
        abort(400, description="Máximo: nombre 80 y especie 20.")

    if permitir_estado is None:
        permitir_estado = usuario_es_administrador()
    estado = estado_actual or "Disponible"
    if permitir_estado:
        estado = datos.get("estado", estado).strip()
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
    # Los visitantes ven disponibles; los usuarios registrados pueden gestionar
    # la información de todas las mascotas. Solo el administrador cambia estados.
    es_admin = usuario_es_administrador()
    puede_gestionar = bool(session.get("usuario_id"))
    # Solo el administrador ve las mascotas En proceso/Adoptado; todos los demás (incluso con sesión) solo las Disponibles.
    filtro = {} if es_admin else {"estado": "Disponible"}
    lista = list(alumnos.find(filtro).sort("nombre", 1))
    return render_template("index.html", alumnos=lista, es_admin=es_admin,
                           puede_gestionar=puede_gestionar)


@app.route("/agregar", methods=["POST"])
@usuario_requerido
def agregar():
    datos = leer_formulario(estado_actual="Disponible")
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
    if alumno.get("estado", "Disponible") != "Disponible" and not usuario_es_administrador():
        abort(404, description="Esta mascota ya no está disponible")
    return render_template("ver_mascota.html", alumno=alumno)


@app.route("/ficha/<id>")
def ficha_qr(id):
    """Ficha pública y limitada que consulta siempre el estado actual en MongoDB."""
    liberar_reservas_vencidas()
    alumno = buscar_alumno(id)
    reserva = alumno.get("reserva") or {}
    vence = reserva.get("vence_en")
    reservada = bool(vence and vence > ahora_utc())
    return render_template(
        "ficha_qr.html",
        alumno=alumno,
        reservada=reservada
    )


@app.route("/qr-imagen/<id>")
def imagen_qr(id):
    """Devuelve la imagen PNG del QR para mostrarla directamente en cada tarjeta."""
    alumno = buscar_alumno(id)
    dominio = os.environ.get("APP_BASE_URL", request.url_root).rstrip("/")
    direccion = dominio + url_for("ficha_qr", id=str(alumno["_id"]))

    imagen = qrcode.make(direccion)
    memoria = io.BytesIO()
    imagen.save(memoria, format="PNG")
    memoria.seek(0)
    return send_file(memoria, mimetype="image/png", download_name=f"qr-{alumno['_id']}.png")


@app.route("/qr/<id>")
def mostrar_qr(id):
    alumno = buscar_alumno(id)
    dominio = os.environ.get("APP_BASE_URL", request.url_root).rstrip("/")
    direccion = dominio + url_for("ficha_qr", id=str(alumno["_id"]))

    imagen = qrcode.make(direccion)
    memoria = io.BytesIO()
    imagen.save(memoria, format="PNG")
    qr_base64 = base64.b64encode(memoria.getvalue()).decode("utf-8")

    return render_template(
        "codigo_qr.html",
        alumno=alumno,
        qr=qr_base64,
        direccion=direccion
    )


@app.route("/editar/<id>", methods=["GET", "POST"])
@usuario_requerido
def editar(id):
    alumno = buscar_alumno(id)
    es_admin = usuario_es_administrador()
    if request.method == "POST":
        datos = leer_formulario(estado_actual=alumno.get("estado", "Disponible"),
                                permitir_estado=es_admin)
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
    return render_template("editar.html", alumno=alumno, es_admin=es_admin)


@app.route("/eliminar/<id>", methods=["POST"])
@usuario_requerido
def eliminar(id):
    alumno = buscar_alumno(id)
    borrar_imagen(alumno.get("imagen"))
    alumnos.delete_one({"_id": alumno["_id"]})
    flash("Mascota eliminada.", "success")
    return redirect(url_for("inicio"))


@app.route("/salud")
def salud():
    """Abre /salud en el navegador para ver si la base responde (y el error real si no)."""
    try:
        cliente.admin.command("ping")
        return jsonify({"base_de_datos": "ok"})
    except PyMongoError as error:
        app.logger.error("Ping a MongoDB falló: %s", error)
        return jsonify({"base_de_datos": "error", "detalle": str(error)[:300]}), 503


@app.errorhandler(PyMongoError)
def error_mongo(error):
    app.logger.error("Error de MongoDB: %s: %s", type(error).__name__, error)
    mensaje = "No fue posible conectar con la base de datos."
    if request.path.startswith("/api/"):
        return jsonify({"error": mensaje}), 503
    return render_template("error.html", mensaje=mensaje), 503


@app.errorhandler(403)
def error_permisos(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description}), 403
    return render_template("error.html", mensaje=error.description), 403


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
        asegurar_admin_configurado()
        usuario = usuarios.find_one({"email": email}) if email else None
        if usuario and check_password_hash(usuario.get("password_hash", ""), password):
            session.clear()
            session["usuario_id"] = str(usuario["_id"])
            session["usuario_nombre"] = usuario.get("nombre", "Usuario")
            session["usuario_email"] = usuario["email"]
            session["usuario_rol"] = usuario.get("rol", "usuario")
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
        "rol": "usuario",
        "fecha_registro": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    resultado = usuarios.insert_one(usuario)
    session.clear()
    session["usuario_id"] = str(resultado.inserted_id)
    session["usuario_nombre"] = nombre
    session["usuario_email"] = email
    session["usuario_rol"] = "usuario"
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


def leer_json(estado_actual="Disponible"):
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        abort(400, description="Envía un objeto JSON válido.")
    return leer_formulario(datos, estado_actual=estado_actual)


@app.route("/api/mascotas", methods=["GET", "POST"])
def api_mascotas():
    liberar_reservas_vencidas()
    if request.method == "POST":
        if not session.get("usuario_id"):
            abort(403, description="Inicia sesión para registrar mascotas.")
        datos = leer_json()
        if not usuario_es_administrador():
            datos["estado"] = "Disponible"
        resultado = alumnos.insert_one(datos)
        mascota = buscar_alumno(str(resultado.inserted_id))
        return jsonify(producto_json(mascota)), 201
    filtro = {} if usuario_es_administrador() else {"estado": "Disponible"}
    lista = alumnos.find(filtro).sort("nombre", 1)
    return jsonify([producto_json(m) for m in lista])

@app.route("/solicitudes")
@administrador_requerido
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


def ids_de_mascotas(solicitud):
    return [ObjectId(m["id"]) for m in solicitud.get("mascotas", [])
            if isinstance(m, dict) and ObjectId.is_valid(str(m.get("id", "")))]


def liberar_mascotas(solicitud):
    """Devuelve a Disponible las mascotas En proceso de una solicitud rechazada o eliminada,
    salvo las que otra solicitud activa (Pendiente, En revisión o Aprobada) siga reteniendo."""
    for mascota_id in ids_de_mascotas(solicitud):
        otra = solicitudes.find_one({
            "_id": {"$ne": solicitud["_id"]},
            "estado": {"$in": ["Pendiente", "En revisión", "Aprobada"]},
            "mascotas.id": str(mascota_id)
        }, {"_id": 1})
        if not otra:
            alumnos.update_one({"_id": mascota_id, "estado": "En proceso"},
                               {"$set": {"estado": "Disponible"}})


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
@administrador_requerido
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

        # Mantener sincronizado el estado de las mascotas ligadas a la solicitud.
        ids_mascotas = [
            ObjectId(m["id"]) for m in solicitud.get("mascotas", [])
            if isinstance(m, dict) and ObjectId.is_valid(str(m.get("id", "")))
        ]
        if ids_mascotas:
            if estado == "Aprobada":
                alumnos.update_many(
                    {"_id": {"$in": ids_mascotas}},
                    {"$set": {"estado": "Adoptado"}, "$unset": {"reserva": ""}}
                )
            elif estado == "Rechazada":
                liberar_mascotas(solicitud)
            elif estado in ["Pendiente", "En revisión"]:
                alumnos.update_many(
                    {"_id": {"$in": ids_mascotas}, "estado": {"$ne": "Adoptado"}},
                    {"$set": {"estado": "En proceso"}, "$unset": {"reserva": ""}}
                )
        flash("Solicitud actualizada.", "success")
        return redirect(url_for("ver_solicitud", id=id))
    return render_template("editar_solicitud.html", solicitud=solicitud)


@app.route("/solicitudes/<id>/eliminar", methods=["POST"])
@administrador_requerido
def eliminar_solicitud(id):
    solicitud = buscar_solicitud(id)
    solicitudes.delete_one({"_id": solicitud["_id"]})
    if solicitud.get("estado") != "Aprobada":
        liberar_mascotas(solicitud)   # si se borra una solicitud abierta, la mascota no debe quedar oculta para siempre
    flash("Solicitud eliminada.", "success")
    return redirect(url_for("solicitudes_lista"))

@app.route("/api/mascotas/<id>", methods=["GET", "PUT", "DELETE"])
def api_mascota(id):
    mascota = buscar_alumno(id)
    if (request.method == "GET" and mascota.get("estado", "Disponible") != "Disponible"
            and not usuario_es_administrador()):
        abort(404, description="Esta mascota ya no está disponible")
    if request.method == "PUT":
        if not session.get("usuario_id"):
            abort(403, description="Inicia sesión para editar mascotas.")
        datos = leer_json(estado_actual=mascota.get("estado", "Disponible"))
        if not usuario_es_administrador():
            datos["estado"] = mascota.get("estado", "Disponible")
        alumnos.update_one({"_id": mascota["_id"]}, {"$set": datos})
        return jsonify(producto_json(buscar_alumno(id)))
    if request.method == "DELETE":
        if not session.get("usuario_id"):
            abort(403, description="Inicia sesión para eliminar mascotas.")
        borrar_imagen(mascota.get("imagen"))
        alumnos.delete_one({"_id": mascota["_id"]})
        return "", 204
    return jsonify(producto_json(mascota))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)