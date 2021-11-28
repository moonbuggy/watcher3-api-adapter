#! /bin/bash
# shellcheck disable=SC2034

#NOOP='true'
#DO_PUSH='true'
#NO_BUILD='true'

DOCKER_REPO="${DOCKER_REPO:-moonbuggy2000/watcher3-api-adapter}"

all_tags='alpine binary'
default_tag='alpine'

. "hooks/.build.sh"
