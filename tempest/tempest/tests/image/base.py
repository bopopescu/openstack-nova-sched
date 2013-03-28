# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 IBM Corp.
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

from tempest import clients
from tempest.common.utils.data_utils import rand_name
from tempest import exceptions
import tempest.test

LOG = logging.getLogger(__name__)


class BaseImageTest(tempest.test.BaseTestCase):
    """Base test class for Image API tests."""

    @classmethod
    def setUpClass(cls):
        cls.os = clients.Manager()
        cls.created_images = []

    @classmethod
    def tearDownClass(cls):
        for image_id in cls.created_images:
            try:
                cls.client.delete_image(image_id)
            except exceptions.NotFound:
                pass

        for image_id in cls.created_images:
                cls.client.wait_for_resource_deletion(image_id)

    @classmethod
    def create_image(cls, **kwargs):
        """Wrapper that returns a test image."""
        name = rand_name(cls.__name__ + "-instance")

        if 'name' in kwargs:
            name = kwargs.pop('name')

        container_format = kwargs.pop('container_format')
        disk_format = kwargs.pop('disk_format')

        resp, image = cls.client.create_image(name, container_format,
                                              disk_format, **kwargs)
        cls.created_images.append(image['id'])
        return resp, image

    @classmethod
    def _check_version(cls, version):
        __, versions = cls.client.get_versions()
        if version == 'v2.0':
            if 'v2.0' in versions:
                return True
        elif version == 'v1.0':
            if 'v1.1' in versions or 'v1.0' in versions:
                return True
        return False


class BaseV1ImageTest(BaseImageTest):

    @classmethod
    def setUpClass(cls):
        super(BaseV1ImageTest, cls).setUpClass()
        cls.client = cls.os.image_client
        if not cls._check_version('v1.0'):
            msg = "Glance API v1 not supported"
            raise cls.skipException(msg)


class BaseV2ImageTest(BaseImageTest):

    @classmethod
    def setUpClass(cls):
        super(BaseV2ImageTest, cls).setUpClass()
        cls.client = cls.os.image_client_v2
        if not cls._check_version('v2.0'):
            msg = "Glance API v2 not supported"
            raise cls.skipException(msg)
