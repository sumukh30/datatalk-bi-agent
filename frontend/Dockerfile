FROM python:3.11-slim

WORKDIR /app

# Copy frontend files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project so imports work
COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "app:app"]