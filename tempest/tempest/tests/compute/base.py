# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack, LLC
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

import logging
import time

from tempest import clients
from tempest.common.utils.data_utils import rand_name
from tempest import exceptions
import tempest.test
from tempest.tests import compute


LOG = logging.getLogger(__name__)


class BaseComputeTest(tempest.test.BaseTestCase):
    """Base test case class for all Compute API tests."""

    conclusion = compute.generic_setup_package()

    @classmethod
    def setUpClass(cls):
        cls.isolated_creds = []

        if cls.config.compute.allow_tenant_isolation:
            creds = cls._get_isolated_creds()
            username, tenant_name, password = creds
            os = clients.Manager(username=username,
                                 password=password,
                                 tenant_name=tenant_name,
                                 interface=cls._interface)
        else:
            os = clients.Manager(interface=cls._interface)

        cls.os = os
        cls.servers_client = os.servers_client
        cls.flavors_client = os.flavors_client
        cls.images_client = os.images_client
        cls.extensions_client = os.extensions_client
        cls.floating_ips_client = os.floating_ips_client
        cls.keypairs_client = os.keypairs_client
        cls.security_groups_client = os.security_groups_client
        cls.quotas_client = os.quotas_client
        cls.limits_client = os.limits_client
        cls.volumes_extensions_client = os.volumes_extensions_client
        cls.volumes_client = os.volumes_client
        cls.interfaces_client = os.interfaces_client
        cls.build_interval = cls.config.compute.build_interval
        cls.build_timeout = cls.config.compute.build_timeout
        cls.ssh_user = cls.config.compute.ssh_user
        cls.image_ref = cls.config.compute.image_ref
        cls.image_ref_alt = cls.config.compute.image_ref_alt
        cls.flavor_ref = cls.config.compute.flavor_ref
        cls.flavor_ref_alt = cls.config.compute.flavor_ref_alt
        cls.servers = []

    @classmethod
    def _get_identity_admin_client(cls):
        """
        Returns an instance of the Identity Admin API client
        """
        os = clients.AdminManager(interface=cls._interface)
        admin_client = os.identity_client
        return admin_client

    @classmethod
    def _get_client_args(cls):

        return (
            cls.config,
            cls.config.identity.admin_username,
            cls.config.identity.admin_password,
            cls.config.identity.uri
        )

    @classmethod
    def _get_isolated_creds(cls):
        """
        Creates a new set of user/tenant/password credentials for a
        **regular** user of the Compute API so that a test case can
        operate in an isolated tenant container.
        """
        admin_client = cls._get_identity_admin_client()
        rand_name_root = rand_name(cls.__name__)
        if cls.isolated_creds:
            # Main user already created. Create the alt one...
            rand_name_root += '-alt'
        username = rand_name_root + "-user"
        email = rand_name_root + "@example.com"
        tenant_name = rand_name_root + "-tenant"
        tenant_desc = tenant_name + "-desc"
        password = "pass"

        try:
            resp, tenant = admin_client.create_tenant(name=tenant_name,
                                                      description=tenant_desc)
        except exceptions.Duplicate:
            if cls.config.compute.allow_tenant_reuse:
                tenant = admin_client.get_tenant_by_name(tenant_name)
                LOG.info('Re-using existing tenant %s' % tenant)
            else:
                msg = ('Unable to create isolated tenant %s because ' +
                       'it already exists. If this is related to a ' +
                       'previous test failure, try using ' +
                       'allow_tenant_reuse in tempest.conf') % tenant_name
                raise exceptions.Duplicate(msg)

        try:
            resp, user = admin_client.create_user(username,
                                                  password,
                                                  tenant['id'],
                                                  email)
        except exceptions.Duplicate:
            if cls.config.compute.allow_tenant_reuse:
                user = admin_client.get_user_by_username(tenant['id'],
                                                         username)
                LOG.info('Re-using existing user %s' % user)
            else:
                msg = ('Unable to create isolated user %s because ' +
                       'it already exists. If this is related to a ' +
                       'previous test failure, try using ' +
                       'allow_tenant_reuse in tempest.conf') % tenant_name
                raise exceptions.Duplicate(msg)

        # Store the complete creds (including UUID ids...) for later
        # but return just the username, tenant_name, password tuple
        # that the various clients will use.
        cls.isolated_creds.append((user, tenant))

        return username, tenant_name, password

    @classmethod
    def clear_isolated_creds(cls):
        if not cls.isolated_creds:
            return
        admin_client = cls._get_identity_admin_client()

        for user, tenant in cls.isolated_creds:
            admin_client.delete_user(user['id'])
            admin_client.delete_tenant(tenant['id'])

    @classmethod
    def clear_servers(cls):
        for server in cls.servers:
            try:
                cls.servers_client.delete_server(server['id'])
            except Exception:
                pass

        for server in cls.servers:
            try:
                cls.servers_client.wait_for_server_termination(server['id'])
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        cls.clear_servers()
        cls.clear_isolated_creds()

    @classmethod
    def create_server(cls, **kwargs):
        """Wrapper utility that returns a test server."""
        name = rand_name(cls.__name__ + "-instance")
        if 'name' in kwargs:
            name = kwargs.pop('name')
        flavor = kwargs.get('flavor', cls.flavor_ref)
        image_id = kwargs.get('image_id', cls.image_ref)

        resp, server = cls.servers_client.create_server(
            name, image_id, flavor, **kwargs)

        if 'wait_until' in kwargs:
            cls.servers_client.wait_for_server_status(
                server['id'], kwargs['wait_until'])

        cls.servers.append(server)
        return resp, server

    def wait_for(self, condition):
        """Repeatedly calls condition() until a timeout."""
        start_time = int(time.time())
        while True:
            try:
                condition()
            except Exception:
                pass
            else:
                return
            if int(time.time()) - start_time >= self.build_timeout:
                condition()
                return
            time.sleep(self.build_interval)


class BaseComputeAdminTest(BaseComputeTest):
    """Base test case class for all Compute Admin API tests."""

    @classmethod
    def setUpClass(cls):
        super(BaseComputeAdminTest, cls).setUpClass()
        admin_username = cls.config.compute_admin.username
        admin_password = cls.config.compute_admin.password
        admin_tenant = cls.config.compute_admin.tenant_name

        if not (admin_username and admin_password and admin_tenant):
            msg = ("Missing Compute Admin API credentials "
                   "in configuration.")
            raise cls.skipException(msg)

        cls.os_adm = clients.ComputeAdminManager(interface=cls._interface)
