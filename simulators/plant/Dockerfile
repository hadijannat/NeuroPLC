FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plant_sim.py .
CMD ["python", "-u", "plant_sim.py"]
