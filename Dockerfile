FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY . .
RUN pip install --no-cache-dir --no-deps -e . \
    && useradd --create-home --uid 1000 balloon \
    && chown -R balloon:balloon /app
USER balloon
EXPOSE 8000
CMD ["python", "-m", "balloon_predictor", "serve", "--host", "0.0.0.0", "--port", "8000"]
