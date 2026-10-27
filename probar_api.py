import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:5000"
BASE = "https://casa-de-adopcion-de-mascotas.onrender.com/"


def pedir(metodo, ruta, datos=None):
    cuerpo = None if datos is None else json.dumps(datos).encode()
    peticion = Request(
        BASE + ruta, data=cuerpo, method=metodo,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(peticion, timeout=30) as respuesta:
            texto = respuesta.read().decode()
            print(metodo, ruta, respuesta.status, texto)
            return json.loads(texto) if texto else None
    except HTTPError as error:
        print("Error:", error.code, error.read().decode())
        raise


mascota = pedir("POST", "/api/mascotas", {
    "nombre": "Prueba API", "categoria": "Perro",
    "estado": "Disponible"
})
ruta = "/api/mascotas/" + mascota["id"]
pedir("GET", ruta)
pedir("PUT", ruta, {
    "nombre": "Prueba API", "categoria": "Perro",
    "estado": "Adoptado"
})
pedir("GET", "/api/mascotas")
pedir("DELETE", ruta)