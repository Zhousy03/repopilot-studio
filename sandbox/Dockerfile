# Build-time network access is explicit. Test execution itself has --network=none.
FROM python:3.12-slim
RUN python -m pip install --no-cache-dir pytest==8.4.2
COPY sandbox/runner.py /opt/repopilot_runner.py
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python", "/opt/repopilot_runner.py"]
