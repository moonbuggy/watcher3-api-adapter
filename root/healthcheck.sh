#!/bin/sh

up="$(s6-svstat -o up /var/run/s6/services/watcher3-api-adapter/)"
ready="$(s6-svstat -o ready /var/run/s6/services/watcher3-api-adapter/)"

echo "Up: ${up}, Ready: ${ready}"

[ "x${up}" = "xtrue" ] && [ "x${ready}" = "xtrue" ] \
	&& exit 0

exit 1
