# Imagen ligera de Nginx para servir la web estatica
FROM nginx:alpine
# Reemplaza la configuracion por defecto con la nuestra
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Copia los archivos de la web al directorio publico de Nginx
COPY public/ /usr/share/nginx/html/
