import abc
import eventlet
import json
from oslo_config import cfg
from oslo_log import log
import six
from terra import exception
from terra.common.cloudapi import CloudAPI
from terra.common import cache
from terra.common import dependency
from terra.common import manager
from terra.common import utils
from terra.common import vm_states
from .business.experiment.experiment import Experiment
from .business.device.device import Device
from . import clean

CONF = cfg.CONF

LOG = log.getLogger(__name__)

MEMOIZE = cache.get_memoization_decorator(group='experiment')


def filter_experiment(experiment_ref):
    if experiment_ref:
        experiment_ref = experiment_ref.copy()
        utils.filter_model_result(experiment_ref)

    return experiment_ref


@dependency.requires('vm_api', 'vne_experiment_api', 'topology_api')
@dependency.provider('container_expt_api')
class ExperimentManager(manager.Manager):

    driver_namespace = 'terra.container_expt'

    def __init__(self):
        super(ExperimentManager, self).__init__(CONF.container_expt.driver)
        self.cloud_api = CloudAPI()
        self._sync_power_pool = eventlet.GreenPool()

    # what is this 'context'
    def expt_create(self, context, topo_dict):
        experiment = Experiment(context=context, driver=self.driver)
        ref = experiment.create(topo_dict)
        return ref

    def expt_delete(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        experiment.delete()

    def expt_detail(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        try:
            ret = experiment.detail()
        except exception.ExperimentNotFound:
            ret = dict()
        return ret

    def expt_restart(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        experiment.restart()

    def expt_start(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        experiment.start()

    def expt_stop(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        experiment.stop()

    def expt_topology(self, context, expt_id):
        experiment = Experiment(context=context, expt_id=expt_id,
                                driver=self.driver)
        return experiment.topology()

    def _get_device_type(self, device_id):
        vm_ref = self.vm_api.get_vm_by_device(device_id)
        device_type = None
        extra = vm_ref.get('other', None)
        if extra:
            device_type = json.load(extra).get('type', None)
        return device_type

    def device_create(self, context, device_values):
        device = Device(context=context)
        return device.create(device_values)

    def device_delete(self, context, device_id):
        device = Device(context=context, id=device_id)
        device.delete()

    def device_start(self, context, device_id):
        self.vne_experiemnt_api.device_start(device_id)

    def device_stop(self, context, device_id):
        self.vne_experiment_api.device_stop(device_id)


@six.add_metaclass(abc.ABCMeta)
class ExperimentDriver(object):

    def generates_uuids(self):
        return True

    @abc.abstractmethod
    def create(self, topo_dict):
        raise exception.NotImplemented()

    @abc.abstractmethod
    def delete(self, experiment_id):
        raise exception.NotImplemented()


Driver = ExperimentDriver
