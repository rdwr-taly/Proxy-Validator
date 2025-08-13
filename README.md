# Proxy Validator Dockerized

This repository packages the [proXXy](https://github.com/Atropa-Solanaceae/proXXy) proxy scraper into a Docker
workflow.  The container runs proXXy, performs an additional HTTP validation pass, and can optionally
push the final list to remote servers.  The aim of this project is the *toolification* of proXXy rather than
a fork of the original code base.

> This project is for educational purposes only – please do not use it for illegal activities.

## Components

- **Dockerfile** – multi‑stage build that installs proXXy and the validation script.  Default environment
  variables for validation are defined here.
- **entrypoint.sh** – orchestrates the container lifecycle:
  1. run `proXXy.py` with any arguments passed to the container,
  2. execute `validate_proxies.py` to re‑check HTTP proxies,
  3. optionally distribute the validated list to remote hosts using `rsync`.
- **validate_proxies.py** – asynchronous validator that reads `/app/output/HTTP.txt`, filters out common
  SOCKS ports, checks each proxy against a target URL, and overwrites the file with successful results.
- **validate.sh** / **validate-proxies.sh** – legacy helper scripts for running the container and copying the
  output.  They are retained for reference but the Docker workflow described below is preferred.

## Building the image

```bash
docker build -t proxy-validator .
```

## Running

```bash
# run proXXy and secondary validation; output persists via volume mount
mkdir -p output

docker run --rm \
  -v "$(pwd)/output:/app/output" \
  proxy-validator --validate
```

The validated HTTP proxies will be written to `output/HTTP.txt` on the host.  Any flags after the image
name are passed directly to `proXXy.py`.

### Validation options

`validate_proxies.py` is configurable through environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `VALIDATION_TARGET_URL` | URL used to test proxies | `http://httpbin.org/ip` |
| `VALIDATION_TIMEOUT` | Timeout in seconds | `5` |
| `VALIDATION_CONCURRENCY` | Maximum concurrent checks | `100` |

Example:

```bash
docker run --rm -e VALIDATION_TIMEOUT=10 proxy-validator --validate
```

### Distributing results

Set the following environment variables to have `entrypoint.sh` send the final file to remote servers via `rsync`:

- `DISTRIBUTE_FILE=true`
- `REMOTE_DEST_PATH=/path/on/remote/host/HTTP.txt`
- `DISTRIBUTION_CONFIG_JSON` – JSON array of server definitions. Each object requires
  `host` and `user` and either `private_key` or `password`.

```bash
docker run --rm \
  -e DISTRIBUTE_FILE=true \
  -e REMOTE_DEST_PATH="/var/www/proxies/HTTP.txt" \
  -e DISTRIBUTION_CONFIG_JSON='[{"host":"1.2.3.4","user":"deploy","private_key":"-----BEGIN...END-----"}]' \
  proxy-validator --validate
```

## License

GPL‑3.0 – see `LICENSE` for details.

