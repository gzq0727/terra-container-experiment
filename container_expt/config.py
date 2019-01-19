import socket

from oslo_cache import core as cache
from oslo_config import cfg
from oslo_db import options as db_options
from oslo_log import log

from terra.common import paths
from terra.i18n import _

_CERTFILE = '/etc/terra/ssl/certs/signing_cert.pem'
_KEYFILE = '/etc/terra/ssl/private/signing_key.pem'

_DEFAULT_SQL_CONNECTION = 'sqlite:///' + paths.state_path_def('terra.sqlite')

_DEFAULT_LOG_LEVELS = ['terra=INFO', 'oslo_messaging=INFO']

_DEFAULT_LOGGING_CONTEXT_FORMAT = ('%(asctime)s.%(msecs)03d %(process)d '
                                   '%(levelname)s %(name)s [%(request_id)s '
                                   '%(user_identity)s]'
                                   '%(message)s')
FILE_OPTIONS = {
    'container_expt': [
        cfg.StrOpt('driver',
                   default='sql',
                   help='Entrypoint for the container_experiment backend driver in the '
                        'terra.container_expt namespace. Supplied drivers are '
                        'sql.'),
    ],
}

CONF = cfg.CONF


def setup_logging():
    log.setup(CONF, "plugin-container_expt")


def configure(conf=None):
    if conf is None:
        conf= CONF
    for section in FILE_OPTIONS:
        for option in FILE_OPTIONS[section]:
            if section:
                conf.register_opt(option, group=section)

    db_options.set_defaults(
        conf,
        connection=_DEFAULT_SQL_CONNECTION,
        sqlite_db='terra.sqlite'
    )
    cache.configure(conf)