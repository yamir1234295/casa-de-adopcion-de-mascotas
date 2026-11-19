# Huellitas - Casa de Adopción de Mascotas
## Descripción del proyecto
Huellitas es una aplicación web desarrollada para facilitar la adopción de mascotas. Su objetivo es conectar a las personas interesadas en adoptar con animales que necesitan un hogar, permitiendo consultar mascotas disponibles, registrarse y administrar solicitudes de adopción.
## Características principales
## Registro e inicio de sesión de usuarios.
  Catálogo de mascotas disponibles.
  Visualización de información y detalles de cada mascota.
  Carrito para agregar y quitar mascotas.
  Registro y consulta de solicitudes de adopción.
  Panel de administración para gestionar las solicitudes.
  Actualización del estado de las solicitudes.
  Sistema de reservas temporales para evitar que dos usuarios reserven la misma mascota.
  Liberación automática de mascotas cuando una reserva vence o se cancela.
  Generación de códigos QR para acceder a la información de la aplicación.
## Tecnologías utilizadas
  **Python:** lenguaje de programación principal.
  **Flask:** framework para desarrollar la aplicación web.
  **MongoDB Atlas:** base de datos en la nube.
  **HTML:** estructura de las páginas.
  **CSS:** diseño y estilos de la aplicación.
  **Git y GitHub:** control de versiones y almacenamiento del código.
  **Render:** alojamiento y publicación de la aplicación.
## Instalación y ejecución
Para ejecutar el proyecto localmente, sigue estos pasos:
1. Clona el repositorio:
git clone https://github.com/yamir1234295/casa-de-adopcion-de-mascotas.git
2. Entra a la carpeta del proyecto:
cd casa-de-adopcion-de-mascotas
3. Crea un entorno virtual:
python -m venv .venv
4. Activa el entorno virtual en visual:
.venv\Scripts\activate
5. Instala las dependencias:
python -m pip install -r requirements.txt
6. Configura las variables de entorno necesarias para la conexión con MongoDB Atlas y el funcionamiento de Flask.
7. Ejecuta la aplicación:
python app.py
8. Abre el navegador y visita:
http://127.0.0.1:5000
## Sistema de reservas temporales
La aplicación cuenta con un sistema de reservas temporales que permite apartar una mascota durante un periodo de tiempo limitado, de entre 3 y 5 minutos.
Durante ese tiempo, otro usuario no puede reservar la misma mascota. Si la reserva se confirma, se cancela o vence, el sistema actualiza su disponibilidad según corresponda
## Código QR
El proyecto incorpora códigos QR que permiten acceder a la aplicación mediante un enlace público. Esto facilita consultar la plataforma desde otros dispositivos.
## Base de datos
Se utiliza MongoDB Atlas para almacenar la información de los usuarios, las mascotas, las solicitudes y las reservas temporales.
## Despliegue
La aplicación está preparada para publicarse mediante Render, utilizando MongoDB Atlas como base de datos en la nube.
## Enlace de la aplicación:
https://crud-practica03.onrender.com/
## Autor
Proyecto desarrollado como trabajo final de la materia de Aplicaciones Web.
Integrantes:Yamir Abdiel López Pech
Angelli Guadalupe Noh Dzul
Proyecto: Huellitas - Casa de Adopción de Mascotas
 Objetivo
Desarrollar una plataforma web que facilite el proceso de adopción de mascotas mediante herramientas digitales, ofreciendo una experiencia sencilla para los usuarios y herramientas de administración para gestionar las solicitudes.
