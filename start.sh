#!/bin/sh

FLASK_DEBUG=1 FLASK_APP=server.py python3 -m flask run --port 8080
