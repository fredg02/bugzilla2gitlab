FROM python:3.9-alpine

RUN apk --no-cache add git bash

RUN python3 -m venv venv && \
source venv/bin/activate && \
python3 -m pip install --upgrade pip

