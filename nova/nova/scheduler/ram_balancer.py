# Copyright (c) 2011 OpenStack Foundation
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
RAM balancing scheduler:
Tries to balance out RAM usage across multiple hosts
"""

import random

from oslo.config import cfg

from nova.compute import instance_types
from nova import exception
from nova.openstack.common import log as logging
from nova.openstack.common.notifier import api as notifier
from nova.scheduler import driver
from nova.scheduler import scheduler_options

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


filter_scheduler_opts = [
    cfg.IntOpt('scheduler_host_subset_size',
               default=1,
               help='New instances will be scheduled on a host chosen '
                    'randomly from a subset of the N best hosts. This '
                    'property defines the subset size that a host is '
                    'chosen from. A value of 1 chooses the '
                    'first host returned by the weighing functions. '
                    'This value must be at least 1. Any value less than 1 '
                    'will be ignored, and 1 will be used instead')
]

CONF.register_opts(filter_scheduler_opts)


class FilterScheduler(driver.Scheduler):
    """Scheduler that can be used for filtering and weighing."""
    def __init__(self, *args, **kwargs):
        super(FilterScheduler, self).__init__(*args, **kwargs)
        self.cost_function_cache = None
        self.options = scheduler_options.SchedulerOptions()

    def schedule_run_instance(self, context, request_spec,
                              admin_password, injected_files,
                              requested_networks, is_first_time,
                              filter_properties):
        """This method is called from nova.compute.api to provision
        an instance.  We first create a build plan (a list of WeightedHosts)
        and then provision.

        Returns a list of the instances created.
        """
        payload = dict(request_spec=request_spec)
        notifier.notify(context, notifier.publisher_id("scheduler"),
                        'scheduler.run_instance.start', notifier.INFO, payload)

        instance_uuids = request_spec.pop('instance_uuids')
        num_instances = len(instance_uuids)
        LOG.debug(_("Attempting to build %(num_instances)d instance(s)") %
                locals())

        weighed_hosts = self._schedule(context, request_spec,
                filter_properties, instance_uuids)

        # NOTE(comstud): Make sure we do not pass this through.  It
        # contains an instance of RpcContext that cannot be serialized.
        filter_properties.pop('context', None)

        for num, instance_uuid in enumerate(instance_uuids):
            request_spec['instance_properties']['launch_index'] = num

            try:
                try:
                    weighed_host = weighed_hosts.pop(0)
                except IndexError:
                    raise exception.NoValidHost(reason="")

                self._provision_resource(context, weighed_host,
                                         request_spec,
                                         filter_properties,
                                         requested_networks,
                                         injected_files, admin_password,
                                         is_first_time,
                                         instance_uuid=instance_uuid)
            except Exception as ex:
                # NOTE(vish): we don't reraise the exception here to make sure
                #             that all instances in the request get set to
                #             error properly
                driver.handle_schedule_error(context, ex, instance_uuid,
                                             request_spec)
            # scrub retry host list in case we're scheduling multiple
            # instances:
            retry = filter_properties.get('retry', {})
            retry['hosts'] = []

        notifier.notify(context, notifier.publisher_id("scheduler"),
                        'scheduler.run_instance.end', notifier.INFO, payload)

    def schedule_prep_resize(self, context, image, request_spec,
                             filter_properties, instance, instance_type,
                             reservations):
        """Select a target for resize.

        Selects a target host for the instance, post-resize, and casts
        the prep_resize operation to it.
        """

        weighed_hosts = self._schedule(context, request_spec,
                filter_properties, [instance['uuid']])
        if not weighed_hosts:
            raise exception.NoValidHost(reason="")
        weighed_host = weighed_hosts.pop(0)

        self._post_select_populate_filter_properties(filter_properties,
                weighed_host.obj)

        # context is not serializable
        filter_properties.pop('context', None)

        # Forward off to the host
        self.compute_rpcapi.prep_resize(context, image, instance,
                instance_type, weighed_host.obj.host, reservations,
                request_spec=request_spec, filter_properties=filter_properties,
                node=weighed_host.obj.nodename)

    def select_hosts(self, context, request_spec, filter_properties):
        """Selects a filtered set of hosts."""
        instance_uuids = request_spec.get('instance_uuids')
        hosts = [host.obj.host for host in self._schedule(context,
            request_spec, filter_properties, instance_uuids)]
        if not hosts:
            raise exception.NoValidHost(reason="")
        return hosts

    def _provision_resource(self, context, weighed_host, request_spec,
            filter_properties, requested_networks, injected_files,
            admin_password, is_first_time, instance_uuid=None):
        """Create the requested resource in this Zone."""
        # NOTE(vish): add our current instance back into the request spec
        request_spec['instance_uuids'] = [instance_uuid]
        payload = dict(request_spec=request_spec,
                       weighted_host=weighed_host.to_dict(),
                       instance_id=instance_uuid)
        notifier.notify(context, notifier.publisher_id("scheduler"),
                        'scheduler.run_instance.scheduled', notifier.INFO,
                        payload)

        # Update the metadata if necessary
        scheduler_hints = filter_properties.get('scheduler_hints') or {}
        group = scheduler_hints.get('group', None)
        values = None
        if group:
            values = request_spec['instance_properties']['system_metadata']
            values.update({'group': group})
            values = {'system_metadata': values}

        updated_instance = driver.instance_update_db(context,
                instance_uuid, extra_values=values)

        self._post_select_populate_filter_properties(filter_properties,
                weighed_host.obj)

        self.compute_rpcapi.run_instance(context, instance=updated_instance,
                host=weighed_host.obj.host,
                request_spec=request_spec, filter_properties=filter_properties,
                requested_networks=requested_networks,
                injected_files=injected_files,
                admin_password=admin_password, is_first_time=is_first_time,
                node=weighed_host.obj.nodename)

    def _post_select_populate_filter_properties(self, filter_properties,
            host_state):
        """Add additional information to the filter properties after a node has
        been selected by the scheduling process.
        """
        # Add a retry entry for the selected compute host and node:
        self._add_retry_host(filter_properties, host_state.host,
                             host_state.nodename)

        self._add_oversubscription_policy(filter_properties, host_state)

    def _add_retry_host(self, filter_properties, host, node):
        """Add a retry entry for the selected compute node. In the event that
        the request gets re-scheduled, this entry will signal that the given
        node has already been tried.
        """
        retry = filter_properties.get('retry', None)
        if not retry:
            return
        hosts = retry['hosts']
        hosts.append([host, node])

    def _add_oversubscription_policy(self, filter_properties, host_state):
        filter_properties['limits'] = host_state.limits

    def _get_configuration_options(self):
        """Fetch options dictionary. Broken out for testing."""
        return self.options.get_configuration()

    def populate_filter_properties(self, request_spec, filter_properties):
        """Stuff things into filter_properties.  Can be overridden in a
        subclass to add more data.
        """
        # Save useful information from the request spec for filter processing:
        project_id = request_spec['instance_properties']['project_id']
        os_type = request_spec['instance_properties']['os_type']
        filter_properties['project_id'] = project_id
        filter_properties['os_type'] = os_type

    def _max_attempts(self):
        max_attempts = CONF.scheduler_max_attempts
        if max_attempts < 1:
            raise exception.NovaException(_("Invalid value for "
                "'scheduler_max_attempts', must be >= 1"))
        return max_attempts

    def _log_compute_error(self, instance_uuid, retry):
        """If the request contained an exception from a previous compute
        build/resize operation, log it to aid debugging
        """
        exc = retry.pop('exc', None)  # string-ified exception from compute
        if not exc:
            return  # no exception info from a prevous attempt, skip

        hosts = retry.get('hosts', None)
        if not hosts:
            return  # no previously attempted hosts, skip

        last_host, last_node = hosts[-1]
        msg = _("Error from last host: %(last_host)s (node %(last_node)s): "
                "%(exc)s") % locals()
        LOG.error(msg, instance_uuid=instance_uuid)

    def _populate_retry(self, filter_properties, instance_properties):
        """Populate filter properties with history of retries for this
        request. If maximum retries is exceeded, raise NoValidHost.
        """
        max_attempts = self._max_attempts()
        retry = filter_properties.pop('retry', {})

        if max_attempts == 1:
            # re-scheduling is disabled.
            return

        # retry is enabled, update attempt count:
        if retry:
            retry['num_attempts'] += 1
        else:
            retry = {
                'num_attempts': 1,
                'hosts': []  # list of compute hosts tried
            }
        filter_properties['retry'] = retry

        instance_uuid = instance_properties.get('uuid')
        self._log_compute_error(instance_uuid, retry)

        if retry['num_attempts'] > max_attempts:
            msg = _("Exceeded max scheduling attempts %(max_attempts)d for "
                    "instance %(instance_uuid)s") % locals()
            raise exception.NoValidHost(reason=msg)

    def _schedule(self, context, request_spec, filter_properties,
                  instance_uuids=None):
        """Returns a list of hosts that meet the required specs,
        ordered by their fitness.
        """
	print "\t\tRequest spec : ", request_spec
	print "\t\tFilter Props : ", filter_properties

        elevated = context.elevated()
        instance_properties = request_spec['instance_properties']
        instance_type = request_spec.get("instance_type", None)

        # Get the group
        update_group_hosts = False
        scheduler_hints = filter_properties.get('scheduler_hints') or {}
        group = scheduler_hints.get('group', None)
        if group:
            group_hosts = self.group_hosts(elevated, group)
            update_group_hosts = True
            if 'group_hosts' not in filter_properties:
                filter_properties.update({'group_hosts': []})
            configured_hosts = filter_properties['group_hosts']
            filter_properties['group_hosts'] = configured_hosts + group_hosts

        config_options = self._get_configuration_options()

        # check retry policy.  Rather ugly use of instance_uuids[0]...
        # but if we've exceeded max retries... then we really only
        # have a single instance.
        properties = instance_properties.copy()
        if instance_uuids:
            properties['uuid'] = instance_uuids[0]
        self._populate_retry(filter_properties, properties)

        filter_properties.update({'context': context,
                                  'request_spec': request_spec,
                                  'config_options': config_options,
                                  'instance_type': instance_type})

        self.populate_filter_properties(request_spec,
                                        filter_properties)

        # Find our local list of acceptable hosts by repeatedly
        # filtering and weighing our options. Each time we choose a
        # host, we virtually consume resources on it so subsequent
        # selections can adjust accordingly.

        # Note: remember, we are using an iterator here. So only
        # traverse this list once. This can bite you if the hosts
        # are being scanned in a filter or weighing function.
        hosts = self.host_manager.get_all_host_states(elevated)
        selected_hosts = []
        if instance_uuids:
            num_instances = len(instance_uuids)
        else:
            num_instances = request_spec.get('num_instances', 1)

	print "Instance props : ", request_spec['instance_properties']

        for num in xrange(num_instances):
            # Filter local hosts based on requirements ...
            hosts = self.host_manager.get_filtered_hosts(hosts,
                    filter_properties)
            if not hosts:
                # Can't get any more locally.
                break

            LOG.debug(_("Filtered %(hosts)s") % locals())

            available_hosts = self.host_manager.get_weighed_hosts(hosts,
                    filter_properties)

            print "\t\tAvailable hosts : ", available_hosts
            state_iter = self.host_manager.get_all_host_states(elevated)
            current_state_dict = {}
            try:
                while True:
                    host_obj = next(state_iter)
                    print "Host state object : ", host_obj
                    current_state_dict[host_obj.host] = host_obj.free_ram_mb
            except StopIteration:
                pass

            # Now we have following objects : 
            # 1. a dictionary current_state_dict of (hostname:free_ram)
            # 2. a list weighed_hosts of WeighedHost objects
            # We have to choose a host with maximum available memory
            maxval = 0
            maxindex = -1
            i = 0
            requested_ram = instance_type['memory_mb']
            for whost in available_hosts:
                print "\t\tWHOST : ", whost
                hostname = whost.obj.host
                available_mem = current_state_dict[hostname]
                if (available_mem >= maxval):
                    maxval = available_mem
                    maxindex = i
                i += 1

            if (maxindex >= 0) and (maxval >= requested_ram) :
                weighed_hosts = [ available_hosts[maxindex] ]
            else:
                weighed_hosts = []

            print "\t\tWeighed hosts updated : ", weighed_hosts

            scheduler_host_subset_size = CONF.scheduler_host_subset_size
            if scheduler_host_subset_size > len(weighed_hosts):
                scheduler_host_subset_size = len(weighed_hosts)
            if scheduler_host_subset_size < 1:
                scheduler_host_subset_size = 1

            try:
                chosen_host = random.choice(
                    weighed_hosts[0:scheduler_host_subset_size])
                LOG.debug(_("Choosing host %(chosen_host)s") % locals())
                selected_hosts.append(chosen_host)

                # Now consume the resources so the filter/weights
                # will change for the next instance.
                chosen_host.obj.consume_from_instance(instance_properties)
                if update_group_hosts is True:
                    filter_properties['group_hosts'].append(chosen_host.obj.host)
            except IndexError:
                print "\t\tERROR : No physical machine currently available. Please try after some time\n";
        return selected_hosts

    def _assert_compute_node_has_enough_memory(self, context,
                                              instance_ref, dest):
        """Checks if destination host has enough memory for live migration.


        :param context: security context
        :param instance_ref: nova.db.sqlalchemy.models.Instance object
        :param dest: destination host

        """
        compute = self._get_compute_info(context, dest)
        node = compute.get('hypervisor_hostname')
        host_state = self.host_manager.host_state_cls(dest, node)
        host_state.update_from_compute_node(compute)

        instance_type = instance_types.extract_instance_type(instance_ref)
        filter_properties = {'instance_type': instance_type}

        hosts = self.host_manager.get_filtered_hosts([host_state],
                                                     filter_properties,
                                                     'RamFilter')
        if not hosts:
            instance_uuid = instance_ref['uuid']
            reason = _("Unable to migrate %(instance_uuid)s to %(dest)s: "
                       "Lack of memory")
            raise exception.MigrationError(reason=reason % locals())
