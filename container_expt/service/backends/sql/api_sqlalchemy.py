""" Sqlalchemy API for Experiment. """
from terra.topology.business.cloudnetwork import CloudNetwork

import json
import sqlalchemy
import sys
import traceback
from oslo_db import exception as db_exc
from oslo_db.sqlalchemy import utils as sqlalchemyutils
from oslo_utils import timeutils
from sqlalchemy import orm
from sqlalchemy.sql.expression import asc
from sqlalchemy.sql.expression import desc
from terra import exception
import terra.db.sqlalchemy.api as sa_api
import sqlalchemy.sql as sa_sql
from sqlalchemy import and_
from sqlalchemy import or_
from . import models
from oslo_log import log as logging
from terra import i18n

from terra.common import subnet_states, vlink_states
from terra.common.constants import EXPT_OPERATE_DIC, VM_TYPE_DIC
from terra.experiment.backends.sql.models import BaseExpt, CloudExptTopo
from terra.topology.backends.sql.models import CloudSubnet, CloudPort, \
    CloudSubnetPort, CloudDevice, CloudTopo, CloudRouter, CloudOSRouter, \
    CloudNetwork, CloudOSNetwork, CloudOSSubnet
from terra.vm.backends.sql.models import CloudVM, CloudOSVM

_ = i18n._
_LI = i18n._LI
_LW = i18n._LW
LOG = logging.getLogger(__name__)


def get_backend():
    """The backend is this module itself."""
    return sys.modules[__name__]


def _paginate_query(query, model, limit, sort_keys, marker=None,
                    sort_dir=None, sort_dirs=None):

    """Returns a query with sorting / pagination criteria added.

    Pagination works by requiring a unique sort_key, specified by sort_keys.
    (If sort_keys is not unique, then we risk looping through values.)
    We use the last row in the previous page as the 'marker' for pagination.
    So we must return values that follow the passed marker in the order.
    With a single-valued sort_key, this would be easy: sort_key > X.
    With a compound-values sort_key, (k1, k2, k3) we must do this to repeat
    the lexicographical ordering:
    (k1 > X1) or (k1 == X1 && k2 > X2) or (k1 == X1 && k2 == X2 && k3 > X3)

    We also have to cope with different sort_directions.

    Typically, the id of the last row is used as the client-facing pagination
    marker, then the actual marker object must be fetched from the db and
    passed in to us as marker.

    :param query: the query object to which we should add paging/sorting
    :param model: the ORM model class
    :param limit: maximum number of items to return
    :param sort_keys: array of attributes by which results should be sorted
    :param marker: the last item of the previous page; we returns the next
                    results after this value.
    :param sort_dir: direction in which results should be sorted (asc, desc)
    :param sort_dirs: per-column array of sort_dirs, corresponding to sort_keys

    :rtype: sqlalchemy.orm.query.Query
    :return: The query with sorting/pagination added.
    """
    if 'id' not in sort_keys:
        # TODO(justinsb): If this ever gives a false-positive, check
        # the actual primary key, rather than assuming its id
        LOG.warn(_LW('Id not in sort_keys; is sort_keys unique?'))

    assert (not (sort_dir and sort_dirs))

    # Default the sort direction to ascending
    if sort_dirs is None and sort_dir is None:
        sort_dir = 'asc'

    # Ensure a per-column sort direction
    if sort_dirs is None:
        sort_dirs = [sort_dir for _sort_key in sort_keys]

    assert (len(sort_dirs) == len(sort_keys))

    # Add sorting
    for current_sort_key, current_sort_dir in zip(sort_keys, sort_dirs):
        sort_dir_func = {
            'asc': sqlalchemy.asc,
            'desc': sqlalchemy.desc,
        }[current_sort_dir]

        try:
            sort_key_attr = getattr(model, current_sort_key)
        except AttributeError:
            raise exception.InvalidSortKey()
        query = query.order_by(sort_dir_func(sort_key_attr))

    default = ''  # Default to an empty string if NULL

    # Add pagination
    if marker is not None:
        marker_values = []
        for sort_key in sort_keys:
            v = getattr(marker, sort_key)
            if v is None:
                v = default
            marker_values.append(v)

        # Build up an array of sort criteria as in the docstring
        criteria_list = []
        for i in range(len(sort_keys)):
            crit_attrs = []
            for j in range(i):
                model_attr = getattr(model, sort_keys[j])
                default = None if isinstance(
                    model_attr.property.columns[0].type,
                    sqlalchemy.DateTime) else ''
                attr = sa_sql.expression.case([(model_attr != None,
                                                model_attr), ],
                                              else_=default)
                crit_attrs.append((attr == marker_values[j]))

            model_attr = getattr(model, sort_keys[i])
            default = None if isinstance(model_attr.property.columns[0].type,
                                         sqlalchemy.DateTime) else ''
            attr = sa_sql.expression.case([(model_attr != None,
                                            model_attr), ],
                                          else_=default)
            if sort_dirs[i] == 'desc':
                crit_attrs.append((attr < marker_values[i]))
            elif sort_dirs[i] == 'asc':
                crit_attrs.append((attr > marker_values[i]))
            else:
                raise ValueError(_("Unknown sort direction, "
                                   "must be 'desc' or 'asc'"))

            criteria = sa_sql.and_(*crit_attrs)
            criteria_list.append(criteria)

        f = sa_sql.or_(*criteria_list)
        query = query.filter(f)

    if limit is not None:
        query = query.limit(limit)

    return query


def _make_conditions_from_filters(filters, is_public=None):
    # NOTE(venkatesh) make copy of the filters are to be altered in this
    # method.
    filters = filters.copy()
    expt_conditions = []
    prop_conditions = []
    tag_conditions = []

    if is_public is not None:
        expt_conditions.append(BaseExpt.is_public == is_public)

    if 'changes-since' in filters:
        # normalize timestamp to UTC, as sqlalchemy doesn't appear to
        # respect timezone offsets
        changes_since = timeutils.normalize_time(filters.pop('changes-since'))
        expt_conditions.append(BaseExpt.updated_at > changes_since)

    if 'deleted' in filters:
        deleted_filter = filters.pop('deleted')
        expt_conditions.append(BaseExpt.deleted == deleted_filter)

    filters = dict([(k, v) for k, v in filters.items() if v is not None])

    for (k, v) in filters.items():
        key = k
        if k.endswith('_min') or k.endswith('_max'):
            key = key[0:-4]
            try:
                v = int(filters.pop(k))
            except ValueError:
                msg = _("Unable to filter on a range "
                        "with a non-numeric value.")
                raise exception.InvalidFilterRangeValue(msg)

            if k.endswith('_min'):
                expt_conditions.append(getattr(BaseExpt, key) >= v)
            if k.endswith('_max'):
                expt_conditions.append(getattr(BaseExpt, key) <= v)

    for (k, v) in filters.items():
        value = filters.pop(k)
        if hasattr(BaseExpt, k):
            expt_conditions.append(getattr(BaseExpt, k)
                            .like('%'+value+'%'))
        else:
            prop_filters = _make_image_property_condition(key=k, value=value)
            prop_conditions.append(prop_filters)

    return expt_conditions


########################### experiment #########################
# def expt_create(context, values):
#     values['expired_at'] = values['expired_at'].replace(tzinfo=None)
#     if values['operate_expired_at']:
#         values['operate_expired_at'] = \
#             values['operate_expired_at'].replace(tzinfo=None)
#     expt_ref = BaseExpt.from_dict(values)
#     expt_ref.update(values)
#     try:
#         expt_ref.save()
#     except:
#         raise
#     return expt_ref
#
#
# def experiment_get(experiment_id, session=None):
#     query = sa_api.model_query(BaseExpt, session=session). \
#         filter_by(id=experiment_id)
#
#     result = query.first()
#     if not result:
#         raise exception.ExperimentNotFound(expt_id=experiment_id)
#
#     return result
#
#
# def experiment_delete(experiment_id):
#     session = sa_api.get_session()
#     with session.begin():
#         ref = sa_api.model_query(BaseExpt, session=session). \
#             filter_by(id=experiment_id). \
#             first()
#         if not ref:
#             raise exception.ExperimentNotFound(expt_id=experiment_id)
#
#         session.delete(ref)
#
#
# def get_experiment_list(filters):
#     # query = sa_api.model_query(BaseExpt, read_deleted="no")
#     # expt_list = sa_api.filter_limit_query(BaseExpt, query, filters)
#     # return expt_list
#
#     filters = filters or {}
#     sort_key = filters.pop('sort_key', 'created_at')
#     sort_dir = filters.pop('sort_dir', 'desc')
#     limit = filters.pop('limit', None)
#     marker = filters.pop("marker", None)
#     showing_deleted = filters.pop('deleted', False)
#     showing_deleting = filters.pop('deleting', 'True') == 'True'
#     owner_id = filters.pop('owner_id', None)
#     name = filters.pop('name', None)
#     name_like = int(filters.pop('name_like', 1))
#     is_public = filters.pop('is_public', False)
#     expt_type = filters.pop('type', None)
#
#     if sort_key:
#         sort_key = [sort_key]
#     if sort_dir:
#         sort_dir = [sort_dir]
#
#     default_sort_dir = 'desc'
#     if not sort_dir:
#         sort_dir = [default_sort_dir] * len(sort_key)
#     elif len(sort_dir) == 1:
#         default_sort_dir = sort_dir[0]
#         sort_dir *= len(sort_key)
#
#     expt_conds = _make_conditions_from_filters(filters)
#     expt_conditional_clause = sa_sql.and_(*expt_conds)
#     session = sa_api.get_session()
#     query_expt = session.query(BaseExpt).filter(expt_conditional_clause)
#
#     if not showing_deleted:
#         query_expt = query_expt.filter(BaseExpt.deleted == False)
#
#     if not showing_deleting:
#         query_expt = query_expt.filter(BaseExpt.operate != EXPT_OPERATE_DIC['deleting'])
#     if is_public:
#         query_expt = query_expt.filter(BaseExpt.is_public == True)
#     if owner_id:
#         query_expt = query_expt.filter(BaseExpt.owner_id == owner_id)
#     if name:
#         if name_like == 1:
#             query_expt = query_expt.filter(BaseExpt.name.like('%'+name+'%'))
#         else:
#             query_expt = query_expt.filter(BaseExpt.name == name)
#     if expt_type:
#         query_expt = query_expt.filter(BaseExpt.type == expt_type)
#
#     marker_expt = None
#     if marker is not None:
#         marker_expt = _get_expt( marker)
#
#     for key in ['created_at', 'id']:
#         if key not in sort_key:
#             sort_key.append(key)
#             sort_dir.append(default_sort_dir)
#     query = _paginate_query(query_expt, BaseExpt, limit,
#                         sort_key,
#                         marker=marker_expt,
#                         sort_dir=None,
#                         sort_dirs=sort_dir)
#     return query.all()
#
#
# def _get_expt(expt_id):
#     session = sa_api.get_session()
#     try:
#         query = session.query(BaseExpt).filter_by(id=expt_id)
#         expt = query.one()
#
#     except:
#         raise exception.ExperimentNotFound(expt_id=expt_id)
#     return expt
#
#
# ########################### device #########################
# def vhost_data_create(values):
#     vhost_ref = models.VneVHost.from_dict(values)
#     vhost_ref.save()
#     return vhost_ref
#
#
# def vswitch_data_create(values):
#     vswitch_ref = models.VneVSwitch.from_dict(values)
#     vswitch_ref.save()
#     return vswitch_ref
#
#
# def vcontroller_data_create(values):
#     vcontroller_ref = models.VneVController.from_dict(values)
#     vcontroller_ref.save()
#     return vcontroller_ref
#
#
# def vgateway_data_create(values):
#     vgateway_ref = models.VneVGateway.from_dict(values)
#     vgateway_ref.save()
#     return vgateway_ref
#
#
# def vrouter_data_create(values):
#     vrouter_ref = models.VneVRouter.from_dict(values)
#     vrouter_ref.save()
#     return vrouter_ref
#
# _expt_topo_and = and_(CloudExptTopo.expt_id == BaseExpt.id,
#                       CloudExptTopo.deleted == False)
# _topo_and = and_(CloudTopo.id == CloudExptTopo.topo_id,
#                  CloudTopo.deleted == False)
#
# def expt_vcontrollers_get(expt_id):
#     _device_and = and_(CloudDevice.topo_id == CloudTopo.id,
# #                        CloudVM.type == VM_TYPE_DIC['vcontroller'],
#                        CloudDevice.deleted == False)
#     _vm_and = and_(CloudVM.device_id == CloudDevice.id,
#                    CloudVM.deleted == False)
#     _port_and = and_(CloudPort.device_id == CloudDevice.id,
#                      CloudPort.deleted == False)
#     query = sa_api.model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSVM.os_vm_uuid,
#                          CloudVM.other,
#                          CloudVM.state,
#                          CloudVM.operate,
#                          CloudVM.name,
#                          CloudVM.description,
#                          CloudDevice.owner_id,
#                          CloudVM.cpu,
#                          CloudVM.ram,
#                          CloudVM.disk,
#                          CloudVM.created_at,
#                          models.VneVController.type,
#                          models.VneVController.generate_type,
#                          models.VneVController.ipaddr,
#                          models.VneVController.port,
#                          models.VneVController.id,
#                          models.VneVController.has_created_backend,
#                          CloudVM.username,
#                          CloudVM.password,
#                          CloudVM.failure_info,
#                          CloudPort.id,
#                          CloudPort.ipaddrs,
#                          models.VneVController.cover_id,
#                          CloudVM.alias,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudVM, _vm_and)).\
#             join((CloudPort, _port_and)).\
#             outerjoin((CloudOSVM,
#                        CloudOSVM.vm_id == CloudVM.id)).\
#             join((models.VneVController,
#                   models.VneVController.device_id == CloudDevice.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     device_type = VM_TYPE_DIC['vcontroller']
#     cts = [{'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#             'operate':q[5], 'name':q[6], 'desc':q[7], 'owner':q[8],
#             'cpu':q[9], 'ram':q[10], 'disk':q[11], 'created_at':q[12],
#             'ct_type':q[13], 'ct_generate_type':q[14], 'ct_ipaddr':q[15],
#             'ct_port':q[16], 'ct_id':q[17], 'ct_created_backend':q[18],
#             'username':q[19], 'password':q[20], 'failure_info':q[21],
#             'port_id':q[22], 'port_ipaddr':q[23], 'cover_id':q[24],
#             'alias':q[25]}
#            for q in query]
#     return cts
#
#
# def expt_vhosts_get(expt_id):
#     _device_and = and_(CloudDevice.topo_id == CloudTopo.id,
#                        CloudDevice.deleted == False)
#     _vm_and = and_(CloudVM.device_id == CloudDevice.id,
#                    CloudVM.deleted == False)
#     query = sa_api.model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSVM.os_vm_uuid,
#                          CloudVM.other,
#                          CloudVM.state,
#                          CloudVM.operate,
#                          CloudVM.name,
#                          CloudVM.description,
#                          CloudDevice.owner_id,
#                          CloudVM.cpu,
#                          CloudVM.ram,
#                          CloudVM.disk,
#                          CloudVM.created_at,
#                          models.VneVHost.id,
#                          CloudVM.alias,
#                          CloudDevice.owner_name,
#                          CloudVM.username,
#                          CloudVM.password,
#                          CloudVM.failure_info,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudVM, _vm_and)).\
#             outerjoin((CloudOSVM,
#                        CloudOSVM.vm_id == CloudVM.id)).\
#             join((models.VneVHost,
#                   models.VneVHost.device_id == CloudDevice.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     device_type = VM_TYPE_DIC['vhost']
#     hosts = [{'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#               'operate':q[5], 'name':q[6], 'desc':q[7], 'owner':q[8],
#               'cpu':q[9], 'ram':q[10], 'disk':q[11], 'created_at':q[12],
#               'vhost_id':q[13], 'alias':q[14], 'owner_name':q[15],
#               'username':q[16], 'password':q[17], 'failure_info':q[18]}
#              for q in query]
#     return hosts
#
#
# def expt_vswitchs_get(expt_id):
#     _device_and = and_(CloudDevice.topo_id == CloudTopo.id,
#                        CloudDevice.deleted == False)
#     _vm_and = and_(CloudVM.device_id == CloudDevice.id,
#                    CloudVM.deleted == False)
#     query = sa_api.model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSVM.os_vm_uuid,
#                          CloudVM.other,
#                          CloudVM.state,
#                          CloudVM.operate,
#                          CloudVM.name,
#                          CloudVM.description,
#                          CloudDevice.owner_id,
#                          CloudVM.cpu,
#                          CloudVM.ram,
#                          CloudVM.disk,
#                          CloudVM.created_at,
#                          models.VneVSwitch.type,
#                          models.VneVSwitch.ipaddr,
#                          models.VneVSwitch.port_num,
#                          models.VneVSwitch.id,
#                          CloudVM.alias,
#                          CloudVM.username,
#                          CloudVM.password,
#                          CloudVM.failure_info,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudVM, _vm_and)).\
#             outerjoin((CloudOSVM,
#                        CloudOSVM.vm_id == CloudVM.id)).\
#             join((models.VneVSwitch,
#                   models.VneVSwitch.device_id == CloudDevice.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     device_type = VM_TYPE_DIC['vswitch']
#     vswitchs = []
#     for q in query:
#         clear = {'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#                  'operate':q[5], 'name':q[6], 'desc':q[7], 'owner':q[8],
#                  'cpu':q[9], 'ram':q[10], 'disk':q[11], 'created_at':q[12],
#                  'vswitch_type':q[13], 'vswitch_ipaddr':q[14],
#                  'vswitch_port_num':q[15], 'vswitch_id':q[16], 'alias':q[17],
#                  'username':q[18], 'password':q[19], 'failure_info':q[20]}
#         clear['ports'] = get_device_ports(q[1])
#         vswitchs.append(clear)
#     return vswitchs
#
#
# def get_device_ports(device_id):
#     _device_and = and_(CloudDevice.id == CloudPort.device_id,
#                        CloudDevice.deleted == False)
#     model_query = sa_api.model_query
#     subq = model_query(CloudPort,
#                         (CloudPort.id,
#                          CloudPort.no,
#                          CloudPort.other,),
#                         read_deleted="no").\
#             join((CloudDevice, _device_and)).\
#             filter(CloudDevice.id == device_id).\
#             order_by(asc(CloudPort.no)).\
#             all()
#     port_ids = [q[0] for q in subq]
#
#     _vnesub_and = and_(models.VneSubnet.cloud_subnet_id == \
#                        CloudSubnetPort.subnet_id,
#                        models.VneSubnet.deleted == False)
#     query = model_query(CloudSubnetPort,
#                         (CloudSubnetPort.port_id,),
#                         read_deleted="no").\
#             join((models.VneSubnet, _vnesub_and)).\
#             filter(CloudSubnetPort.port_id.in_(port_ids)).\
#             all()
#     used_ports = [q[0] for q in query]
#
#     _vlink_filter = and_(or_(models.VneVlink.src_port_id == CloudPort.id,
#                              models.VneVlink.dst_port_id == CloudPort.id),
#                          models.VneVlink.deleted == False)
#     query = model_query(CloudPort,
#                         (CloudPort.id,),
#                         read_deleted="no").\
#             join((models.VneVlink, _vlink_filter)).\
#             filter(CloudPort.id.in_(port_ids)).\
#             all()
#     used_ports.extend([q[0] for q in query])
#
#     ports = []
#     for q in subq:
#         clear = {}
#         clear['id'] = q[0]
#         clear['no'] = q[1]
#         clear['type'] = json.loads(q[2]).get('type', 0)
#         if q[0] in used_ports:
#             clear['has_used'] = True
#         else:
#             clear['has_used'] = False
#         ports.append(clear)
#     return ports
#
#
# def expt_vgateways_get(expt_id):
#     _device_and = and_(CloudDevice.topo_id == CloudTopo.id,
#                        CloudDevice.deleted == False)
#     _vm_and = and_(CloudVM.device_id == CloudDevice.id,
#                    CloudVM.deleted == False)
#     model_query = sa_api.model_query
#     query = model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSVM.os_vm_uuid,
#                          CloudVM.other,
#                          CloudVM.state,
#                          CloudVM.operate,
#                          CloudVM.name,
#                          CloudVM.description,
#                          CloudDevice.owner_id,
#                          CloudVM.cpu,
#                          CloudVM.ram,
#                          CloudVM.disk,
#                          CloudVM.created_at,
#                          models.VneVGateway.id,
#                          CloudVM.alias,
#                          CloudDevice.owner_name,
#                          CloudVM.username,
#                          CloudVM.password,
#                          CloudVM.failure_info,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudVM, _vm_and)).\
#             outerjoin((CloudOSVM,
#                        CloudOSVM.vm_id == CloudVM.id)).\
#             join((models.VneVGateway,
#                   models.VneVGateway.device_id == CloudDevice.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     device_type = VM_TYPE_DIC['vgateway']
#     vgateways = [{'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#               'operate':q[5], 'name':q[6], 'desc':q[7], 'owner':q[8],
#               'cpu':q[9], 'ram':q[10], 'disk':q[11], 'created_at':q[12],
#               'vgateway_id':q[13], 'alias':q[14], 'owner_name':q[15],
#               'username':q[16], 'password':q[17], 'failure_info':q[18]}
#              for q in query]
#     return vgateways
#
#
# def expt_vrouters_get(expt_id):
#     _device_and = and_(CloudDevice.topo_id == CloudTopo.id,
#                        CloudDevice.deleted == False)
#     _vm_and = and_(CloudVM.device_id == CloudDevice.id,
#                    CloudVM.deleted == False)
#     model_query = sa_api.model_query
#     query = model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSVM.os_vm_uuid,
#                          CloudVM.other,
#                          CloudVM.state,
#                          CloudVM.operate,
#                          CloudVM.name,
#                          CloudVM.description,
#                          CloudDevice.owner_id,
#                          CloudVM.cpu,
#                          CloudVM.ram,
#                          CloudVM.disk,
#                          CloudVM.created_at,
#                          models.VneVRouter.id,
#                          CloudVM.alias,
#                          CloudDevice.owner_name,
#                          CloudVM.username,
#                          CloudVM.password,
#                          models.VneVRouter.is_extnet_router,
#                          CloudVM.failure_info,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudVM, _vm_and)).\
#             outerjoin((CloudOSVM,
#                        CloudOSVM.vm_id == CloudVM.id)).\
#             join((models.VneVRouter,
#                   models.VneVRouter.device_id == CloudDevice.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     device_type = VM_TYPE_DIC['vrouter']
#     vrouters = [{'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#                  'operate':q[5], 'name':q[6], 'desc':q[7], 'owner':q[8],
#                  'cpu':q[9], 'ram':q[10], 'disk':q[11], 'created_at':q[12],
#                  'vrouter_id':q[13], 'alias':q[14], 'owner_name':q[15],
#                  'username':q[16], 'password':q[17], 'is_service':False,
#                  'is_extnet_router':q[18], 'failure_info':q[19]}
#                 for q in query]
#
#     # get router which is provide like service
#     _router_and = and_(CloudRouter.device_id == CloudDevice.id,
#                        CloudRouter.deleted == False)
#     query = model_query(BaseExpt,
#                         (BaseExpt.id,
#                          CloudDevice.id,
#                          CloudOSRouter.os_router_uuid,
#                          CloudRouter.other,
#                          CloudRouter.state,
#                          CloudRouter.name,
#                          CloudDevice.owner_id,
#                          CloudRouter.created_at,
#                          CloudRouter.id,
#                          CloudRouter.alias,
#                          CloudDevice.owner_name,
#                          CloudRouter.attach_ext,
#                          CloudRouter.failure_info,
#                          ),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((CloudDevice, _device_and)).\
#             join((CloudRouter, _router_and)).\
#             outerjoin((CloudOSRouter,
#                        CloudOSRouter.router_id == CloudRouter.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     vrt_sers = [{'id':q[1], 'uuid':q[2], 'type':device_type, 'state':q[4],
#                  'operate':'', 'name':q[5], 'desc':'', 'owner':q[6],
#                  'cpu':0, 'ram':0, 'disk':0, 'created_at':q[7],
#                  'vrouter_id':q[8], 'alias':q[9], 'owner_name':q[10],
#                  'username':'', 'password':'', 'is_service':True,
#                  'is_extnet_router':q[11], 'failure_info':q[12]}
#                 for q in query]
#
#     vrouters.extend(vrt_sers)
#     return vrouters
#
#
# def port_attach_link_get_all(port_ids):
#     query = sa_api.model_query(models.VneVlink, read_deleted='no').\
#             filter(models.VneVlink.src_port_id.in_(port_ids)).\
#             all()
#     vlinks = {}
#     if query:
#         for q in query:
#             vlinks[q.src_port_id] = {'vlink_id': q.id, 'peer_port_id': q.dst_port_id}
#     if vlinks:
#         port_ids = list(set(port_ids) - set(vlinks.keys()))
#
#     query = sa_api.model_query(models.VneVlink, read_deleted='no').\
#             filter(models.VneVlink.dst_port_id.in_(port_ids)).\
#             all()
#     if query:
#         for q in query:
#             vlinks[q.dst_port_id] = {'vlink_id': q.id, 'peer_port_id': q.src_port_id}
#     if vlinks:
#         port_ids = list(set(port_ids) - set(vlinks.keys()))
#
#     query = sa_api.model_query(CloudPort,
#                         (CloudPort.id,
#                          models.VneSubnet.id,
#                         ),
#                         read_deleted="no").\
#             join((CloudSubnetPort, CloudSubnetPort.port_id == CloudPort.id)).\
#             join((CloudSubnet, and_(CloudSubnet.id == CloudSubnetPort.subnet_id,
#                                       CloudSubnet.deleted == False))).\
#             join((models.VneSubnet, models.VneSubnet.cloud_subnet_id == CloudSubnet.id)).\
#             filter(CloudPort.id.in_(port_ids)).\
#             all()
#     subs = {}
#     if query:
#         for q in query:
#             subs[q[0]] = q[1]
#
#     return {'vlinks': vlinks, 'subnets': subs}
#
#
# def ports_get_attach_devices(port_ids):
#     _device_and = and_(CloudDevice.id == CloudPort.device_id,
#                        CloudDevice.deleted == False)
#     model_query = sa_api.model_query
#     query = model_query(CloudPort,
#                         (CloudPort.id,
#                          CloudDevice.id,
#                          CloudVM.state,
#                          CloudVM.operate,),
#                         read_deleted="no"). \
#         join((CloudDevice, _device_and)). \
#         join(CloudVM, CloudVM.device_id == CloudDevice.id).\
#         filter(CloudPort.id.in_(port_ids)).all()
#     devices = []
#     for q in query:
#         device = {}
#         device['port_id'] = q[0]
#         device['device_id'] = q[1]
#         device['device_state'] = q[2]
#         device['device_operate'] = q[3]
#         device['is_service'] = False
#         devices.append(device)
#
#     query = model_query(CloudPort,
#                         (CloudPort.id,
#                          CloudDevice.id,
#                          CloudRouter.state,),
#                         read_deleted="no"). \
#         join((CloudDevice, _device_and)). \
#         join(CloudRouter, CloudRouter.device_id == CloudDevice.id).\
#         filter(CloudPort.id.in_(port_ids)).all()
#     for q in query:
#         device = {}
#         device['port_id'] = q[0]
#         device['device_id'] = q[1]
#         device['device_state'] = q[2]
#         device['device_operate'] = ''
#         device['is_service'] = True
#         devices.append(device)
#
#     return devices
#
#
# def port_mapping_create(real_port_id, mapping_port_id, cloud_subnet_id):
#     values = {}
#     values['cloud_subnet_id'] = cloud_subnet_id
#     values['real_port_id'] = real_port_id
#     values['mapping_port_id'] = mapping_port_id
#     port_ref = models.VneOptv10PortMapping.from_dict(values)
#     port_ref.save()
#     return port_ref
#
#
# def port_mapping_get_by_real_port_id(real_port_id):
#     port = sa_api.model_query(models.VneOptv10PortMapping, read_deleted="no"). \
#         filter_by(real_port_id=real_port_id).first()
#     return port
#
# ########################### vlink #########################
# def create_vlink_data(values):
#     vlink_ref = models.VneVlink.from_dict(values)
#     vlink_ref.save()
#     return vlink_ref
#
#
# def vlink_get(vlink_id):
#     session = sa_api.get_session()
#     vlink = sa_api.model_query(models.VneVlink, read_deleted="no"). \
#         filter_by(id=vlink_id).first()
#
#     return vlink
#
#
# def vlink_get_by_cloud_network_id(cloud_network_id):
#     session = sa_api.get_session()
#     vlink = sa_api.model_query(models.VneVlink, read_deleted="no"). \
#         filter_by(cloud_network_id=cloud_network_id)
#     if vlink:
#         vlink = vlink.first()
#     else:
#         return None
#     return vlink
#
#
# def vlink_get_all_by_filters(hints):
#     query = sa_api.model_query(models.VneVlink, read_deleted="no")
#     vlink_refs = sa_api.filter_limit_query(models.VneVlink, query, hints)
#     return vlink_refs
#
#
# def vlink_update_state(vlink_ids, state):
#     try:
#         values = {'state': state}
#         sa_api.model_query(models.VneVlink, read_deleted="no"). \
#             filter_by(id=vlink_ids).update(values)
#     except:
#         raise
#
#
# def vlink_logic_delete(vlink_id):
#     try:
#         values = {'deleted': True,
#                   'deleted_at': timeutils.utcnow(),
#                   'state': vlink_states.DELETED}
#         sa_api.model_query(models.VneVlink, read_deleted="no"). \
#             filter_by(id=vlink_id).update(values)
#     except:
#         raise
#
#
# def expt_vlinks_get(expt_id):
#     _vlink_and = and_(models.VneVlink.topo_id == CloudTopo.id,
#                       models.VneVlink.deleted == False)
#     model_query = sa_api.model_query
#     query = model_query(BaseExpt,
#                         (BaseExpt.id,
#                          models.VneVlink.id,
#                          models.VneVlink.state,
#                          CloudOSNetwork.os_network_uuid,
#                          CloudOSSubnet.os_subnet_uuid,
#                          CloudNetwork.id,
#                          CloudSubnet.id),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((models.VneVlink, _vlink_and)).\
#             join((CloudNetwork,
#                   and_(CloudNetwork.id == models.VneVlink.cloud_network_id,
#                        CloudNetwork.deleted == False))).\
#             join((CloudSubnet,
#                   and_(CloudSubnet.network_id == CloudNetwork.id,
#                        CloudSubnet.deleted == False))).\
#             outerjoin((CloudOSNetwork,
#                        CloudOSNetwork.network_id == CloudNetwork.id)).\
#             outerjoin((CloudOSSubnet,
#                        CloudOSSubnet.subnet_id == CloudSubnet.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#     vlinks = [{'id':q[1], 'state':q[2],
#                'network_uuid':q[3], 'subnet_uuid':q[4], 'cloud_network_id': q[5], 'cloud_subnet_id': q[6]}
#               for q in query]
#     return vlinks
#
#
# def expt_subnets_get(expt_id):
#     model_query = sa_api.model_query
#     query = model_query(BaseExpt,
#                         (BaseExpt.id,
#                          models.VneSubnet.id,
#                          CloudSubnet.state,
#                          CloudOSNetwork.os_network_uuid,
#                          CloudOSSubnet.os_subnet_uuid,
#                          CloudNetwork.id,
#                          CloudSubnet.id),
#                         read_deleted="no").\
#             join((CloudExptTopo, _expt_topo_and)).\
#             join((CloudTopo, _topo_and)).\
#             join((models.VneSubnet,
#                   and_(models.VneSubnet.topo_id == CloudTopo.id,
#                        models.VneSubnet.deleted == False))).\
#             join((CloudSubnet,
#                   and_(CloudSubnet.id == models.VneSubnet.cloud_subnet_id,
#                        CloudSubnet.deleted == False))).\
#             join((CloudNetwork,
#                   and_(CloudNetwork.id == CloudSubnet.network_id,
#                        CloudNetwork.deleted == False))).\
#             outerjoin((CloudOSNetwork,
#                        CloudOSNetwork.network_id == CloudNetwork.id)).\
#             outerjoin((CloudOSSubnet,
#                        CloudOSSubnet.subnet_id == CloudSubnet.id)).\
#             filter(BaseExpt.id == expt_id).\
#             all()
#
#     subnets = [{'id':q[1], 'state':q[2],
#                 'network_uuid':q[3], 'subnet_uuid':q[4], 'cloud_network_id': q[5], 'cloud_subnet_id': q[6]}
#                for q in query]
#     return subnets
#
#
# ########################### subnet #########################
# def subnet_data_create(values):
#     subnet_ref = models.VneSubnet.from_dict(values)
#     subnet_ref.save()
#     return subnet_ref
#
#
# def subnet_get_by_id(id):
#     _sub_and = and_(CloudSubnet.id == models.VneSubnet.cloud_subnet_id,
#                     CloudSubnet.deleted == False)
#     query = sa_api.model_query(models.VneSubnet,
#                                 (
#                                 models.VneSubnet.id,
#                                 models.VneSubnet.topo_id,
#                                 models.VneSubnet.cloud_subnet_id,
#                                 CloudSubnet.name,
#                                 CloudSubnet.fixed_ips,
#                                 CloudSubnet.host_routes,
#                                 CloudSubnet.other,
#                                 CloudSubnet.state,
#                                 CloudSubnet.alias,
#                                 ), read_deleted="no"). \
#                     join((CloudSubnet, _sub_and)). \
#                     filter(models.VneSubnet.id ==id).first()
#     if query:
#         return {'vne_subnet_id': query[0],
#                 'topo_id': query[1],
#                 'cloud_subnet_id': query[2],
#                 'name': query[3],
#                 'fixed_ips': query[4],
#                 'host_routes': query[5],
#                 'other': query[6],
#                 'state': query[7],
#                 'alias': query[8]}
#     else:
#         return None
#
#
# def subnet_get_by_cloud_subnet_id(cloud_subnet_id):
#     _sub_and = and_(CloudSubnet.id == models.VneSubnet.cloud_subnet_id,
#                     CloudSubnet.deleted == False)
#     query = sa_api.model_query(models.VneSubnet,
#                                (
#                                    models.VneSubnet.id,
#                                    models.VneSubnet.topo_id,
#                                    models.VneSubnet.cloud_subnet_id,
#                                    CloudSubnet.name,
#                                    CloudSubnet.fixed_ips,
#                                    CloudSubnet.host_routes,
#                                    CloudSubnet.other,
#                                    CloudSubnet.state,
#                                    CloudSubnet.alias,
#                                ), read_deleted="no"). \
#         join((CloudSubnet, _sub_and)). \
#         filter(models.VneSubnet.cloud_subnet_id == cloud_subnet_id).first()
#
#     if query:
#         return {'vne_subnet_id': query[0],
#                 'topo_id': query[1],
#                 'cloud_subnet_id': query[2],
#                 'name': query[3],
#                 'fixed_ips': query[4],
#                 'host_routes': query[5],
#                 'other': query[6],
#                 'state': query[7],
#                 'alias': query[8]}
#     else:
#         return None
#
#
# def subnet_delete_by_id(subnet_id):
#     try:
#         values = {'deleted': True,
#                   'deleted_at': timeutils.utcnow()}
#         sa_api.model_query(models.VneSubnet, read_deleted="no"). \
#             filter_by(id=subnet_id).update(values)
#     except:
#         raise
#
#
# def topo_get_subnets(topo_id):
#     _port_and = and_(CloudPort.id == CloudSubnetPort.port_id,
#                      CloudPort.deleted == False)
#     _sub_and = and_(CloudSubnet.id == models.VneSubnet.cloud_subnet_id,
#                     CloudSubnet.deleted == False)
#     query = sa_api.model_query(
#                 models.VneSubnet,
#                 (models.VneSubnet.id,
#                  CloudPort.id,
#                  CloudPort.no,
#                  CloudPort.device_id,
#                  CloudSubnet.name,
#                  CloudSubnet.fixed_ips,
#                  CloudSubnet.host_routes,
#                  CloudSubnet.other,
#                  CloudSubnet.state,
#                  CloudSubnet.alias,
#                  CloudPort.other,),
#                 read_deleted="no").\
#             join((CloudSubnet, _sub_and)).\
#             join((CloudSubnetPort,
#                   CloudSubnetPort.subnet_id == CloudSubnet.id)).\
#             join((CloudPort, _port_and)).\
#             filter(models.VneSubnet.deleted == False).\
#             filter(models.VneSubnet.topo_id == topo_id).\
#             all()
#     sub_dic = {}
#     if query:
#         for q in query:
#             sub_id = int(q[0])
#             if not sub_dic.has_key(sub_id):
#                 subnet_extra = json.loads(q[7])
#                 coordinate = subnet_extra.get('coordinate', {})
#                 sub_dic[sub_id] = {'id': q[0],
#                                    'name': q[4],
#                                    'alias': q[9],
#                                    'state': q[8],
#                                    'fixed_ips': q[5],
#                                    'x': coordinate.get('x', 0),
#                                    'y': coordinate.get('y', 0),
#                                    'ports': []}
#
#             port_extra = json.loads(q[10])
#             sub_dic[sub_id]['ports'].append({'port_id': q[1],
#                                              'port_no': q[2],
#                                              'attach_device': q[3],
#                                              'type': port_extra.get('type')})
#     return sub_dic
#
#
# def subnet_update_host_routes(subnet_id):
#     try:
#         session = sa_api.get_session()
#         model_query = sa_api.model_query
#         with session.begin():
#             # get all vrouter connect to this subnet
#             _port_and = and_(CloudPort.device_id == CloudRouter.device_id,
#                              CloudPort.deleted == False)
#             _sub_and = and_(CloudSubnet.id == CloudSubnetPort.subnet_id,
#                             CloudSubnet.deleted == False)
#             _vnesub_and = and_(models.VneSubnet.cloud_subnet_id == CloudSubnet.id,
#                                models.VneSubnet.deleted == False)
#             query = model_query(CloudRouter,
#                                 (CloudRouter.id,),
#                                 read_deleted="no", session=session).\
#                     join((CloudDevice,
#                           and_(CloudDevice.id == CloudRouter.device_id,
#                                CloudDevice.deleted == False))).\
#                     join((CloudPort, _port_and)).\
#                     join((CloudSubnetPort,
#                           CloudSubnetPort.port_id == CloudPort.id)).\
#                     join((CloudSubnet, _sub_and)).\
#                     join((models.VneSubnet, _vnesub_and)).\
#                     filter(models.VneSubnet.id == subnet_id).\
#                     filter(CloudRouter.attach_ext == False).\
#                     all()
#             update_subnet = None
#             if not query:
#                 return update_subnet
#             rt_ids = [q[0] for q in query]
#
#             # get all subnets connect to vrouters which get above
#             query = model_query(CloudRouter,
#                                 (CloudRouter.id,
#                                  CloudPort.id,
#                                  CloudPort.ipaddrs,
#                                  models.VneSubnet.id,
#                                  CloudSubnet.id,
#                                  CloudSubnet.fixed_ips,),
#                                 read_deleted="no", session=session).\
#                     join((CloudPort, _port_and)).\
#                     join((CloudSubnetPort,
#                           CloudSubnetPort.port_id == CloudPort.id)).\
#                     join((CloudSubnet, _sub_and)).\
#                     join((models.VneSubnet, _vnesub_and)).\
#                     filter(CloudRouter.id.in_(rt_ids)).\
#                     all()
#
#             rt_dic = {}
#             base_subnet_id = None
#             for q in query:
#                 if subnet_id == q[3]:
#                     base_subnet_id = q[4]
#                     rt_dic[q[0]] = q[2]
#             host_routes = []
#             for q in query:
#                 if subnet_id == q[3]:
#                     continue
#                 host_route = {'destination': q[5],
#                               'nexthop': rt_dic[q[0]]}
#                 host_routes.append(host_route)
#             if host_routes and base_subnet_id is not None:
#                 db_sub = model_query(CloudSubnet,
#                                      read_deleted="no", session=session).\
#                          filter_by(id=base_subnet_id).first()
#                 db_sub.update({'host_routes': json.dumps(host_routes)})
#                 update_subnet = db_sub
#
#             return update_subnet
#     except:
#         session.rollback()
#         traceback.print_exc()
#         raise
#
#
# def vrouter_get_by_device(device_id):
#     query = sa_api.model_query(models.VneVRouter).\
#         filter_by(device_id=device_id).\
#         first()
#     if not query:
#         raise exception.VrouterNotFoundByDevice(device_id=device_id)
#     return query
#
#
# def vlink_get_by_port_ids(port_ids):
#     query = sa_api.model_query(models.VneVlink, read_deleted='no').\
#             filter(or_(models.VneVlink.src_port_id.in_(port_ids),
#                        models.VneVlink.dst_port_id.in_(port_ids))).\
#             all()
#     vlinks = {}
#     if query:
#         for q in query:
#             if q.src_port_id in port_ids:
#                 if not vlinks.has_key(q.src_port_id):
#                     vlinks[q.src_port_id] = []
#                     vlinks[q.src_port_id].append(q)
#                 else:
#                     vlinks[q.src_port_id].append(q)
#             if q.dst_port_id in port_ids:
#                 if not vlinks.has_key(q.dst_port_id):
#                     vlinks[q.dst_port_id] = []
#                     vlinks[q.dst_port_id].append(q)
#                 else:
#                     vlinks[q.dst_port_id].append(q)
#
#     # query = sa_api.model_query(CloudSubnet, read_deleted="no").\
#     #     join((models.VneSubnet,
#     #           and_(models.VneSubnet.cloud_subnet_id == CloudSubnet.id,
#     #                models.VneSubnet.deleted == False))).\
#     #     join((CloudSubnetPort,
#     #           and_(CloudSubnetPort.subnet_id == CloudSubnet.id,
#     #                CloudSubnetPort.deleted == False))).\
#     #     filter(CloudSubnet.id == subnet_id).\
#     #     filter(CloudRouter.attach_ext == False).\
#     #     all()
#
#     print "vlinks:*********************************************", vlinks
#     return vlinks