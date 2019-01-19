# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Piston Cloud Computing, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
SQLAlchemy models for nova data.
"""

from oslo_config import cfg
from oslo_db.sqlalchemy import models
from oslo_serialization import jsonutils
from oslo_utils import timeutils
import six
from sqlalchemy import (Table, Column, Index, Integer, BigInteger, Enum, String,
                         MetaData, schema, Unicode)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import orm
from sqlalchemy import ForeignKey, DateTime, Boolean, \
            Text, Float, TypeDecorator, SmallInteger
from terra.common import vm_states, vm_operates
import terra.common.constants as CONSTANT


CONF = cfg.CONF
BASE = declarative_base()


def MediumText():
    return Text().with_variant(MEDIUMTEXT(), 'mysql')


class JsonBlob(TypeDecorator):

    impl = Text

    def process_bind_param(self, value, dialect):
        return jsonutils.dumps(value)

    def process_result_value(self, value, dialect):
        return jsonutils.loads(value)


class SoftDeleteMixin(object):
    deleted_at = Column(DateTime)
    deleted = Column(Boolean, default=False)

    def soft_delete(self, session):
        """Mark this object as deleted."""
        self.deleted = True
        self.deleted_at = timeutils.utcnow()
        self.save(session=session)


class TerraBase(SoftDeleteMixin,
                models.TimestampMixin,
                models.ModelBase):
    metadata = None
    attributes = []

    def __copy__(self):
        """Implement a safe copy.copy().

        SQLAlchemy-mapped objects travel with an object
        called an InstanceState, which is pegged to that object
        specifically and tracks everything about that object.  It's
        critical within all attribute operations, including gets
        and deferred loading.   This object definitely cannot be
        shared among two instances, and must be handled.

        The copy routine here makes use of session.merge() which
        already essentially implements a "copy" style of operation,
        which produces a new instance with a new InstanceState and copies
        all the data along mapped attributes without using any SQL.

        The mode we are using here has the caveat that the given object
        must be "clean", e.g. that it has no database-loaded state
        that has been updated and not flushed.   This is a good thing,
        as creating a copy of an object including non-flushed, pending
        database state is probably not a good idea; neither represents
        what the actual row looks like, and only one should be flushed.

        """
        session = orm.Session()

        copy = session.merge(self, load=False)
        session.expunge(copy)
        return copy

    def save(self, session=None):
        from terra.db.sqlalchemy import api

        if session is None:
            session = api.get_session()

        super(TerraBase, self).save(session=session)

    @classmethod
    def from_dict(cls, d):
        """Returns a model instance from a dictionary."""
        return cls(**d)

    def to_dict(self):
        """Returns the model's attributes as a dictionary."""
        d = dict()
        for c in self.__table__.columns:
            d[c.name] = getattr(self, c.name)
        return d

    def __getitem__(self, item):
        if item in self.extra:
            return self.extra[item]
        return getattr(self, item)


class DictBase(TerraBase):
    @classmethod
    def from_dict(cls, d):
        """Returns a model instance from a dictionary."""
        new_d = d.copy()

        col_names = [c.name for c in cls.__table__.columns]
        new_d['extra'] = {k: new_d.pop(k) for k in six.iterkeys(d)
                          if k not in col_names and k != 'extra'}
        return cls(**new_d)

    def to_dict(self, include_extra_dict=False):
        """Returns the model's attributes as a dictionary."""
        d = self.extra.copy()
        for c in self.__table__.columns:
            if c.name != 'extra':
                d[c.name] = getattr(self, c.name)

        if include_extra_dict:
            d['extra'] = self.extra.copy()

        return d