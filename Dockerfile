# Симуляция лифта: зависимостей нет, образ — голый Python.
#   docker build -t elevator-sim . && docker run -p 8000:8000 elevator-sim
FROM python:3.12-slim
WORKDIR /app
COPY sim/ sim/
COPY web/ web/
COPY scenarios/ scenarios/
EXPOSE 8000
CMD ["python3", "-m", "web.server", "--port", "8000"]
