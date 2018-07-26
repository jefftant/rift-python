import sys
sys.path.append('gen-py')

import enum
import logging
import os
import socket
import sortedcontainers
import uuid

import common.constants
import constants
import fsm
import interface
import offer
import rift
import table
import timer
import utils

# TODO: Command line argument and/or configuration option for CLI port

class Node:

    _next_node_nr = 1

    ZTP_MIN_NUMBER_OF_PEER_FOR_LEVEL = 3

    class State(enum.Enum):
        UPDATING_CLIENTS = 1
        HOLDING_DOWN = 2
        COMPUTE_BEST_OFFER = 3

    class Event(enum.Enum):
        CHANGE_LOCAL_LEAF_INDICATIONS = 1
        CHANGE_LOCAL_CONFIGURED_LEVEL = 2
        NEIGHBOR_OFFER = 3
        # WITHDRAW_NEIGHBOR_OFFER = 4       Removed. See deviation DEV-1 in doc/deviations.md. TODO: remove line completely.
        BETTER_HAL = 5
        BETTER_HAT = 6
        LOST_HAL = 7
        LOST_HAT = 8
        COMPUTATION_DONE = 9
        HOLDDOWN_EXPIRED = 10
        SHORT_TICK_TIMER = 11

    def remove_offer(self, offer, reason):
        offer.removed = True
        offer.removed_reason = reason
        self._rx_offers[offer.interface_name] = offer
        self.compare_offers()

    def update_offer(self, offer):
        self._rx_offers[offer.interface_name] = offer
        self.compare_offers()

    def better_offer(self, offer1, offer2, three_way_only):
        # Don't consider removed offers
        if (offer1 != None) and (offer1.removed):
            offer1 = None
        if (offer2 != None) and (offer2.removed):
            offer2 = None
        # Don't consider offers that are marked "not a ZTP offer"
        if (offer1 != None) and (offer1.not_a_ztp_offer):
            offer1 = None
        if (offer2 != None) and (offer2.not_a_ztp_offer):
            offer2 = None
        # If asked to do so, only consider offers from neighbors in state 3-way as valid candidates
        if three_way_only:
            if (offer1 != None) and (offer1.state != interface.Interface.State.THREE_WAY):
                offer1 = None
            if (offer2 != None) and (offer2.state != interface.Interface.State.THREE_WAY):
                offer2 = None
        # If there is only one candidate, it automatically wins. If there are no canidates, there is no best.
        if offer1 == None:
            return offer2
        if offer2 == None:
            return offer1
        # Pick the offer with the highest level
        if offer1.level > offer2.level:        
            return offer1
        if offer2.level < offer1.level:
            return offer2
        # If the level is the same for both offers, pick offer with lowest system id as the tie braker
        if offer1.system_id < offer2.system_id:
            return offer1
        return offer2

    def compare_offers(self):
        # Select "best offer" and "best offer in 3-way state" and do update flags on the offers
        best_offer = None
        best_offer_three_way = None
        for offer in self._rx_offers.values():
            offer.best = False
            offer.best_three_way = False
            best_offer = self.better_offer(best_offer, offer, False)
            best_offer_three_way = self.better_offer(best_offer_three_way, offer, True)
        if best_offer:
            best_offer.best = True
        if best_offer_three_way:
            best_offer_three_way.best_three_way = True
        # Determine if the Highest Available Level (HAL) would change based on the current offer.
        # If it would change, push an event, but don't update the HAL yet.
        if best_offer: 
            hal = best_offer.level
        else:
            hal = None
        if self._highest_available_level != hal:
            if hal:
                self._fsm.push_event(self.Event.BETTER_HAL)
            else:
                self._fsm.push_event(self.Event.LOST_HAL)
        # Determine if the Highest Adjacency Three-way (HAT) would change based on the current offer.
        # If it would change, push an event, but don't update the HAL yet.
        if best_offer_three_way:
            hat = best_offer_three_way.level
        else:
            hat = None
        if self._highest_adjacency_three_way != hat:
            if hat:
                self._fsm.push_event(self.Event.BETTER_HAT)
            else:
                self._fsm.push_event(self.Event.LOST_HAT)

    def level_compute(self):
        # Find best offer overall and best offer in state 3-way. This was computer earlier in compare_offers,
        # which set the flag on the offer to remember the result.
        best_offer = None
        best_offer_three_way = None
        for offer in self._rx_offers.values():
            if offer.best:
                best_offer = offer
            if offer.best_three_way:
                best_offer_three_way = offer
        # Update Highest Available Level (HAL)
        if best_offer: 
            hal = best_offer.level
        else:
            hal = None
        self._highest_available_level = hal
        # Update Adjacency Three-way (HAT)
        if best_offer_three_way:
            hat = best_offer_three_way.level
        else:
            hat = None
        self._highest_adjacency_three_way = hat
        # Push event COMPUTATION_DONE
        self._fsm.push_event(self.Event.COMPUTATION_DONE)

    def action_no_action(self):
        pass

    def action_level_compute(self):
        self.level_compute()

    def action_store_leaf_flags(self, leaf_flags):
        # TODO: on ChangeLocalLeafIndications in UpdatingClients finishes in ComputeBestOffer: store leaf flags
        pass

    def action_update_or_remove_offer(self, offer):
        if offer.not_a_ztp_offer == True:
            self.remove_offer(offer, "Not a ZTP offer flag set")
        elif offer.level == None:
            self.remove_offer(offer, "Level is undefined")
        elif offer.level <= common.constants.leaf_level:
            self.remove_offer(offer, "Level is leaf")
        else:
            self.update_offer(offer)

    def action_store_level(self, level):
        self._configured_level = level

    def action_purge_offers(self):
        # TODO
        pass

    def action_check_hold_time_expired(self):
        # TODO
        pass

    def action_update_all_lie_fsms_with_computation_results(self):
        if self._highest_available_level == None:
            self._derived_level = None
        elif self._highest_available_level > 0:
            self._derived_level = self._highest_available_level - 1
        else:
            self._derived_level = 0

    def action_update_holddown_timer_on_lost_hal(self):
        # TODO:
        # if any southbound adjacencies present
        #   then update holddown timer to normal duration
        #   else fire   holddown    timer    immediately
        pass

    _state_updating_clients_transitions = {
        Event.CHANGE_LOCAL_LEAF_INDICATIONS:    (State.COMPUTE_BEST_OFFER, [action_store_leaf_flags]),
        Event.LOST_HAT:                         (State.COMPUTE_BEST_OFFER, [action_no_action]),
        Event.BETTER_HAT:                       (State.COMPUTE_BEST_OFFER, [action_no_action]),
        Event.SHORT_TICK_TIMER:                 (None, [action_no_action]),
        Event.LOST_HAL:                         (State.HOLDING_DOWN, [action_update_holddown_timer_on_lost_hal]),
        Event.NEIGHBOR_OFFER:                   (None, [action_update_or_remove_offer]),
        Event.CHANGE_LOCAL_CONFIGURED_LEVEL:    (State.COMPUTE_BEST_OFFER, [action_store_level]),
        Event.BETTER_HAL:                       (State.COMPUTE_BEST_OFFER, [action_no_action]),
        # Event.WITHDRAW_NEIGHBOR_OFFER:          (None, [action_remove_offer]),     Removed. See deviation DEV-1 in doc/deviations.md. TODO: remove line completely.
    }

    _state_holding_down_transitions = {
        Event.LOST_HAT:                         (None, [action_no_action]),
        Event.LOST_HAL:                         (None, [action_no_action]),
        Event.BETTER_HAT:                       (None, [action_no_action]),
        Event.CHANGE_LOCAL_CONFIGURED_LEVEL:    (State.COMPUTE_BEST_OFFER,[action_store_level]),
        Event.HOLDDOWN_EXPIRED:                 (State.COMPUTE_BEST_OFFER,[action_purge_offers]),
        Event.SHORT_TICK_TIMER:                 (None, [action_check_hold_time_expired]),
        Event.NEIGHBOR_OFFER:                   (None, [action_update_or_remove_offer]),
        Event.BETTER_HAL:                       (None, [action_no_action]),
        # Event.WITHDRAW_NEIGHBOR_OFFER:          (None, [action_remove_offer]),     Removed. See deviation DEV-1 in doc/deviations.md. TODO: remove line completely.
        Event.CHANGE_LOCAL_LEAF_INDICATIONS:    (State.COMPUTE_BEST_OFFER, [action_store_leaf_flags]),
        Event.COMPUTATION_DONE:                 (None, [action_no_action])
    }

    _state_compute_best_offer_transitions = {
        Event.LOST_HAT:                         (None, [action_level_compute]),
        Event.NEIGHBOR_OFFER:                   (None, [action_update_or_remove_offer]),
        Event.BETTER_HAL:                       (None, [action_level_compute]),
        Event.SHORT_TICK_TIMER:                 (None, [action_no_action]),
        Event.COMPUTATION_DONE:                 (State.UPDATING_CLIENTS, [action_no_action]),
        Event.CHANGE_LOCAL_CONFIGURED_LEVEL:    (None, [action_store_level, action_level_compute]),
        Event.LOST_HAL:                         (State.HOLDING_DOWN, [action_update_holddown_timer_on_lost_hal]),
        Event.BETTER_HAT:                       (None, [action_level_compute]),
        # Event.WITHDRAW_NEIGHBOR_OFFER:          (None, [action_remove_offer]),     Removed. See deviation DEV-1 in doc/deviations.md. TODO: remove line completely.
        Event.CHANGE_LOCAL_LEAF_INDICATIONS:    (None, [action_store_leaf_flags, action_level_compute])
    }

    _transitions = {
        State.UPDATING_CLIENTS: _state_updating_clients_transitions,
        State.HOLDING_DOWN: _state_holding_down_transitions,
        State.COMPUTE_BEST_OFFER: _state_compute_best_offer_transitions
    }

    _state_entry_actions = {
        State.UPDATING_CLIENTS:     [action_update_all_lie_fsms_with_computation_results],
        State.COMPUTE_BEST_OFFER:   [action_level_compute]
    }

    fsm_definition = fsm.FsmDefinition(
        state_enum = State, 
        event_enum = Event, 
        transitions = _transitions, 
        state_entry_actions = _state_entry_actions,
        initial_state = State.COMPUTE_BEST_OFFER)    

    def __init__(self, rift, config):
        # TODO: process state_thrift_services_port field in config
        # TODO: process config_thrift_services_port field in config
        # TODO: process v4prefixes field in config
        # TODO: process v6prefixes field in config
        self._rift = rift
        self._config = config
        self._node_nr = Node._next_node_nr
        Node._next_node_nr += 1
        self._name = self.get_config_attribute('name', self.generate_name())
        self._passive = self.get_config_attribute('passive', False)
        self._running = self.is_running()
        self._system_id = self.get_config_attribute('systemid', self.generate_system_id())
        self._log_id = utils.system_id_str(self._system_id)
        self._log = logging.getLogger('node')
        self._log.info("[{}] Create node".format(self._log_id))
        self.parse_level_config()
        self._interfaces = sortedcontainers.SortedDict()
        self._mcast_loop = True      # TODO: make configurable
        self._rx_lie_ipv4_mcast_address = self.get_config_attribute('rx_lie_mcast_address', constants.DEFAULT_LIE_IPV4_MCAST_ADDRESS)
        self._tx_lie_ipv4_mcast_address = self.get_config_attribute('tx_lie_mcast_address', constants.DEFAULT_LIE_IPV4_MCAST_ADDRESS)
        self._rx_lie_ipv6_mcast_address = self.get_config_attribute('rx_lie_v6_mcast_address', constants.DEFAULT_LIE_IPV6_MCAST_ADDRESS)
        self._tx_lie_ipv6_mcast_address = self.get_config_attribute('tx_lie_v6_mcast_address', constants.DEFAULT_LIE_IPV6_MCAST_ADDRESS)
        self._rx_lie_port = self.get_config_attribute('rx_lie_port', constants.DEFAULT_LIE_PORT)
        self._tx_lie_port = self.get_config_attribute('tx_lie_port', constants.DEFAULT_LIE_PORT)
        self._lie_send_interval_secs = constants.DEFAULT_LIE_SEND_INTERVAL_SECS   # TODO: make configurable
        self._rx_tie_port = self.get_config_attribute('rx_tie_port', constants.DEFAULT_TIE_PORT)
        self._derived_level = None
        self._rx_offers = {}
        self._tx_offers = {}
        self._highest_available_level = None
        self._highest_adjacency_three_way = None
        self._fsm_log = self._log.getChild("fsm")
        # TODO: Take ztp hold time from init file
        self._holdtime = 1
        # TODO: Add where a leaf comes from
        self._next_interface_id = 1
        if 'interfaces' in config:
            for interface_config in self._config['interfaces']:
                self.create_interface(interface_config)
        self._fsm = fsm.Fsm(
            definition = self.fsm_definition,
            action_handler = self,
            log = self._fsm_log,
            log_id = self._log_id)
        self._fsm.start()
        self._one_second_timer = timer.Timer(1.0, lambda: self._fsm.push_event(self.Event.SHORT_TICK_TIMER))

    def info(self, logger, msg):
        logger.info("[{}] {}".format(self._node._log_id, msg))   # TODO: Make node._log_id public

    def warning(self, logger, msg):
        logger.warning("[{}] {}".format(self._node._log_id, msg))

    def generate_system_id(self):
        mac_address = uuid.getnode()
        pid = os.getpid()
        system_id = ((mac_address & 0xffffffffff) << 24) | (pid & 0xffff) << 8 | (self._node_nr & 0xff)
        return system_id

    def generate_name(self):
        return socket.gethostname().split('.')[0] + str(self._node_nr)

    def parse_level_config(self):
        self._configured_level_symbol = self.get_config_attribute('level', 'undefined')
        if self._configured_level_symbol == 'undefined':
            self._configured_level = None
            self._leaf_only = False
            self._leaf_2_leaf = False
            self._superspine_flag = False
        elif self._configured_level_symbol == 'leaf':
            self._configured_level = None
            self._leaf_only = True
            self._leaf_2_leaf = False
            self._superspine_flag = False
        elif self._configured_level_symbol == 'leaf-2-leaf':
            self._configured_level = None
            self._leaf_only = True
            self._leaf_2_leaf = True
            self._superspine_flag = False
        elif self._configured_level_symbol == 'superspine':
            self._configured_level = None
            self._leaf_only = False
            self._leaf_2_leaf = False
            self._superspine_flag = True
        elif isinstance(self._configured_level_symbol, int):
            self._configured_level = self._configured_level_symbol
            self._leaf_only = (self._configured_level == 0)
            self._leaf_2_leaf = False
            self._superspine_flag = False
        else:
            # Should have been caught earlier by config validation.
            assert False, "Invalid level {}".format(self._configured_level)

    def level_value(self):
        if self._configured_level != None:
            return self._configured_level
        elif self._superspine_flag:
            return common.constants.default_superspine_level
        elif self._leaf_only:
            return common.constants.leaf_level
        else:
            return self._derived_level

    def level_value_str(self):
        level_value = self.level_value()
        if level_value == None:
            return 'undefined'
        else:
            return str(level_value)

    def record_tx_offer(self, tx_offer):
        self._tx_offers[tx_offer.interface_name] = tx_offer

    def is_poison_reverse_interface(self, interface_name):
        # TODO: Introduce concept of HALS (HAL offering Systems) and simply check for membership
        # Section 4.2.9.4.6 / Section B.1.3.2
        # If we received a valid offer over the interface, and the level in that offer is equal to the highest
        # available level (HAL) for this node, then we need to poison reverse, i.e. we need to set the 
        # not_a_ztp_offer flag on offers that we send out over the interface.
        if not interface_name in self._rx_offers:
            # We did not receive an offer over the interface
            return False
        rx_offer = self._rx_offers[interface_name]
        if rx_offer.removed:
            # We received an offer, but it was removed from consideration for some reason
            # (e.g. level undefined, not-a-ztp-offer flag was set, received from a leaf, ...)
            return False
        if rx_offer.level == self._highest_available_level:
            return True
        return False

    def zero_touch_provisioning_enabled(self):
        # Is "Zero Touch Provisiniong (ZTP)" aka "automatic level derivation" aka "level determination procedure"
        # aka "auto configuration" active? The criteria that determine whether ZTP is enabled are spelled out in 
        # the first paragraph of section 4.2.9.4.
        if self._configured_level != None:
            return False
        elif self._superspine_flag:
            return False
        elif self._leaf_only:
            return False
        else:
            return True

    def is_running(self):
        if self._rift.active_nodes == rift.Rift.ActiveNodes.ONLY_PASSIVE_NODES:
            running = self._passive
        elif self._rift.active_nodes == rift.Rift.ActiveNodes.ALL_NODES_EXCEPT_PASSIVE_NODES:
            running = not self._passive
        else:
            running = True
        return running

    def get_config_attribute(self, attribute, default):
        if attribute in self._config:
            return self._config[attribute]
        else:
            return default

    def create_interface(self, interface_config):
        interface_name = interface_config['name']
        self._interfaces[interface_name] = interface.Interface(self, interface_config)

    def cli_detailed_attributes(self):
        return [
            ["Name", self._name],
            ["Passive", self._passive],
            ["Running", self.is_running()],
            ["System ID", utils.system_id_str(self._system_id)],
            ["Configured Level", self._configured_level_symbol],
            ["Leaf Only", self._leaf_only],
            ["Leaf 2 Leaf", self._leaf_2_leaf],
            ["Superspine Flag", self._superspine_flag],
            ["Zero Touch Provisioning (ZTP) Enabled", self.zero_touch_provisioning_enabled()],
            ["Highest Available Level (HAL)", self._highest_available_level],
            ["Highest Adjacency Three-way (HAT)", self._highest_adjacency_three_way],
            ["Level Value", self.level_value()],
            ["Multicast Loop", self._mcast_loop],
            ["Receive LIE IPv4 Multicast Address", self._rx_lie_ipv4_mcast_address],
            ["Transmit LIE IPv4 Multicast Address", self._tx_lie_ipv4_mcast_address],
            ["Receive LIE IPv6 Multicast Address", self._rx_lie_ipv6_mcast_address],
            ["Transmit LIE IPv6 Multicast Address", self._tx_lie_ipv6_mcast_address],
            ["Receive LIE Port", self._rx_lie_port],
            ["Transmit LIE Port", self._tx_lie_port],
            ["LIE Send Interval", "{} secs".format(self._lie_send_interval_secs)],
            ["Receive TIE Port", self._rx_tie_port]
        ]

    def allocate_interface_id(self):
        # We assume an i32 is never going to wrap (i.e. no more than ~2M interfaces)
        interface_id = self._next_interface_id
        self._next_interface_id += 1
        return interface_id

    @staticmethod
    def cli_summary_headers():
        return [
            ["Node", "Name"],
            ["System", "ID"],
            ["Running"]]

    def cli_summary_attributes(self):
        return [
            self._name,
            utils.system_id_str(self._system_id),
            self._running]

    @staticmethod
    def cli_level_headers():
        return [
            ["Node", "Name"],
            ["System", "ID"],
            ["Running"],
            ["Configured", "Level"],
            ["Level", "Value"]]

    def cli_level_attributes(self):
        if self._running:
            return [
                self._name,
                utils.system_id_str(self._system_id),
                self._running,
                self._configured_level_symbol,
                self.level_value_str()]
        else:
            return [
                self._name,
                utils.system_id_str(self._system_id),
                self._running,
                self._configured_level_symbol,
                '?']

    def command_show_node(self, cli_session):
        cli_session.print("Node:")
        tab = table.Table(separators = False)
        tab.add_rows(self.cli_detailed_attributes())
        cli_session.print(tab.to_string())
        cli_session.print("Received Offers:")
        tab = table.Table()
        tab.add_row(offer.RxOffer.cli_headers())
        sorted_rx_offers = sortedcontainers.SortedDict(self._rx_offers)
        for off in sorted_rx_offers.values():
            tab.add_row(off.cli_attributes())
        cli_session.print(tab.to_string())
        cli_session.print("Sent Offers:")
        tab = table.Table()
        tab.add_row(offer.TxOffer.cli_headers())
        sorted_tx_offers = sortedcontainers.SortedDict(self._tx_offers)
        for off in sorted_tx_offers.values():
            tab.add_row(off.cli_attributes())
        cli_session.print(tab.to_string())

    def command_show_node_fsm_history(self, cli_session):
        tab = self._fsm.history_table()
        cli_session.print(tab.to_string())

    def command_show_interfaces(self, cli_session):
        # TODO: Report neighbor uptime (time in THREE_WAY state)
        tab = table.Table()
        tab.add_row(interface.Interface.cli_summary_headers())
        for intf in self._interfaces.values():
            tab.add_row(intf.cli_summary_attributes())
        cli_session.print(tab.to_string())

    def command_show_interface(self, cli_session, parameters):
        interface_name = parameters['interface']
        if not interface_name in self._interfaces:
            cli_session.print("Error: interface {} not present".format(interface_name))
            return
        inteface_attributes = self._interfaces[interface_name].cli_detailed_attributes()
        tab = table.Table(separators = False)
        tab.add_rows(inteface_attributes)
        cli_session.print("Interface:")
        cli_session.print(tab.to_string())
        neighbor_attributes = self._interfaces[interface_name].cli_detailed_neighbor_attributes()
        if neighbor_attributes:
            tab = table.Table(separators = False)
            tab.add_rows(neighbor_attributes)
            cli_session.print("Neighbor:")
            cli_session.print(tab.to_string())

    def command_show_interface_fsm_history(self, cli_session, parameters):
        interface_name = parameters['interface']
        if not interface_name in self._interfaces:
            cli_session.print("Error: interface {} not present".format(interface_name))
            return
        interface = self._interfaces[interface_name]
        tab = interface._fsm.history_table()
        cli_session.print(tab.to_string())

    @property
    def name(self):
        return self._name

    # TODO: get rid of these properties, more complicated than needed. Just remote _ instead
    @property
    def system_id(self):
        return self._system_id

    @property
    def configured_level(self):
        return self._configured_level

    @property
    def lie_ipv4_mcast_address(self):
        return self._rx_lie_ipv4_mcast_address

    @property
    def lie_ipv6_mcast_address(self):
        return self._rx_lie_ipv6_mcast_address

    @property
    def lie_destination_port(self):
        return self._tx_lie_port

    @property
    def lie_send_interval_secs(self):
        return self._lie_send_interval_secs

    @property
    def mcast_loop(self):
        return self._mcast_loop

    @property
    def running(self):
        return self._running
