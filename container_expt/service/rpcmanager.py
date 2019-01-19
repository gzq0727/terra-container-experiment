import eventlet
import json
import oslo_messaging as messaging
from oslo_config import cfg
from oslo_service import periodic_task
import terra.context
from terra.common import dependency, rpc
# from terra import exception
# from terra.common import vm_states, vm_operates, vlink_states, vlink_operates, \
#     subnet_states
# from terra.common.api import build_driver_hints, has_quotas, consume_quotas, recycle_quotas
# from terra.common.constants import XLAB_OWNER_TYPE, VM_TYPE_DIC, \
#     EXPT_STATE_DIC, EXPT_OPERATE_DIC, XLAB_EXPT_TYPE, RESOURCE_VM, \
#     RESOURCE_CPU, RESOURCE_MEMORY, RESOURCE_DISK
from oslo_log import log as logging

interval_opts = [
    cfg.IntOpt('container_expt_state_sync_interval',
               default=2,
               help='Interval in seconds for sync experiment state between '
                    'openstack and openlab tables. ')
]

CONF = cfg.CONF
CONF.register_opts(interval_opts)
LOG = logging.getLogger(__name__)


@dependency.require('container_expt_api')
class ExperimentPRCManager(rpc.Manager):
    
    target = messaging.Target(version='1.0')
    
    def __init__(self):
        self.context = terra.context.get_admin_context()
        self._sync_power_pool = eventlet.GreenPool()
        
    def container_expt_create(self, context, topo_dict):
        return self.container_expt_api.create(context, topo_dict)

    def container_expt_delete(self, context, expt_id):
        return self.container_expt_api.expt_delete(context, expt_id)

    def container_expt_detail(self, context, expt_id):
        return self.container_expt_api.expt_detail(context, expt_id)

    def container_expt_restart(self, context, expt_id):
        self.container_expt_api.expt_restart(context, expt_id)

    def container_expt_start(self, context, expt_id):
        self.container_expt_api.expt_start(context, expt_id)

    def container_expt_stop(self, context, expt_id):
        self.container_expt_api.expt_stop(context, expt_id)

    def container_expt_topology(self, context, expt_id):
        return self.container_expt_api.expt_topology(context, expt_id)


# ---------------devices------------------------#

    def container_device_create(self, context, device_value):
        return self.container_expt_api.device_create(context, device_value)

    def container_device_delete(self, context, device_id):
        self.container_expt_api.device_delete(context, device_id)

    def container_device_start(self, context, device_id):
        self.container_expt_api.device_start(context, device_id)

    def container_device_stop(self, context, device_id):
        self.container_expt_api.device_stop(context, device_id)

    @staticmethod
    @periodic_task.periodic_task(spacing=CONF.container_expt_state_sync_interval, run_immediately=True)
    def container_sync_expt_state(obj, context):
        """
        sync experiments states between openlab and openstack.
        """
        if CONF.container_expt_state_sync_interval <= 0:
            return

