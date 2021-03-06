import collections
import time

import table

# TODO: Check completeness of FSM
# TODO: Report superfluous transitions (same effect in every state)
# TODO: Report could-be-implicit transitions (no effect: no actions, no pushed events,
#       back to same state)
# TODO: Report implicit transitions

_MAX_RECORDS = 25

def _action_to_name(action):
    action_name = action.__name__
    if action_name.startswith("action_"):
        action_name = action_name[len("action_"):]
    return action_name

def _event_to_name(event):
    return event.name

def _state_to_name(state):
    if state is None:
        # In the FSM, to-state None means no state change, so treat that special
        return 'None'
    else:
        return state.name

class FsmDefinition:

    def __init__(self, state_enum, event_enum, transitions, state_entry_actions, initial_state,
                 verbose_events=None):
        self.state_enum = state_enum
        self.event_enum = event_enum
        self.transitions = transitions
        self.state_entry_actions = state_entry_actions
        self.initial_state = initial_state
        if verbose_events is None:
            self.verbose_events = []
        else:
            self.verbose_events = verbose_events

    @staticmethod
    def parse_transition(transition):
        to_state = transition[0]
        actions = transition[1]
        if len(transition) > 2:
            push_events = transition[2]
        else:
            push_events = []
        return (to_state, actions, push_events)

    # Everything in this class below here is concerned with producing a human-readable textual
    # representation of the FSM

    def states_table(self):
        tab = table.Table()
        tab.add_row(['State'])
        for state in self.state_enum:
            tab.add_row([state.name])
        return tab

    def events_table(self):
        tab = table.Table()
        tab.add_row(['Event', 'Verbose'])
        for event in self.event_enum:
            verbose = (event in self.verbose_events)
            tab.add_row([event.name, verbose])
        return tab

    def transition_table(self, report_missing=False):
        tab = table.Table()
        tab.add_row(['From state', 'Event', 'To state', 'Actions', 'Push events'])
        for from_state in self.state_enum:
            if from_state in self.transitions:
                from_state_transitions = self.transitions[from_state]
            else:
                from_state_transitions = []
            self._add_from_transitions_to_table(tab, from_state, from_state_transitions,
                                                report_missing)
        return tab

    def state_entry_actions_table(self):
        tab = table.Table()
        tab.add_row(['State', 'Actions'])
        for state in self.state_entry_actions:
            actions = self.state_entry_actions[state]
            action_names = list(map(_action_to_name, actions))
            if action_names == []:
                action_names = '-'
            tab.add_row([state.name, action_names])
        return tab

    def _add_from_transitions_to_table(self, tab, from_state, from_state_transitions,
                                       report_missing):
        for event in self.event_enum:
            if event in from_state_transitions:
                transition = from_state_transitions[event]
                self._add_transition_to_table(tab, from_state, event, transition)
            else:
                if report_missing:
                    self._add_missing_transition_to_table(tab, from_state, event)

    def _add_transition_to_table(self, tab, from_state, event, transition):
        from_state_name = from_state.name
        event_name = event.name
        (to_state, actions, push_events) = FsmDefinition.parse_transition(transition)
        action_names = list(map(_action_to_name, actions))
        if to_state is None:
            to_state_name = "-"
        else:
            to_state_name = to_state.name
        action_names = list(map(_action_to_name, actions))
        if action_names == []:
            action_names = '-'
        push_event_names = list(map(_event_to_name, push_events))
        if push_event_names == []:
            push_event_names = '-'
        tab.add_row([from_state_name, event_name, to_state_name, action_names, push_event_names])

    # TODO: Add a way to report this
    def _add_missing_transition_to_table(self, tab, from_state, event):
        from_state_name = from_state.name
        event_name = event.name
        tab.add_row([from_state_name, event_name, '* MISSING *',
                     table.Table.Format.EXTEND_LEFT_CELL, table.Table.Format.EXTEND_LEFT_CELL])

    def command_show_fsm(self, cli_session):
        self.command_show_states(cli_session)
        self.command_show_events(cli_session)
        self.command_show_transitions(cli_session)
        self.command_show_state_entry_acts(cli_session)

    def command_show_states(self, cli_session):
        cli_session.print("States:")
        tab = self.states_table()
        cli_session.print(tab.to_string())

    def command_show_events(self, cli_session):
        cli_session.print("Events:")
        tab = self.events_table()
        cli_session.print(tab.to_string())

    def command_show_transitions(self, cli_session):
        cli_session.print("Transitions:")
        tab = self.transition_table()
        cli_session.print(tab.to_string())

    def command_show_state_entry_acts(self, cli_session):
        cli_session.print("State entry actions:")
        tab = self.state_entry_actions_table()
        cli_session.print(tab.to_string())

class FsmRecord:

    _next_seq_nr = 1

    def __init__(self, fsm, from_state, event, verbose):
        self.fsm = fsm
        self.seq_nr = FsmRecord._next_seq_nr
        FsmRecord._next_seq_nr += 1
        self.time = time.time()
        self.skipped = 0
        self.from_state = from_state
        self.event = event
        self.verbose = verbose
        self.actions_and_pushed_events = []
        self.to_state = None
        self.implicit = False

    def log_str(self):
        log_msg = ("FSM transition sequence-nr={} from-state={} event={} "
                   "actions-and-pushed-events={} to-state={} implicit={}").format(
                       self.seq_nr,
                       _state_to_name(self.from_state),
                       _event_to_name(self.event),
                       ",".join(self.actions_and_pushed_events),
                       _state_to_name(self.to_state),
                       self.implicit)
        return log_msg

class Fsm:

    # See DEV-7 in doc/deviations.md for meaning of _event_queue vs _chained_event_queue

    _event_queue = collections.deque()

    _chained_event_queue = collections.deque()

    def info(self, msg):
        if self._log:
            self._log.info("[{}] {}".format(self._log_id, msg))

    def info_or_debug(self, debug, msg):
        if self._log:
            if debug:
                self._log.debug("[{}] {}".format(self._log_id, msg))
            else:
                self._log.info("[{}] {}".format(self._log_id, msg))

    def warning(self, msg):
        if self._log:
            self._log.warning("[{}] {}".format(self._log_id, msg))

    def __init__(self, definition, action_handler, log, log_id):
        self._definition = definition
        self._log = log
        self._log_id = log_id
        self._state_enum = definition.state_enum
        self._event_enum = definition.event_enum
        self._transitions = definition.transitions
        self._state_entry_actions = definition.state_entry_actions
        self._verbose_events = definition.verbose_events
        self._state = None
        self._action_handler = action_handler
        self._records = collections.deque([], _MAX_RECORDS)
        self._verbose_records = collections.deque([], _MAX_RECORDS)
        self._current_record = None
        self._verbose_records_skipped = 0
        self.info("Create FSM")

    def start(self):
        self._state = self._definition.initial_state
        self.info("Start FSM, state={}".format(self._state.name))
        self.invoke_state_entry_actions(self._state)

    def push_event(self, event, event_data=None):
        fsm = self
        event_tuple = (fsm, event, event_data)
        if self._current_record is not None:
            # We are pushing an event to an FSM which is in the middle of executing a transaction.
            # We conclude that the FSM is executing an action which pushes an event back to the same
            # FSM instance, hence it is a chained event. (This logic only holds in a single-threaded
            # application, which is what we currently have.)
            self._chained_event_queue.append(event_tuple)
            self._current_record.actions_and_pushed_events.append(event.name)
        else:
            # Normal (external) event
            self._event_queue.append(event_tuple)
            verbose = (event in self._verbose_events)
            self.info_or_debug(verbose, "FSM push event, event={}".format(event.name))

    @staticmethod
    def process_queued_events():
        while True:
            if Fsm._chained_event_queue:
                event_tuple = Fsm._chained_event_queue.popleft()
            elif Fsm._event_queue:
                event_tuple = Fsm._event_queue.popleft()
            else:
                return
            fsm = event_tuple[0]
            event = event_tuple[1]
            event_data = event_tuple[2]
            fsm.process_event(event, event_data)

    def invoke_actions(self, actions, event_data=None):
        for action in actions:
            if self._current_record:
                self._current_record.actions_and_pushed_events.append(_action_to_name(action))
            if event_data:
                action(self._action_handler, event_data)
            else:
                action(self._action_handler)

    def invoke_state_entry_actions(self, state):
        if state in self._state_entry_actions:
            self.invoke_actions(self._state_entry_actions[state])

    def process_event(self, event, event_data):
        assert self._current_record is None
        from_state = self._state
        verbose = (event in self._verbose_events)
        self._current_record = FsmRecord(self, from_state, event, verbose)
        if from_state in self._transitions:
            from_state_transitions = self._transitions[from_state]
        else:
            from_state_transitions = []
        if event in from_state_transitions:
            transition = from_state_transitions[event]
            (to_state, actions, push_events) = FsmDefinition.parse_transition(transition)
            self.invoke_actions(actions, event_data)
            for push_event in push_events:
                self.push_event(push_event, None)
            if to_state is not None:
                self._current_record.to_state = to_state
                if to_state != self._state:
                    self._state = to_state
                    self.invoke_state_entry_actions(to_state)
        else:
            self._current_record.implicit = True
        self._verbose_records.appendleft(self._current_record)
        if self._current_record.verbose:
            self._verbose_records_skipped += 1
        else:
            self._current_record.skipped = self._verbose_records_skipped
            self._verbose_records_skipped = 0
            self._records.appendleft(self._current_record)
        self.info_or_debug(self._current_record.verbose, self._current_record.log_str())
        self._current_record = None

    def history_table(self, verbose):
        tab = table.Table()
        tab.add_row([
            ['Sequence', 'Nr'],
            ['Time', 'Delta'],
            ['Verbose', 'Skipped'],
            ['From', 'State'],
            'Event',
            ['Actions and', 'Pushed Events'],
            ['To', 'State'],
            ['Implicit']])
        prev_time = time.time()
        if verbose:
            records_to_show = self._verbose_records
        else:
            records_to_show = self._records
        for record in records_to_show:
            time_delta = prev_time - record.time
            tab.add_row([
                record.seq_nr,
                "{:06f}".format(time_delta),
                record.skipped,
                _state_to_name(record.from_state),
                _event_to_name(record.event),
                record.actions_and_pushed_events,
                _state_to_name(record.to_state),
                record.implicit])
            prev_time = record.time
        return tab

    @property
    def state(self):
        return self._state
