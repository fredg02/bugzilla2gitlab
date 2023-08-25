FROM python:3.10-alpine

RUN apk --no-cache add git bash

RUN python3 -m venv venv && \
source venv/bin/activate && \
python3 -m pip install --upgrade pip

WORKDIR /bugzilla2gitlab

COPY requirements.txt .
RUN pip install -r requirements.txt
