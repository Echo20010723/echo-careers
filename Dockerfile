FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PUBLIC_MODE=true MODEL_MODE=mock VECTOR_BACKEND=memory APP_DATA_DIR=/app/data
COPY requirements.render.txt ./
RUN pip install --no-cache-dir -r requirements.render.txt
COPY app ./app
COPY prompts ./prompts
COPY config/config.yaml config/error_messages.json config/logging_rules.json config/occupations.tsv ./config/
COPY examples/jobs_demo.json examples/resume_demo.md examples/agent_jobs.json ./examples/
RUN useradd --create-home workbench && install -d -o workbench -g workbench /app/data
USER workbench
EXPOSE 8766
CMD ["sh","-c","exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8766} --workers 1 --no-access-log"]
