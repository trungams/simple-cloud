FROM docker:18.09 AS docker-base
FROM python:3.7-slim

# docker cli
COPY --from=docker-base /usr/local/bin/docker /usr/bin/docker

RUN apt update && \
    apt upgrade && \
    apt install -y iproute2 uuid-runtime

# ovs stuffs
COPY ovs-scripts/ovs* /usr/bin/

# python code
WORKDIR /code
COPY . /code

# python dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["bash"]
