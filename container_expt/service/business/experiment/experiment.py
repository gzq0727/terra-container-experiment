import time
import datetime
import eventlet
import json
from oslo_log import log as logging
from oslo_utils import timeutils
from terra import utils
from terra.common import dependency
from terra.i18n import _
from container_expt.service.business.topology import topology
from ..device.device import Device
from terra import exception
from terra.common import vlink_states, port_states, vm_states, vm_operates, \
    subnet_states, network_states, router_states
from terra.common.constants import EXPT_OPERATE_DIC, EXPT_STATE_DIC, \
    RESOURCE_EXPERIMENT, RESOURCE_VM, RESOURCE_CPU, RESOURCE_MEMORY, \
    RESOURCE_DISK, VM_TYPE_DIC, EXPT_OPERATE_TIMEOUT, XLAB_OWNER_TYPE, \
    RESOURCE_ROUTER, RESOURCE_SUBNET
from terra.common.api import has_quotas, consume_quotas, recycle_quotas
from terra.common.api import build_driver_hints, get_external_lock_path
import re


LOG = logging.getLogger(__name__)


@dependency.requires('vne_experiment_api',
                     'experiment_api',
                     'vm_api',
                     'topology_api',
                     'vne_expt_rpcapi')
class Experiment(object):

    def __init__(self, context=None, expt_id=None, driver=None):
        self.expt_id = expt_id
        self.topo = topology.Topology(context=context)
        self.context = context
        self.driver = driver
        self._sync_power_pool = eventlet.GreenPool()

    def _recycle_expt_quota(self, expt, devices, cloud_subnets,
                            need_rollback=False):
        expt_owner = expt['owner_id']
        user_res = dict()
        user_res[expt_owner] = {
            RESOURCE_EXPERIMENT: 0,
            RESOURCE_VM: 0,
            RESOURCE_CPU: 0,
            RESOURCE_MEMORY: 0,
            RESOURCE_DISK: 0
            # RESOURCE_ROUTER: 0,
            # RESOURCE_SUBNET: 0
        }

        if not expt['has_recycle']:
            user_res[expt_owner][RESOURCE_EXPERIMENT] = 1

        for device in devices:
            owner_id = device['owner_id']
            if not user_res.has_key(owner_id):
                user_res[owner_id] = {
                    RESOURCE_VM: 0,
                    RESOURCE_CPU: 0,
                    RESOURCE_MEMORY: 0,
                    RESOURCE_DISK: 0
                    # RESOURCE_ROUTER: 0
                }
            if device['has_recycle']:
                continue
            if device['type'] == VM_TYPE_DIC['vrouter'] \
                    and device['is_service']:
                continue
            # if device['type'] == VM_TYPE_DIC['vrouter'] and \
            #         device['is_service']:
            #     if device['alias'] == 'route_connect_ext':
            #         continue  # TODO:liuzhaol
            #     user_res[owner_id][RESOURCE_ROUTER] += 1
            #     continue

            user_res[owner_id][RESOURCE_VM] += 1
            user_res[owner_id][RESOURCE_CPU] += int(device['cpu'])
            user_res[owner_id][RESOURCE_MEMORY] += int(device['ram'])
            user_res[owner_id][RESOURCE_DISK] += int(device['disk'])

        # for subnet in cloud_subnets:
        #     owner_id = subnet['owner_id']
        #     if not user_res.has_key(owner_id):
        #         user_res[owner_id] = {RESOURCE_SUBNET: 0}
        #     else:
        #         user_res[owner_id].setdefault(RESOURCE_SUBNET, 0)
        #     if subnet['has_recycle']:
        #         continue
        #     user_res[owner_id][RESOURCE_SUBNET] += 1

        expt_name = expt['name']
        for owner_id in user_res:
            if need_rollback:
                LOG.info('container experiment delete.name:%s consume quotas:%s'
                         % (expt_name, user_res))
                consume_quotas(owner_id, user_res[owner_id])
            else:
                LOG.info('container experiment delete.name:%s recycle quotas:%s'
                         % (expt_name, user_res))
                recycle_quotas(owner_id, user_res[owner_id])

    def create(self, values):
        owner_id = values['owner_id']
        owner_name = values['owner_name']
        expt_name = values['name']
        has_consume_quotas = False
        expt_id = None
        try:
            topos_dic = values['topos']
            resources = self._get_expt_resources(topos_dic)
            if resources[RESOURCE_SUBNET] > 5:
                raise exception.SubnetLimitExceeded()
            resources.pop(RESOURCE_SUBNET)
            has_quotas(owner_id, resources)
            LOG.info('***experiment create.name:%s consume quotas:%s' %
                     (expt_name, resources))
            consume_quotas(owner_id, resources)
            has_consume_quotas = True

            # sync external network
            self._sync_ext_network(owner_id, owner_name, XLAB_OWNER_TYPE)

            # create record in the terra experiment database
            expt_ref = self._create_experiment_data(values)

            expt_id = expt_ref['id']
            for topo_dic in topos_dic:
                # create record in the terra topology database
                self.topo.create(self.context, expt_id, expt_name,
                                 owner_id, owner_name, topo_dic)

            for topo_dic in topos_dic:
                self._sync_power_pool.spawn_n(self.topo.os_create, self.context,
                                              topo_dic, expt_id, expt_name)

            return expt_ref
        # except (exception.ExperimentExist, exception.CreateExperimentFailed):
        #     if resources:
        #         LOG.info('***experiment create.name:%s recycle quotas:%s' %
        #                  (expt_name, resources))
        #         recycle_quotas(owner_id, resources)
        #     raise
        except Exception as ex:
            LOG.exception(ex)
            if resources and has_consume_quotas:
                LOG.info('***experiment create.name:%s recycle quotas:%s' %
                         (expt_name, resources))
                if expt_id:
                    self.experiment_api.update_experiment(
                        expt_id, {'has_recycle': True})
                    devices = self.experiment_api.devices_get(expt_id)
                    vm_ids = [device['cloud_vm_id']
                              for device in devices
                              if not device['is_service']]
                    self.vm_api.db_vm_update_recycle_state(vm_ids, True)
                try:
                    recycle_quotas(owner_id, resources)
                except Exception as ex:
                    LOG.exception(ex)
                    if expt_id:
                        self.experiment_api.update_experiment(
                            expt_id, {'has_recycle': False})
                        self.vm_api.db_vm_update_recycle_state(vm_ids, False)
            raise

    def _get_expt_resources(self, topos_data):
        _vm_count = 0
        _vm_cpu = 0
        _vm_ram = 0
        _vm_disk = 0
        _subnet = 0
        # _router = 1  # will add an extra router service to connect ext net
        _router = 0
        for topo_data in topos_data:
            vms = topo_data['devices']
            # _router += len(topo_data['routers'])
            for vm in vms:
                _vm_count += 1
                _vm_cpu += int(vm['cpu'])
                _vm_ram += int(vm['ram'])
                _vm_disk += int(vm['disk'])
            for network in topo_data['networks']:
                _subnet += len(network['subnets'])
        return {RESOURCE_EXPERIMENT: 1,
                RESOURCE_SUBNET: _subnet,
                # RESOURCE_ROUTER: _router,
                RESOURCE_VM: _vm_count, RESOURCE_CPU: _vm_cpu,
                RESOURCE_MEMORY: _vm_ram, RESOURCE_DISK: _vm_disk}

    def _create_experiment_data(self, values):
        expt_values = {}
        expt_values['name'] = values['name']
        expt_values['description'] = values['description']
        expt_values['type'] = 'Container'
        expt_values['state'] = EXPT_STATE_DIC['nostate']
        expt_values['operate'] = EXPT_OPERATE_DIC['building']
        expt_values['owner_id'] = values['owner_id']
        expt_values['owner_name'] = values['owner_name']
        expt_values['platform'] = values.get('platform', 'SDN')  # this is what?
        expt_values['expired_at'] = \
            timeutils.utcnow() + datetime.timedelta(hours=values['life'])
        expt_values['operate_expired_at'] = timeutils.utcnow() + \
                                            datetime.timedelta(minutes=EXPT_OPERATE_TIMEOUT)
        return self.experiment_api.create(expt_values)

    def _sync_ext_network(self, owner_id, owner_name, owner_type):
        hints = build_driver_hints({'type': 'External',
                                    'owner_type': XLAB_OWNER_TYPE,
                                    'from_os': True})
        external_networks = self.topology_api.db_list_networks(hints=hints)
        if len(external_networks) == 0:
            topo_values = {}
            topo_values['name'] = 'sync external network'
            topo_values['description'] = 'sync external network'
            topo_values['owner_id'] = owner_id
            topo_values['owner_name'] = owner_name
            topo_values['owner_type'] = owner_type
            topo_ref = self.topology_api.db_create_topo(topo_values)
            post_values = {'topo_id': topo_ref['id'], 'owner_id': owner_id,
                           'owner_name': owner_name}
            try:
                self.topology_api.sync_external_network(context=None,
                                                        values=post_values)
            except:
                import traceback
                traceback.print_exc()

    def detail(self):
        ret = {}
        try:
            expt = self.experiment_api.get(self.expt_id)
            if not expt:
                raise exception.ExperimentNotFound(expt_id=self.expt_id)
            copy_keys = {'id', 'name', 'description', 'type', 'state',
                         'operate', 'owner_id', 'owner_name', 'created_at',
                         'expired_at', 'is_public', 'notes'}
            for key in copy_keys:
                ret[key] = expt[key]
        except:
            raise
        ret['topology'] = self.topology_data()
        return ret

    def topology_data(self):
        topology_data = []
        allowed_types = ['router', 'vm', 'host']
        available_devices = self.get_devices()
        available_device_ids = [int(x['id']) for x in available_devices]
        try:
            topos = self.get_topos()
            for topo in topos:
                topo_ref = self.topology_api.get_topo_detail(topo['id'])
                devices = topo_ref['devices']
                routers = []
                hosts = []
                for network in topo_ref['networks']:
                    subnets = network['subnets']
                    for subnet in subnets:
                        other = json.loads(subnet.pop('other', json.dumps({})))
                        if 'coordinate' in other:
                            coordinate = other.get('coordinate', {})
                            subnet['x'] = coordinate.get('x', 0)
                            subnet['y'] = coordinate.get('y', 0)

                for device in devices:
                    device_type = device['type'].lower()
                    if device_type not in allowed_types:
                        continue

                    # get the subnets device attaches
                    ports = device.pop('ports', [])
                    device['attach_subnets'] = []
                    for port in ports:
                        port_subnets = port.get('subnets', [])
                        for port_subnet in port_subnets:
                            device['attach_subnets'].append(port_subnet['id'])
                        device['ipaddrs'] = port.get('ipaddrs', '127.0.0.1')
                        # if port.get('device_owner') == 'network:router_gateway':
                        #     device['attach_ext'] = True
                        # else:
                        #     device['attach_ext'] = False

                    # get the coordinates
                    other = json.loads(device.pop('other', json.dumps({})))
                    if 'coordinate' in other:
                        coordinate = other.get('coordinate', {})
                        device['x'] = coordinate.get('x', 0)
                        device['y'] = coordinate.get('y', 0)
                    device['vtype'] = other.get('vtype', 0)
                    if device['type'].lower() == 'router':
                        routers.append(device)
                    elif device['type'].lower() == 'vm':
                        if int(device['id']) in available_device_ids:
                            hosts.append(device)
                topo_ref['devices'] = hosts
                topo_ref['routers'] = routers
                topology_data.append(topo_ref)
        except:
            raise

        return topology_data

    def topology(self):
        ret = dict(topos=[])
        try:
            topos = self.get_topos()
            for topo in topos:
                topo_ref = self.topology_api.get_topo_detail(topo['id'])
                ret['topos'].append(topo_ref)
            ret['id'] = self.expt_id
        except:
            raise

        return ret

    def get_topos(self):
        topos = self.experiment_api.get_topos(self.expt_id)
        return topos

    def delete(self):
        try:
            # get all device in expt
            db_devices = self.get_devices()
            devices = []
            vm_ids = []
            for d in db_devices:
                devices.append(d)
                if d['is_service']:
                    continue
                vm_ids.append(d['cloud_vm_id'])

            # update network and subnet state to deleting
            topo_id = self.get_topos()[0]['id']
            cloud_subnets = []
            hints = build_driver_hints({'topo_id': topo_id})
            networks = self.topology_api.db_list_networks(hints=hints)
            for network in networks:
                hints = build_driver_hints({'network_id': network['id']})
                subnets = self.topology_api.db_list_subnets(hints=hints)
                for subnet in subnets:
                    cloud_subnets.append(subnet)

            @utils.synchronized(self.expt_id,
                                external=True,
                                lock_path=get_external_lock_path())
            def do_recycle_expt(devices, vm_ids, cloud_subnets):
                expt = self.experiment_api.get(self.expt_id)
                # recycle all resources about expt
                if expt['operate'] != EXPT_OPERATE_DIC['deleting']:
                    self.experiment_api.update_experiment(
                        self.expt_id, {'has_recycle': True})
                    self.vm_api.db_vm_update_recycle_state(vm_ids, True)
                    result = False
                    retry_count = 3
                    while retry_count > 0 and not result:
                        try:
                            self._recycle_expt_quota(expt, devices,
                                                     cloud_subnets)
                            result = True
                        except Exception as ex:
                            LOG.exception(ex)
                            retry_count -= 1
                            if retry_count == 0:
                                self.vm_api.db_vm_update_recycle_state(
                                    vm_ids, False)
                                self.experiment_api.update_experiment(
                                    self.expt_id, {'has_recycle': False})
                                raise
                            eventlet.sleep(1)
                    self.update_state(None, EXPT_OPERATE_DIC['deleting'])

            do_recycle_expt(devices, vm_ids, cloud_subnets)

            # update device state to deleting
            # for device in devices:
            #     if device['is_service']:
            #         continue
            #     try:
            #         LOG.info('***container expt delete device. vm_id: %s' %
            #                  device['cloud_vm_id'])
            #         self.vm_api.db_update_vm(
            #             None, device['cloud_vm_id'],
            #             {'operate': vm_operates.DELETING})
            #     except Exception as ex:
            #         LOG.exception(ex)
            #         pass

            # update router service state to deleting
            routers = self.topology_api.db_get_routers_in_topo(topo_id)
            for router in routers:
                try:
                    self.topology_api.db_update_router(
                        router['id'], {'state': router_states.DELETING})
                except Exception as ex:
                    LOG.exception(ex)
                    pass

            # update network and subnet state to deleting
            for network in networks:
                network_id = network['id']
                try:
                    self.topology_api.db_update_network(
                        network_id, {'state': network_states.DELETING})
                except Exception as ex:
                    LOG.exception(ex)
                    pass
            for subnet in cloud_subnets:
                try:
                    self.topology_api.db_update_subnet(
                        subnet['id'], {'state': subnet_states.DELETING})
                except Exception as ex:
                    LOG.exception(ex)
                    pass

            # update port state to deleting
            ports = self.experiment_api.ports_get(self.expt_id)
            for port in ports:
                try:
                    self.topology_api.db_update_port(
                        port['id'], {'state': port_states.DELETING})
                except Exception:
                    pass

            # self.update_state(None, EXPT_OPERATE_DIC['deleting'])

            # delete backent devices, ports, networks and subnets async
            self._sync_power_pool.spawn_n(self._delete_async, self.context,
                                          self.expt_id, devices, routers,
                                          networks, cloud_subnets)
        except Exception as ex:
            LOG.exception('delete experiment %s failed.' % self.expt_id)
            self.experiment_api.expt_operate_failed(self.expt_id, str(ex))
            import traceback
            traceback.print_exc()

    def _delete_async(self, context, expt_id, devices,
                      routers, networks, subnets):
        LOG.info("delete experiment")
        LOG.info("devices:%s \n routers:%s \n networks:%s \n subnets:%s" % (
            devices, routers, networks, subnets))

        try:
            expt_error_msg = ''
            high_priority_error_msg = ''

            # delete devices
            for device in devices:
                if device['is_service']:
                    continue
                # if device['uuid']:
                #     try:
                #         vm_id = device['cloud_vm_id']
                #         self.vm_api.delete_os_vm(vm_id)
                #         cloud_os_vm = self.vm_api.get_os_vm_by_vmid(vm_id)
                #         if cloud_os_vm:
                #             updates = {'operate': vm_operates.DELETING}
                #             self.vm_api.update_os_vm(cloud_os_vm['id'], updates)
                #     except Exception as ex:
                #         LOG.exception(ex)
                #         if not expt_error_msg:
                #             expt_error_msg = str(ex)
                try:
                    device_cls = Device(context=context, id=device['id'])
                    device_cls.delete()
                except exception.ConnectionOSError as ex:
                    high_priority_error_msg = str(ex)
                except Exception as ex:
                    LOG.exception(ex)
                    if not expt_error_msg:
                        expt_error_msg = str(ex)

            # remove interface router and delete router.
            for rt in routers:
                try:
                    self.topology_api.os_delete_router(None, rt['id'])
                except Exception as ex:
                    LOG.exception(ex)
                    if not expt_error_msg:
                        expt_error_msg = str(ex)

            # # delete subnet and attach ports
            # for subnet in subnets:
            #     try:
            #         self.topology_api.os_delete_subnet(context, subnet['id'])
            #     except exception.SubnetNotFound:
            #         pass
            #     except Exception as ex:
            #         LOG.exception(ex)
            #         if not expt_error_msg:
            #             expt_error_msg = str(ex)
            #
            # # delete network
            # for network in networks:
            #     try:
            #         self.topology_api.os_delete_network(
            #             context, network['id'])
            #     except exception.NetworkNotFound:
            #         pass
            #     except Exception as ex:
            #         LOG.exception(ex)
            #         if not expt_error_msg:
            #             expt_error_msg = str(ex)

            if high_priority_error_msg:
                expt_error_msg = high_priority_error_msg
            if expt_error_msg:
                self.experiment_api.expt_operate_failed(
                    expt_id, expt_error_msg)
            # self.experiment_api.delete(self.expt_id)
            # self.experiment_api.update_experiment(
            #     self.expt_id, {'has_recycle': True})
        except Exception as ex:
            self.experiment_api.expt_operate_failed(expt_id, str(ex))
            LOG.exception('delete experiment %s failed. msg: %s' %
                          (expt_id, str(ex)))

    def _operate_failed(self, model_obj, obj_id, err_msg):
        model_obj.id = obj_id
        model_obj.operate_failed(err_msg)

    def update_state(self, state, operate):
        self.experiment_api.update_state(self.expt_id, state, operate)

    def operate_failed(self, failure_info):
        return self.object_expt.operate_failed(failure_info)

    def get_devices(self):
        return self.experiment_api.devices_get(self.expt_id)

    def update_state_by_device(self, device_id, change_to_error):
        try:
            expt = self.experiment_api.get_by_device(device_id)
            if not expt:
                return
            expt_id = expt['id']
            if change_to_error:
                # update expt error when has vm error in itself
                if expt['state'] != EXPT_STATE_DIC['failed']:
                    self.experiment_api.update_state(
                        expt_id, EXPT_STATE_DIC['failed'], None)
            else:
                # when vm change from error to other state
                # check all vms in expt
                if expt['state'] == EXPT_STATE_DIC['failed'] \
                        and expt['operate'] != EXPT_OPERATE_DIC['deleting']:
                    devices = self.experiment_api.devices_get(expt_id)
                    has_error_device = False
                    expt_state = EXPT_STATE_DIC['stop']
                    for device in devices:
                        if device['state'] == vm_states.ERROR:
                            has_error_device = True
                            break
                        elif device['state'] == vm_states.ACTIVE:
                            expt_state = EXPT_STATE_DIC['running']
                    if not has_error_device:
                        self.experiment_api.update_state(expt_state, None)
        except:
            pass

    def get_experiment_vms_state(self, context, expt_id):
        pass

    def get_ports(self, context):
        return self.object_expt.get_ports(context)

    def restart(self):
        # self.experiment_api.update_state(self.expt_id,
        #                                  None, EXPT_OPERATE_DIC['restarting'])
        #
        # devices = self.get_devices()
        # for device in devices:
        #     if device['is_service']:
        #         continue  #
        #
        #     self.vne_experiment_api.update_device_state(
        #         device['cloud_vm_id'], None, vm_operates.REBOOTING)
        #     try:
        #         if device['state'] == vm_states.STOPPED:
        #             self.vm_api.start_os_vm(device['uuid'])
        #         else:
        #             self.vm_api.reboot_os_vm(device['uuid'])
        #     except Exception as ex:
        #         LOG.exception(str(ex))
        #         pass
        #     self.vm_api.update_os_vm(device['cloud_os_vm_id'],
        #                              {'operate': vm_operates.REBOOTING})
        self.vne_experiment_api.expt_restart(self.expt_id)

    def start(self):
        # self.experiment_api.update_state(self.expt_id,
        #                                  None, EXPT_OPERATE_DIC['starting'])
        #
        # devices = self.get_devices()
        # for device in devices:
        #     if device['is_service']:
        #         continue  #
        #
        #     self.vne_experiment_api.update_device_state(
        #         device['cloud_vm_id'], None, vm_operates.POWERING_ON)
        #     try:
        #         self.vm_api.start_os_vm(device['uuid'])
        #     except Exception as ex:
        #         LOG.exception(str(ex))
        #         pass
        #     self.vm_api.update_os_vm(device['cloud_os_vm_id'],
        #                              {'operate': vm_operates.POWERING_ON})
        self.vne_experiment_api.expt_start(self.expt_id)

    def stop(self):
        # self.experiment_api.update_state(self.expt_id,
        #                                  None, EXPT_OPERATE_DIC['stopping'])
        # devices = self.get_devices()
        # for device in devices:
        #     if device['is_service']:
        #         continue  #
        #
        #     self.vne_experiment_api.update_device_state(
        #         device['cloud_vm_id'], None, vm_operates.POWERING_OFF)
        #     try:
        #         self.vm_api.stop_os_vm(device['uuid'])
        #     except Exception as ex:
        #         LOG.exception(str(ex))
        #         pass
        #     self.vm_api.update_os_vm(device['cloud_os_vm_id'],
        #                              {'operate': vm_operates.POWERING_OFF})
        self.vne_experiment_api.expt_stop(self.expt_id)
