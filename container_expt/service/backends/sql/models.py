""" Models for Experiemnt """

from oslo_utils import timeutils
from sqlalchemy import (Table, Column, Index, Integer, BigInteger, Enum, String,
                        MetaData, schema, Unicode)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import orm
from sqlalchemy import ForeignKey, DateTime, Boolean, Text, Float, SmallInteger
from terra.db.sqlalchemy.models import BASE, TerraBase
from terra.common.constants import EXPT_STATE_DIC, EXPT_OPERATE_DIC,\
    EXPT_APPLY_STATE_DIC, EXPT_INVITE_STATE_DIC
from terra.topology.backends.sql.models import CloudTopo
from terra.common import vlink_states, vlink_operates
# from terra.experiment.backends.sql import models as expt_models
from terra.topology.backends.sql import models as topo_models
from terra.vm.backends.sql import models as vm_models


# class VneVlink(BASE, TerraBase):
#     __tablename__ = 'vne_vlink'
#     __table_args__ = ()
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     name = Column(String(64), nullable=False)
#     topo_id = Column(Integer, ForeignKey('cloud_topo.id'), nullable=False)
#     topo = orm.relationship(topo_models.CloudTopo,
#                             backref=orm.backref('vlinks'),
#                             foreign_keys=topo_id,
#                             primaryjoin=topo_id == topo_models.CloudTopo.id)
#
#     src_port_id = Column(Integer, nullable=False)
#     dst_port_id = Column(Integer, nullable=False)
#     cloud_network_id = Column(Integer, ForeignKey('cloud_network.id'), nullable=False)
#     cloud_network = orm.relationship(topo_models.CloudNetwork,
#                             backref=orm.backref('vlinks'),
#                             foreign_keys=cloud_network_id,
#                             primaryjoin=cloud_network_id == topo_models.CloudNetwork.id)
#     bandwidth = Column(Integer)
#     admin_state = Column(Enum('UP', 'DOWN'), nullable=False, server_default='UP')
#     state = Column(String(36), nullable=False, default=vlink_states.BUILDING)
#     operate = Column(String(36), nullable=False, default=vlink_operates.BUILDING)
#     extra = Column(Text)
#
#
# class VneSubnet(BASE, TerraBase):
#     __tablename__ = 'vne_subnet'
#     __table_args__ = ()
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     topo_id = Column(Integer, ForeignKey('cloud_topo.id'), nullable=False)
#     topo = orm.relationship(topo_models.CloudTopo,
#                             backref=orm.backref('subnets'),
#                             foreign_keys=topo_id,
#                             primaryjoin=topo_id == topo_models.CloudTopo.id)
#
#     cloud_subnet_id = Column(Integer, ForeignKey('cloud_subnet.id'), nullable=False)
#     cloud_subnet = orm.relationship(topo_models.CloudSubnet,
#                             backref=orm.backref('l3_subnets'),
#                             foreign_keys=cloud_subnet_id,
#                             primaryjoin=cloud_subnet_id == topo_models.CloudSubnet.id)
#
# #     name = Column(String(64), nullable=False)
# #     alias = Column(String(50), nullable=False)
# #     fixed_ips = Column(String(20), nullable=False)
# #     enable_dhcp = Column(Boolean, nullable=False, default=True)
# #     host_routes = Column(Text, default='[]')
# #     failure_info = Column(String(256), nullable=True)
# #     has_recycle = Column(Boolean, nullable=False, default=False)
# #     state = Column(String(255), nullable=False, default=subnet_states.BUILDING)
# #     operate = Column(String(36), nullable=False, default=subnet_operates.BUILDING)
# #     extra = Column(String(256), nullable=True)
#
#
# class VneVController(BASE, TerraBase):
#     __tablename__ = 'vne_vcontroller'
#     __table_args__ = (
#         Index('vne_vcontroller_device_id_idx', 'device_id'),
#     )
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#
#     device_id = Column(Integer, ForeignKey('cloud_device.id'), nullable=False)
#     device = orm.relationship(topo_models.CloudDevice,
#                               backref=orm.backref('vcontrollers'),
#                               foreign_keys=device_id,
#                               primaryjoin=device_id == topo_models.CloudDevice.id)
#     type = Column(String(36), nullable=False)
#     generate_type = Column(SmallInteger, nullable=False, default=0)
#     ipaddr = Column(String(20))
#     port = Column(Integer, nullable=False)
#     has_created_backend = Column(Boolean, nullable=False, default=False)
#     cover_id = Column(SmallInteger, nullable=False)
#     extra = Column(Text)
#
#
# class VneVSwitch(BASE, TerraBase):
#     __tablename__ = 'vne_vswitch'
#     __table_args__ = (
#         Index('vne_vswitch_device_id_idx', 'device_id'),
#     )
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     dpid = Column(String(128), nullable=True)
#
#     device_id = Column(Integer, ForeignKey('cloud_device.id'), nullable=False)
#     device = orm.relationship(topo_models.CloudDevice,
#                               backref=orm.backref('vswitchs'),
#                               foreign_keys=device_id,
#                               primaryjoin=device_id == topo_models.CloudDevice.id)
#
#     type = Column(SmallInteger, nullable=False, default=0)
#     ipaddr = Column(String(20))
#     port_num = Column(Integer, nullable=True)
#     extra = Column(Text)
#
#
# class VneVHost(BASE, TerraBase):
#     __tablename__ = 'vne_vhost'
#     __table_args__ = (
#         Index('vne_vhost_device_id_idx', 'device_id'),
#     )
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#
#     device_id = Column(Integer, ForeignKey('cloud_device.id'), nullable=False)
#     vm = orm.relationship(topo_models.CloudDevice,
#                           backref=orm.backref('vhosts'),
#                           foreign_keys=device_id,
#                           primaryjoin=device_id == topo_models.CloudDevice.id)
#
#
#     type = Column(Enum('SDN','Mininet'), nullable=False, server_default='SDN')
#     extra = Column(Text)
#
#
# class VneVGateway(BASE, TerraBase):
#     __tablename__ = 'vne_vgateway'
#     __table_args__ = (
#         Index('vne_vgateway_device_id_idx', 'device_id'),
#     )
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#
#     device_id = Column(Integer, ForeignKey('cloud_device.id'), nullable=False)
#     vm = orm.relationship(topo_models.CloudDevice,
#                           backref=orm.backref('vgateways'),
#                           foreign_keys=device_id,
#                           primaryjoin=device_id == topo_models.CloudDevice.id)
#
#     has_dhcp = Column(Boolean, nullable=False, default=False)
#     extra = Column(Text)
#
#
# class VneVRouter(BASE, TerraBase):
#     __tablename__ = 'vne_vrouter'
#     __table_args__ = (
#         Index('vne_vrouter_device_id_idx', 'device_id'),
#     )
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#
#     device_id = Column(Integer, ForeignKey('cloud_device.id'), nullable=False)
#     device = orm.relationship(topo_models.CloudDevice,
#                               backref=orm.backref('vnerouters'),
#                               foreign_keys=device_id,
#                               primaryjoin=device_id == topo_models.CloudDevice.id)
#
#     is_extnet_router = Column(Boolean, nullable=False, default=False)
#
#
# class VneTempNetwork(BASE, TerraBase):
#     __tablename__ = 'vne_temp_network'
#     __table_args__ = ()
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     port_id = Column(Integer, ForeignKey('cloud_port.id'), nullable=False)
#     port = orm.relationship(topo_models.CloudPort,
#                               backref=orm.backref('temp_network'),
#                               foreign_keys=port_id,
#                               primaryjoin=port_id == topo_models.CloudPort.id)
#     network_id = Column(Integer, ForeignKey('cloud_network.id'),  nullable=False)
#     network = orm.relationship(topo_models.CloudNetwork,
#                               backref=orm.backref('temp_network'),
#                               foreign_keys=network_id,
#                               primaryjoin=network_id == topo_models.CloudNetwork.id)
#
# class VneOptv10PortMapping(BASE, TerraBase):
#     __tablename__ = 'vneoptv10_port_mapping'
#     __table_args__ = ()
#
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     mapping_port_id = Column(Integer, ForeignKey('cloud_port.id'), nullable=False)
#     port = orm.relationship(topo_models.CloudPort,
#                             backref=orm.backref('vneoptv10_port_mapping'),
#                             foreign_keys=mapping_port_id,
#                             primaryjoin=mapping_port_id == topo_models.CloudPort.id)
#     real_port_id = Column(Integer, ForeignKey('cloud_port.id'), nullable=False)
#     cloud_subnet_id = Column(Integer, ForeignKey('cloud_subnet.id'), nullable=False)