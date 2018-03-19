"""
Interface module encapsulates interface types for security engines.
All interface have a 'top level' such as Physical or Tunnel Interface.
These top level interfaces have certain common settings that can be
modified such as assigning a zone.

IP addresses, netmask, management settings, VLANs, etc are part of an
interfaces 'sub' interface. Sub interfaces can be retrieved from an engine
reference and call to :func:`~smc.core.interfaces.Interface.sub_interfaces`

The interface hierarchy resembles:

::

        Interface
            |
    Physical/Tunnel Interface
            |
            | - PhysicalVlanInterface (is a PhysicalInterface)
            |      /
        Sub Interfaces (SingleNodeInterface, NodeInterface, InlineInterface, etc)
            |
        Attributes (address, network_value, vlan_id, etc)

Sub interfaces are documented in :py:mod:`smc.core.sub_interfaces`.

VLANs are properties of specific interfaces and can also be retrieved by
first getting the top level interface, and calling :func:`~smc.core.interfaces.Interface.vlan_interface`
to view or modify specific aspects of a VLAN, such as addresses, etc.
"""
from smc.base.model import SubElement, lookup_class, ElementCache
from smc.api.exceptions import EngineCommandFailed, ModificationAborted,\
    InterfaceNotFound
from smc.core.sub_interfaces import (
    NodeInterface, SingleNodeInterface, ClusterVirtualInterface,
    InlineInterface, CaptureInterface, _add_vlan_to_inline,
    get_sub_interface, InlineL2FWInterface, InlineIPSInterface,
    SubInterfaceCollection)
from smc.base.decorators import deprecated
from smc.elements.helpers import zone_helper, logical_intf_helper
from smc.base.structs import BaseIterable


def dispatch(instance, builder, interface=None):
    """
    Dispatch to SMC. Once successful, reset the
    engine level cache or instances will have a stale
    copy of the engine data.
    """
    if interface: # Modify
        interface.update(
            EngineCommandFailed,
            href=interface.href,
            etag=interface.etag,
            json=builder.data)
    else:
        # Create
        instance.make_request(
            EngineCommandFailed,
            method='create',
            href=instance.href,
            json=builder.data)

    # Clear cache, next call for attributes will refresh it
    instance._engine._del_cache()


class InterfaceOptions(object):
    """
    Interface Options allow you to define settings related to the roles of
    the firewall interfaces:
    
    * Which IP addresses are used as the primary and backup Control IP address
    * Which interfaces are used as the primary and backup heartbeat interface
    * The default IP address for outgoing traffic

    You can optionally change which interface is used for each of these purposes,
    and define a backup Control IP address and backup Heartbeat Interface. If
    calling the `set` methods, using a value of None will unset the option.
    
    .. note:: Setting an interface option will commit the change immediately.
    """
    def __init__(self, engine):
        self._engine = engine
        self.interface = InterfaceModifier.byEngine(engine)
    
    @property
    def primary_mgt(self):
        """
        Obtain the interface specified as the primary management interface.
        This will always return a value as you must have at least one
        physical interface specified for management.
        
        :return: interface id 
        :rtype: str
        """
        return self.interface.find_mgmt_interface('primary_mgt')
        
    @property
    def backup_mgt(self):
        """
        Obtain the interface specified as the backup management
        interface. This can return None if no backup has been
        defined
        
        :return: interface id 
        :rtype: str
        """
        return self.interface.find_mgmt_interface('backup_mgt')

    @property
    def primary_heartbeat(self):
        """
        Obtain the interface specified as the primary heartbeat
        interface. This will return None if this is not
        a clustered engine.
        
        :return: interface id 
        :rtype: str
        """
        return self.interface.find_mgmt_interface('primary_heartbeat')

    @property
    def backup_heartbeat(self):
        """
        Obtain the interface specified as the backup heartbeat
        interface. This may return None if a backup has not been
        specified or this is not a cluster.
        
        :return: interface id 
        :rtype: str
        """
        return self.interface.find_mgmt_interface('backup_heartbeat')
    
    @property
    def outgoing(self):
        """
        Obtain the interface specified as the "Default IP address for
        outgoing traffic". This will always return a value.
        
        :return: interface id 
        :rtype: str
        """
        return self.interface.find_mgmt_interface('outgoing')
    
    @property
    def auth_request(self):
        """
        Return the interface for authentication requests. Can be either
        a PhysicalInterface or LoopbackInterface
        
        :return: interface id
        :rtype: str
        """
        return self.interface.find_mgmt_interface('auth_request')
        
    def set_primary_heartbeat(self, interface_id):
        """
        Set this interface as the primary heartbeat for this engine. 
        This will 'unset' the current primary heartbeat and move to
        specified interface_id.
        Clusters and Master NGFW Engines only.
        
        :param str,int interface_id: interface specified for primary mgmt
        :raises InterfaceNotFound: specified interface is not found
        :raises UpdateElementFailed: failed modifying interfaces
        :return: None
        """
        self.interface.set_unset(interface_id, 'primary_heartbeat')
        dispatch(self, self.interface, self._engine)
    
    def set_backup_heartbeat(self, interface_id):
        """
        Set this interface as the backup heartbeat interface.
        Clusters and Master NGFW Engines only.
        
        :param str,int interface_id: interface as backup
        :raises InterfaceNotFound: specified interface is not found
        :raises UpdateElementFailed: failure to update interface
        :return: None
        """
        self.interface.set_unset(interface_id, 'backup_heartbeat')
        dispatch(self, self.interface, self._engine)
            
    def set_primary_mgt(self, interface_id, auth_request=None,
                        address=None):
        """
        Specifies the Primary Control IP address for Management Server
        contact. For single FW and cluster FW's, this will enable 'Outgoing',
        'Auth Request' and the 'Primary Control' interface. For clusters, the
        primary heartbeat will NOT follow this change and should be set
        separately using :meth:`.set_primary_heartbeat`.
        For virtual FW engines, only auth_request and outgoing will be set.
        For master engines, only primary control and outgoing will be set.
        
        Primary management can be set on an interface with single IP's,
        multiple IP's or VLANs.
        ::
        
            engine.interface_options.set_primary_mgt(1)
        
        Set primary management on a VLAN interface::
        
            engine.interface_options.set_primary_mgt('1.100')
            
        Set primary management and different interface for auth_request::
        
            engine.interface_options.set_primary_mgt(
                interface_id='1.100', auth_request=0)
            
        Set on specific IP address of interface VLAN with multiple addresses::
        
             engine.interface_options.set_primary_mgt(
                 interface_id='3.100', address='50.50.50.1')
        
        :param str,int interface_id: interface id to make management
        :param str address: if the interface for management has more than
            one ip address, this specifies which IP to bind to.
        :param str,int auth_request: if setting primary mgt on a cluster
            interface with no CVI, you must pick another interface to set
            the auth_request field to (default: None)
        :raises InterfaceNotFound: specified interface is not found
        :raises UpdateElementFailed: updating management fails
        :return: None
        
        .. note:: Setting primary management on a cluster interface with no
            CVI requires you to set the interface for auth_request.
        """
        intfattr = ['primary_mgt', 'outgoing']
        if self.interface.engine.type in ('virtual_fw',):
            intfattr.remove('primary_mgt')
        
        for attribute in intfattr:
            self.interface.set_unset(interface_id, attribute, address)
        
        if auth_request is not None:
            self.interface.set_auth_request(auth_request)
        else:
            self.interface.set_auth_request(interface_id, address)
        
        dispatch(self, self.interface, self._engine)
        
    def set_backup_mgt(self, interface_id):
        """
        Set this interface as a backup management interface.
        
        Backup management interfaces cannot be placed on an interface with
        only a CVI (requires node interface/s). To 'unset' the specified
        interface address, set interface id to None
        ::
        
            engine.interface_options.set_backup_mgt(2)
            
        Set backup on interface 1, VLAN 201::
    
            engine.interface_options.set_backup_mgt('1.201')
        
        Remove management backup from engine::
        
            engine.interface_options.set_backup_mgt(None)
     
        :param str,int interface_id: interface identifier to make the backup
            management server.
        :raises InterfaceNotFound: specified interface is not found
        :raises UpdateElementFailed: failure to make modification
        :return: None
        """
        self.interface.set_unset(interface_id, 'backup_mgt')
        dispatch(self, self.interface, self._engine)
    
    def set_outgoing(self, interface_id):
        """
        Specifies the IP address that the engine uses to initiate connections
        (such as for system communications and ping) through an interface
        that has no Node Dedicated IP Address. In clusters, you must select
        an interface that has an IP address defined for all nodes.
        Setting primary_mgt also sets the default outgoing address to the same
        interface.
        
        :param str,int interface_id: interface to set outgoing
        :raises InterfaceNotFound: specified interface is not found
        :raises UpdateElementFailed: failure to make modification
        :return: None
        """
        self.interface.set_unset(interface_id, 'outgoing')
        dispatch(self, self.interface, self._engine)
            

class Interface(SubElement):
    """
    Interface settings common to all interface types.
    """
    def __init__(self, **meta):
        self._engine = meta.pop('engine', None)  # Engine reference
        super(Interface, self).__init__(**meta)
                        
    def delete(self):
        """
        Override delete in parent class, this will also delete
        the routing configuration referencing this interface.
        ::

            engine = Engine('vm')
            interface = engine.interface.get(2)
            interface.delete()
        """
        super(Interface, self).delete()
        for routes in self._engine.routing:
            if routes.name == self.name or \
                    routes.name.startswith('VLAN {}.'.format(self.interface_id)):
                routes.delete()
        self._engine._del_cache()
    
    def update(self, *args, **kw):
        """
        Update/save this interface information back to SMC. When interface
        changes are made, especially to sub interfaces, call `update` on
        the top level interface.

        Example of changing the IP address of an interface::

            >>> engine = Engine('sg_vm')
            >>> interface = engine.physical_interface.get(1)
            >>> interface.zone_ref = zone_helper('mynewzone')
            >>> interface.update()

        :raises UpdateElementFailed: failure to save changes
        :return: None
        """
        super(Interface, self).update(*args, **kw)
        self._engine._del_cache()
    
    def save(self):
        self.update()
        self._engine._del_cache()
    
    def _get_builder(self):
        """
        Get a builder object with the current interface. This is an internal
        method that allows multiple interfaces to be added to an existing
        interface without submitting each serially. Use the builder methods
        to modify the interface details. Call interface.update() after making
        modifications.
        
        :return: tuple(builder, interface)
        """
        return InterfaceBuilder.getBuilder(self, self.interface_id)
    
    @property
    def all_interfaces(self):
        """
        Access to all assigned sub-interfaces on this interface. A sub
        interface is the node level where IP addresses are assigned, or
        a inline interface is defined, VLANs, etc.
        Example usage::
        
            >>> engine = Engine('dingo')
            >>> itf = engine.interface.get(0)
            >>> assigned = itf.all_interfaces
            >>> list(assigned)
            [SingleNodeInterface(address=1.1.1.10), SingleNodeInterface(address=1.1.1.25)]
            >>> assigned.get(address='1.1.1.10')
            SingleNodeInterface(address=1.1.1.10)
            >>> itf = engine.interface.get(1)
            >>> assigned = itf.all_interfaces
            >>> list(assigned)
            [PhysicalVlanInterface(address=12.12.12.12,12.12.12.13,vlan_id=1),
             PhysicalVlanInterface(vlan_id=3),
             PhysicalVlanInterface(address=36.35.35.37,vlan_id=2)]
            >>> assigned.get(address='12.12.12.12')
            SingleNodeInterface(address=12.12.12.12, vlan_id=1)
            >>> assigned.get(vlan_id='2')
            SingleNodeInterface(address=36.35.35.37, vlan_id=2)
        
        :rtype: BaseIterable(AllInterfaces)
        """
        return AllInterfaces([
            self.vlan_interface, self.interfaces])
    
    @property
    def interfaces(self):
        """
        Access to assigned `sub-interfaces` on this interface. A sub
        interface is the node level where IP addresses are assigned, or
        a layer 2 interface is defined.
            
            >>> itf = engine.interface.get(20)
            >>> assigned = itf.interfaces
            >>> list(assigned)
            [SingleNodeInterface(address=20.20.20.20), SingleNodeInterface(address=21.21.21.21)]
            >>> assigned.get(address='20.20.20.20')
            SingleNodeInterface(address=20.20.20.20)
            
        :rtype: BaseIterable(SubInterfaceCollection)
        """
        return SubInterfaceCollection(self)

    @property
    def vlan_interface(self):
        """
        Access VLAN interfaces for this interface, if any.
        Example usage::
        
            >>> itf = engine.interface.get(1)
            >>> assigned = itf.vlan_interface
            >>> list(assigned)
            [PhysicalVlanInterface(address=12.12.12.12,12.12.12.13,vlan_id=1),
             PhysicalVlanInterface(vlan_id=3),
             PhysicalVlanInterface(address=36.35.35.37,vlan_id=2)]
            >>> assigned.get(address='12.12.12.13')
            SingleNodeInterface(address=12.12.12.13, vlan_id=1)
            >>> assigned.get(vlan_id='1')
            SingleNodeInterface(address=12.12.12.12, vlan_id=1)
            >>> assigned.get(vlan_id='2')
            SingleNodeInterface(address=36.35.35.37, vlan_id=2)

        :rtype: BaseIterable(PhysicalVlanInterface)
        """
        return VlanCollection(self)
        
    def vlan_interfaces(self):
        return VlanCollection(self)
    
    @property
    def addresses(self):
        """
        Return 3-tuple with (address, network, nicid)

        :return: address related information of interface as 3-tuple list
        :rtype: list
        """
        addresses = []
        for i in self.all_interfaces:
            if isinstance(i, PhysicalVlanInterface):
                for v in i.interfaces:
                    addresses.append((v.address, v.network_value, v.nicid))
            else:
                addresses.append((i.address, i.network_value, i.nicid))
        return addresses

    @property
    def contact_addresses(self):
        """
        Configure an interface contact address for this interface.
        Note that an interface may have multiple IP addresses assigned
        so you may need to iterate through contact addresses.
        Example usage::
        
            >>> itf = engine.interface.get(0)
            >>> itf.contact_addresses
            [ContactAddressInterface(interface_id=0, interface_ip=1.1.1.10),
             ContactAddressInterface(interface_id=0, interface_ip=1.1.1.25)]
            >>> for ca in itf.contact_addresses:
            ...   print("IP: %s, addresses: %s" % (ca.interface_ip, list(ca)))
            ... 
            IP: 1.1.1.10, addresses: []
            IP: 1.1.1.25, addresses: [ContactAddress(address=172.18.1.20, location=Default)]

            >>> for ca in itf.contact_addresses:
            ...   if ca.interface_ip == '1.1.1.10':
            ...     ca.add_contact_address('10.5.5.5', location='remote')
            
        :return: list of interface contact addresses
        :rtype: ContactAddressInterface

        .. seealso:: :py:mod:`smc.core.contact_address`
        """
        return self._engine.contact_addresses.get(self.interface_id)

    @property
    def has_vlan(self):
        """
        Does the interface have VLANs

        :return: Whether VLANs are configured
        :rtype: bool
        """
        return bool(self.data.get('vlanInterfaces', []))

    @property
    def has_interfaces(self):
        """
        Does the interface have interface have any sub interface
        types assigned. For example, a physical interface with no
        IP addresses would return False.

        :return: Does this interface have actual types assigned
        :rtype: bool
        """
        return bool(self.data.get('interfaces', []))

    def sub_interfaces(self):
        """
        Flatten out all top level interfaces and only return sub interfaces.
        It is recommended to use :meth:`~all_interfaces`, :meth:`~interfaces`
        or :meth:`~vlan_interfaces` which return collections with helper
        methods to get sub interfaces based on index or attribute value pairs.

        :rtype: list(SubInterface)
        """
        
        interfaces = self.all_interfaces
        sub_interfaces = []
        for interface in interfaces:
            if isinstance(interface, PhysicalVlanInterface):
                if interface.has_interfaces:
                    for subaddr in interface.interfaces:
                        sub_interfaces.append(subaddr)
                else:
                    sub_interfaces.append(interface)
            else:
                sub_interfaces.append(interface)
        
        return sub_interfaces
        
    def get_boolean(self, name):
        """
        Get the boolean value for attribute specified from the
        sub interface/s.
        """
        for interface in self.all_interfaces:
            if isinstance(interface, PhysicalVlanInterface):
                if any(vlan for vlan in interface.interfaces
                       if getattr(vlan, name)):
                    return True
            else:
                if getattr(interface, name):
                    return True
        return False

    def change_single_interface(self, address, network_value, zone_ref=None,
                                vlan_id=None):
        """
        Change an existing single firewall IP address. If the interface has
        VLANs you must use the ``vlan_id`` parameter to identify the correct
        sub interface. In the case of VLANs, This will also adjust the routing
        table network for the new IP address if necessary (i.e. network/mask
        changes will remove the old routing entry that will become obsolete).
        
        :param str address: new IP address to assign
        :param str network_value: new network in cidr format; 1.1.1.0/24
        :param str replace_ip: required if multiple IP addresses exist on
            an interface. Specify the IP address to change.
        :raises UpdateElementFailed: failed updating IP address
        :raises ModificationAborted: Requirements to make change not met
        :raises InterfaceNotFound: If vlan_id is specified but the VLAN does
            not exist by ID
        :return: boolean indicating success or failure
        :rtype: bool
        
        .. note:: This method does not apply to changing cluster addresses or
            interfaces with multiple IP addresses
        """
        if self.has_vlan and not vlan_id:
            raise ModificationAborted(
                'Interface with VLANs configured require a vlan id be specified to '
                'change the correct VLAN address.')
        
        if vlan_id:
            # raises InterfaceNotFound
            itf = self.vlan_interface.get_vlan(vlan_id)
            nicid = itf.interface_id
            interfaces = itf.interfaces
        else:
            interfaces = self.interfaces
            nicid = self.interface_id
        
        change_made = False
        
        if zone_ref:
            zone = zone_helper(zone_ref)
            if self.zone_ref != zone:
                if vlan_id:
                    itf.zone_ref = zone
                else:
                    self.zone_ref = zone
                change_made = True

        original_network = None
        for interface in interfaces:
            if interface.address != address:
                original_network = interface.network_value
                interface.update(address=address, network_value=network_value)
                change_made = True

        if change_made:
            self.update()
            
            if original_network and original_network != network_value:
                routes = self._engine.routing.get(nicid)
                for route in routes:
                    if route.invalid:
                        route.delete()
        return change_made
       
    def change_cluster_interface(self, cluster_virtual=None, network_value=None,
                                 nodes=None, macaddress=None, vlan_id=None,
                                 zone_ref=None, comment=None):
        """
        Change cluster interface settings. If the cluster interface only has a
        CVI, provide only ``cluster_virtual`` param. If only NDI's are on the
        interface, provide only ``nodes`` param. Provide both if using CVI and
        NDI's. If the cluster interface is on a VLAN, you must provide the VLAN id.
        The cluster interface will update only if there are differences in
        the the provided configuration.
        
        Node syntax is the same format as creating cluster interfaces::
        
            nodes=[
                {'address':'5.5.5.2', 'network_value':'5.5.5.0/24', 'nodeid':1}, 
                {'address':'5.5.5.3', 'network_value':'5.5.5.0/24', 'nodeid':2}]
                
        Change only CVI address::
        
            >>> itf = engine.interface.get(0)
            >>> itf.sub_interfaces()
            [ClusterVirtualInterface(address=1.1.1.250), NodeInterface(address=1.1.1.3),
             NodeInterface(address=1.1.1.2)]
            >>> itf.change_cluster_interface(cvi='1.1.1.254')
            >>> itf.sub_interfaces()
            [ClusterVirtualInterface(address=1.1.1.254), NodeInterface(address=1.1.1.3),
             NodeInterface(address=1.1.1.2)]
        
        Change NDI addresses only::
        
            >>> itf = engine.interface.get(1)
            >>> itf.sub_interfaces()
            [NodeInterface(address=2.2.2.1), NodeInterface(address=2.2.2.2)]
            >>> itf.change_cluster_interface(
                    nodes=[{'address':'22.22.22.1', 'network_value':'22.22.22.0/24', 'nodeid':1},
                           {'address':'22.22.22.2', 'network_value':'22.22.22.0/24', 'nodeid':2}])
            >>> itf.sub_interfaces()
            [NodeInterface(address=22.22.22.1), NodeInterface(address=22.22.22.2)]
        
        .. note:: This does not support changing the cluster address if multiple
            addresses are assigned.
            
        :param str cvi: new CVI address. Optional if CVI in use on this
            interface
        :param str cvi_network_value: network in cidr format; 1.1.1.0/24
        :param list nodes: node addresses for interface. Optional if only a
            Cluster Virtual Interface (CVI) is used or only changing NDI's
        :type nodes: list(dict)
        :param str,int vlan_id: Required if the cluster address is on a VLAN
        :param str zone_ref: If a new zone is provided and this is a non-VLAN
            interface it is applied at the interface level, otherwise it will
            be applied on the VLAN.
        :raises UpdateElementFailed: Failure to make change with reason
        :raises ModificationAborted: Requirements to make change not met
        :raises InterfaceNotFound: If vlan_id is specified but the VLAN does
            not exist by ID
        :return: boolean indicating success or failure
        :rtype: bool
        
        .. versionchanged:: 0.6.1
            Renamed from change_cluster_ipaddress
        """
        if self.has_vlan and not vlan_id:
            raise ModificationAborted(
                'Interface with VLANs configured require a vlan id be specified to '
                'change the correct VLAN address.')
        
        if vlan_id:
            # raises InterfaceNotFound
            itf = self.vlan_interface.get_vlan(vlan_id)
            nicid = itf.interface_id
            interfaces = itf.interfaces
        else:
            interfaces = self.interfaces
            nicid = self.interface_id
        
        change_made = False
        
        # First process fields that do not require sub-interfaces
        if macaddress and self.macaddress != macaddress:
            self.macaddress = macaddress
            change_made = True
        
        if comment is not None and comment != self.comment:
            self.comment = comment
            change_made = True
        
        if zone_ref:
            zone = zone_helper(zone_ref)
            if self.zone_ref != zone:
                if vlan_id:
                    itf.zone_ref = zone
                else:
                    self.zone_ref = zone
                change_made = True
        
        # It would not be common to only provide a new cluster mask as you
        # would also be required to provide a nodes definition with the new
        # network_value as well so only check for cluster virtual or nodes.
        original_network = None
        if interfaces and (nodes or cluster_virtual):
            for interface in interfaces:
                original_network = interface.network_value
                if cluster_virtual and isinstance(interface, ClusterVirtualInterface):
                    if cluster_virtual != interface.address:
                        interface.update(address=cluster_virtual)
                        change_made = True
                    if network_value and network_value != interface.network_value:
                        interface.update(network_value=network_value)
                        change_made = True
                elif nodes and isinstance(interface, NodeInterface):
                    for node in nodes:
                        if node.get('nodeid') == interface.nodeid:
                            if interface.address != node.get('address') or \
                                interface.network_value != node.get('network_value'):
                                interface.update(
                                    address=node.get('address'),
                                    network_value=node.get('network_value'))
                                change_made = True
        if change_made:
            self.update()
            
            if original_network:
                new_mask = network_value if network_value else \
                    next(i.network_value for i in self.interfaces)
                
                # Only update routing if the netmask has changed
                if new_mask != original_network:
                    routes = self._engine.routing.get(nicid)
                    for route in routes:
                        if route.invalid:
                            route.delete()
        return change_made

    def reset_interface(self):
        """
        Reset the interface by removing all assigned addresses and VLANs.
        This will not delete the interface itself, only the sub interfaces that
        may have addresses assigned. This will not affect inline or capture
        interfaces.
        Note that if this interface is used as a primary control, auth request
        or outgoing interface, the update will fail. You should move that
        functionality to another interface before calling this. See also::
        :class:`smc.core.engine.interface_options`.
        
        :raises UpdateElementFailed: failed to update the interfaces. This is
            usually caused when the interface is assigned as a control, outgoing,
            or auth_request interface.
        :return: None
        """
        self.data['interfaces'] = []
        self.data['vlanInterfaces'] = []
        self.update()
        try:
            routing = self._engine.routing.get(self.interface_id)
            routing.delete()
        except InterfaceNotFound: # Only VLAN identifiers, so no routing
            pass
    
    @property
    def interface_id(self):
        """
        The Interface ID automatically maps to a physical network port
        of the same number during the initial configuration of the engine,
        but the mapping can be changed as necessary. Call 
        :meth:`.change_interface_id` to change inline, VLAN, cluster and
        single interface ID's.
        
        .. note:: It is not possible to change an interface ID from a 
            PhysicalVlanInterface. You must call on the parent PhysicalInterface.
        
        :param str value: interface_id
        :rtype: str
        """
        return self.data.get('interface_id')

    @interface_id.setter
    def interface_id(self, newid):
        self.data['interface_id'] = newid
    
    def change_interface_id(self, interface_id):
        """
        Change the interface ID for this interface. This can be used on any
        interface type. If the interface is an Inline interface, you must
        provide the ``interface_id`` in format '1-2' to define both
        interfaces in the pair. The change is committed after calling this
        method.
        ::
        
            itf = engine.interface.get(0)
            itf.change_interface_id(10)
            
        Or inline interface pair 10-11::
        
            itf = engine.interface.get(10)
            itf.change_interface_id('20-21')
        
        :param str,int interface_id: new interface ID. Format can be single
            value for non-inline interfaces or '1-2' format for inline.
        :raises UpdateElementFailed: changing the interface failed with reason
        :return: None
        """
        splitted = str(interface_id).split('-')
        if1 = splitted[0]
        
        for interface in self.all_interfaces:
            # Set top level interface, this only uses a single value which
            # will be the leftmost interface
            if isinstance(interface, PhysicalVlanInterface):
                interface.interface_id = '{}.{}'.format(if1,
                    interface.interface_id.split('.')[-1])
                
                if interface.has_interfaces:
                    for sub_interface in interface.interfaces:
                        
                        if isinstance(sub_interface, InlineInterface):
                            sub_interface.change_interface_id(interface_id)
                        else:
                            # PhysicalVlanInterface only (i.e. CVI, NDI, etc)
                            sub_interface.change_interface_id(if1)  
            else:
                if isinstance(interface, InlineInterface):
                    interface.change_interface_id(interface_id)
                else:
                    interface.update(nicid=if1)
        
        self.interface_id = if1
        self.update()
    
    @property
    def name(self):
        """
        Read only name tag
        """
        name = super(Interface, self).name
        return name if name else self.data.get('name')
    
    @property
    def comment(self):
        """
        Optional interface comment
        
        :return: str or None
        """
        return self.data.get('comment', None)
    
    @comment.setter
    def comment(self, value):
        self.data['comment'] = value if value is not None else ''
    
    @property
    def zone_ref(self):
        """
        Zone for this physical interface.

        :param str value: href of zone, set to None to remove existing zone
        :rtype: str
        """
        return self.data.get('zone_ref')

    @zone_ref.setter
    def zone_ref(self, value):
        if value is None:
            self.data.pop('zone_ref', None)
        else:
            self.data['zone_ref'] = zone_helper(value)


class TunnelInterface(Interface):
    """
    This interface type represents a tunnel interface that is typically used for
    route based VPN traffic.
    Nested interface nodes can be SingleNodeInterface (for L3 NGFW), a NodeInterface
    (for cluster's with only NDI's) or ClusterVirtualInterface (CVI) for cluster VIP.
    Tunnel Interfaces are only available under layer 3 routed interfaces and do not
    support VLANs.
    """
    typeof = 'tunnel_interface'

    def add_layer3_interface(self, interface_id, address, network_value,
                             zone_ref=None, comment=None):
        """
        Creates a tunnel interface with sub-type single_node_interface. This is
        to be used for single layer 3 firewall instances.

        :param str,int tunnel_id: the tunnel id for the interface, used as nicid also
        :param str address: ip address of interface
        :param str network_value: network cidr for interface; format: 1.1.1.0/24
        :param str zone_ref: zone reference for interface can be name, href or Zone
        :raises EngineCommandFailed: failure during creation
        :return: None
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        builder.comment = comment
        builder.add_sni_only(address, network_value)
        if zone_ref:
            builder.zone = zone_ref

        dispatch(self, builder, interface)
    
    @deprecated('add_layer3_interface')    
    def add_single_node_interface(self, tunnel_id, address, network_value,
                                  zone_ref=None, comment=None):
        return self.add_layer3_interface(tunnel_id, address, network_value,
                                         zone_ref, comment)
    
    def add_cluster_virtual_interface(self, interface_id, cluster_virtual=None,
                                      network_value=None, nodes=None,
                                      zone_ref=None, comment=None):
        """
        Add a tunnel interface on a clustered engine. For tunnel interfaces
        on a cluster, you can specify a CVI only, NDI interfaces, or both.
        This interface type is only supported on layer 3 firewall engines.
        ::

            Add a tunnel CVI and NDI:

            engine.tunnel_interface.add_cluster_virtual_interface(
                tunnel_id=3000,
                cluster_virtual='4.4.4.1',
                network_value='4.4.4.0/24',
                nodes=nodes)

            Add tunnel NDI's only:

            engine.tunnel_interface.add_cluster_virtual_interface(
                tunnel_id=3000,
                nodes=nodes)

            Add tunnel CVI only:

            engine.tunnel_interface.add_cluster_virtual_interface(
                tunnel_id=3000,
                cluster_virtual='31.31.31.31',
                network_value='31.31.31.0/24',
                zone_ref='myzone')

        :param str,int tunnel_id: tunnel identifier (akin to interface_id)
        :param str cluster_virtual: CVI ipaddress (optional)
        :param str network_value: CVI network; required if ``cluster_virtual`` set
        :param list nodes: nodes for clustered engine with address,network_value,nodeid
        :param str zone_ref: zone reference, can be name, href or Zone
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        builder.comment = comment
        if cluster_virtual and network_value:
            builder.add_cvi_only(cluster_virtual, network_value)
        if zone_ref:
            builder.zone = zone_ref
        if nodes:
            for node in nodes:
                builder.add_ndi_only(**node)

        dispatch(self, builder, interface)

    @property
    def ndi_interfaces(self):
        return []


class PhysicalInterface(Interface):
    """
    Physical Interfaces on NGFW. This represents the following base configuration for
    the following interface types:

        * Single Node Interface
        * Node Interface
        * Capture Interface
        * Inline Interface
        * Cluster Virtual Interface
        * Virtual Physical Interface (used on Virtual Engines)
        * DHCP Interface

    This should be used to add interfaces to an engine after it has been created.
    First get the engine context by loading the engine then get the engine property for
    physical interface::

        engine = Engine('myfw')
        engine.physical_interface.add_layer3_interface(.....)
        engine.physical_interface.add(5) #single unconfigured physical interface
        engine.physical_interface.add_inline_ips_interface('5-6', ....)
        ....

    When making changes, the etag used should be the top level engine etag.
    """
    typeof = 'physical_interface'

    def add(self, interface_id, virtual_mapping=None,
            virtual_resource_name=None, zone_ref=None, comment=None):
        """
        Add single physical interface with interface_id. Use other methods
        to fully add an interface configuration based on engine type.
        Virtual mapping and resource are only used in Virtual Engines.

        :param str,int interface_id: interface identifier
        :param int virtual_mapping: virtual firewall id mapping
               See :class:`smc.core.engine.VirtualResource.vfw_id`
        :param str virtual_resource_name: virtual resource name
               See :class:`smc.core.engine.VirtualResource.name`
        :raises EngineCommandFailed: failure creating interface
        :return: None
        """
        builder = InterfaceBuilder()
        builder.interface_id = interface_id
        builder.zone = zone_ref
        builder.comment = comment
        builder.virtual_mapping = virtual_mapping
        builder.virtual_resource_name = virtual_resource_name

        dispatch(self, builder)

    @deprecated('add_layer3_interface')
    def add_single_node_interface(self, interface_id, address, network_value,
                                  zone_ref=None, is_mgmt=False, **kw):
        """
        .. deprecated:: 0.5.6
            Use add_layer3_interface
            
        Adds an interface to a single fw instance.

        :param str,int interface_id: interface identifier
        :param str address: ip address
        :param str network_value: network/cidr (12.12.12.0/24)
        :param str zone_ref: zone reference, can be name, href or Zone
        :param bool is_mgmt: enable as management interface
        :param kw: key word arguments are valid SingleNodeInterface
            sub-interface settings passed in during create time. For example,
            'backup_mgt=True' to enable this interface as the management backup.
        :raises EngineCommandFailed: failure creating interface
        :return: None

        .. note::
            If an existing ip address exists on the interface and zone_ref is
            provided, this value will overwrite any previous zone definition.

        See :py:class:`smc.core.sub_interfaces.SingleNodeInterface` for more information
        """
        return self.add_layer3_interface(
            interface_id, address, 
            network_value, zone_ref,
            is_mgmt, **kw)

    @deprecated('add_layer3_interface')
    def add_node_interface(self, interface_id, address, network_value,
                           zone_ref=None, is_mgmt=False, comment=None, **kw):
        """
        .. deprecated:: 0.5.6
            Use add_layer3_interface
        
        Node interfaces are used on all engine types except single fw
        engines. For inline and IPS engines, this interface type represents
        a layer 3 routed (node dedicated) interface. For clusters, use the
        cluster related methods such as :func:`add_cluster_virtual_interface`

        :param str,int interface_id: interface identifier
        :param str address: ip address
        :param str network_value: network/cidr (12.12.12.0/24)
        :param str zone_ref: zone reference, can be name, href or Zone
        :param bool is_mgmt: enable management
        :param kw: key word arguments are valid NodeInterface sub-interface
            settings passed in during create time. For example, 'backup_mgt=True'
            to enable this interface as the management backup.
        :raises EngineCommandFailed: failure creating interface
        :return: None

        .. note::
            If an existing ip address exists on the interface and zone_ref is
            provided, this value will overwrite any previous zone definition.

        See :py:class:`smc.core.sub_interfaces.NodeInterface` for more information
        """
        return self.add_layer3_interface(
            interface_id, address,
            network_value, zone_ref,
            is_mgmt, comment, **kw)

    def add_layer3_interface(self, interface_id, address, network_value,
                             zone_ref=None, is_mgmt=False, comment=None, **kw):
        """
        Add a layer 3 interface on a non-clustered engine.
        For Layer 2 FW and IPS engines, this interface type represents
        a layer 3 routed (node dedicated) interface. For clusters, use the
        cluster related methods such as :func:`add_cluster_virtual_interface`

        :param str,int interface_id: interface identifier
        :param str address: ip address
        :param str network_value: network/cidr (12.12.12.0/24)
        :param str zone_ref: zone reference, can be name, href or Zone
        :param bool is_mgmt: enable management
        :param kw: keyword arguments are passed to the sub-interface during
            create time. If the engine is a single FW, the sub-interface type
            is :class:`smc.core.sub_interfaces.SingleNodeInterface`. For all
            other engines, the type is :class:`smc.core.sub_interfaces.NodeInterface`
            For example, pass 'backup_mgt=True' to enable this interface as the
            management backup.
        :raises EngineCommandFailed: failure creating interface
        :return: None

        .. note::
            If an existing ip address exists on the interface and zone_ref is
            provided, this value will overwrite any previous zone definition.
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        if zone_ref:
            builder.zone = zone_ref
        if self._engine.type in ('single_fw',):
            builder.add_sni_only(address, network_value, is_mgmt, **kw)
        else:
            builder.add_ndi_only(address, network_value, is_mgmt=is_mgmt, **kw)
        
        builder.comment = comment
        dispatch(self, builder, interface)
    
    def add_capture_interface(self, interface_id, logical_interface_ref,
                              zone_ref=None, comment=None):
        """
        Add a capture interface. Capture interfaces are supported on
        Layer 2 FW and IPS engines.
        
        ..note::
            Capture interface are supported on Layer 3 FW/clusters for NGFW engines
            version >= 6.3 and SMC >= 6.3.
        
        :param str,int interface_id: interface identifier
        :param str logical_interface_ref: logical interface name, href or LogicalInterface.
            If None, 'default_eth' logical interface will be used.
        :param str zone_ref: zone reference, can be name, href or Zone
        :raises EngineCommandFailed: failure creating interface
        :return: None

        See :class:`smc.core.sub_interfaces.CaptureInterface` for more information
        """
        builder = InterfaceBuilder()
        builder.interface_id = interface_id
        builder.comment = comment
        builder.add_capture(logical_interface_ref)
        if zone_ref:
            builder.zone = zone_ref
        
        dispatch(self, builder)

    def add_inline_interface(self, interface_id, logical_interface_ref=None,
                             vlan_id=None, vlan_id2=None,
                             zone_ref_intf1=None,
                             zone_ref_intf2=None,
                             failure_mode='normal',
                             comment=None,
                             **kw):
        """
        .. versionchanged:: 0.5.6
            VLANs can be specified when when creating interface.
        
        Add an inline interface pair. Use this when adding inline interfaces
        on a Layer 2 Firewall or IPS Engine.
        
        :param str interface_id: interface id; '1-2', '3-4', etc
        :param str logical_interface_ref: logical interface name or str reference.
            If None, 'default_eth' logical interface will be used.
        :param int vlan_id: optional VLAN id for first inline interface
        :param int vlan_id2: optional VLAN id for second inline interface
        :param zone_ref_intf1: zone for inline interface 1, can be name,
            str href or Zone
        :param zone_ref_intf2: zone for inline interface 2, can be name,
            str href or Zone
        :param str failure_mode: 'normal' or 'bypass'. Note: if specifying
            bypass, the inline interfaces must be using fail-open physical
            interfaces for bypass to work.
        :raises EngineCommandFailed: failure creating interface
        :return: None
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        builder.interface_id = interface_id.split('-')[0]
        builder.comment = comment
        if not vlan_id:
            builder.zone = zone_ref_intf1
        
        builder.add_l2_inline(
            interface_id, logical_interface_ref, 
            vlan_id, vlan_id2, zone_ref_intf1, zone_ref_intf2,
            failure_mode=failure_mode, **kw)

        dispatch(self, builder, interface)
        
    def add_inline_l2fw_interface(self, interface_id, logical_interface_ref=None,
                                  vlan_id=None, vlan_id2=None,
                                  zone_ref_intf1=None,
                                  zone_ref_intf2=None, comment=None):
        """
        .. versionadded:: 0.5.6
            Requires NGFW engine >=6.3 and layer 3 FW or cluster
        
        An inline L2 FW interface is a new interface type for Layer 3 NGFW
        engines version >=6.3. Traffic passing an Inline Layer 2 Firewall
        interface will have a default action in access rules of Discard.
        Layer 2 Firewall interfaces are not bypass capable, so when NGFW is
        powered off, in an offline state or overloaded, traffic is blocked on
        this interface. If a VLAN ID is provided, it will be used on both
        inline interfaces.
        
        :param str interface_id: interface id; '1-2', '3-4', etc
        :param str logical_interface_ref: logical interface name, href or LogicalInterface.
            If None, 'default_eth' logical interface will be used.
        :param str vlan_id: optional VLAN id for first interface pair
        :param str vlan_id2: optional VLAN id for second interface pair
        :param zone_ref_intf1: zone for first interface in pair, can be name,
            str href or Zone
        :param zone_ref_intf2: zone for second interface in pair, can be name,
            str href or Zone
        :raises EngineCommandFailed: failure creating interface
        :return: None
        
        .. note:: If a VLAN ID is provided with zones, the zones will be applied
            to the VLAN interfaces. If an inline interface with VLAN is applied
            on a layer 3 FW/cluster, only a single VLAN (vlan_id) is supported.
        """
        if_type = InlineL2FWInterface
        if self._engine.type not in ('single_fw', 'fw_cluster'):
            if_type = InlineInterface
        
        return self.add_inline_interface(
            interface_id, logical_interface_ref,
            vlan_id, vlan_id2,
            zone_ref_intf1, zone_ref_intf2,
            comment=comment,
            if_type=if_type)
    
    def add_inline_ips_interface(self, interface_id, logical_interface_ref=None,
                                 vlan_id=None, vlan_id2=None,
                                 failure_mode='normal',
                                 zone_ref_intf1=None,
                                 zone_ref_intf2=None,
                                 comment=None):
        """
        .. versionadded:: 0.5.6
            Using an inline interface on a layer 3 FW requires SMC and engine
            version >= 6.3.
            
        An inline IPS interface is a new interface type for Layer 3 NGFW
        engines version >=6.3. Traffic passing an Inline IPS interface will
        have a access rule default action of Allow. Inline IPS interfaces are
        bypass capable. When using bypass interfaces and NGFW is powered off,
        in an offline state or overloaded, traffic is allowed through without
        inspection regardless of the access rules.
        
        Zone references will be applied to the VLANs if specified. If the
        intent is to only apply zones at the top level interface pair, first
        add the interface with zones (no VLANs), then add VLANs as a second
        step.
        
        :param str interface_id: interface id; '1-2', '3-4', etc
        :param str logical_interface_ref: logical interface name, href or LogicalInterface.
            If None, 'default_eth' logical interface will be used.
        :param str vlan_id: optional VLAN id for first interface pair
        :param str vlan_id2: optional VLAN id for second interface pair
        :param str failure_mode: 'normal' or 'bypass' (default: normal).
            Bypass mode requires fail open interfaces.
        :param zone_ref_intf1: zone for first interface in pair, can be name,
            str href or Zone
        :param zone_ref_intf2: zone for second interface in pair, can be name,
            str href or Zone
        :raises EngineCommandFailed: failure creating interface
        :return: None
        
        .. note:: If a VLAN ID is provided with zones, the zones will be applied
            to the VLAN interfaces. If an inline interface with VLAN is applied
            on a layer 3 FW/cluster, only a single VLAN (vlan_id) is supported.
        """
        if_type = InlineIPSInterface
        if self._engine.type not in ('single_fw', 'fw_cluster'):
            if_type = InlineInterface
        
        return self.add_inline_interface(
            interface_id, logical_interface_ref,
            vlan_id, vlan_id2,
            zone_ref_intf1, zone_ref_intf2,
            failure_mode=failure_mode,
            comment=comment,
            if_type=if_type)
        
    def add_dhcp_interface(self, interface_id, dynamic_index,
                           is_mgmt=False, zone_ref=None, comment=None):
        """
        Add a DHCP interface on a single FW

        :param int interface_id: interface id
        :param int dynamic_index: index number for dhcp interface
        :param bool primary_mgt: whether to make this primary mgt
        :param str zone_ref: zone reference, can be name, href or Zone
        :param int nodeid: node identifier
        :raises EngineCommandFailed: failure creating interface
        :return: None

        See :class:`~DHCPInterface` for more information
        """
        builder = InterfaceBuilder()
        builder.interface_id = interface_id
        builder.comment = comment
        builder.add_dhcp(dynamic_index, is_mgmt)
        builder.zone = zone_ref

        dispatch(self, builder)

    def add_layer3_cluster_interface(self, interface_id, cluster_virtual=None,
                                      network_value=None, macaddress=None, 
                                      nodes=None, cvi_mode='packetdispatch',
                                      zone_ref=None, is_mgmt=False, comment=None,
                                      **kw):
        """
        Add cluster virtual interface. A "CVI" interface is used as a VIP
        address for clustered engines. Providing 'nodes' will create the
        node specific interfaces. You can also add a cluster address with only
        a CVI, or only NDI's.
        
        Add CVI only:: 
             
            engine.physical_interface.add_cluster_virtual_interface(
                interface_id=30,
                cluster_virtual='30.30.30.1',
                network_value='30.30.30.0/24', 
                macaddress='02:02:02:02:02:06')
        
        Add NDI's only:: 
 
            engine.physical_interface.add_cluster_virtual_interface( 
                interface_id=30, 
                nodes=nodes) 
        
        Add CVI and NDI's::
        
            engine.physical_interface.add_cluster_virtual_interface(
                cluster_virtual='5.5.5.1',
                network_value='5.5.5.0/24',
                macaddress='02:03:03:03:03:03',
                nodes=[{'address':'5.5.5.2', 'network_value':'5.5.5.0/24', 'nodeid':1},
                       {'address':'5.5.5.3', 'network_value':'5.5.5.0/24', 'nodeid':2}])

        .. versionchanged:: 0.6.1
            Renamed from add_cluster_virtual_interface
        
        :param str,int interface_id: physical interface identifier
        :param str cluster_virtual: CVI address (VIP) for this interface
        :param str network_value: network value for VIP; format: 10.10.10.0/24
        :param str macaddress: mandatory mac address if cluster_virtual and
            cluster_mask provided
        :param list nodes: list of dictionary items identifying cluster nodes
        :param str cvi_mode: packetdispatch is recommended setting
        :param str zone_ref: zone reference, can be name, href or Zone
        :param bool is_mgmt: enable management
        :param kw: key word arguments are valid NodeInterface sub-interface
            settings passed in during create time. For example, 'backup_mgt=True'
            to enable this interface as the management backup.
        :raises EngineCommandFailed: failure creating interface
        :return: None
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        builder.interface_id = interface_id
        builder.comment = comment
        if cluster_virtual and network_value:
            builder.macaddress = macaddress
            builder.cvi_mode = cvi_mode
            builder.add_cvi_only(cluster_virtual, network_value, is_mgmt=is_mgmt)
        else:
            builder.cvi_mode = None
            builder.macaddress = None

        nodes = nodes if nodes else []
        for node in nodes:
            node.update(is_mgmt=is_mgmt, **kw)
            builder.add_ndi_only(**node)
        
        builder.zone = zone_ref
    
        dispatch(self, builder, interface)
    
    @deprecated('add_layer3_cluster_interface')    
    def add_cluster_virtual_interface(self, interface_id, cluster_virtual=None,
                                      network_value=None, macaddress=None, 
                                      nodes=None, cvi_mode='packetdispatch',
                                      zone_ref=None, is_mgmt=False, comment=None,
                                      **kw):
        return self.add_layer3_cluster_interface(interface_id, cluster_virtual,
            network_value, macaddress, nodes, cvi_mode, zone_ref, is_mgmt,
            comment, **kw)

    def add_cluster_interface_on_master_engine(self, interface_id,
                                               macaddress, nodes,
                                               is_mgmt=False,
                                               zone_ref=None, comment=None):
        """
        Add a cluster address specific to a master engine. Master engine
        clusters will not use "CVI" interfaces like normal layer 3 FW clusters,
        instead each node has a unique address and share a common macaddress.

        :param str,int interface_id: interface id to use
        :param str macaddress: mac address to use on interface
        :param list nodes: interface node list
        :param bool is_mgmt: is this a management interface
        :param zone_ref: zone to use, by name, str href or Zone
        :raises EngineCommandFailed: failure creating interface
        :return: None
        """
        builder = InterfaceBuilder()
        builder.interface_id = interface_id
        builder.comment = comment
        builder.macaddress = macaddress

        for node in nodes:
            node.update(is_mgmt=is_mgmt)
            builder.add_ndi_only(**node)

        builder.zone = zone_ref

        dispatch(self, builder)
    
    def add_layer3_vlan_interface(self, interface_id, vlan_id,
                                  address=None, network_value=None,
                                  virtual_mapping=None,
                                  virtual_resource_name=None,
                                  zone_ref=None, comment=None):
        """
        Add a Layer 3 VLAN interface. Optionally specify an address
        and netmask if assigning an IP to the VLAN. This method will
        also assign an IP address to an existing VLAN, or add an additional
        address to an existing VLAN. This method may commonly be used on a
        Master Engine to create VLANs for virtual firewall engines.
        
        :param str,int interface_id: interface identifier
        :param int vlan_id: vlan identifier
        :param str address: optional IP address to assign to VLAN
        :param str network_value: network cidr if address is specified. In
            format: 10.10.10.0/24.
        :param str zone_ref: zone to use, by name, href, or Zone
        :param str comment: optional comment for VLAN level of interface
        :param int virtual_mapping: virtual engine mapping id
               See :class:`smc.core.engine.VirtualResource.vfw_id`
        :param str virtual_resource_name: name of virtual resource
               See :class:`smc.core.engine.VirtualResource.name`
        :raises EngineCommandFailed: failure creating interface
        :return: None
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        if address is None:
            builder.add_vlan_only(
                vlan_id, virtual_mapping, 
                virtual_resource_name, zone_ref=zone_ref,
                comment=comment)
        else:
            if self._engine.type in ('single_fw',):
                builder.add_sni_to_vlan(
                    address, network_value, vlan_id,
                    zone_ref=zone_ref,
                    comment=comment)
            else:
                builder.add_ndi_to_vlan(
                    address, network_value, vlan_id,
                    zone_ref=zone_ref,
                    comment=comment)

        dispatch(self, builder, interface)
    
    @deprecated('add_layer3_vlan_interface')
    def add_vlan_to_node_interface(self, interface_id, vlan_id,
                                   virtual_mapping=None,
                                   virtual_resource_name=None,
                                   zone_ref=None, comment=None):
        """
        .. deprecated:: 0.5.6
            Use add_layer3_vlan_interface
            
        Add vlan to a routed interface. Interface is created if
        it doesn't already exist. This can be used on any engine
        type, but is typically used to create an interface on a
        master engine with a virtual mapping and no IP address.

        :param str,int interface_id: interface identifier
        :param int vlan_id: vlan identifier
        :param int virtual_mapping: virtual engine mapping id
               See :class:`smc.core.engine.VirtualResource.vfw_id`
        :param str virtual_resource_name: name of virtual resource
               See :class:`smc.core.engine.VirtualResource.name`
        :raises EngineCommandFailed: failure creating interface
        :return: None

        .. note::
            If the interface does not exist, it will be create automatically.
        """
        return self.add_layer3_vlan_interface(
            interface_id, vlan_id,
            virtual_mapping=virtual_mapping, 
            virtual_resource_name=virtual_resource_name,
            zone_ref=zone_ref, comment=comment)

    @deprecated('add_layer3_vlan_interface')
    def add_ipaddress_to_vlan_interface(self, interface_id, address,
                                        network_value,
                                        vlan_id, zone_ref=None,
                                        comment=None):
        """
        .. deprecated:: 0.5.6
            Use add_layer3_vlan_interface
            
        When an existing interface VLAN exists but has no IP address assigned,
        use this to add an ip address to the VLAN. Multiple addresses on the
        same interface can also be added using this method.
        This is supported on any non-clustered engine as long as the interface
        type supports IP address assignment.

        :param str,int interface_id: interface to modify
        :param str address: ip address for vlan
        :param str network_value: network for address; format: 10.10.10.0/24
        :param int vlan_id: id of vlan
        :raises EngineCommandFailed: invalid interface
        :return: None

        .. note::
            If the interface vlan does not exist, it will be create automatically.
        """
        return self.add_layer3_vlan_interface(
            interface_id, vlan_id,
            address, network_value,
            zone_ref=zone_ref, comment=comment)

    def add_layer3_vlan_cluster_interface(self, interface_id, vlan_id,
                                          nodes=None, cluster_virtual=None,
                                          network_value=None,
                                          macaddress=None,
                                          cvi_mode='packetdispatch',
                                          zone_ref=None, comment=None):
        """
        Add IP addresses to VLANs on a firewall cluster. The minimum params
        required are ``interface_id`` and ``vlan_id``.
        To create a VLAN interface with a CVI, specify ``cluster_virtual``,
        ``cluster_mask`` and ``macaddress``.

        To create a VLAN with only NDI, specify ``nodes`` parameter.

        Nodes data structure is expected to be in this format::

            nodes=[{'address':'5.5.5.2', 'network_value':'5.5.5.0/24', 'nodeid':1},
                   {'address':'5.5.5.3', 'network_value':'5.5.5.0/24', 'nodeid':2}]

        :param str,int interface_id: interface id to assign VLAN.
        :param str,int vlan_id: vlan identifier
        :param list nodes: optional addresses for node interfaces (NDI's). For a cluster,
            each node will require an address specified using the nodes format.
        :param str cluster_virtual: cluster virtual ip address (optional). If specified, cluster_mask
            parameter is required
        :param str network_value: Specifies the network address, i.e. if cluster virtual is 1.1.1.1,
            cluster mask could be 1.1.1.0/24.
        :param str macaddress: (optional) if used will provide the mapping from node interfaces
            to participate in load balancing.
        :param str cvi_mode: cvi mode for cluster interface (default: packetdispatch)
        :param zone_ref: zone to assign, can be name, str href or Zone
        :raises EngineCommandFailed: failure creating interface
        :return: None

        .. note::
            If the ``interface_id`` specified already exists, it is still possible
            to add additional VLANs and interface addresses.
        """
        builder, interface = InterfaceBuilder.getBuilder(self, interface_id)
        if cluster_virtual and network_value:   # Add CVI
            builder.add_cvi_to_vlan(cluster_virtual, network_value, vlan_id, zone_ref,
                                    comment=comment)
            if macaddress:
                builder.macaddress = macaddress
                builder.cvi_mode = cvi_mode
            else:
                builder.cvi_mode = None
        else: # VLAN on an NDI
            builder.add_vlan_only(vlan_id, zone_ref=zone_ref, comment=comment)
        if nodes:
            for node in nodes:
                node.update(vlan_id=vlan_id)
                builder.add_ndi_to_vlan(**node)

        dispatch(self, builder, interface)
        
    @deprecated('add_layer3_vlan_cluster_interface')    
    def add_ipaddress_and_vlan_to_cluster(self, interface_id, vlan_id,
                                          nodes=None, cluster_virtual=None,
                                          network_value=None,
                                          macaddress=None,
                                          cvi_mode='packetdispatch',
                                          zone_ref=None, comment=None):
        return self.add_layer3_vlan_cluster_interface(interface_id,
            vlan_id, nodes, cluster_virtual, network_value, macaddress,
            cvi_mode, zone_ref, comment)

    @property
    def is_primary_mgt(self):
        """
        Is this physical interface tagged as the backup management
        interface for this cluster.
        
        :return: is backup heartbeat
        :rtype: bool
        """
        return self.get_boolean('primary_mgt')

    @property
    def is_backup_mgt(self):
        """
        Is this physical interface tagged as the backup management
        interface for this cluster.
        
        :return: is backup heartbeat
        :rtype: bool
        """
        return self.get_boolean('backup_mgt')

    @property
    def is_primary_heartbeat(self):
        """
        Is this physical interface tagged as the primary heartbeat
        interface for this cluster.
        
        :return: is backup heartbeat
        :rtype: bool
        """
        return self.get_boolean('primary_heartbeat')
    
    @property
    def is_auth_request(self):
        """
        Is this physical interface tagged as the interface for 
        authentication requests
        
        :rtype: bool
        """
        return self.get_boolean('auth_request')

    @property
    def is_backup_heartbeat(self):
        """
        Is this physical interface tagged as the backup heartbeat
        interface for this cluster.
        
        :return: is backup heartbeat
        :rtype: bool
        """
        return self.get_boolean('backup_heartbeat')
    
    @property
    def is_outgoing(self):
        """
        Is this the default interface IP used for outgoing for system
        communications.
        
        :return: is dedicated outgoing IP interface
        :rtype: bool
        """
        return self.get_boolean('outgoing')
    
    @property
    def ndi_interfaces(self):
        """
        Return a formatted dict list of NDI interfaces on this engine.
        This will ignore CVI or any inline or layer 2 interface types.
        This can be used to identify  to indicate available IP addresses
        for a given interface which can be used to run services such as
        SNMP or DNS Relay.
        
        :return: list of dict items [{'address':x, 'nicid':y}]
        :rtype: list(dict)
        """
        return [{'address': interface.address, 'nicid': interface.nicid}
                for interface in self.interfaces
                if isinstance(interface, (NodeInterface, SingleNodeInterface))]
                    
    def change_vlan_id(self, original, new):
        """
        Change VLAN ID for a single VLAN, cluster VLAN or inline
        interface. When changing a single or cluster FW vlan, you
        can specify the original VLAN and new VLAN as either single
        int or str value. If modifying an inline interface VLAN when
        the interface pair has two different VLAN identifiers per
        interface, use a str value in form: '10-11' (original), and
        '20-21' (new).
        
        Single VLAN id::
        
            >>> engine = Engine('singlefw')
            >>> itf = engine.interface.get(1)
            >>> itf.vlan_interfaces()
            [PhysicalVlanInterface(vlan_id=11), PhysicalVlanInterface(vlan_id=10)]
            >>> itf.change_vlan_id(11, 100)
            >>> itf.vlan_interfaces()
            [PhysicalVlanInterface(vlan_id=100), PhysicalVlanInterface(vlan_id=10)]
        
        Inline interface with unique VLAN on each interface pair::
        
            >>> itf = engine.interface.get(2)
            >>> itf.vlan_interfaces()
            [PhysicalVlanInterface(vlan_id=2-3)]
            >>> itf.change_vlan_id('2-3', '20-30')
            >>> itf.vlan_interfaces()
            [PhysicalVlanInterface(vlan_id=20-30)]
        
        :param str,int original: original VLAN to change.
        :param str,int new: new VLAN identifier/s.
        :raises InterfaceNotFound: VLAN not found
        :raises UpdateElementFailed: failed updating the VLAN id
        :return: None
        """
        vlan = self.vlan_interface.get_vlan(original)
        newvlan = str(new).split('-')
        splitted = vlan.interface_id.split('.')
        vlan.interface_id = '{}.{}'.format(splitted[0], newvlan[0])
        for interface in vlan.interfaces:
            if isinstance(interface, InlineInterface):
                interface.change_vlan_id(new)
            else:
                interface.change_vlan_id(newvlan[0])
        self.update()

    def remove_vlan(self, vlan_id):
        """
        Remove a VLAN from any engine. This is a no-op if the VLAN specified
        does not exist. Any routing associated with this VLAN interface will
        also be automatically removed as well.
        
        Check for VLANs::
        
            interface = engine.interface.get(12)
            print(interface.vlan_interfaces())
        
        Delete VLAN id 14 on interface 12::
        
            interface = engine.interface.get(12)
            interface.remove_vlan(14)

        .. note::
            If a VLAN to be removed has IP addresses assigned, they
            will be removed along with any associated entries in the
            route table.
        
        :param str,int interface_id: interface identifier
        :param int vlan_id: vlan identifier
        :raises InterfaceNotFound: VLAN not found
        :raises UpdateElementFailed: fail to update
        :return: None
        """
        self.vlan_interface.get_vlan(vlan_id)
        builder, _ = InterfaceBuilder.getBuilder(self, self.interface_id)
        builder.remove_vlan(vlan_id)
        self.update(json=builder.data)
    
        for routes in self._engine.routing:
            if routes.name == 'VLAN {}.{}'.format(self.interface_id, vlan_id):
                routes.delete()
                break
    
    def enable_aggregate_mode(self, mode, interfaces):
        """    
        Enable Aggregate (LAGG) mode on this interface. Possible LAGG
        types are 'ha' and 'lb' (load balancing). For HA, only one
        secondary interface ID is required. For load balancing mode, up
        to 7 additional are supported (8 max interfaces).
        
        :param str mode: 'lb' or 'ha'
        :param list interfaces: secondary interfaces for this LAGG
        :type interfaces: list(str,int)
        :raises UpdateElementFailed: failed adding aggregate
        :return: None
        """
        if mode in ['lb', 'ha']:
            self.data['aggregate_mode'] = mode
            self.data['second_interface_id'] = ','.join(map(str, interfaces))   
            self.save() 
    
    @property
    def aggregate_mode(self):
        """
        LAGG configuration mode for this interface. Values are 'ha' or
        'lb' (load balancing). This can return None if LAGG is not
        configured.

        :return: aggregate mode set, if any
        :rtype: str, None
        """
        return self.data.get('aggregate_mode')

    def static_arp_entry(self, ipaddress, macaddress, arp_type='static', netmask=32):
        """
        Add an arp entry to this physical interface.
        ::

            interface = engine.physical_interface.get(0)
            interface.static_arp_entry(
                ipaddress='23.23.23.23',
                arp_type='static',
                macaddress='02:02:02:02:04:04')
            interface.save()

        :param str ipaddress: ip address for entry
        :param str macaddress: macaddress for ip address
        :param str arp_type: type of entry, 'static' or 'proxy' (default: static)
        :param str,int netmask: netmask for entry (default: 32)
        :return: None
        """
        self.data['arp_entry'].append({
            'ipaddress': ipaddress,
            'macaddress': macaddress,
            'netmask': netmask,
            'type': arp_type})

    @property
    def arp_entry(self):
        """
        Return any manually configured ARP entries for this physical
        interface

        :return: arp entries as dict
        :rtype: list
        """
        return self.data.get('arp_entry')

    @property
    def cvi_mode(self):
        """
        HA Cluster mode. Not valid for non-cluster engines.

        :return: possible values: packetdispatch, unicast, multicast,
            multicastgmp
        :rtype: str
        """
        return self.data.get('cvi_mode')

    @property
    def macaddress(self):
        """
        MAC Address for cluster virtual interface.
        Only valid for cluster engines.

        :param str value: macaddress
        :rtype: str
        """
        return self.data.get('macaddress')

    @macaddress.setter
    def macaddress(self, value):
        self.data['macaddress'] = value

    @property
    def mtu(self):
        """
        Set MTU on interface. Enter a value between 400-65535.
        The same MTU is automatically applied to any VLANs
        created under this physical interface

        :param int value: MTU
        :rtype: int
        """
        return self.data.get('mtu')

    @mtu.setter
    def mtu(self, value):
        self.data['mtu'] = value

    @property
    def multicast_ip(self):
        """
        Enter a multicast address, that is, an IP address from the
        range 224.0.0.0-239.255.255.255.
        The address is used for automatically calculating a MAC address.
        Required only if multicastigmp cvi mode is selected as the cvi_mode.

        :param str value: address
        :rtype: str
        """
        return self.data.get('multicast_ip')

    @multicast_ip.setter
    def multicast_ip(self, value):
        self.data['multicast_ip'] = value

    @property
    def second_interface_id(self):
        """
        Peer interfaces used in LAGG configuration.

        :param str value: comma seperated nic id's for LAGG peers
        :rtype: str
        """
        return self.data.get('second_interface_id')

    @property
    def virtual_mapping(self):
        """
        The virtual mapping id. Required if Virtual Resource chosen.
        See :py:class:`smc.core.engine.VirtualResource.vfw_id`

        :param int value: vfw_id
        :rtype: int
        """
        if self.data.get('virtual_mapping'):
            return int(self.data.get('virtual_mapping'))

    @virtual_mapping.setter
    def virtual_mapping(self, value):
        self.data['virtual_mapping'] = value

    @property
    def virtual_resource_name(self):
        """
        Virtual Resource name used on Master Engine to map a virtual engine.
        See :py:class:`smc.core.engine.VirtualResource.name`

        :param str value: virtual resource name
        :rtype: str
        """
        return self.data.get('virtual_resource_name')

    @virtual_resource_name.setter
    def virtual_resource_name(self, value):
        self.data['virtual_resource_name'] = value

    @property
    def virtual_engine_vlan_ok(self):
        """
        Whether to allow VLAN creation on the Virtual Engine.
        Only valid for master engine.

        :param bool value: enable/disable
        :rtype: bool
        """
        return self.data.get('virtual_engine_vlan_ok')

    @virtual_engine_vlan_ok.setter
    def virtual_engine_vlan_ok(self, value):
        self.data['virtual_engine_vlan_ok'] = value
    

class PhysicalVlanInterface(PhysicalInterface):
    """
    This is a container class used when enumerating vlan interfaces
    from it's top level parent physical interface. This handles being
    able to more cleanly identify the interface type as well as
    being able to modify the top level parent as well as nested nodes
    of this interface. This will always be created from a reference of
    the top level parent interface when attempting to enumerate VLANs.
    """
    typeof = 'physical_vlan_interface'

    def __init__(self, parent, data):
        self._parent = parent
        super(PhysicalVlanInterface, self).__init__(href=None)
        self.data = data
           
    def change_interface_id(self, interface_id):
        """
        Must be performed from the parent PhysicalInterface
        """
        raise NotImplementedError('Changing an interface ID must be done '
            'from the top level PhysicalInterface.')
    
    def change_vlan_id(self, vlan_id):
        """
        Change the VLAN id for this VLAN interface. If this is an
        inline interface, you can specify two interface values to
        create unique VLANs on both sides of the inline pair. Or
        provide a single to use the same VLAN id.
        
        :param str vlan_id: string value for new VLAN id.
        :raises UpdateElementFailed: failed to update the VLAN id
        :return: None
        """
        self._parent.change_vlan_id(self.vlan_id, vlan_id)
    
    def delete(self):
        """
        Delete this VLAN. This is a helper method that allows the VLAN
        to be removed directly from this object reference.
        
        :raises EngineCommandFailed: fail to update
        :return: None
        """
        self._parent.remove_vlan(self.vlan_id)

    @staticmethod
    def create(interface_id, vlan_id,
               virtual_mapping=None,
               virtual_resource_name=None,
               zone_ref=None, **kwargs):
        """
        VLAN Interface
        These interfaces can be applied on all engine types but will be bound to
        being on a physical interface. VLAN's can be applied to layer 3 routed
        interfaces as well as inline interfaces.

        :param int interface_id: id of interface to assign VLAN to
        :param int vlan_id: ID of vlan
        :param int virtual_mapping: The interface ID for the virtual engine. Virtual engine
               interface mapping starts numbering at 0 by default, which means you must
               account for interfaces used by master engine
        :param str virtual_resource_name: Name of virtual resource for this VLAN if a VE
        :rtype: dict
        """
        interface_id = '{}.{}'.format(str(interface_id), str(vlan_id))
        intf = {'interface_id': interface_id,
                'virtual_mapping': virtual_mapping,
                'virtual_resource_name': virtual_resource_name,
                'interfaces': [],
                'zone_ref': zone_helper(zone_ref),
                'comment': kwargs.pop('comment', '')}
        
        interface = kwargs.pop('interface', None)
        if interface:  # Should be sub-interface type
            intf.get('interfaces').append(interface)
        return intf

    @property
    def address(self):
        addr = [data.get('address') for i in self.data.get('interfaces')
                for _, data in i.items()
                if 'address' in data]
        return ','.join(addr) if addr else None

    @property
    def vlan_id(self):
        """
        Retrieve the VLAN id/s for this interface. If this is
        an inline interface with a unique vlan on each interface
        pair, return will be in format: 'interface1-interface2'. For
        example: '11-12'; interface 1 has VLAN 11, and interface pair
        two has VLAN 12.
        
        :return: vlan ID
        :rtype: str
        """
        vlans = self.interface_id.split('.')[-1:]
        for interface in self.data.get('interfaces'):
            for typeof, data in interface.items():
                if typeof == InlineInterface.typeof:
                    v_split = data.get('nicid').split('-')
                    second_vlan = v_split[-1].split('.')[-1]
                    if second_vlan not in vlans:
                        vlans.append(second_vlan)
                        break
            break
        return '-'.join(vlans)
        
    def __str__(self):
        if self.address is not None:
            return '{0}(address={1},vlan_id={2})'.format(
                self.__class__.__name__, self.address, self.vlan_id)
        else:
            return '{0}(vlan_id={1})'.format(
                self.__class__.__name__, self.vlan_id)

    def __repr__(self):
        return str(self)


class VirtualPhysicalInterface(PhysicalInterface):
    """
    VirtualPhysicalInterface
    This interface type is used by virtual engines and has subtle differences
    to a normal interface. For a VE in layer 3 firewall, it also specifies a
    Single Node Interface as the physical interface sub-type.
    When creating the VE, one of the interfaces must be designated as the source
    for Auth Requests and Outgoing.
    """
    typeof = 'virtual_physical_interface'


class AllInterfaces(BaseIterable):
    """
    Iterable for obtaining all Sub Interfaces for PhysicalInterface
    types. This is a iterable over a VPNCollection and SubInterfaceCollection.
    Using `get` will check each collection for the interface result based
    on kwarg arguments or return None.
    
    :param list interfaces: list of iterable classes (VlanCollection,
        SubInterfaceCollection)
    :rtype: SubInterface or PhysicalVlanInterface
    """
    def __init__(self, interfaces):
        super(AllInterfaces, self).__init__(interfaces)
    
    def __iter__(self):
        for it in self.items:
            for element in it:
                yield element   
    
    def __len__(self):
        return sum(len(x) for x in self.items)
    
    def get(self, *args, **kwargs):
        """
        Get from the interface collection. It is more accurate to use
        kwargs to specify an attribute of the sub interface to retrieve
        rather than using an index value. If retrieving using an index,
        the collection will first check vlan interfaces and standard
        interfaces second. In most cases, if VLANs exist, standard
        interface definitions will be nested below the VLAN with exception
        of Inline Interfaces which may have both.
        
        :param int args: index to retrieve
        :param kwargs: key value for sub interface
        :rtype: SubInterface or None
        """
        for collection in self.items:
            if args:
                index = args[0]
                if len(collection) and (index <= len(collection)-1):
                    return collection[index]
            else:
                # Collection with get
                result = collection.get(**kwargs)
                if result is not None:
                    return result
        return None


class VlanCollection(BaseIterable):
    """
    A collection of VLAN interfaces. This will return
    PhysicalVlanInterface types that will inherit from PhysicalInterface.
    
    :param SubInterfaceCollection interface: interface collection
    :rtype: PhysicalVlanInterface
    """
    def __init__(self, interface):
        data = [PhysicalVlanInterface(interface, vlan)
                for vlan in interface.data.get('vlanInterfaces', [])]
        super(VlanCollection, self).__init__(data)

    @property
    def vlan_ids(self):
        """
        Return all VLAN ids in use by the interface
        
        :return: list of VLANs by ID
        :rtype: list
        """
        return [vlan.vlan_id for vlan in self]
    
    def get_vlan(self, *args, **kwargs):
        """
        Get the PhysicalVlanInterface from this PhysicalInterface.
        Use args if you want to specify only the VLAN id. Otherwise
        you can specify a valid attribute for the PhysicalVlanInterface
        such as `address` for example::
        
            >>> itf = engine.interface.get(2)
            >>> list(itf.vlan_interface)
            [PhysicalVlanInterface(address=36.35.35.37,vlan_id=2),
             PhysicalVlanInterface(vlan_id=3),
             PhysicalVlanInterface(address=12.12.12.13,12.12.12.12,vlan_id=4)]
            >>> itf.vlan_interface.get_vlan(2)
            PhysicalVlanInterface(address=36.35.35.37,vlan_id=2)
            >>> itf.vlan_interface.get_vlan(4)
            PhysicalVlanInterface(address=12.12.12.13,12.12.12.12,vlan_id=4)
            >>> itf.vlan_interface.get_vlan(address='36.35.35.37')
            PhysicalVlanInterface(address=36.35.35.37,vlan_id=2)
        
        :param int args: args are translated to vlan_id=args[0]
        :param kwargs: key value for sub interface
        :raises InterfaceNotFound: VLAN interface could not be found
        :rtype: PhysicalVlanInterface
        """
        if args:
            kwargs = {'vlan_id': str(args[0])}
        key, value = kwargs.popitem()
        for vlan in self:
            if getattr(vlan, key, None) == value:
                return vlan
        raise InterfaceNotFound('VLAN ID {} was not found on this engine.'
            .format(value))
        
    def get(self, *args, **kwargs):
        """
        Get the sub interfaces for this PhysicalVlanInterface.
        
            >>> itf = engine.interface.get(2)
            >>> list(itf.vlan_interface)
            [PhysicalVlanInterface(address=36.35.35.37,vlan_id=2),
             PhysicalVlanInterface(vlan_id=3),
             PhysicalVlanInterface(address=12.12.12.13,12.12.12.12,vlan_id=4)]
            >>> itf.vlan_interface.get(2)
            SingleNodeInterface(address=36.35.35.37, vlan_id=2)
            >>> vlan4 = itf.vlan_interface.get_vlan(4)
            >>> list(vlan4.interfaces)
            [SingleNodeInterface(address=12.12.12.13, vlan_id=4), SingleNodeInterface(address=12.12.12.12, vlan_id=4)]
            >>> itf.vlan_interface.get(address='36.35.35.37')
            SingleNodeInterface(address=36.35.35.37, vlan_id=2)
        
        :param int args: args are translated to vlan_id=args[0]
        :param kwargs: key value for sub interface
        :rtype: SubInterface or None
        """
        if args:
            kwargs = {'vlan_id': str(args[0])}
        key, value = kwargs.popitem()
        for item in self:
            for vlan in item.interfaces:
                if getattr(vlan, key, None) == value:
                    return vlan


class InterfaceModifier(object):
    """
    Helper class to manipulate the engine physical interfaces. This is
    useful when changes need to be made against multiple interfaces and
    submitted back to SMC. For example, changing primary management requires
    the existing interface disable this setting.
    In addition, since we already have the full engine json, this eliminates
    additional SMC calls.
    """
    
    def __init__(self, data, **kwargs):
        self._data = data if data else []
        self.engine = kwargs.get('engine')
    
    @classmethod
    def byEngine(cls, engine):
        interfaces = []
        for intf in engine.data.get('physicalInterfaces'):
            for typeof, data in intf.items():
                subif_type = extract_sub_interface(data)
                if isinstance(subif_type, InlineInterface):
                    nicids = subif_type.nicid.split('-')
                    name = data['name'] if 'name' in data else \
                        'Interface %s - Interface %s (Inline)' % (nicids[0], nicids[1])
                else:
                    name = data['name'] if 'name' in data else \
                        'Interface %s' % data.get('interface_id')
                
                clazz = lookup_class((typeof), Interface)(
                    name=name,
                    type=typeof,
                    href=InterfaceModifier.href_from_link(data.get('link')))
                clazz.data = ElementCache(data)
                clazz._engine = engine
                interfaces.append(clazz)
        return cls(interfaces, engine=engine)
    
    def get(self, interface_id):
        """
        Get the interface from engine json
        
        :param str interface_id: interface ID to find
        :raises InterfaceNotFound: Cannot find interface
        """
        # From within engine, skips nested iterators for this find
        # Make sure were dealing with a string
        interface_id = str(interface_id)
        for intf in self:
            if intf.interface_id == interface_id:
                intf._engine = self.engine
                return intf
            else: # Check for inline interfaces
                if '.' in interface_id:
                    # It's a VLAN interface
                    vlan = interface_id.split('.')
                    # Check that we're on the right interface
                    if intf.interface_id == vlan[0]:
                        if intf.has_vlan:
                            return intf.vlan_interface.get_vlan(vlan[-1])

                elif intf.has_interfaces:
                    for interface in intf.interfaces:
                        if isinstance(interface, InlineInterface):
                            split_intf = interface.nicid.split('-')
                            if interface_id == interface.nicid or \
                                str(interface_id) in split_intf:
                                intf._engine = self.engine
                                return intf

        raise InterfaceNotFound(
            'Interface id {} was not found on this engine.'.format(interface_id))
    
    @staticmethod        
    def href_from_link(link):
        for links in link:
            if links.get('href'):
                return links['href']
    
    def __len__(self):
        return len(self._data)
            
    def __iter__(self):
        for interface in self._data:
            yield interface
    
    def find_mgmt_interface(self, mgmt):
        """
        Find the management interface specified and return
        either the string representation of the interface_id.
        
        Valid options: primary_mgt, backup_mgt, primary_heartbeat,
            backup_heartbeat, outgoing, auth_request
        
        :return: str interface_id
        """
        for intf in self:
            for allitf in intf.all_interfaces:
                if isinstance(allitf, PhysicalVlanInterface):
                    for vlan in allitf.interfaces:
                        if getattr(vlan, mgmt, None):
                            return allitf.interface_id
                else:
                    if getattr(allitf, mgmt, None):
                        return intf.interface_id
                            
    def set_unset(self, interface_id, attribute, address=None):
        """
        Set attribute to True and unset the same attribute for all other
        interfaces. This is used for interface options that can only be
        set on one engine interface. 
        
        :raises InterfaceNotFound: raise if specified address does not exist or
            if the interface is not supported for this management role (i.e. you
            cannot set primary mgt to a CVI interface with no nodes).
        """
        interface = self.get(interface_id) if interface_id is not None else None
        if address is not None:
            target_network = None
            sub_interface = interface.all_interfaces.get(address=address)
            if sub_interface:
                target_network = sub_interface.network_value

            if not target_network:
                raise InterfaceNotFound('Address specified: %s was not found on interface '
                    '%s' % (address, interface_id))
        
        for interface in self:
            all_subs = interface.sub_interfaces()
            for sub_interface in all_subs:
                # Skip VLAN only interfaces (no addresses)
                if not isinstance(sub_interface, (PhysicalVlanInterface, InlineInterface)):
                    if getattr(sub_interface, attribute) is not None:
                        if sub_interface.nicid == str(interface_id):
                            if address is not None:
                                if sub_interface.network_value == target_network:
                                    sub_interface[attribute] = True
                                else:
                                    sub_interface[attribute] = False
                            else:
                                sub_interface[attribute] = True
                        else: #unset
                            sub_interface[attribute] = False

    def set_auth_request(self, interface_id, address=None):
        """
        Set auth request, there can only be one per engine so unset all
        other interfaces. If this is a cluster, auth request can only be
        set on an interface with a CVI (not valid on NDI only cluster
        interfaces).
        If this is a cluster interface, address should be CVI IP.
        """
        for engine_type in ['master', 'layer2', 'ips']:
            if engine_type in self.engine.type:
                return
        
        interface = self.get(interface_id)
        if 'cluster' in self.engine.type:
            # Auth request on a cluster interface must have at least a CVI.
            # It cannot bind to NDI only interfaces
            if not any(isinstance(itf, ClusterVirtualInterface)
                       for itf in interface.all_interfaces):
                raise InterfaceNotFound('The interface specified: %s does not have '
                    'a CVI interface defined and therefore cannot be used as an '
                    'interface for auth_requests. If setting the primary_mgt interface '
                    'provide an interface id for auth_request.' % interface_id)
        
        current_interface = self.get(
            self.engine.interface_options.auth_request)
        for itf in current_interface.all_interfaces:
            if getattr(itf, 'auth_request', False):
                itf['auth_request'] = False
        # Set
        sub_if = interface.all_interfaces
        if any(isinstance(itf, ClusterVirtualInterface) for itf in sub_if):
            for itf in sub_if:
                if isinstance(itf, ClusterVirtualInterface):
                    if address:
                        if getattr(itf, 'address', None) == address:
                            itf['auth_request'] = True
                    else:
                        itf['auth_request'] = True
        else:
            for itf in sub_if:
                if getattr(itf, 'auth_request', None) is not None:
                    if address:
                        if getattr(itf, 'address', None) == address:
                            itf['auth_request'] = True
                    else:
                        itf['auth_request'] = True
    
    @property
    def data(self):
        interfaces = [{intf.typeof: intf.data} for intf in self._data]
        self.engine.data['physicalInterfaces'] = interfaces
        return self.engine.data
    

class InterfaceBuilder(object):
    """
    InterfaceBuilder is a configuration container to simplify
    access to interface creation and modification.
    """

    def __init__(self, cls=PhysicalInterface, **kw):
        if kw:  # existing interface
            for name, value in kw.items():
                setattr(self, name, value)
        else:
            setattr(self, 'interface_id', None)
            setattr(self, 'interfaces', [])

            if cls is PhysicalInterface:
                setattr(self, 'vlanInterfaces', [])
                setattr(self, 'zone_ref', None)
    
    @property
    def zone(self):
        pass
    
    @zone.setter
    def zone(self, value):
        setattr(self, 'zone_ref', zone_helper(value))
    
    def add_vlan_only(self, vlan_id, virtual_mapping=None,
                      virtual_resource_name=None, zone_ref=None,
                      comment=None):
        """
        Create a VLAN interface, no addresses, layer 3 interfaces only
        """
        vlan = PhysicalVlanInterface.create(
            self.interface_id,
            vlan_id,
            virtual_mapping,
            virtual_resource_name,
            zone_ref=zone_ref,
            comment=comment)

        self.vlanInterfaces.append(vlan)
    
    def add_cvi_only(self, address, network_value,
                     is_mgmt=False):
        """
        Add a CVI and NDI
        """
        cvi = ClusterVirtualInterface.create(
            self.interface_id,
            address, network_value)

        if is_mgmt:
            #cvi.auth_request = True
            cvi.update(auth_request=True)

        self.interfaces.append({cvi.typeof: cvi.data})

    def add_sni_only(self, address, network_value, is_mgmt=False,
                     **kw):
        """
        Add Single Node Interface - Layer 3 single FW only
        """
        sni = SingleNodeInterface.create(
            self.interface_id,
            address,
            network_value,
            **kw)

        if is_mgmt:
            sni.update(auth_request=True, outgoing=True, primary_mgt=True)

        self.__dict__.setdefault('interfaces', []).append(
            {sni.typeof: sni.data})

    def add_ndi_only(self, address, network_value, nodeid=1, is_mgmt=False,
                     **kw):
        """
        Add NDI, for all engine types except single fw
        """
        ndi = NodeInterface.create(
            interface_id=self.interface_id,
            address=address,
            network_value=network_value,
            nodeid=nodeid,
            **kw)

        if is_mgmt:
            ndi.update(primary_mgt=True, outgoing=True, primary_heartbeat=True)

        self.interfaces.append({ndi.typeof: ndi.data})
    
    def add_capture(self, logical_interface_ref):
        """
        Add capture interface, only for layer 2, IPS
        """
        capture = CaptureInterface.create(
            self.interface_id,
            logical_intf_helper(logical_interface_ref))
    
        self.interfaces.append({capture.typeof: capture.data})
        
    def add_inline(self, interface_id, logical_interface_ref,
                   failure_mode='normal', zone_ref=None):
        """
        Add inline interface. This is a generic builder for
        inline interfaces on layer 2 and IPS engines only.
        For layer 3 FW engines, use add_l2_inline.
        """
        inline = InlineInterface.create(
            interface_id,
            logical_interface_ref=logical_intf_helper(logical_interface_ref),
            failure_mode=failure_mode,
            zone_ref=zone_ref)  # Zone ref directly on the inline interface

        self.interfaces.append({inline.typeof: inline.data})
    
    def add_l2_inline(self, interface_id, logical_interface_ref,
                      vlan_id=None, vlan_id2=None,
                      zone_ref_intf1=None,
                      zone_ref_intf2=None,
                      if_type=InlineInterface,
                      **kw):
        """
        Add a layer 2 inline interface. This handles inline interfaces
        for L2FW, IPS engine and Layer 3 FW.
        
        For inline IPS interfaces, use **kw to provide failure_mode setting
        if needed. Default is 'normal'.
        
        :param if_type: specify the sub interface type. Default is InlineInterface.
            If this should be an inline interface for an l3 firewall, this value
            will be InlineIPSInterface or InlineL2FWInterface.
        """
        logical_interface_ref = logical_intf_helper(logical_interface_ref)
        
        if not self.interfaces:
            # Create base parent inline
            inline = if_type.create(
                interface_id,
                logical_interface_ref=logical_interface_ref)
            
            if not vlan_id:
                inline.update(zone_ref=zone_helper(zone_ref_intf2))
            
            if if_type is not InlineL2FWInterface:
                inline.update(failure_mode=kw.get('failure_mode', 'normal'))
            
            self.interfaces.append({inline.typeof: inline.data})
        
        if vlan_id:
            first_intf = interface_id.split('-')[0]

            vlan = PhysicalVlanInterface.create(
                first_intf,
                vlan_id,
                zone_ref=zone_ref_intf1)
            
            inline_intf = if_type.create(
                interface_id,
                logical_interface_ref,
                zone_ref=zone_helper(zone_ref_intf2))
            
            if if_type is not InlineL2FWInterface:
                # Get failure mode setting from parent interface
                parent_if = if_type(self.interfaces[0][if_type.typeof])
                inline_intf.update(failure_mode=parent_if.failure_mode)
            
            if if_type is not InlineInterface:
                # Layer 3 FW inline interfaces can only have 1 VLAN on the
                # interface pair, whereas L2FW and IPS can each have unique.
                vlan_id2 = vlan_id
            
            vlan.get('interfaces').append(
                _add_vlan_to_inline(
                    {inline_intf.typeof: inline_intf.data},
                    vlan_id,
                    vlan_id2))

            self.vlanInterfaces.append(vlan)
            
    def add_dhcp(self, dynamic_index, is_mgmt=False):
        """
        Add a DHCP interface
        """
        intf = SingleNodeInterface.create_dhcp(
            self.interface_id,
            dynamic_index)

        if is_mgmt:
            intf.update(primary_mgt=True, reverse_connection=True,
                        automatic_default_route=True)
        
        self.interfaces.append({intf.typeof: intf.data})

    def add_sni_to_vlan(self, address, network_value, vlan_id,
                        nodeid=1, zone_ref=None, comment=None,
                        cls=SingleNodeInterface, **kw):
        """
        Helper method to call add_ndi_to_vlan. This is only for single
        node interfaces. Keyword arguments can be passed for any node field
        that is valid (i.e. backup_mgt, etc).
        """
        self.add_ndi_to_vlan(
            address, network_value, vlan_id, nodeid, zone_ref, comment, cls, **kw)
    
    def add_ndi_to_vlan(self, address, network_value, vlan_id,
                        nodeid=1, zone_ref=None, comment=None, cls=NodeInterface, **kw):
        """
        Add IP address to an ndi/sni. If the VLAN doesn't exist,
        create it. Interface class is passed in to create the
        proper sub-interface (SingleNode or Node). Keyword arguments can
        be passed for any node field that is valid (i.e. backup_mgt, etc).
        """
        vlan_str = '{}.{}'.format(self.interface_id, vlan_id)
        found = False
        for vlan in self.vlanInterfaces:
            if vlan.get('interface_id') == vlan_str:
                intf = cls.create(
                    self.interface_id,
                    address,
                    network_value,
                    nodeid=nodeid,
                    nicid=vlan_str,
                    **kw)
                
                if vlan.get('interfaces'):
                    vlan['interfaces'].append({intf.typeof: intf.data})
                else:  # VLAN exists but no interfaces assigned
                    vlan['interfaces'] = [{intf.typeof: intf.data}]
                found = True
                break
        if not found:  # create new
            intf = cls.create(
                self.interface_id,
                address,
                network_value,
                nicid=vlan_str,
                nodeid=nodeid,
                **kw)
            vlan = PhysicalVlanInterface.create(
                self.interface_id,
                vlan_id,
                zone_ref=zone_ref,
                interface={intf.typeof: intf.data},
                comment=comment)

            self.vlanInterfaces.append(vlan)

    def add_cvi_to_vlan(self, address, network_value, vlan_id, zone_ref=None,
                        comment=None, **kw):
        """
        Add a CVI into a vlan.
        If this CVI needs to be a management interface, pass auth_request=True
        as keyword
        """
        vlan_str = '{}.{}'.format(self.interface_id, vlan_id)
        cvi = ClusterVirtualInterface.create(
            self.interface_id,
            address,
            network_value,
            nicid=vlan_str,
            **kw)

        vlan = PhysicalVlanInterface.create(
            self.interface_id,
            vlan_id,
            interface={cvi.typeof: cvi.data},
            zone_ref=zone_ref,
            comment=comment)

        self.vlanInterfaces.append(vlan)
        
    def remove_vlan(self, vlan_id):
        """
        Remove vlan from existing interface
        """
        vlan_str = '{}.{}'.format(self.interface_id, vlan_id)
        vlans = []
        for vlan in self.vlanInterfaces:
            if not vlan.get('interface_id') == vlan_str:
                vlans.append(vlan)

        self.vlanInterfaces = vlans
    
    @property
    def data(self):
        return vars(self)

    @classmethod
    def getBuilder(cls, instance, interface_id):
        """
        Optional loader to get a builder and load an existing
        configuration based on interface id. This allows toggling
        between updating (PUT) on the interface, versus creating
        new (POST). If 'interface' attribute is None, an existing
        interface did not exist, otherwise return that reference.
        """
        try:
            interfaces = InterfaceModifier.byEngine(instance._engine)
            interface = interfaces.get(interface_id)
        except InterfaceNotFound:
            if instance.__class__ is TunnelInterface:
                builder = InterfaceBuilder(TunnelInterface)
            else:
                builder = InterfaceBuilder()
            builder.interface_id = interface_id
            interface = None
        else:
            builder = InterfaceBuilder(**interface.data)

        return (builder, interface)  # Return builder, interface ref


def extract_sub_interface(data):
    for intf in data.get('interfaces', []):
        for if_type, values in intf.items():
            return get_sub_interface(if_type)(values)

