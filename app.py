from flask import (Flask, render_template, request, redirect,
                   url_for, abort, jsonify)
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime
import os

app = Flask(__name__)
mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")
    mongo_uri = "mongodb://127.0.0.1:27017/"
cliente = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
base_datos = cliente["casa_adopcion_de_mascotas"]
alumnos = base_datos["perros"]


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
    if estado not in ["Disponible", "Adoptado"]:
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
    lista = list(alumnos.find().sort("nombre", 1))
    return render_template("index.html", alumnos=lista)


@app.route("/agregar", methods=["POST"])
def agregar():
    datos = leer_formulario()
    alumnos.insert_one(datos)
    return redirect(url_for("inicio"))


@app.route("/editar/<id>", methods=["GET", "POST"])
def editar(id):
    alumno = buscar_alumno(id)
    if request.method == "POST":
        datos = leer_formulario()
        alumnos.update_one({"_id": alumno["_id"]}, {"$set": datos})
        return redirect(url_for("inicio"))
    return render_template("editar.html", alumno=alumno)


@app.route("/eliminar/<id>", methods=["POST"])
def eliminar(id):
    alumno = buscar_alumno(id)
    alumnos.delete_one({"_id": alumno["_id"]})
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


@app.route("/login")
def login():
    # Pantalla visual de inicio de sesión. La autenticación real requiere
    # usuarios guardados, contraseñas cifradas y manejo de sesiones.
    return render_template("login.html")


@app.route("/carrito")
def carrito():
    # El carrito de solicitudes de adopción se conserva en el navegador.
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
    if request.method == "POST":
        datos = leer_json()
        resultado = alumnos.insert_one(datos)
        mascota = buscar_alumno(str(resultado.inserted_id))
        return jsonify(producto_json(mascota)), 201
    lista = alumnos.find().sort("nombre", 1)
    return jsonify([producto_json(m) for m in lista])


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