FROM python:3.13-slim

WORKDIR /app

# Dependencies first so a code change does not rebuild the wheel layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# The SQLite file lives on a mounted volume. Without one, every deploy would
# reset the pilot's measurements -- and the gate would silently read zero.
ENV CAE_DB=/data/cae.db
RUN mkdir -p /data

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
