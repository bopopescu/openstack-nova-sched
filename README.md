Openstack VM Scheduling

Openstack deployed using scripts from devstack.org.
It contains all modules of openstack except the data folder, which unfortunalety contains large files that git can't handle.
The data folder is compressed and added as a tar.gz file. It will be updated occasionally. !!

Steps to install DevStack:

Install Ubuntu on a fresh VM.
Install python-netaddr, mysql-server,git
Download the devstack scripts:
git clone git://github.com/openstack-dev/devstack.git
Start the installation:
cd devstack;
./stack.sh

The installation folder is in /opt/stack




