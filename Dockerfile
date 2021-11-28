ARG BUILD_PYTHON_VERSION="3.9"
ARG FROM_IMAGE="moonbuggy2000/alpine-s6-python:${BUILD_PYTHON_VERSION}"

ARG BUILDER_ROOT="/builder-root"

ARG TARGET_ARCH_TAG="amd64"

ARG PUID=1000
ARG PGID=1000


## build the virtual environment and prepare files
#
FROM "${FROM_IMAGE}" AS builder

# QEMU static binaries from pre_build
ARG QEMU_DIR=""
ARG QEMU_ARCH=""
COPY _dummyfile "${QEMU_DIR}/qemu-${QEMU_ARCH}-static*" /usr/bin/

ARG APP_PATH="/app"
ARG BUILDER_ROOT
WORKDIR "${BUILDER_ROOT}${APP_PATH}"

ARG VIRTUAL_ENV="${APP_PATH}/venv"
ENV	VIRTUAL_ENV="${VIRTUAL_ENV}" \
	PYTHONDONTWRITEBYTECODE="1" \
	PYTHONUNBUFFERED="1" \
	LIBSODIUM_MAKE_ARGS="-j4"

RUN python3 -m pip install --upgrade virtualenv \
	&& python3 -m virtualenv --download "${BUILDER_ROOT}${VIRTUAL_ENV}"

COPY ./requirements.txt ./

# Python wheels from pre_build
ARG TARGET_ARCH_TAG="amd64"
ARG IMPORTS_DIR=".imports"
COPY _dummyfile "${IMPORTS_DIR}/${TARGET_ARCH_TAG}*" "/${IMPORTS_DIR}/"

# activate virtual env
ENV ORIGINAL_PATH="$PATH"
ENV PATH="${BUILDER_ROOT}${VIRTUAL_ENV}/bin:$PATH"

RUN if ! python3 -m pip install --only-binary=:all: --find-links "/${IMPORTS_DIR}/" -r requirements.txt; then \
			echo "ERROR: Could not build with binary wheels. Attempting to build from source.."; \
			apk add --no-cache \
				gcc \
				linux-headers \
				musl-dev; \
			python3 -m pip install --find-links "/${IMPORTS_DIR}/" -r requirements.txt; \
		fi

COPY ./watcher3-api-adapter.conf ./conf/
COPY ./watcher3-api-adapter.py ./watcher3-api-adapter

WORKDIR "${BUILDER_ROOT}"

COPY ./root ./

RUN add-contenv \
		APP_PATH="${APP_PATH}" \
		PATH="${VIRTUAL_ENV}/bin:${ORIGINAL_PATH}" \
		VIRTUAL_ENV="${VIRTUAL_ENV}" \
		PYTHONDONTWRITEBYTECODE="1" \
		PYTHONUNBUFFERED="1" \
	&& cp /etc/contenv_extra ./etc/ \
	&& chmod a+x ./healthcheck.sh


## build the image
#
FROM "${FROM_IMAGE}" AS builder-final

# QEMU static binaries from pre_build
ARG QEMU_DIR=""
ARG QEMU_ARCH=""
COPY _dummyfile "${QEMU_DIR}/qemu-${QEMU_ARCH}-static*" /usr/bin/

ARG PUID
ARG PGID

# Install packages
RUN apk --update add --no-cache \
		shadow \
	# Add user and group \
	&& addgroup -g ${PGID} adapter \
	&& adduser -DH -u ${PUID} -G adapter adapter

# Remove QEMU binaries
RUN rm -f "/usr/bin/qemu-${QEMU_ARCH}-static" >/dev/null 2>&1

ARG BUILDER_ROOT
COPY --from=builder "${BUILDER_ROOT}/" /


## drop QEMU static binaries
#
FROM "moonbuggy2000/scratch:${TARGET_ARCH_TAG}"

ARG PUID
ARG PGID

ENV PUID="${PUID}" \
		PGID="${PGID}"

COPY --from=builder-final / /

HEALTHCHECK --start-period=5s --timeout=10s CMD /healthcheck.sh

ENTRYPOINT ["/init"]
