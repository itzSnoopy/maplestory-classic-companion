FROM python:3.12-slim
WORKDIR /app
COPY party_relay.py .
USER nobody
ENV PORT=8787
EXPOSE 8787
CMD ["python3", "-u", "party_relay.py"]
