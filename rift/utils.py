import netifaces

def interface_ipv4_address(interface_name, default_ip_if_none_found):
    interface_addresses = netifaces.interfaces()
    if not interface_name in netifaces.interfaces():
        return default_ip_if_none_found
    interface_addresses = netifaces.ifaddresses(interface_name)
    if not netifaces.AF_INET in interface_addresses:
        return default_ip_if_none_found
    return interface_addresses[netifaces.AF_INET][0]['addr']

def system_id_str(system_id):
    # Heuristic: if the system_id < 1000000 then it is probably configured, otherwise it is probably
    # derived from the MAC address, the process ID, and the node nr.
    if system_id < 1000000:
        return "{:d}".format(system_id)
    else:
        return "{:016x}".format(system_id)
