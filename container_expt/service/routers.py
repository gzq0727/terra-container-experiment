from terra.wsgi import APIRouter
from container_expt.service import controllers


class Routers(APIRouter):

    def add_routes(self, mapper):
        experiment_controller = controllers.Experiment()

        # create experiment
        mapper.connect("/container/experiments",
                       controller=experiment_controller,
                       action='create',
                       conditions={"method": ['POST']})

        # delete experiment
        mapper.connect("/container/experiments/{expt_id}",
                       controller=experiment_controller,
                       action='delete',
                       conditions={"method": ['DELETE']})

        # get experiment details
        mapper.connect("/container/experiments/{expt_id}",
                       controller=experiment_controller,
                       action='detail',
                       conditions={"method": ['GET']})

        # restart experiment
        mapper.connect("/container/experiments/{expt_id}/restart",
                       controller=experiment_controller,
                       action='restart',
                       conditions={"method": ['PUT']})

        # start experiment
        mapper.connect("/container/experiments/{expt_id}/start",
                       controller=experiment_controller,
                       action='start',
                       conditions={"method": ['PUT']})

        # stop experiment
        mapper.connect("/container/experiments/{expt_id}/stop",
                       controller=experiment_controller,
                       action='stop',
                       conditions={"method": ['PUT']})

        # get experiment topology
        mapper.connect("/container/topology/{expt_id}",
                       controller=experiment_controller,
                       action='topology',
                       conditions={"method": ['GET']})

# -------------------- device -------------------- #

        # create device
        mapper.connect("/container/devices",
                       controller=experiment_controller,
                       action='create_device',
                       conditions={"method": ['POST']})
        # delete device
        mapper.connect("/container/devices/{device_id}",
                       controller=experiment_controller,
                       action='delete_device',
                       conditions={"method": ['DELETE']})

        # start device
        mapper.connect("/container/devices/{device_id}/start",
                       controller=experiment_controller,
                       action='start_device',
                       conditions={"method": ['PUT']})

        # stop device
        mapper.connect("/container/devices/{device_id}/stop",
                       controller=experiment_controller,
                       action='stop_device',
                       conditions={"method": ['PUT']})