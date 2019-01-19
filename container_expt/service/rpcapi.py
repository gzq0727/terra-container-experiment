
from oslo_config import cfg
import oslo_messaging as messaging

from terra.common import dependency
from terra.common import rpc
from terra.context import get_current

CONF = cfg.CONF


@dependency.provider('container_expt_rpcapi')
class ExperimentAPI(object):
    def __init__(self):
        super(ExperimentAPI, self).__init__()
        target = messaging.Target(topic='experiment', version='1.0')
        serializer = messaging.JsonPayloadSerializer()
        self.client = rpc.get_client(target,
                                     version_cap='1.0',
                                     serializer=serializer)

    def expt_create(self, topo_dict):
        cctxt = self.client.prepare(timeout=300)
        return cctxt.call(get_current(), 'container_expt_create',
                          topo_dict=topo_dict)

    def expt_delete(self, expt_id):
        cctxt = self.client.prepare(timeout=300)
        return cctxt.call(get_current(), 'container_expt_delete',
                          expt_id=expt_id)

    def expt_detail(self, expt_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_expt_detail',
                          expt_id=expt_id)

    def expt_restart(self, expt_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_expt_restart',
                          expt_id=expt_id)

    def expt_start(self, expt_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_expt_start',
                          expt_id=expt_id)

    def expt_stop(self, expt_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_expt_stop',
                          expt_id=expt_id)

    def expt_topology(self, expt_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_expt_topology',
                          expt_id=expt_id)

# ----------------device------------------------#

    def device_create(self, device_value):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_device_create',
                          device_value=device_value)

    def device_delete(self, device_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_device_delete',
                          device_id=device_id)

    def device_start(self, device_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_device_start',
                          device_id=device_id)

    def device_stop(self, device_id):
        cctxt = self.client.prepare()
        return cctxt.call(get_current(), 'container_device_stop',
                          device_id=device_id)
