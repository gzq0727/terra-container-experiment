""" api description for a experiment driver. """

import copy
from oslo_config import cfg
from oslo_log import log
from terra import exception
from terra.i18n import _LI, _
from terra import experiment
from terra.common import utils
from tenant_expt.service import core as core_driver
from tenant_expt.service.backends.sql import api as sql_api

CONF = cfg.CONF
LOG = log.getLogger(__name__)


class Experiment(core_driver.ExperimentDriver):
    """Interface description for an Experiment driver."""

    ######################### experiment #########################
    def create(self, experiment_value):
        """Creates a new experiment.

        :raises terra.exception.Conflict: If a duplicate experiment exists.

        """
        experiment_ref = sql_api.experiment_create(experiment_value)
        return experiment.filter_experiment(experiment_ref.to_dict())

    def get(self, experiment_id):
        """Get a experiment detail by ID.

        :returns: experiment_ref
        :raises terra.exception.ExperimentNotFound: If the experiment doesn't exist.

        """
        experiment_ref = sql_api.experiment_get(experiment_id)
        return experiment.filter_experiment(experiment_ref.to_dict())

    def delete(self, experiment_id):
        """delete a experiment detail by ID.

        :raises terra.exception.ExperimentNotFound: If the experiment doesn't exist.

        """
        sql_api.experiment_delete(experiment_id)

    def get_experiment_list(self, filters):
        expt_refs = sql_api.get_experiment_list(filters)
        expts = []
        for ref in expt_refs:
            expts.append(experiment.filter_experiment(ref))
        return expts

    ######################### device #########################
    def vhost_data_create(self, values):
        vhost_ref = sql_api.vhost_data_create(values)
        return vne_experiment.filter_device(vhost_ref.to_dict())

    def vswitch_data_create(self, values):
        vswitch_ref = sql_api.vswitch_data_create(values)
        return vne_experiment.filter_device(vswitch_ref.to_dict())

    def vcontroller_data_create(self, values):
        vcontroller_ref = sql_api.vcontroller_data_create(values)
        return vne_experiment.filter_device(vcontroller_ref.to_dict())

    def vgateway_data_create(self, values):
        vgateway_ref = sql_api.vgateway_data_create(values)
        return vne_experiment.filter_device(vgateway_ref.to_dict())

    def vrouter_data_create(self, values):
        vrouter_ref = sql_api.vrouter_data_create(values)
        return vne_experiment.filter_device(vrouter_ref.to_dict())

    def vcontrollers_get(self, expt_id):
        return sql_api.expt_vcontrollers_get(expt_id)

    def vhosts_get(self, expt_id):
        return sql_api.expt_vhosts_get(expt_id)

    def vswitchs_get(self, expt_id):
        return sql_api.expt_vswitchs_get(expt_id)

    def vgateways_get(self, expt_id):
        return sql_api.expt_vgateways_get(expt_id)

    def vrouters_get(self, expt_id):
        return sql_api.expt_vrouters_get(expt_id)

    def device_get_ports(self, device_id):
        return sql_api.device_get_ports(device_id)

    ######################### port #########################
    def ports_get_attach_links(self, port_ids):
        return sql_api.ports_get_attach_links(port_ids)

    def ports_get_attach_devices(self, port_ids):
        return sql_api.ports_get_attach_devices(port_ids)

    def port_mapping_create(self, real_port_id, mapping_port_id, cloud_subnet_id):
        return sql_api.port_mapping_create(real_port_id, mapping_port_id, cloud_subnet_id)

    def port_mapping_get_by_real_port_id(self, real_port_id):
        port_mapping = sql_api.port_mapping_get_by_real_port_id(real_port_id)
        return vne_experiment.filter_port_mapping(port_mapping.to_dict())

    ######################### vlink #########################
    def create_vlink_data(self, values):
        vlink_ref = sql_api.create_vlink_data(values)
        return vne_experiment.filter_vlink(vlink_ref.to_dict())

    def vlink_get(self, vlink_id):
        vlink = sql_api.vlink_get(vlink_id)
        return vne_experiment.filter_vlink(vlink.to_dict())

    def list_vlinks(self, hints=None):
        vlink_refs = sql_api.vlink_get_all_by_filters(hints=hints)
        return [vne_experiment.filter_vlink(x.to_dict()) for x in vlink_refs]

    def vlinks_get_in_expt(self, expt_id):
        return sql_api.expt_vlinks_get(expt_id)

    def vlink_update_state(self, vlink_ids, state):
        return sql_api.vlink_update_state(vlink_ids, state)

    def subnets_get_in_expt(self, expt_id):
        return sql_api.expt_subnets_get(expt_id)

    def vlink_logic_delete(self, vlink_id):
        return sql_api.vlink_logic_delete(vlink_id)

    def vlink_get_by_cloud_network_id(self, cloud_network_id):
        return sql_api.vlink_get_by_cloud_network_id(cloud_network_id)

    def vlink_get_by_port_ids(self, port_ids):
        return sql_api.vlink_get_by_port_ids(port_ids)

    ######################### subnet #########################
    def subnet_data_create(self, values):
        subnet_ref = sql_api.subnet_data_create(values)
        return vne_experiment.filter_subnet(subnet_ref.to_dict())

    def subnet_get_by_id(self, id):
        subnet = sql_api.subnet_get_by_id(id)
        return subnet

    def subnet_delete_by_id(self, subnet_id):
        sql_api.subnet_delete_by_id(subnet_id)

    def subnet_update_host_routes(self, subnet_id):
        return sql_api.subnet_update_host_routes(subnet_id)

    def subnet_get_by_cloud_subnet_id(self, cloud_subnet_id):
        return sql_api.subnet_get_by_cloud_subnet_id(cloud_subnet_id)

    ######################### vrouter #########################
    def vrouter_get_by_device(self, device_id):
        vrouter_ref = sql_api.vrouter_get_by_device(device_id)
        return vne_experiment.filter_vrouter(vrouter_ref.to_dict())

    ######################### topology #########################
    def topo_get_subnets(self, topo_id):
        return sql_api.topo_get_subnets(topo_id)
