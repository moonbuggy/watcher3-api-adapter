#!/usr/bin/env python3

"""Watcher3 API Adapter."""

# pylint: disable=E1101,C0103

import os
import sys
import signal
import argparse
import configparser
import json
import logging
from types import SimpleNamespace
from typing import Dict
from threading import Thread
import requests
import urllib3  # type: ignore
from bottle import Bottle, response, request  # type: ignore
from waitress import serve
from psutil import disk_usage

CONFIG_FILE = 'watcher3-api-adapter.conf'
CONFIG_PATHS = [os.path.dirname(os.path.realpath(__file__)), '/etc/', '/conf/']

DEFAULT_LOG_LEVEL = logging.INFO

STDOUT_HANDLER = logging.StreamHandler(sys.stdout)
STDOUT_HANDLER.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))


def signal_handler(_sig, _frame):
    """Handle SIGINT cleanly."""
    print('\nSignal interrupt. Exiting.')
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)

loggers: Dict[str, str] = {}


def get_logger(class_name, log_level):
    """Get logger objects for individual classes."""
    name = os.path.splitext(os.path.basename(__file__))[0]
    if log_level == logging.DEBUG:
        name = '.'.join([name, class_name])

    if loggers.get(name):
        return loggers.get(name)

    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(STDOUT_HANDLER)
    logger.setLevel(log_level)

    # prevent duplicate messages from the web server thread
    logger.propagate = False

    loggers[name] = logger
    return logger


def underline(string: str):
    """Return a string with ANSI underline."""
    return f'\x1b[4m{string}\x1b[24m'


def log_connection(func):
    """Print connection information."""
    def inner(*args, **kwargs):
        logger = \
            logging.getLogger('watcher3-api-adapter')

        if not logger.handlers:
            logger.addHandler(STDOUT_HANDLER)

        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info('%s %s', underline(f'{request.method}:'), request.url)

        logger.debug('url: %s', request.url)
        logger.debug('method: %s', request.method)
        logger.debug('body: %s', request.body.read())
        for name, value in request.params.items():
            logger.debug('param: %s = %s', name, value)
        logger.debug('query:%s', dict(request.query.decode()))

        return func(*args, **kwargs)

    return inner


class WatcherHandler():
    """Handle connections to Watcher3."""

    def __init__(self, **kwargs):
        """Configure connection parameters."""
        self.params = SimpleNamespace(**kwargs)

        self.logger = \
            get_logger(self.__class__.__name__, self.params.log_level)

        self.watcher3_url = \
            f'{self.params.watcher3_scheme}://' \
            f'{self.params.watcher3_host}:{self.params.watcher3_port}' \
            f'/api?apikey={self.params.watcher3_apikey}'

        self.client = requests.session()

        try:
            self.config = self.get_data('getconfig')['config']
        except KeyError:
            sys.exit('ERROR: Could not get Watcher3 configuration. Exiting.')

        self.path_template = self.config['Postprocessing']['moverpath']
        self.rootfolder = self.path_template.rsplit('/', 1)[0]

    def get_data(self, mode: str, get_vars: str = ''):
        """Get and return data for a given mode."""
        if get_vars:
            get_vars = '&' + get_vars

        url = f'{self.watcher3_url}&mode={mode}{get_vars}'

        self.logger.debug('Fetching URL: %s', url)

        if self.params.watcher3_ssl_cert:
            ssl_verify = self.params.watcher3_ssl_cert
        else:
            ssl_verify = json.loads(self.params.watcher3_ssl_verify.lower())

        if not ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        try:
            data = self.client.get(url, verify=ssl_verify)
        except requests.exceptions.SSLError:
            self.logger.error(
                'Could not find a suitable TLS CA certificate bundle, '
                'invalid path: %s', self.params.watcher3_ssl_cert)
            sys.exit(1)
        except requests.exceptions.ConnectionError:
            error = 'Could not connect to host ' \
                f'{self.params.watcher3_scheme}://{self.params.watcher3_host}'
            self.logger.error(error)
            data = {
                "response": False,
                "error": error
            }

        try:
            data = data.json()
        except json.decoder.JSONDecodeError:
            data = data.text

        return data

    def get_path_template(self):
        """Return path template."""
        return self.config['Postprocessing']['moverpath']


class QualityProfile(list):
    """Generate a list of quality profiles."""

    def __init__(self, watcher_handler):
        """Parse Quality[] from getconfig into qualityProfile."""
        super().__init__()

        quality_data = watcher_handler.config['Quality']

        profile_count = 0
        for profile in quality_data['Profiles']:
            profile_count += 1
            self.append(self.parse_single_quality(
                profile,
                quality_data['Profiles'][profile],
                profile_count))

    @staticmethod
    def parse_single_quality(name, data: dict, profile_count: int):
        """Parse a single quality."""
        output = {}
        output['name'] = name

        items = []
        for item in data['Sources']:
            item_data = data['Sources'][item]

            try:
                source, res_string = item.lower().split('-', 1)
            except ValueError:
                source = item.lower()
                res_string = "0"

            res_map = {
                "sd": 480,
                "720p": 720,
                "1080p": 1080,
                "4k": 2160
            }

            resolution = res_map.get(res_string, 0)

            items.append({
                "quality": {
                    "id": item_data[1],
                    "name": item,
                    "source": source,
                    "resolution": int(resolution),
                    "modifier": "none"
                },
                "items": [],
                "allowed": item_data[0]
            })

        output['items'] = items
        output['minFormatScore'] = 0
        output['cutoffFormatScore'] = 0
        output['id'] = profile_count

        return output


class MovieList(list):
    """Generate a list of MovieDicts."""

    def __init__(self, watcher_handler, movie_id: str = None):
        """Fetch data for requested movie(s) and put it in MovieDict."""
        super().__init__()

        do_metadata = True
        if not movie_id:
            # In this case we're getting _all_ movies. We'll disable fetching
            # and parsing movie_metadata in the MovieDict.
            do_metadata = False
            id_string = ''
        elif movie_id[:2] == "tt":
            id_string = 'imdbid=' + movie_id
        else:
            id_string = 'tmdbid=' + movie_id

        liststatus = watcher_handler.get_data('liststatus', id_string)
        try:
            liststatus = liststatus['movies']
        except KeyError:
            print(f'ERROR: Could not fetch data for movie (id: {movie_id}).')
            self.append(liststatus)
        else:
            for movie in liststatus:
                movie_dict = MovieDict(watcher_handler, movie, do_metadata)
                self.append(movie_dict)


class MovieDict(dict):
    """Movie data collection and storage."""

    def __init__(self, watcher_handler, liststatus: dict, meta: bool = False):
        """Fetch movie data and populate dictionary.

        Start with Watcher's liststatus output, optionally add more data from
        the movie_metadata output. The movie_metadata is pulled one movie at a
        time, which takes ages if we're parsing a large library, so it makes
        sense to disable it if we're parsing more than a single movie.

        At least until I learn how to work asynchronous HTTP requests. :)
        """
        super().__init__()

        self.parse_liststatus(liststatus)

        if meta:
            try:
                metadata = watcher_handler.get_data(
                    'movie_metadata', 'imdbid=' + self['imdbId'])['tmdb_data']
                self.parse_movie_metadata(metadata)
            except KeyError:  # no movie movie_metadata
                pass

    def parse_liststatus(self, liststatus: dict):
        """Rewrite Watcher3 liststatus JSON in Radarr format.

        The data map translates data that requires minimal processing and sets
        some default blank values for data that either requires some processing
        which will be handled later, or won't be provided at all but may raise
        errors downstream if it's entirely absent.
        """
        data_map = {
            "tmdbId": int(liststatus['tmdbid']),
            "imdbId": liststatus['imdbid'],
            "title": liststatus['title'],
            "originalTitle": liststatus['title'],
            "sortTitle": liststatus['sort_title'].lower(),
            "sizeOnDisk": 0,
            "overview": liststatus['plot'],
            "images": [],
            "website": "",
            "year": "",
            "youTubeTrailerId": "",
            "studio": "",
            "cleanTitle": liststatus['title'].replace(' ', '').lower(),
            "titleSlug": liststatus['tmdbid'],
            "added": liststatus['added_date'] + 'T00:00:00Z',
            "ratings": {
                "votes": 0,
                "value": float(liststatus['score'])
            },
            "id": int(liststatus['tmdbid'])
        }

        for key, value in data_map.items():
            self[key] = value

        if liststatus['finished_file']:
            if os.path.isfile(liststatus['finished_file']):
                self['sizeOnDisk'] = \
                    os.path.getsize(liststatus['finished_file'])

            self['path'] = \
                '/'.join(liststatus['finished_file'].split('/')[:3])
            self['folderName'] = self['path']

        try:
            self['year'] = int(liststatus['year'])
        except ValueError:
            self['year'] = ''

        if liststatus['media_release_date']:
            self['physicalRelease'] = \
                liststatus['media_release_date'] + 'T00:00:00Z'

        if liststatus['release_date']:
            self['inCinemas'] = \
                liststatus['release_date'] + 'T00:00:00Z'

        if liststatus['rated']:
            self['certification'] = liststatus['rated']

        # alternate titles
        # This currently assumes everything is in English. Ideally we should be
        # using the more complete data from movie_metadata and parsing the
        # ISO 3166 codes there.
        self['alternateTitles'] = []
        if liststatus['alternative_titles']:
            title_count = 1
            for title in liststatus['alternative_titles'].split(','):
                title_count += 1  # start at 2 because 1 is the main title
                self['alternateTitles'].append({
                    "sourceType": "tmdb",
                    "movieId": int(liststatus['tmdbid']),
                    "title": title,
                    "sourceId": int(liststatus['tmdbid']),
                    "votes": 0,
                    "voteCount": 0,
                    "language": {
                        "id": 1,
                        "name": "English"
                    },
                    "id": title_count
                })

    def parse_movie_metadata(self, metadata: dict):
        """Rewrite Watcher3 movie_metadata JSON in Radarr format."""
        data_map = {
            "status": metadata['status'].lower(),
            "website": metadata['homepage'],
            "runtime": metadata['runtime'],
            "ratings": {
                "votes": metadata['vote_count'],
                "value": metadata['vote_average']
            }
        }

        for key, value in data_map.items():
            self[key] = value

        try:
            self['studio'] = metadata['production_companies'][0]['name']
        except IndexError:  # no production company data
            pass

        try:
            release_country = \
                metadata['production_countries'][0]['iso_3166_1']
        except IndexError:  # no production country data
            local_country = False
        else:
            # Radarr's API seems to use user-country specific data for releases
            # and certification. Watcher3's API provides data from multiple
            # countries for these settings, so we should allow a user to
            # configure this for the API translation
            local_country = release_country

        # get all local releases
        try:
            release_dates = \
                list(filter(lambda x: x['iso_3166_1'] == local_country,
                            metadata['release_dates']['results']
                            ))[0]['release_dates']
        except IndexError:  # no data for release local_country
            pass
        else:
            self.parse_release_dates(release_dates)

        self['images'] = []

        # poster image
        if metadata['poster_path']:
            self['images'].append({
                "coverType": "poster",
                "remoteURL": "https://image.tmdb.org/t/p/original" +
                             metadata['poster_path']
                })

        # fanart image
        if metadata['backdrop_path']:
            self['images'].append({
                "coverType": "fanart",
                "remoteURL": "https://image.tmdb.org/t/p/original" +
                             metadata['backdrop_path']
                })

        # genres
        self['genres'] = []
        for genre in metadata['genres']:
            self['genres'].append(genre['name'])

        # alternate titles
        self['alternateTitles'] = []
        title_count = 1
        for title in metadata['alternative_titles']['titles']:
            title_count += 1
            self['alternateTitles'].append({
                "sourceType": "tmdb",
                "movieId": metadata['id'],
                "title": title['title'],
                "sourceId": metadata['id'],
                "votes": 0,
                "voteCount": 0,
                "language": {
                    "id": 1,
                    "name": "English"
                },
                "id": title_count
            })

    def parse_release_dates(self, release_dates: dict):
        """Parse the release date data for a specific country."""
        # local cinema release data
        try:
            cinema_release = \
                list(filter(lambda x: x['type'] == 3, release_dates))[0]
        except IndexError:  # no cinema release date found
            pass
        else:
            self['inCinemas'] = cinema_release['release_date']
            self['certification'] = cinema_release['certification']

        # physical release
        try:
            self['physicalRelease'] = \
                list(filter(lambda x: x['type'] == 5,
                            release_dates))[0]['release_date']
        except IndexError:  # no phyiscal release date found
            pass

        # digital release
        try:
            self['digitalRelease'] = \
                list(filter(lambda x: x['type'] == 4,
                            release_dates))[0]['release_date']
        except IndexError:  # no digital release date found
            pass


class RequestHandler():
    """Handle API connections."""

    def __init__(self, app, watcher_handler, **kwargs):
        """Get args and set route."""
        self.params = SimpleNamespace(**kwargs)

        self.logger = get_logger(
            self.__class__.__name__, self.params.log_level)

        self.app = app
        self.app.router.add_filter('id_filter', self.id_filter)

        self.app.route('/api/v3/movie<movie_id:id_filter>',
                       method="GET", callback=self.get_movie)
        self.app.route('/api/v3/movie<movie_id:id_filter>',
                       method="PUT", callback=self.put_movie)
        self.app.route('/api/v3/movie',
                       method="POST", callback=self.add_movie)
        self.app.route('/api/v3/qualityProfile',
                       method="GET", callback=self.get_qualities)
        self.app.route('/api/v3/rootfolder',
                       method="GET", callback=self.get_rootfolder)
        self.app.route('/api/v3/system/status',
                       method="GET", callback=self.get_status)
        self.app.route('/api/v3/Command/',
                       method="POST", callback=self.do_command)
        self.app.route('/<path:path>',
                       method="ANY", callback=self.log_unknown)

        self.watcher_handler = watcher_handler

    @staticmethod
    def id_filter(_config):
        """Match an id provided to /movie."""
        regexp = r'/?((tt)?\d*)?'

        def to_id(match):
            return match.replace('/', '')

        def to_url(movie_id):
            return '/' + movie_id

        return regexp, to_id, to_url

    def start(self):
        """Start the server in a thread, report readiness if appropriate."""
        server_thread = Thread(target=self.run_server)
        server_thread.start()

        if self.params.ready_fd:
            self.logger.info('Initialization done. Signalling readiness.')
            self.logger.debug(
                'Readiness signal writing to file descriptor %s.',
                self.params.ready_fd)
            try:
                os.write(int(self.params.ready_fd), '\n'.encode())
            except OSError:
                self.logger.warning('Could not signal file descriptor \'%s\'.',
                                    self.params.ready_fd)
        else:
            self.logger.info('Initialization done.')

    def run_server(self):
        """Run the WSGI server."""
        serve(self.app, host=self.params.ip, port=self.params.port)

    def respond(self, status: int, content):
        """Respond to client with data."""
        self.logger.debug('Returning data to client.')

        response.content_type = 'application/json; charset=utf-8'
        response.status = status

        if content:
            return json.dumps(content, indent=2)
        return False

    @log_connection
    def get_movie(self, movie_id: str):
        """Get a single movie."""
        if not movie_id:
            id_string = None
        elif movie_id[:2] == "tt":
            id_string = 'imdbid=' + movie_id
        else:
            id_string = 'tmdbid=' + movie_id

        if id_string:
            self.logger.debug('Getting movie with id: %s..', movie_id)
        else:
            self.logger.debug('Getting all movies.')

        movie_list = MovieList(self.watcher_handler, movie_id)

        if not movie_list:
            return self.respond(404, {'message': 'NotFound'})

        if movie_id:
            output = movie_list[0]
        else:
            output = movie_list

        return self.respond(200, output)

    @log_connection
    def put_movie(self, movie_id):
        """Edit existing movie.

        This doesn't actually edit anything at the moment.
        """
        self.logger.info('PUT movie request: %s', movie_id)
        self.logger.debug('url: %s', request.url)
        self.logger.debug('method: %s', request.method)
        for name, value in request.params.items():
            self.logger.debug('param: %s = %s', name, value)
        self.logger.debug('body: %s', request.body.read())

        self.logger.info('Doing nothing.')
        return self.respond(200, False)

    @log_connection
    def add_movie(self):
        """Add a movie."""
        request_data = request.json

        try:
            movie_id = str(request_data["tmdbId"])
            id_string = 'tmdbid=' + movie_id
        except KeyError:
            try:
                movie_id = request_data["imdbId"]
                id_string = 'imdbid=' + movie_id
            except KeyError:
                self.logger.error('No imdb or tmdb id provided for add_movie.')
                return self.respond(
                    400, self.add_movie_error(0, "NotEmptyValidator"))

        self.logger.info('Adding movie with id string: %s', id_string)
        self.logger.debug('Request data:\n%s',
                          json.dumps(request_data, indent=2))

        data = self.watcher_handler.get_data('addmovie', id_string)

        response_data = self.get_movie(movie_id)

        if not data['response']:
            if 'already exists' in data['error']:
                error_data = self.add_movie_error(
                    response_data['tmdbId'], "MovieExistsValidator")
            else:
                error_data = {"error": data['error']}

            return self.respond(400, error_data)

        # add 'addOptions' from the request
        response_data['addOptions'] = request_data['addOptions']

        return self.respond(201, response_data)

    @staticmethod
    def add_movie_error(tmdb_id: str, error_code: str):
        """Return error on movie addition failure."""
        error_map = {
            "NotEmptyValidator": "\u0027Tmdb Id\u0027 must not be empty.",
            "MovieExistsValidator": "This movie has already been added"
        }

        return [{
            "propertyName": "TmdbId",
            "errorMessage": error_map[error_code],
            "attemptedValue": int(tmdb_id),
            "severity": "error",
            "errorCode": "NotEmptyValidator",
            "formattedMessageArguments": [],
            "formattedMessagePlaceholderValues": {
                "propertyName": "Tmdb Id",
                "propertyValue": int(tmdb_id)
            },
            "resourceName": error_code
        }]

    @log_connection
    def get_qualities(self):
        """Get quality profiles."""
        return self.respond(200, QualityProfile(self.watcher_handler))

    @log_connection
    def get_rootfolder(self):
        """Get quality profiles."""
        data = self.watcher_handler.get_data('getconfig')

        if not data['response']:
            response.status = 405
            return dict({"error": data['error']})

        if os.path.isdir(self.watcher_handler.rootfolder):
            free_space = disk_usage(self.watcher_handler.rootfolder).total
        else:
            free_space = 1000000000000

        output = [{
            "path": self.watcher_handler.rootfolder,
            "accessible": True,
            "freeSpace": free_space,
            "id": 1
            }]

        return self.respond(200, output)

    @log_connection
    def get_status(self):
        """Get the system status."""
        data = self.watcher_handler.get_data('version')

        if not data['response']:
            return self.respond(405, dict({"error": data['error']}))

        output = {"version": data['version']}
        return self.respond(200, output)

    @log_connection
    def get_search_results(self, movie_id):
        """Search a movie by imdbid or tmdbid."""
        self.logger.info('url: %s', request.url)
        self.logger.info('method: %s', request.method)
        post_data = request.json
        self.logger.debug('POST data:\n%s', json.dumps(post_data, indent=4))
        self.logger.info('body: %s', request.body.read())

        # convert any tmbdid to an imdbid
        if movie_id[:2] != "tt":
            movie_id = str(self.get_movie(movie_id)['imdbId'])

        data = self.watcher_handler.get_data(
            'search_results', 'imdbid=' + movie_id)

        return self.respond(200, data)

    @log_connection
    def do_command(self):
        """Handle POSTed commands.

        Currently just does MoviesSearch.
        """
        for name, value in request.forms.items():
            self.logger.info('param: %s = %s', name, value)

        post_data = request.json
        self.logger.debug('POST data:\n%s', json.dumps(post_data, indent=4))

        if post_data['name'] == "MoviesSearch":
            movie_id = str(post_data['movieIds'][0])
            self.logger.info('Searching movie: %s', movie_id)
            output = self.get_search_results(movie_id)
            return self.respond(200, json.dumps(output))

        self.logger.error('Unknown command: %s', post_data['name'])
        return self.respond(404, False)

    @log_connection
    def log_unknown(self, path):
        """Log requests to unconfigured paths."""
        self.logger.info('Unknown request: %s', path)
        self.logger.debug('url: %s', request.url)
        self.logger.debug('method: %s', request.method)
        for name, value in request.params.items():
            self.logger.debug('param: %s = %s', name, value)
        self.logger.debug('body: %s', request.body.read())

        return self.respond(404, False)


class ConfigHandler():
    """Read config files and parse commandline arguments."""

    log_level = DEFAULT_LOG_LEVEL

    def __init__(self):
        """Set default values then parse configs."""
        self.defaults = {
            'config_file': CONFIG_FILE,
            'log_level': self.log_level,
            'ip': '0.0.0.0',
            'port': '8080',
            'watcher3_host': None,
            'watcher3_port': '80',
            'watcher3_scheme': 'http',
            'watcher3_apikey': '',
            'watcher3_ssl_cert': None,
            'watcher3_ssl_verify': True,
            'pidfile': '/tmp/watcher3-api-adapter.pid',
            'debug': True
        }

        self.args = []
        self.config_parser = argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=__doc__, add_help=False)

        self.parse_initial_config()
        self.parse_config_file()
        self.parse_command_line()

    def parse_initial_config(self):
        """Just enough argparse to specify a config file and a debug flag."""
        self.config_parser.add_argument(
            '-c', '--config_file', action='store', metavar='FILE',
            help='external configuration file')
        self.config_parser.add_argument(
            '--debug', action='store_true', help='turn on debug messaging')

        self.args = self.config_parser.parse_known_args()[0]

        if self.args.debug:
            self.log_level = logging.DEBUG
            self.defaults['log_level'] = logging.DEBUG

        self.logger = get_logger(self.__class__.__name__, self.log_level)

        self.logger.debug('Initial args: %s',
                          json.dumps(vars(self.args), indent=4))

    def parse_config_file(self):
        """Find and read external configuration files, if they exist."""
        self.logger.debug('self.args.config_file: %s', self.args.config_file)

        # find external configuration if none is specified
        if self.args.config_file is None:
            for config_path in CONFIG_PATHS:
                config_file = os.path.join(config_path, CONFIG_FILE)
                self.logger.debug('Looking for config file: %s', config_file)
                if os.path.isfile(config_file):
                    self.logger.info('Found config file: %s', config_file)
                    self.args.config_file = config_file
                    break

        if self.args.config_file is None:
            self.logger.info('No config file found.')

        # read external configuration if specified and found
        if self.args.config_file is not None:
            if os.path.isfile(self.args.config_file):
                config = configparser.ConfigParser()
                config.read(self.args.config_file)
                self.defaults.update(dict(config.items("api")))
                self.defaults.update(dict(config.items("watcher3")))
                self.logger.debug('Args from config file: %s',
                                  json.dumps(self.defaults, indent=4))
            else:
                self.logger.error('Config file (%s) does not exist.',
                                  self.args.config_file)

    def parse_command_line(self):
        """
        Parse command line arguments.

        Overwrite both default config and anything found in a config file.
        """
        parser = argparse.ArgumentParser(
            description='Watcher3 API Adapter', parents=[self.config_parser])
        parser.set_defaults(**self.defaults)
        parser.add_argument(
            '-i', '--ip', action='store', metavar='ADDRESS',
            help='ip to listen on (default \'%(default)s\')')
        parser.add_argument(
            '-p', '--port', action='store', metavar='PORT',
            help='port to listen on (default \'%(default)s\')')
        parser.add_argument(
            '-w', '--watcher3-host', action='store', metavar='ADDRESS',
            help='Watcher3 host (default \'%(default)s\')')
        parser.add_argument(
            '-P', '--watcher3-port', action='store', metavar='PORT',
            help='Watcher3 port (default \'%(default)s\')')
        parser.add_argument(
            '-s', '--watcher3-scheme', action='store', metavar='SCHEME',
            choices=['http', 'https'],
            help='Watcher3 scheme (default \'%(default)s\')')
        parser.add_argument(
            '-k', '--watcher3-apikey', action='store', metavar='KEY',
            help='Watcher3 apikey (default \'%(default)s\')')
        parser.add_argument(
            '-C', '--watcher3-ssl-cert', action='store', metavar='CERTIFICATE',
            help='Watcher3 SSL certificate path (default \'%(default)s\')')
        parser.add_argument(
            '-S', '--watcher3-ssl-verify', action='store', metavar='BOOL',
            help='Watcher3 SSL verification (default \'%(default)s\')')
        parser.add_argument(
            '--ready_fd', action='store', metavar='INT',
            help='set to an integer to enable signalling readiness by writing '
            'a new line to that integer file descriptor')
        self.args = parser.parse_args()

        self.logger.debug('Parsed command line:\n%s',
                          json.dumps(vars(self.args), indent=4))

    def get_args(self):
        """Return all config parameters."""
        return self.args


def main():
    """Do all the things."""
    config = ConfigHandler()
    args = config.get_args()

    app = Bottle()

    watcher_handler = WatcherHandler(**vars(args))
    request_handler = \
        RequestHandler(app, watcher_handler, **vars(args))

    try:
        request_handler.start()
    except SystemExit:
        pass


if __name__ == '__main__':
    main()
