FROM python:3.12-slim

WORKDIR /app

# Force unbuffered Python output for Cloud Logging
ENV PYTHONUNBUFFERED=1

# Install nginx
RUN apt-get update && apt-get install -y nginx && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server/*.py ./
COPY client/ /usr/share/nginx/html/

# Configure nginx for Cloud Run (single container)
RUN echo 'events { worker_connections 1024; }\n\
http {\n\
    include /etc/nginx/mime.types;\n\
    server {\n\
        listen 8080;\n\
        location / {\n\
            root /usr/share/nginx/html;\n\
            index index.html;\n\
        }\n\
        location /api/ {\n\
            proxy_pass http://127.0.0.1:5001;\n\
        }\n\
        location = /ws {\n\
            proxy_pass http://127.0.0.1:5001;\n\
            proxy_http_version 1.1;\n\
            proxy_set_header Upgrade $http_upgrade;\n\
            proxy_set_header Connection "upgrade";\n\
        }\n\
    }\n\
}' > /etc/nginx/nginx.conf

# Start script to run both nginx and Flask
RUN echo '#!/bin/bash\nnginx && python app.py' > /start.sh && chmod +x /start.sh

EXPOSE 8080

CMD ["/start.sh"]
