import json
from oslo_utils import timeutils
from terra.common import dependency
from terra.common.constants import VM_TYPE_DIC
from terra import wsgi
from terra import exception
from terra.i18n import _
from webob import exc
from terra.common.constants import VM_TYPE_DIC


@dependency.requires("container_expt_api",
                     "container_expt_rpcapi")
class Experiment(wsgi.V1Controller):

    collection_name = "experiment"
    member_name = "experiment"

    def __init__(self):
        super(Experiment, self).__init__()
        self.get_member_from_driver = self.container_expt_api.get

    def create(self, context, experiment):
        """

                experiment: a dict describing the experiment instance
                   {
                       "name": "tenant experiment name",
                       "description": "tenant experiment desc",
                       "owner_id": "xxxxfafsfff",
                       "owner_name": "super",
                       "ext_net_id": "uuid",
                       "life": 200, # (in hours)
                       "topos": [{
                           "name": "tenant topo name",
                           "description": "tenant topo desc",
                           "networks": [{
                               "id": 1,
                               "subnets": [{
                                   "id": 1,
                                   "name": "sub1",
                                   "fixed_ips": "10.0.0.0/24",
                                   "gateway_ip": "10.0.0.1",
                                   "enable_dhcp": True,
                                   "x": 123,
                                   "y": 456,
                               },]
                           }],
                           "routers": [{
                               "id": 1,
                               "name": "router1",
                               "attach_subnets": [subnet_id1, subnet_id2],
                               "attach_ext": false,
                               "x": 123,
                               "y": 456,
                           }],
                           "devices": [{
                               "id": 1,
                               "name": "route1",
                               "type": "1",
                               "x": 123,
                               "y": 456,
                               "owner_id": "uuid",
                               "owner_name": "super",

                               "ne_uuid": "uuid",
                               "image_name": "ubuntu",
                               "image_uuid": "uuid",
                               "flavor": "uuid",
                               "cpu": 123,
                               "ram": ,
                               "disk": ,
                               "username": "",
                               "password": "",

                               "ports": ,
                               "manage_ports": ,

                               "attach_subnets": [subnet_id1, subnet_id2],
                           }],
                       }, ],
                   }
               """
        ref = self._normalize_dict(experiment)
        ref = self.container_expt_rpcapi.expt_create(ref)
        return Experiment.wrap_member(context, ref)

    def delete(self, context, expt_id):
        self.container_expt_rpcapi.expt_delete(expt_id)

    def detail(self, context, expt_id):
        ref = self.container_expt_rpcapi.expt_detai(expt_id)
        if not ref:
            raise exception.ExperimentNotFound(expt_id=expt_id)
        return Experiment.wrap_member(context, ref)

    def restart(self, context, expt_id, experiment):
        try:
            self.container_expt_rpcapi.expt_restart(expt_id)
        except exception as err:
            return exc.HTTPBadRequest(exception=err.format_message())

    def start(self, context, expt_id, experiment):
        try:
            self.container_expt_rpcapi.expt_restart(expt_id)
        except exception as err:
            return exc.HTTPBadRequest(exception=err.format_message())

    def stop(self, context, expt_id, experiment):
        try:
            self.container_expt_rpcapi.expt_stop(expt_id)
        except exception as err:
            return exc.HTTPBadRequest(explanation=err.format_message())

    def topology(self, context, expt_id):
        ref = self.container_expt_rpcapi.expt_topology(expt_id)
        return Experiment.wrap_member(context, ref)

    # -------------------device--------------#
    def create_deivce(self, context, device):
        try:
            vm_dict = self._normalize_dict(device)
        except KeyError as err:
            return {'error': 'Key \'%s\' MUST exist in body.' % err}

        vm_ref = self.container_expt_rpcapi.device_create(vm_dict)
        return {'device': vm_ref}

    def delete_device(self, context, device_id):
        try:
            self.container_expt_rpcapi.device_delete(device_id)
        except Exception, ex:
            return exc.HTTPBadRequest(explanation=ex.format_message())

    def start_device(self, context, device_id, device):
        try:
            self.container_expt_rpcapi.device_start(device_id)
        except exception as err:
            return exc.HTTPBadRequest(explanation=err.format_message())

    def stop_device(self, context, device_id, device):
        try:
            self.container_expt_rpcapi.device_stop(device_id)
        except exception as err:
            return exc.HTTPBadRequest(explanation=err.format_message())
