# Watcher3 API Adapter
Sits in front of Watcher3's API and pretends to be Radarr.

*   [Rationale](#rationale)
*   [Usage](#usage)
*   [Setup](#setup)
    *   [Installation of script](#installation-of-script)
    *   [Installation of Docker container](#installation-of-docker-container)
*   [Links](#links)

## Rationale
I don't know C# well enough to add a Watcher3 API to Ombi. I know enough Python to make Watcher3 pretend to be Radarr though. Since Ombi knows how to talk to Radarr, this seems like a semi-reasonable thing to do. :)

This script only implements the bare minimum that Ombi requires, within the limitations of what Watcher3's API is easily capable of. It may not work in other situations, and in fact should currently be considered a beta that may not work entirely properly in the intended situation.

The script can be run standalone or as the provided Docker container.

## Usage
```
usage: watcher3-api-adapter.py [-h] [-c FILE] [--debug] [-i ADDRESS] [-p PORT]
                               [-s SCHEME] [-w ADDRESS] [-P PORT] [-k KEY]
                               [--ready_fd INT]

Watcher3 API Adapter

optional arguments:
  -h, --help            show this help message and exit
  -c FILE, --config_file FILE
                        external configuration file
  --debug               turn on debug messaging
  -i ADDRESS, --ip ADDRESS
                        ip to listen on (default '0.0.0.0')
  -p PORT, --port PORT  port to listen on (default '8080')
  -w ADDRESS, --watcher3-host ADDRESS
                        Watcher3 host (default '')
  -P PORT, --watcher3-port PORT
                        Watcher3 port (default '80')
  -s SCHEME, --watcher3-scheme SCHEME
                        Watcher3 scheme (default 'http')
  -k KEY, --watcher3-apikey KEY
                        Watcher3 apikey (default '')
  -C CERTIFICATE, --watcher3-ssl-cert CERTIFICATE
                        Watcher3 SSL certificate path (default '')
  -S BOOL, --watcher3-ssl-verify BOOL
                        Watcher3 SSL verification (default 'True')
  --ready_fd INT        set to an integer to enable signalling readiness by
                        writing a new line to that integer file descriptor
```

Any command line parameters take precedence over settings in `watcher3-api-adapter.conf`.

If using the `https` scheme on a local network using a privately generated SSL certificate it will be necessary to either provide the script with the certificate file with `--watcher3-ssl-cert` or to disable certificate verification with `--watcher-ssl-verify`.

## Setup
Watcher3 API Adapter requires at least Python 3.6 and the bottle, psutil, requests and waitress modules.

If Watcher3's movie root path is not accessible to the script it will report 1TB of free disk space, an arbitrary value to always indicate there's significant available capacity. Likewise, media file sizes will be reported as zero bytes.

### Installation of script
Install requirements: `pip3 install -r requirements.txt`

Put `watcher3-api-adapter.py` anywhere in the path.

Put `watcher3-api-adapter.conf` in `/etc/` or in the same directory as the script (which takes precedence over any config file in `/etc/`).

### Installation of Docker container
```
docker run -d --name watcher3-api-adapter \
           -v <movie_root_path>:/movies \
           -v <cert_path>:/certs \
           -e <env vars> \
           moonbuggy2000/watcher3-api-adapter
```

If you're using a config file instead of environment variables you'll need to persist it with `-v <host path>:/app/conf/watcher3-api-adapter.conf`.

The movie root path mount in the container should match that used in Watcher3 to accurately report free disk space and determine media file sizes. The mount is not used for anything else and can be omitted if you're happy to always report there's lots of free capacity and media files are zero bytes.

The cert path is optional, only required if necessary for verifiable SSL to talk to Watcher3. Otherwise you can use SSL with verification disabled, or just plain HTTP.

#### Docker environment variables
Almost all the command line parameters (see Usage) can be set with environment variables:

*   `WAA_PORT`                 - port to listen on, defaults to `8080`
*   `WAA_WATCHER3_HOST`        - Watcher3 API host to connect to, defaults to `watcher3`
*   `WAA_WATCHER3_PORT`        - Watcher3 API port to connect to, defaults to `80`
*   `WAA_WATCHER3_SCHEME`      - Watcher3 scheme to use, defaults to `http`
*   `WAA_WATCHER3_APIKEY`      - Watcher3 API key
*   `WAA_WATCHER3_SSL_CERT`    - the path to the SSL certificate file Watcher3 uses, defaults to none
*   `WAA_WATCHER3_SSL_VERIFY`  - enable SSL certificate verification, defaults to `True`
*   `WAA_DEBUG`                - enable debugging messaging
*   `PUID`                     - user ID to run as (default: `1000`)
*   `PGID`                     - group ID to run as (default: `1000`)

`PUID`/`GUID` may be required to be set to ensure the script has permissions to determine free drive capacity, they currently makes no difference for anything else.

#### Tags
To minimize the Docker image size, and to theoretically improve run times, the default build is binary, tagged as `latest` and `binary`.

A build using the uncompiled Python script is available, tagged `script`.

#### Architectures
The main `latest`, `binary` and `script` tags should automatically provide images compatible with `amd64`, `arm`/`armv7`, `armhf`/`armv6`, `arm64`, `386` and `ppc64le` platforms. Tags for specific single-arch images are available, in the form `alpine-<arch>` and `alpine-binary-<arch>` for the `script` and `binary` builds respectively.

**Note:** I haven't necessarily tested any particular build on anything other than `amd64`. The `script` build is more portable and less likely to have problems on un-tested architectures (although the `binary` builds _should_ be fine). If `binary` doesn't work on a particular piece of hardware, `script` would be worth trying.

## Links
GitHub: <https://github.com/moonbuggy/watcher3-api-adapter>

Docker Hub: <https://hub.docker.com/r/moonbuggy2000/watcher3-api-adapter>
