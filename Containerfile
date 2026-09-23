# Gunakan base image resmi Red Hat UBI untuk Python 3.10
FROM registry.access.redhat.com/ubi9/python-39:latest 

# Beralih ke root sementara untuk menghindari masalah permission di /app
USER root

# Set direktori kerja
WORKDIR /app

# Salin file requirements.txt
COPY requirements.txt /app/

# Install dependensi
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh kode aplikasi
COPY . /app/

# Kembalikan ke user non-root (default UBI adalah user 1001) demi keamanan OpenShift
USER 1001

# Buka port 8000
EXPOSE 8000

# Jalankan FastAPI
CMD ["fastapi", "run"]