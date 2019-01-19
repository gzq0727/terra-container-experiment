
from terra.plugin import manager
from container_expt import service
from container_expt.service import routers
from container_expt.db import migration
from container_expt import config

IMPL = migration


class ContainerExptPlugin(manager.BusinessPluginBase):

    def get_api_routers(self):
        return routers.Routers

    def get_api(self):
        api = dict(
            container_expt_api=service.ExperimentManager(),
            container_expt_rpcapi=service.ExperimentAPI(),
        )
        return api

    def get_rpcmanager(self):
        return 'container_expt.service.rpcmanager'

    def sync_db(self,version=None):
        IMPL.db_sync()

    def configure(self):
        config.configure()

    def db_version(self):
        pass
