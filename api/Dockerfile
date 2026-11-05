FROM python:3.12-slim
# Docker CLI only: the API launches throwaway sandbox containers through the host's Docker socket.
COPY --from=docker:27.3.1-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
