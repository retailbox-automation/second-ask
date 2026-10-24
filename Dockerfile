# Second Ask demo: REPLAY by default; LIVE only with DEMO_LIVE=1 and NEBIUS_API_KEY set at run time.
# The live budget is the ledger tag "demo" ($5): keep the ledger on a volume (TF_LEDGER or the default path below), or a
# restart starts it from zero. Built here for a later deployment; not deployed from this repository.
FROM python:3.12.7-slim-bookworm

WORKDIR /app
COPY second_ask/ second_ask/
COPY demo/ demo/
COPY seeds/ seeds/
COPY tournament/buyers.json tournament/buyers.json
COPY LICENSE NOTICE README.md RESULTS.md ./

RUN useradd --create-home app && mkdir -p /home/app/.local/state/nebius-hackathon && chown -R app /home/app
USER app

ENV HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "demo.server"]
