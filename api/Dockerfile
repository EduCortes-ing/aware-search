# Imagen base de Python ligera
FROM python:3.12-slim
# Carpeta de trabajo dentro del contenedor
WORKDIR /code
# Copia primero las dependencias (aprovecha la cache de Docker)
COPY requirements.txt .
# Instala las dependencias sin guardar cache
RUN pip install --no-cache-dir -r requirements.txt
# Copia el codigo de la aplicacion
COPY app/ ./app/
# Arranca el servidor web en el puerto 8000, visible dentro de la red Docker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
