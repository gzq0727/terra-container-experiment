from oslo_config import cfg
from oslo_db import concurrency
from oslo_log import log as logging

CONF = cfg.CONF

_BACKEND_MAPPING = {'sqlalchemy': 'container_expt.service.backends.sql.api_sqlalchemy'}

IMPL = concurrency.TpoolDbapiWrapper(CONF, backend_mapping=_BACKEND_MAPPING)

LOG = logging.getLogger(__name__)