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

import testtools

from tempest.test import attr
from tempest.tests import compute
from tempest.tests.compute import base


class ServerDiskConfigTestJSON(base.BaseComputeTest):
    _interface = 'json'

    @classmethod
    def setUpClass(cls):
        if not compute.DISK_CONFIG_ENABLED:
            msg = "DiskConfig extension not enabled."
            raise cls.skipException(msg)
        super(ServerDiskConfigTestJSON, cls).setUpClass()
        cls.client = cls.os.servers_client

    @attr(type='positive')
    def test_rebuild_server_with_manual_disk_config(self):
        # A server should be rebuilt using the manual disk config option
        resp, server = self.create_server(disk_config='AUTO',
                                          wait_until='ACTIVE')

        #Verify the specified attributes are set correctly
        resp, server = self.client.get_server(server['id'])
        self.assertEqual('AUTO', server['OS-DCF:diskConfig'])

        resp, server = self.client.rebuild(server['id'],
                                           self.image_ref_alt,
                                           disk_config='MANUAL')

        #Wait for the server to become active
        self.client.wait_for_server_status(server['id'], 'ACTIVE')

        #Verify the specified attributes are set correctly
        resp, server = self.client.get_server(server['id'])
        self.assertEqual('MANUAL', server['OS-DCF:diskConfig'])

        #Delete the server
        resp, body = self.client.delete_server(server['id'])

    @attr(type='positive')
    def test_rebuild_server_with_auto_disk_config(self):
        # A server should be rebuilt using the auto disk config option
        resp, server = self.create_server(disk_config='MANUAL',
                                          wait_until='ACTIVE')

        #Verify the specified attributes are set correctly
        resp, server = self.client.get_server(server['id'])
        self.assertEqual('MANUAL', server['OS-DCF:diskConfig'])

        resp, server = self.client.rebuild(server['id'],
                                           self.image_ref_alt,
                                           disk_config='AUTO')

        #Wait for the server to become active
        self.client.wait_for_server_status(server['id'], 'ACTIVE')

        #Verify the specified attributes are set correctly
        resp, server = self.client.get_server(server['id'])
        self.assertEqual('AUTO', server['OS-DCF:diskConfig'])

        #Delete the server
        resp, body = self.client.delete_server(server['id'])

    @attr(type='positive')
    @testtools.skipUnless(compute.RESIZE_AVAILABLE, 'Resize not available.')
    def test_resize_server_from_manual_to_auto(self):
        # A server should be resized from manual to auto disk config
        resp, server = self.create_server(disk_config='MANUAL',
                                          wait_until='ACTIVE')

        #Resize with auto option
        self.client.resize(server['id'], self.flavor_ref_alt,
                           disk_config='AUTO')
        self.client.wait_for_server_status(server['id'], 'VERIFY_RESIZE')
        self.client.confirm_resize(server['id'])
        self.client.wait_for_server_status(server['id'], 'ACTIVE')

        resp, server = self.client.get_server(server['id'])
        self.assertEqual('AUTO', server['OS-DCF:diskConfig'])

        #Delete the server
        resp, body = self.client.delete_server(server['id'])

    @attr(type='positive')
    @testtools.skipUnless(compute.RESIZE_AVAILABLE, 'Resize not available.')
    def test_resize_server_from_auto_to_manual(self):
        # A server should be resized from auto to manual disk config
        resp, server = self.create_server(disk_config='AUTO',
                                          wait_until='ACTIVE')

        #Resize with manual option
        self.client.resize(server['id'], self.flavor_ref_alt,
                           disk_config='MANUAL')
        self.client.wait_for_server_status(server['id'], 'VERIFY_RESIZE')
        self.client.confirm_resize(server['id'])
        self.client.wait_for_server_status(server['id'], 'ACTIVE')

        resp, server = self.client.get_server(server['id'])
        self.assertEqual('MANUAL', server['OS-DCF:diskConfig'])

        #Delete the server
        resp, body = self.client.delete_server(server['id'])


class ServerDiskConfigTestXML(ServerDiskConfigTestJSON):
    _interface = 'xml'
