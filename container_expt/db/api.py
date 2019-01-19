"""Implementation of SQLAlchemy backend."""

import functools
import sys
import threading
import time
from terra.common import driver_hints, utils

from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_db import options as oslo_db_options
from oslo_db.sqlalchemy import session as db_session
from oslo_db.sqlalchemy import utils as sqlalchemyutils
from oslo_log import log as logging
from oslo_utils import timeutils
import six
from sqlalchemy import or_
from sqlalchemy import Boolean

from terra import exception
from terra.i18n import _

CONF = cfg.CONF
CONF.register_opts(oslo_db_options.database_opts, 'database')

LOG = logging.getLogger(__name__)


_LOCK = threading.Lock()
_FACADE = None


def _create_facade_lazily():
    global _LOCK
    with _LOCK:
        global _FACADE
        if _FACADE is None:
            _FACADE = db_session.EngineFacade(
                CONF.database.connection,
                **dict(CONF.database)
            )

        return _FACADE


def get_engine():
    facade = _create_facade_lazily()
    return facade.get_engine()


def get_session(**kwargs):
    facade = _create_facade_lazily()
    return facade.get_session(**kwargs)


def dispose_engine():
    get_engine().dispose()


def get_backend():
    """The backend is this module itself."""
    return sys.modules[__name__]


def _retry_on_deadlock(f):
    """Decorator to retry a DB API call if Deadlock was received."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        while True:
            try:
                return f(*args, **kwargs)
            except db_exc.DBDeadlock:
                LOG.warning("Deadlock detected when running "
                                "'%(func_name)s': Retrying...",
                            dict(func_name=f.__name__))
                # Retry!
                time.sleep(0.5)
                continue
    functools.update_wrapper(wrapped, f)
    return wrapped


def model_query(model,
                args=None,
                session=None,
                use_slave=False,
                read_deleted=None):
    """Query helper that accounts for context's `read_deleted` field.

    :param model:       Model to query. Must be a subclass of ModelBase.
    :param args:        Arguments to query. If None - model is used.
    :param session:     If present, the session to use.
    :param use_slave:   If true, use a slave connection to the DB if creating a
                        session.
    :param read_deleted: If not None, overrides context's read_deleted field.
                        Permitted values are 'no', which does not return
                        deleted values; 'only', which only returns deleted
                        values; and 'yes', which does not filter deleted
                        values.
    """

    if session is None:
        if CONF.database.slave_connection == '':
            use_slave = False
        session = get_session(use_slave=use_slave)
    
    if read_deleted is None:
        read_deleted = 'no'

    query_kwargs = {}
    if 'no' == read_deleted:
        query_kwargs['deleted'] = False
    elif 'only' == read_deleted:
        query_kwargs['deleted'] = True
    elif 'yes' == read_deleted:
        pass
    else:
        raise ValueError("Unrecognized read_deleted value '%s'"
                           % read_deleted)

    query = sqlalchemyutils.model_query(model, session, args, **query_kwargs)

    return query


def convert_objects_related_datetimes(values, *datetime_keys):
    for key in datetime_keys:
        if key in values and values[key]:
            if isinstance(values[key], six.string_types):
                values[key] = timeutils.parse_strtime(values[key])
            # NOTE(danms): Strip UTC timezones from datetimes, since they're
            # stored that way in the database
            values[key] = values[key].replace(tzinfo=None)
    return values


def process_sort_params(sort_keys, sort_dirs,
                        default_keys=['created_at', 'id'],
                        default_dir='asc'):
    """Process the sort parameters to include default keys.

    Creates a list of sort keys and a list of sort directions. Adds the default
    keys to the end of the list if they are not already included.

    When adding the default keys to the sort keys list, the associated
    direction is:
    1) The first element in the 'sort_dirs' list (if specified), else
    2) 'default_dir' value (Note that 'asc' is the default value since this is
    the default in sqlalchemy.utils.paginate_query)

    :param sort_keys: List of sort keys to include in the processed list
    :param sort_dirs: List of sort directions to include in the processed list
    :param default_keys: List of sort keys that need to be included in the
                         processed list, they are added at the end of the list
                         if not already specified.
    :param default_dir: Sort direction associated with each of the default
                        keys that are not supplied, used when they are added
                        to the processed list
    :returns: list of sort keys, list of sort directions
    :raise exception.InvalidInput: If more sort directions than sort keys
                                   are specified or if an invalid sort
                                   direction is specified
    """
    # Determine direction to use for when adding default keys
    if sort_dirs and len(sort_dirs) != 0:
        default_dir_value = sort_dirs[0]
    else:
        default_dir_value = default_dir

    # Create list of keys (do not modify the input list)
    if sort_keys:
        result_keys = list(sort_keys)
    else:
        result_keys = []

    # If a list of directions is not provided, use the default sort direction
    # for all provided keys
    if sort_dirs:
        result_dirs = []
        # Verify sort direction
        for sort_dir in sort_dirs:
            if sort_dir not in ('asc', 'desc'):
                msg = _("Unknown sort direction, must be 'desc' or 'asc'")
                raise exception.InvalidInput(reason=msg)
            result_dirs.append(sort_dir)
    else:
        result_dirs = [default_dir_value for _sort_key in result_keys]

    # Ensure that the key and direction length match
    while len(result_dirs) < len(result_keys):
        result_dirs.append(default_dir_value)
    # Unless more direction are specified, which is an error
    if len(result_dirs) > len(result_keys):
        msg = _("Sort direction size exceeds sort key size")
        raise exception.InvalidInput(reason=msg)

    # Ensure defaults are included
    for key in default_keys:
        if key not in result_keys:
            result_keys.append(key)
            result_dirs.append(default_dir_value)

    return result_keys, result_dirs


def _get_regexp_op_for_connection(db_connection):
    db_string = db_connection.split(':')[0].split('+')[0]
    regexp_op_map = {
        'postgresql': '~',
        'mysql': 'REGEXP',
        'sqlite': 'REGEXP'
    }
    return regexp_op_map.get(db_string, 'LIKE')


def exact_model_filter(query, model, filters, legal_keys):
    """Applies exact match filtering to a query.

    Returns the updated query.  Modifies filters argument to remove
    filters consumed.

    :param query: query to apply filters to
    :param model: a sqlalchemy model class
    :param filters: dictionary of filters; values that are lists,
                    tuples, sets, or frozensets cause an 'IN' test to
                    be performed, while exact matching ('==' operator)
                    is used for other values
    :param legal_keys: list of keys to apply exact filtering to
    """

    filter_dict = {}

    # Walk through all the keys
    for key in legal_keys:
        # Skip ones we're not filtering on
        if key not in filters:
            continue

        # OK, filtering on this key; what value do we search for?
        value = filters.pop(key)

        if isinstance(value, (list, tuple, set, frozenset)):
            # Looking for values in a list; apply to query directly
            column_attr = getattr(model, key)
            query = query.filter(column_attr.in_(value))
        else:
            # OK, simple exact match; save for later
            filter_dict[key] = value

    # Apply simple exact matches
    if filter_dict:
        query = query.filter_by(**filter_dict)

    return query


def regex_model_filter(query, model, filters):
    """Applies regular expression filtering to an query.

    Returns the updated query.

    :param query: query to apply filters to
    :param model: a sqlalchemy model class
    :param filters: dictionary of filters with regex values
    """

    db_regexp_op = _get_regexp_op_for_connection(CONF.database.connection)
    for filter_name in filters.iterkeys():
        try:
            column_attr = getattr(model, filter_name)
        except AttributeError:
            continue
        if 'property' == type(column_attr).__name__:
            continue
        if db_regexp_op == 'LIKE':
            query = query.filter(column_attr.op(db_regexp_op)(
                '%%s%') % filters[filter_name])
        else:
            query = query.filter(column_attr.op(db_regexp_op)(
                '%s' % filters[filter_name]))
    return query


def constraint(**conditions):
    return Constraint(conditions)


def equal_any(*values):
    return EqualityCondition(values)


def not_equal(*values):
    return InequalityCondition(values)


class Constraint(object):

    def __init__(self, conditions):
        self.conditions = conditions

    def apply(self, model, query):
        for key, condition in self.conditions.iteritems():
            for clause in condition.clauses(getattr(model, key)):
                query = query.filter(clause)
        return query


class EqualityCondition(object):

    def __init__(self, values):
        self.values = values

    def clauses(self, field):
        # method signature requires us to return an iterable even if for OR
        # operator this will actually be a single clause
        return [or_(*[field == value for value in self.values])]


class InequalityCondition(object):

    def __init__(self, values):
        self.values = values

    def clauses(self, field):
        return [field != value for value in self.values]


def truncated(f):
    return driver_hints.truncated(f)


class _WontMatch(Exception):
    """Raised to indicate that the filter won't match.

    This is raised to short-circuit the computation of the filter as soon as
    it's discovered that the filter requested isn't going to match anything.

    A filter isn't going to match anything if the value is too long for the
    field, for example.

    """

    @classmethod
    def check(cls, value, col_attr):
        """Check if the value can match given the column attributes.

        Raises this class if the value provided can't match any value in the
        column in the table given the column's attributes. For example, if the
        column is a string and the value is longer than the column then it
        won't match any value in the column in the table.

        """
        col = col_attr.property.columns[0]
        if isinstance(col.type, Boolean):
            # The column is a Boolean, we should have already validated input.
            return
        if not hasattr(col.type, 'length'):
            # The column doesn't have a length so can't validate anymore.
            return
        if len(value) > col.type.length:
            raise cls()
            # Otherwise the value could match a value in the column.


def _filter(model, query, hints):
    """Applies filtering to a query.

    :param model: the table model in question
    :param query: query to apply filters to
    :param hints: contains the list of filters yet to be satisfied.
                  Any filters satisfied here will be removed so that
                  the caller will know if any filters remain.

    :returns query: query, updated with any filters satisfied

    """
    def inexact_filter(model, query, filter_, satisfied_filters):
        """Applies an inexact filter to a query.

        :param model: the table model in question
        :param query: query to apply filters to
        :param dict filter_: describes this filter
        :param list satisfied_filters: filter_ will be added if it is
                                       satisfied.

        :returns query: query updated to add any inexact filters we could
                        satisfy

        """
        column_attr = getattr(model, filter_['name'])

        # TODO(henry-nash): Sqlalchemy 0.7 defaults to case insensitivity
        # so once we find a way of changing that (maybe on a call-by-call
        # basis), we can add support for the case sensitive versions of
        # the filters below.  For now, these case sensitive versions will
        # be handled at the controller level.

        if filter_['case_sensitive']:
            return query

        if filter_['comparator'] == 'contains':
            _WontMatch.check(filter_['value'], column_attr)
            query_term = column_attr.ilike('%%%s%%' % filter_['value'])
        elif filter_['comparator'] == 'startswith':
            _WontMatch.check(filter_['value'], column_attr)
            query_term = column_attr.ilike('%s%%' % filter_['value'])
        elif filter_['comparator'] == 'endswith':
            _WontMatch.check(filter_['value'], column_attr)
            query_term = column_attr.ilike('%%%s' % filter_['value'])
        else:
            # It's a filter we don't understand, so let the caller
            # work out if they need to do something with it.
            return query

        satisfied_filters.append(filter_)
        return query.filter(query_term)

    def exact_filter(model, query, filter_, satisfied_filters):
        """Applies an exact filter to a query.

        :param model: the table model in question
        :param query: query to apply filters to
        :param dict filter_: describes this filter
        :param list satisfied_filters: filter_ will be added if it is
                                       satisfied.
        :returns query: query updated to add any exact filters we could
                        satisfy
        """
        key = filter_['name']

        col = getattr(model, key)
        if isinstance(col.property.columns[0].type, Boolean):
            filter_val = utils.attr_as_boolean(filter_['value'])
        else:
            _WontMatch.check(filter_['value'], col)
            filter_val = filter_['value']

        satisfied_filters.append(filter_)
        return query.filter(col == filter_val)

    try:
        satisfied_filters = []
        for filter_ in hints.filters:
            if filter_['name'] not in model.__table__.columns:
                continue
            if filter_['comparator'] == 'equals':
                query = exact_filter(model, query, filter_,
                                     satisfied_filters)
            else:
                query = inexact_filter(model, query, filter_,
                                       satisfied_filters)

        # Remove satisfied filters, then the caller will know remaining filters
        for filter_ in satisfied_filters:
            hints.filters.remove(filter_)

        return query
    except _WontMatch:
        hints.cannot_match = True
        return


def _limit(query, hints):
    """Applies a limit to a query.

    :param query: query to apply filters to
    :param hints: contains the list of filters and limit details.

    :returns: updated query

    """
    # NOTE(henry-nash): If we were to implement pagination, then we
    # we would expand this method to support pagination and limiting.

    # If we satisfied all the filters, set an upper limit if supplied
    if hints.limit:
        query = query.limit(hints.limit['limit'])
    return query


def filter_limit_query(model, query, hints):
    """Applies filtering and limit to a query.

    :param model: table model
    :param query: query to apply filters to
    :param hints: contains the list of filters and limit details.  This may
                  be None, indicating that there are no filters or limits
                  to be applied. If it's not None, then any filters
                  satisfied here will be removed so that the caller will
                  know if any filters remain.

    :returns: updated query

    """
    if hints is None:
        return query

    # First try and satisfy any filters
    query = _filter(model, query, hints)

    if hints.cannot_match:
        # Nothing's going to match, so don't bother with the query.
        return []

    # NOTE(henry-nash): Any unsatisfied filters will have been left in
    # the hints list for the controller to handle. We can only try and
    # limit here if all the filters are already satisfied since, if not,
    # doing so might mess up the final results. If there are still
    # unsatisfied filters, we have to leave any limiting to the controller
    # as well.

    if not hints.filters:
        return _limit(query, hints)
    else:
        return query