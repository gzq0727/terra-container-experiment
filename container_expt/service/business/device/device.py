import datetime
import eventlet
import json
from oslo_log import log as logging
from oslo_utils import timeutils
from terra import exception
from terra import utils
from terra.common import dependency
# from terra.common.api import has_quota, consume_quotas, is_superuser, \
#     get_saturn_image, add_image_ref, del_image_ref, add_ne_stats, del_ne_stats
from terra.common import port_states, vm_states, vm_operates
from terra.common.constants import VM_TYPE_DIC, RESOURCE_VM, \
    RESOURCE_CPU, RESOURCE_MEMORY, RESOURCE_DISK, XLAB_OWNER_TYPE, \
    DEVICE_OPERATE_TIMEOUT, PORT_TYPE_DIC, DEVICE_LOCK_NAME, PORT_FIP_LOCK_NAME
from terra.common.api import has_quotas, consume_quotas, recycle_quotas, \
    build_driver_hints, get_external_lock_path
from terra.vne_experiment.business.device.vhost import VHost
from terra.vne_experiment.business.device.vcontroller import VController
from terra.vne_experiment.business.device.device import Device as VneDevice

LOG = logging.getLogger(__name__)


@dependency.requires('vne_experiment_api',
                     'topology_api',
                     'vm_api')


class Device(object):

    def __init__(self, context=None, id=None):
        self.context = context
        self._device_id = id
        self.__sync_power_pool = eventlet.GreenPool()

    def create(self, values):
        try:
            device_id = None

            # check quota
            resource = dict()
            resource[RESOURCE_VM] = 1
            resource[RESOURCE_CPU] = int(values['cpu'])
            resource[RESOURCE_MEMORY] = int(values['ram'])
            resource[RESOURCE_DISK] = int(values['disk'])
            owner_id = values['owner_id']
            has_quotas(owner_id, resource)
            connected_subnet = values.get('connected_subnet_id')
            if not connected_subnet:
                pass

            # save device
            device_type = values.get('type')
            port_no = 1
            port = {'subnet_id': connected_subnet}
            if device_type == VM_TYPE_DIC['vcontroller']:
                port['type'] = PORT_TYPE_DIC['manager']
                values['manage_ports'] = {port_no: port}
            else:
                port['type'] = PORT_TYPE_DIC['data']
                values['data_ports'] = {port_no: port}
            consume_quotas(owner_id, resource)
            try:
                device_id = self.vne_experiment_api.create_device_data(
                    self.context, values['expt_name'], values['topo_id'], values)
            except exception.DeviceCreatedFailed:
                recycle_quotas(owner_id, resource)
                raise
            values['id'] = device_id

            # create device in backend async
            self.__sync_power_pool.spawn_n(self.create_backend, self.context, values)
            return {'id': device_id}

        except Exception as ex:
            if device_id:
                device = self.topology_api.get_device_detail(device_id)
                if device:
                    self.vne_experiment_api.\
                        device_operate_failed_and_change_expt_state(
                            device['obj_id'], vm_states.ERROR, None, str(ex))
            raise

    def create_backend(self, context, device):
        device_id = device['id']
        # get os network uuid
        connected_subnet = device.get('connected_subnet_id')
        subnet_ref = self.topology_api.db_get_subnet(connected_subnet)
        network_ref = self.topology_api.\
            get_network_detail(subnet_ref['network_id'])
        os_network_uuid = network_ref['os_network']['os_network_uuid']
        nics = [{'network_uuid': os_network_uuid}]

        try:
            self.vm_api.create_os_vm(
                context, device_id, nics, get_os_image=True)
        except Exception as ex:
            device_ref = self.topology_api.get_device_detail(device_id)
            if device_ref:
                self.vne_experiment_api. \
                    device_operate_failed_and_change_expt_state(
                        device_ref['obj_id'], vm_states.ERROR, None, str(ex))

    def delete(self, need_update_operate=True):
        device = self.topology_api.get_device_detail(self._device_id)
        vm_id = device['obj_id']
        if not vm_id:
            return
        try:
            device_lock_name = DEVICE_LOCK_NAME + str(self._device_id)

            @utils.synchronized(device_lock_name,
                                external=True,
                                lock_path=get_external_lock_path())
            def _delete_os_vm():
                LOG.info('***container delete device. vm_id: %s' % vm_id)
                if need_update_operate:
                    self.vm_api.db_update_vm(
                        None, vm_id, {'operate': vm_operates.DELETING})

                for port in device['ports']:
                    try:
                        port_id = port['id']
                        port_fip_lock_name = \
                            PORT_FIP_LOCK_NAME+str(port_id)

                        @utils.synchronized(port_fip_lock_name,
                                            external=True,
                                            lock_path=get_external_lock_path())
                        def _del_port_fip():
                            if need_update_operate:
                                self.topology_api.db_update_port(
                                    port_id, {'state': port_states.DELETING})
                            LOG.info("delete floating ip addr, port_id:%s" %
                                     port_id)
                            self.topology_api.del_port_floatingip(
                                self.context, port_id)

                        _del_port_fip()
                        self.topology_api.os_delete_port(self.context, port_id)
                    except exception.PortNotFound as e:
                        LOG.exception(e)
                    except Exception as e:
                        LOG.exception(e)

                cloud_os_vm = self.vm_api.get_os_vm_by_vmid(vm_id)
                if cloud_os_vm:
                    self.vm_api.delete_os_vm(vm_id)
                    _updates = {'operate': vm_operates.DELETING}
                    self.vm_api.update_os_vm(cloud_os_vm['id'], _updates)

            _delete_os_vm()
        except Exception as ex:
            LOG.exception(ex)
            self.vne_experiment_api.\
                device_operate_failed_and_change_expt_state(
                    vm_id, vm_states.ERROR, None, str(ex))
            raise
