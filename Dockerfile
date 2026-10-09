FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py config.json ./
COPY static ./static
COPY assets ./assets
RUN mkdir -p /app/data
ENV DATA_DIR=/app/data
EXPOSE 8080
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8080","--proxy-headers"]
