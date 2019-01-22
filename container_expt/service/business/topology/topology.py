import json
from oslo_config import cfg
from oslo_log import log as logging
import eventlet
from terra.vne_experiment.business.topology.vlink import Vlink
from terra.vne_experiment.business.topology.subnet import Subnet
from terra.common import dependency
from terra.common import router_states
from terra.common import vm_states, subnet_states
from terra.common.api import build_driver_hints
from terra.common.constants import XLAB_OWNER_TYPE, EXPT_OPERATE_DIC, \
    VM_TYPE_DIC, PORT_TYPE_DIC
import sys
reload(sys)
sys.setdefaultencoding('utf-8')


interval_opts = [
    cfg.IntOpt('create_vlink_sync_interval',
               default=2,
               help='Interval in seconds for sync device state between '
                    'openstack and openlab tables. '),
]

timeout_opts = [
    cfg.IntOpt("create_vlink_sync_timeout",
               default=120,
               help="Amount of time in seconds an vm can be operate successfully "
                    "before going into ERROR status. "
                    "Set to 0 to disable."),
]

CONF = cfg.CONF
CONF.register_opts(interval_opts)
CONF.register_opts(timeout_opts)
LOG = logging.getLogger(__name__)


@dependency.requires('experiment_api', 'topology_api')
@dependency.requires('vne_experiment_api', 'vm_api')
class Topology(object):

    def __init__(self, context=None, topo_id=None):
        self.context = context
        self.topo_id = topo_id
        self.vlink = Vlink(context=context)
        self.subnet = Subnet(context=context)
        self._sync_power_pool = eventlet.GreenPool()

    def create(self, context, expt_id, expt_name,
               owner_id, owner_name, topo_data):
        # create topo data
        topo_values = dict()
        topo_values['name'] = topo_data['name']
        topo_values['description'] = topo_data['description']
        topo_values['owner_id'] = owner_id
        topo_values['owner_name'] = owner_name
        topo_values['owner_type'] = XLAB_OWNER_TYPE
        topo_id = self._save_topo_data(expt_id, topo_values)
        topo_data['id'] = topo_id

        # create network and subnet data
        network_dict, subnet_dict = self.create_subnets_data(
            expt_name, topo_id, owner_id, owner_name, topo_data['networks'])
        topo_data['os_networks'] = network_dict

        # add extra routers service to connect ext net
        # each subnets provided with one router
        ext_routers = []
        for net in topo_data['networks']:
            for sub in net.get('subnets', []):
                ext_router = {
                    'name': 'route_connect_ext',
                    'attach_ext': True,
                    'attach_subnets': [sub['id']]
                }
                routers = self.create_routers_data(
                    expt_name, topo_id, owner_id, owner_name,
                    [ext_router], subnet_dict)
                routers[0]['attach_ext'] = True
                ext_routers.append(routers[0])

        # add router service data which connect subnets
        routers = self.create_routers_data(
            expt_name, topo_id, owner_id, owner_name,
            topo_data['routers'], subnet_dict)
        topo_data['os_routers'] = routers
        for rt in ext_routers:
            topo_data['os_routers'].insert(0, rt)

        # create devices
        devices = self.create_devices_data(
            expt_name, topo_id, owner_id, owner_name,
            topo_data['devices'], subnet_dict)
        topo_data['os_devices'] = devices

    def _save_topo_data(self, expt_id, topo_values):
        topo_ref = self.topology_api.db_create_topo(topo_values)
        topo_id = topo_ref['id']

        mapping_values = dict()
        mapping_values['expt_id'] = expt_id
        mapping_values['topo_id'] = topo_id
        self.experiment_api.topo_mapping(mapping_values)

        self.topo_id = topo_id
        return topo_id

    def create_subnets_data(self, expt_name, topo_id, owner_id,
                            owner_name, networks):
        subnet_dict = {}
        network_dict = {}
        for net in networks:
            values = dict()
            net_id = net['id']
            values['name'] = 'container_%s_%s_%s_network' % (
                expt_name, str(topo_id), str(net_id))
            if len(values['name'].decode('utf-8')) > 60:
                values['name'] = 'container_%s_%s_network' % (
                    str(topo_id), str(net_id))
            values['topo_id'] = topo_id
            values['owner_id'] = owner_id
            values['owner_name'] = owner_name
            values['owner_type'] = XLAB_OWNER_TYPE
            network_ref = self.topology_api.db_create_network(values)
            net_subs = []
            network_dict[net_id] = {
                'network_id': network_ref['id'],
                'subnets': net_subs
            }

            subnets = net['subnets']
            for sub in subnets:
                values = dict()
                values['alias'] = sub['name']
                sub_name = 'container_%s_%s_subnet_%s' % (
                    expt_name, str(topo_id), str(sub['name']))
                if len(sub_name.decode('utf-8')) > 60:
                    sub_name = sub['name']
                values['name'] = sub_name
                values['network_id'] = network_ref['id']
                values['fixed_ips'] = sub['fixed_ips']
                values['gateway_ip'] = sub['gateway']
                values['enable_dhcp'] = sub['enable_dhcp']
                values['owner_id'] = owner_id
                values['owner_name'] = owner_name
                values['dns_nameservers'] = \
                    CONF.cloudclient_args.dns_nameservers
                other = {'coordinate': {}}
                if 'x' in sub:
                    other['coordinate']['x'] = sub['x']
                if 'y' in sub:
                    other['coordinate']['y'] = sub['y']
                values['other'] = json.dumps(other)
                subnet_ref = self.topology_api.db_create_subnet(values)
                net_subs.append(subnet_ref['id'])
                subnet_dict[sub['id']] = {
                    'subnet_id': subnet_ref['id'],
                    'network_id': network_ref['id'],
                    'routers': []
                }

                values = dict()
                values['topo_id'] = topo_id
                values['cloud_subnet_id'] = subnet_ref['id']
                self.vne_experiment_api.subnet_data_create(values)

        return network_dict, subnet_dict

    def create_routers_data(self, expt_name, topo_id, owner_id,
                            owner_name, routers_data, subnet_dict):
        routers = []
        for router in routers_data:
            device_value = dict()
            device_value['alias'] = router['name']
            device_value['topo_id'] = topo_id
            # device table
            device_value['type'] = 'Router'
            # router table
            router_name = "container_%s_%s_%s" % (
                expt_name, str(topo_id), str(router['name']))
            device_value['name'] = router_name
            device_value['attach_ext'] = router['attach_ext']
            device_value['state'] = router_states.BUILDING
            device_value['owner_id'] = owner_id
            device_value['owner_name'] = owner_name
            device_value['owner_type'] = XLAB_OWNER_TYPE
            other = {'coordinate': {}}
            if 'x' in router:
                other['coordinate']['x'] = router['x']
            if 'y' in router:
                other['coordinate']['y'] = router['y']
            device_value['other'] = json.dumps(other)
            router_ref = self.topology_api.db_create_router(device_value)
            device_id = router_ref['device_id']

            # TODO(zhangyuliang): create router ports
            ports = []
            for idx, subnet_no in enumerate(router['attach_subnets']):
                if subnet_no in subnet_dict:
                    port_value = dict()
                    port_name = "%s_%s_%s_port_%d" % (
                        expt_name, str(topo_id), str(router['name']), idx)
                    port_value['name'] = \
                        port_name.decode('utf8')[0:63].encode('utf8')
                    port_value['device_id'] = device_id
                    port_value['subnet_id'] = subnet_dict.get(
                        subnet_no).get('subnet_id')
                    port_value['device_owner'] = 'network:router_interface'
                    port_ref = self.topology_api.db_create_port(port_value)

                    subnet_routers = subnet_dict[subnet_no]['routers']
                    port_dict = {
                        'port_id': port_ref['id'],
                        'subnet_id': subnet_dict[subnet_no]['subnet_id'],
                    }
                    if subnet_routers and not router['attach_ext']:
                        port_dict['need_create_port_first'] = True
                    else:
                        subnet_routers.append(device_id)
                        port_dict['need_create_port_first'] = False
                    ports.append(port_dict)

            routers.append({'router_id': router_ref['id'],
                            'device_id': device_id,
                            'ports': ports})
        return routers

    def create_devices_data(self, expt_name, topo_id, owner_id,
                            owner_name, devices_data, subnet_dict=None):
        devices = []
        for device_data in devices_data:
            device_data['alias'] = device_data['name']
            device_data['owner_id'] = owner_id
            device_data['owner_name'] = owner_name
            device_data['owner_type'] = XLAB_OWNER_TYPE
            device_data['username'] = device_data.get('username', 'root')
            device_data['password'] = device_data.get('password', '123')
            device_data['type'] = int(device_data.get('type', 0))
            device_data['device_type'] = 'VM'
            device_data['topo_id'] = topo_id
            device_data['description'] = device_data.get(
                'description', '%s_description' % device_data['name'])
            device_data['name'] = 'container_%s_%s_%s' % (
                expt_name, str(topo_id), device_data['name'])
            other = {
                'vtype': device_data.get('vtype', 0),
                'coordinate': {}
            }
            if 'type' in device_data:
                other['type'] = device_data['type']
            if 'x' in device_data:
                other['coordinate']['x'] = device_data['x']
            if 'y' in device_data:
                other['coordinate']['y'] = device_data['y']
            device_data['other'] = json.dumps(other)
            device_ref = self.vm_api.create_db_vm(device_data)
            device_id = device_ref['device_id']
            device_data['no'] = device_ref['id']

            ports = []
            for idx, subnet_no in enumerate(device_data['attach_subnets']):
                if subnet_no in subnet_dict:
                    port_value = dict()
                    port_value['name'] = "%s_port_%d" % (
                        device_data['name'], idx)
                    port_value['name'] = port_value['name'][:64]
                    port_value['device_id'] = device_id
                    port_value['subnet_id'] = subnet_dict.get(
                        subnet_no).get('subnet_id')
                    port_value['device_owner'] = 'compute:nova'
                    if device_data.get('type') == VM_TYPE_DIC['vcontroller']:
                        other = {'type': PORT_TYPE_DIC['manager']}
                    else:
                        other = {'type': PORT_TYPE_DIC['data']}
                    port_value['other'] = json.dumps(other)
                    allocate_ip = device_data['ip_address']
                    port_ref = self.topology_api.db_create_port(port_value, allocate_ip=allocate_ip)
                    ports.append({
                        'port_id': port_ref['id'],
                        'subnet_id': subnet_dict[subnet_no]['subnet_id'],
                        'network_id': subnet_dict[subnet_no]['network_id'],
                    })

            devices.append({'device_id': device_id, 'ports': ports})
        return devices

    def os_create(self, context, topo_dic, expt_id, expt_name):
        try:
            # create os networks
            network_mapper = {}
            subnet_mapper = {}
            err_dic = {}
            network_dict = topo_dic['os_networks']
            for net_no, network in network_dict.items():
                db_net_id = network['network_id']
                try:
                    os_net = self.topology_api.os_create_network(
                        context, db_net_id, create_subnet=True)
                    network_mapper[db_net_id] = os_net['os_network_uuid']
                    db_subnets = network['subnets']
                    for sub_id in db_subnets:
                        sub_ref = self.topology_api.db_get_subnet(sub_id)
                        subnet_mapper[sub_id] = \
                            sub_ref['os_subnet']['os_subnet_uuid']
                except Exception as ex:
                    LOG.exception(ex)
                    if self.is_expt_deleting(expt_id):
                        return
                    err_dic[db_net_id] = str(ex)
                    db_subnets = network['subnets']
                    for sub_id in db_subnets:
                        self.topology_api.db_update_subnet(
                            sub_id, {'state': subnet_states.ERROR})
            # net_ids = [net['network_id'] for net in network_dict.values()]
            # try:
            #     db_os_networks, db_os_subnets = \
            #         self.topology_api.os_mult_create_network(
            #             None, net_ids, create_subnet=True)
            #     for os_net in db_os_networks:
            #         network_mapper[os_net['network_id']] = \
            #             os_net['os_network_uuid']
            #     for os_sub in db_os_subnets:
            #         subnet_mapper[os_sub['subnet_id']] = \
            #             os_sub['os_subnet_uuid']
            # except Exception as ex:
            #     LOG.exception(ex)

            # create os routes
            try:
                external_networks = []
                routers = topo_dic['os_routers']
                for router_dict in routers:
                    self.topology_api.os_create_router(
                        context, router_dict['router_id']
                    )
                    ports = router_dict['ports']
                    for port_dict in ports:
                        if port_dict.get('need_create_port_first', False):
                            os_port = self.topology_api.os_create_port(
                                context, port_dict['port_id']
                            )
                            router_interface = dict(
                                os_port_uuid=os_port['os_port_uuid'],
                                os_subnet_uuid=subnet_mapper[
                                    port_dict['subnet_id']]
                            )
                            self.topology_api.os_add_router_interface(
                                context, router_dict['router_id'],
                                router_interface
                            )

                            self.topology_api.db_update_os_port(
                                os_port['port_id'], {'attach_device': True})
                        else:
                            router_interface = dict(
                                os_subnet_uuid=subnet_mapper[
                                    port_dict['subnet_id']]
                            )
                            os_port_id = self.topology_api. \
                                os_add_router_interface(
                                context, router_dict['router_id'],
                                router_interface)

                            self.topology_api.db_create_os_port({
                                'port_id': port_dict['port_id'],
                                'os_port_uuid': os_port_id,
                                'attach_device': True
                            })

                        if router_dict.get('attach_ext', False):
                            if not external_networks:
                                hints = build_driver_hints(
                                    {'type': 'External',
                                     'owner_type': XLAB_OWNER_TYPE,
                                     'from_os': True})
                                external_networks = self.topology_api. \
                                    db_list_networks(hints=hints)
                            for external_netowrk in external_networks:
                                self.topology_api.add_router_gateway(
                                    context, router_dict['router_id'],
                                    {'ext_net_id': external_netowrk['id']})
                # update route hosts
                subnets = self.vne_experiment_api. \
                    topo_get_subnets(topo_dic['id'])
                for subnet_id, subnet in subnets.items():
                    update_subnet = self.vne_experiment_api. \
                        subnet_update_host_routes(subnet_id)
                    if update_subnet:
                        try:
                            host_routes = update_subnet.host_routes
                            LOG.info("subnet host route: %s, type:%s" %
                                     (host_routes, type(host_routes)))
                            self.topology_api.os_update_subnet(
                                context, update_subnet.id,
                                {'host_routes': host_routes})
                        except Exception as ex:
                            LOG.exception(ex)
            except Exception as ex:
                LOG.exception(ex)

            # create os devices
            devices_list = topo_dic['os_devices']
            for device in devices_list:
                err_msg = ''
                nics = []
                ports = device['ports']
                for port in ports:
                    network_id = port['network_id']
                    if network_id in err_dic:
                        err_msg = err_dic[network_id]
                        continue
                    os_port = self.topology_api.os_create_port(
                        context, port['port_id']
                    )
                    nics.append({
                        'network_uuid': network_mapper.get(network_id),
                        'port_uuid': os_port['os_port_uuid']
                    })

                if err_msg:
                    if self.is_expt_deleting(expt_id):
                        return
                    self._set_device_error(device['device_id'], err_msg)
                    continue
                try:
                    LOG.info('container expt create os vm. device_id: %s, nics: %s'
                             % (device['device_id'], nics))
                    self.vm_api.create_os_vm(
                        context, device['device_id'],
                        nics, get_os_image=True
                    )
                except Exception as ex:
                    if self.is_expt_deleting(expt_id):
                        return
                    self._set_device_error(device['device_id'], str(ex))

            # add router service to provide this network
            # with access external network capability
            # ext_router = topo_dic['ext_router']
            # router_dic = {
            #     'id': ext_router['device_id'],
            #     'is_router_service': True,
            #     'is_ext_router': True
            # }
            # self.vne_experiment_api.create_vrouter(context, router_dic)

            # self.vne_experiment_api.expt_add_floating_ip(context, expt_id)
        except Exception as ex:
            LOG.exception(str(ex))
            raise

    def is_expt_deleting(self, expt_id):
        expt = self.experiment_api.get(expt_id)
        if expt and expt['operate'] == EXPT_OPERATE_DIC['deleting']:
            return True
        return False

    def _set_device_error(self, device_id, err_msg):
        device_ref = self.topology_api.get_device_detail(device_id)
        self.vne_experiment_api.update_device_state(
            device_ref['obj_id'], vm_states.ERROR, None)
        self.vne_experiment_api.device_operate_failed(
            device_ref['obj_id'], err_msg)
