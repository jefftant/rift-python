import os
import time
import traceback
import pexpect
import pytest

class RiftExpectSession:

    start_converge_secs = 10.0

    # Maximum amount of time to fully reconverge after a uni-directional interface failure
    # If node1 can still send to node2, but node1 cannot receive from node2:
    # - It takes up to 3 seconds for node2 to detect that it is not receiving LIEs from node1
    # - At that point node2 will stop reflecting node1
    # - It takes up to 1 second for node1 to receive the first LIE without the reflection
    # - Add 1 second of slack time
    #
    reconverge_secs = 5.0

    expect_timeout = 1.0

    def __init__(self, topology_file, converge_secs=start_converge_secs):
        rift_cmd = ("rift "
                    "--interactive "
                    "--non-passive "
                    "--log-level debug "
                    "topology/{}.yaml"
                    .format(topology_file))
        cmd = "coverage run --parallel-mode {}".format(rift_cmd)
        results_file_name = "rift_expect.log"
        if "RIFT_TEST_RESULTS_DIR" in os.environ:
            results_file_name = os.environ["RIFT_TEST_RESULTS_DIR"] + "/" + results_file_name
        self._results_file = open(results_file_name, 'ab')
        self._expect_session = pexpect.spawn(cmd, logfile=self._results_file)
        time.sleep(converge_secs)
        self.wait_prompt()

    def stop(self):
        # Attempt graceful exit
        self._expect_session.sendline("exit")
        # Give some time for coverage to write .coverage results file
        time.sleep(1.0)
        # Terminate it forcefully, in case the graceful exit did not work for some reason
        self._expect_session.terminate(force=True)

    def sendline(self, line):
        self._expect_session.sendline(line)

    def log_expect_failure(self):
        self._results_file.write(b"\n\n*** Did not find expected pattern\n\n")
        # Generate a call stack in rift_expect.log for easier debugging
        # But pytest call stacks are very deep, so only show the "interesting" lines
        for line in traceback.format_stack():
            if "tests/" in line:
                self._results_file.write(line.strip().encode())
                self._results_file.write(b"\n")

    def expect(self, pattern, timeout=expect_timeout):
        msg = "\n\n*** Expect: {}\n\n".format(pattern)
        self._results_file.write(msg.encode())
        try:
            self._expect_session.expect(pattern, timeout)
        except pexpect.TIMEOUT:
            # Report the failure outside of this block, otherwise pytest reports a huge callstack
            failed = True
        else:
            failed = False
        if failed:
            self.log_expect_failure()
            pytest.fail('Timeout expecting "{} (see rift_expect.log for details)"'.format(pattern))

    def table_expect(self, pattern, timeout=expect_timeout):
        # Allow multiple spaces at end of each cell, even if only one was asked for
        pattern = pattern.replace(" |", " +|")
        # The | character is a literal end-of-cell, not a regexp OR
        pattern = pattern.replace("|", "[|]")
        pattern = pattern.replace("/", "|")
        return self.expect(pattern, timeout)

    def wait_prompt(self, node_name=None):
        if node_name is None:
            self.expect(".*> ")
        else:
            self.expect("{}> ".format(node_name))

    def check_adjacency_1way(self, node, interface):
        # Go to the node that we want to check
        self.sendline("set node {}".format(node))
        self.wait_prompt(node)
        # Show interfaces reports the adjacency with the other node as ONE_WAY
        self.sendline("show interfaces")
        self.table_expect("| {} | | .* | ONE_WAY |".format(interface))
        self.wait_prompt(node)
        # Show interface <interface-name> reports the adjacency with the other node as ONE_WAY
        self.sendline("show interface {}".format(interface))
        self.table_expect("Interface:")
        self.table_expect("| Interface Name | {} |".format(interface))
        self.table_expect("| State | ONE_WAY |")
        self.table_expect("| Neighbor | False |")
        self.wait_prompt(node)

    def check_adjacency_2way(self, node, interface, other_node, other_interface):
        # Construct full interface names as reported in LIE packets
        other_full_name = other_node + "-" + other_interface
        # Go to the node that we want to check
        self.sendline("set node {}".format(node))
        self.wait_prompt(node)
        # Show interfaces reports the adjacency with the other node as TWO_WAY
        self.sendline("show interfaces")
        self.table_expect("| {} | {} | .* | TWO_WAY |".format(interface, other_full_name))
        self.wait_prompt(node)
        # Show interface <interface-name> reports the adjacency with the other node as TWO_WAY
        self.sendline("show interface {}".format(interface))
        self.table_expect("Interface:")
        self.table_expect("| Interface Name | {} |".format(interface))
        self.table_expect("| State | TWO_WAY |")
        self.table_expect("| Received LIE Accepted or Rejected | Accepted |")
        self.table_expect("| Neighbor | True |")
        self.table_expect("Neighbor:")
        self.table_expect("| Name | {} |".format(other_full_name))
        self.wait_prompt(node)

    def check_adjacency_3way(self, node, interface):
        # Go to the node that we want to check
        self.sendline("set node {}".format(node))
        self.wait_prompt(node)
        # Show interfaces reports the adjacency with the other node as THREE_WAY
        self.sendline("show interfaces")
        self.table_expect("| {} | .* | .* | THREE_WAY |".format(interface))
        self.wait_prompt(node)
        # Show interface <interface-name> reports the adjacency with the other node as THREE_WAY
        self.sendline("show interface {}".format(interface))
        self.table_expect("Interface:")
        self.table_expect("| Interface Name | {} |".format(interface))
        self.table_expect("| State | THREE_WAY |")
        self.table_expect("| Received LIE Accepted or Rejected | Accepted |")
        self.table_expect("| Neighbor | True |")
        self.table_expect("Neighbor:")
        self.table_expect("| Name | .* |")
        self.wait_prompt(node)

    def check_rx_offer(self, node, interface, system_id, level, not_a_ztp_offer, state, best,
                       best_3way, removed, removed_reason):
        # Go to the node that we want to check
        self.sendline("set node {}".format(node))
        # Show node reports the offers
        self.sendline("show node")
        # Look for the expected offer
        expected_offer = "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            interface, system_id, level, not_a_ztp_offer, state, best, best_3way, removed,
            removed_reason)
        self.table_expect("Received Offers:")
        self.table_expect(expected_offer)
        self.wait_prompt(node)

    def check_tx_offer(self, node, interface, system_id, level, not_a_ztp_offer, state):
        # Go to the node that we want to check
        self.sendline("set node {}".format(node))
        # Show node reports the offers
        self.sendline("show node")
        # Look for the expected offer
        expected_offer = "| {} | {} | {} | {} | {} |".format(interface, system_id, level,
                                                             not_a_ztp_offer, state)
        self.table_expect("Sent Offers:")
        self.table_expect(expected_offer)
        self.wait_prompt(node)

    def check_level(self, node, configured_level, hal, hat, level_value):
        # Check using "show node"
        self.sendline("set node {}".format(node))
        self.sendline("show node")
        self.table_expect("| Configured Level | {} |".format(configured_level))
        self.table_expect("| Highest Available Level .HAL. | {} |".format(hal))
        self.table_expect("| Highest Adjacency Three-way .HAT. | {} |".format(hat))
        self.table_expect("| Level Value | {} |".format(level_value))
        # Check for consistency in "show nodes level"
        self.sendline("show nodes level")
        # Look for the expected offer
        expected_level = "| {} | .* | .* | {} | {} |".format(node, configured_level, level_value)
        self.table_expect(expected_level)
        self.wait_prompt()

    def check_lie_accepted(self, node, interface, reason):
        self.sendline("set node {}".format(node))
        self.sendline("show interface {}".format(interface))
        self.table_expect("Interface:")
        self.table_expect("| Received LIE Accepted or Rejected | Accepted |")
        self.table_expect("| Received LIE Accept or Reject Reason | {} |".format(reason))
        self.wait_prompt()

    def check_lie_rejected(self, node, interface, reason):
        self.sendline("set node {}".format(node))
        self.sendline("show interface {}".format(interface))
        self.table_expect("Interface:")
        self.table_expect("| Received LIE Accepted or Rejected | Rejected |")
        self.table_expect("| Received LIE Accept or Reject Reason | {} |".format(reason))
        self.wait_prompt()

    def interface_failure(self, node, interface, failure):
        # Go to the node whose interface needs to fail
        self.sendline("set node {}".format(node))
        # Fail the interface
        self.sendline("set interface {} failure {}".format(interface, failure))
        # Make sure it took
        self.sendline("show interface {}".format(interface))
        expected_failure = "| Failure | {} |".format(failure)
        self.table_expect(expected_failure)
        self.wait_prompt()
        # Let reconverge
        time.sleep(self.reconverge_secs)
