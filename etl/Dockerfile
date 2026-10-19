# Imagen base de Python ligera
FROM python:3.12-slim
# Carpeta de trabajo
WORKDIR /code
# Dependencias del ETL
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Script de carga
COPY load_excel.py .
# Ejecuta la carga al correr el contenedor
CMD ["python", "load_excel.py"]
