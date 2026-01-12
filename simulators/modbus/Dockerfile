FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY modbus_server.py .
EXPOSE 5020
CMD ["python", "-u", "modbus_server.py"]
