FROM python:3.9-slim

RUN pip install --no-cache-dir ryu eventlet==0.30.2

EXPOSE 6653 8080

CMD ["ryu-manager", "--verbose", "ryu.app.simple_switch_13", "ryu.app.ofctl_rest"]

