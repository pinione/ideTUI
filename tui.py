import curses
import configparser
import os
import subprocess
import re
import yaml  # Requires PyYAML installed
import urllib.parse
import socket
from collections import defaultdict
import json

CONFIG_FILE = "config.ini"
BASE_DIR = os.path.expanduser("~/env_repos")  # Base directory for repositories

def strip_credentials(url):
    """
    Strips username and password from the given URL.
    Returns the URL with only the scheme, hostname, port (if any), path, query, and fragment.
    """
    parts = urllib.parse.urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

def load_config():
    """
    Loads the [environments] section.
    Each environment line must have:
      env name, env type, git repo, jumphost, kubectl context (optional)
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    environments = defaultdict(list)
    if "environments" in config:
        for key, value in config["environments"].items():
            parts = [p.strip() for p in value.split(",")]
            if len(parts) >= 4:
                env_name, env_type, git_repo, jumphost = parts[:4]
                context = parts[4] if len(parts) >= 5 and parts[4] != "" else None
                environments[env_name].append((env_type, git_repo, jumphost, context))
    return environments

def load_jump_hosts():
    """
    Loads the [jump-hosts] section.
    Each jump host is defined with:
      subscription, resourcegroup, vm name, localization
    Returns a dictionary mapping jump host keys to a tuple.
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    jump_hosts = {}
    if "jump-hosts" in config:
        for key, value in config["jump-hosts"].items():
            parts = [p.strip() for p in value.split(",")]
            if len(parts) >= 4:
                subscription, resourcegroup, vm_name, localization = parts[:4]
                jump_hosts[key] = (subscription, resourcegroup, vm_name, localization)
    return jump_hosts

def get_external_ip():
    """
    Retrieves the external IP by calling the provided URL.
    """
    try:
        result = subprocess.run("curl -s v2.com7.pl/IP/", shell=True, check=True, text=True, stdout=subprocess.PIPE)
        ip = result.stdout.strip()
        return ip
    except subprocess.CalledProcessError as e:
        return None

def build_jit_payload(jump_params, external_ip):
    """
    Given jump host parameters and external_ip, constructs a JSON payload
    for initiating JIT access.
    """
    subscription, resourcegroup, vm_name, localization = jump_params
    vm_id = f"/subscriptions/{subscription}/resourceGroups/{resourcegroup}/providers/Microsoft.Compute/virtualMachines/{vm_name}"
    payload = {
        "virtualMachines": [
            {
                "id": vm_id,
                "ports": [
                    {
                        "number": 3389,
                        "duration": "PT1H",
                        "allowedSourceAddressPrefix": external_ip
                    }
                ]
            }
        ],
        "justification": "testing a new version of the product"
    }
    return payload

def run_jit(jump_params, external_ip):
    """
    Uses az rest to initiate JIT access with the given jump host parameters and external IP.
    Constructs the URL from the jump host definition.
    """
    subscription, resourcegroup, vm_name, localization = jump_params
    # Construct the URL dynamically
    url = (f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{resourcegroup}/"
           f"providers/Microsoft.Security/locations/{localization}/jitNetworkAccessPolicies/default/initiate"
           "?api-version=2020-01-01")
    payload = build_jit_payload(jump_params, external_ip)
    payload_json = json.dumps(payload)
    az_cmd = f"az rest --method POST --url \"{url}\" --body '{payload_json}'"
    try:
        result = subprocess.run(az_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
        return az_cmd, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return az_cmd, f"Error executing az rest: {e}"

def select_option(stdscr, title, options, get_label, include_back=False, include_exit=False, search_enabled=False):
    """
    Displays a scrollable selection menu.
    """
    curses.curs_set(0)
    stdscr.clear()
    stdscr.refresh()
    original_options = options[:]
    if include_back and "Go Back" not in original_options:
        original_options.insert(0, "Go Back")
    if include_exit and "Exit" not in original_options:
        original_options.append("Exit")
    filtered_options = original_options
    search_query = ""
    current_row = 0
    scroll_pos = 0
    max_rows, _ = stdscr.getmaxyx()
    max_visible_items = max_rows - 4

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, title, curses.A_BOLD | curses.A_UNDERLINE)
        if search_enabled:
            stdscr.addstr(1, 2, f"Search: {search_query}_", curses.A_DIM)
        if current_row >= scroll_pos + max_visible_items:
            scroll_pos = current_row - max_visible_items + 1
        elif current_row < scroll_pos:
            scroll_pos = current_row
        visible_options = filtered_options[scroll_pos:scroll_pos + max_visible_items]
        for idx, option in enumerate(visible_options):
            label = get_label(option) if option not in ["Go Back", "Exit"] else option
            line_pos = idx + 3
            if scroll_pos + idx == current_row:
                stdscr.addstr(line_pos, 2, f"> {label}", curses.A_REVERSE)
            else:
                stdscr.addstr(line_pos, 2, f"  {label}")
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP and current_row > 0:
            current_row -= 1
        elif key == curses.KEY_DOWN and current_row < len(filtered_options) - 1:
            current_row += 1
        elif key in [curses.KEY_ENTER, 10, 13]:
            return filtered_options[current_row]
        elif search_enabled and (32 <= key <= 126):
            search_query += chr(key)
            filtered_options = [opt for opt in original_options if search_query.lower() in get_label(opt).lower()]
            current_row = 0
            scroll_pos = 0
        elif search_enabled and key in [curses.KEY_BACKSPACE, 127]:
            search_query = search_query[:-1]
            filtered_options = [opt for opt in original_options if search_query.lower() in get_label(opt).lower()]
            current_row = 0
            scroll_pos = 0

def clone_or_pull_repo(env_name, env_type, git_repo):
    """Clones or pulls the repository (output suppressed)."""
    env_dir = os.path.join(BASE_DIR, f"{env_name}_{env_type}")
    os.makedirs(BASE_DIR, exist_ok=True)
    try:
        if os.path.exists(env_dir):
            subprocess.run(["git", "-C", env_dir, "pull"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           text=True)
        else:
            subprocess.run(["git", "clone", git_repo, env_dir],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           text=True)
    except subprocess.CalledProcessError:
        pass
    return env_dir

def find_kubernetes_namespaces(repo_dir):
    """Scans the repository for YAML files defining a Kubernetes Namespace."""
    namespaces = set()
    for root, _, files in os.walk(repo_dir):
        for file in files:
            if file.endswith(".yaml") or file.endswith(".yml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as yaml_file:
                        docs = list(yaml.safe_load_all(yaml_file))
                        for doc in docs:
                            if isinstance(doc, dict) and doc.get("kind") == "Namespace":
                                metadata = doc.get("metadata", {})
                                if "name" in metadata:
                                    namespaces.add(metadata["name"])
                except Exception:
                    pass
    return sorted(namespaces)

def find_application_conf(repo_dir, namespace):
    """Recursively searches under <repo_dir>/secrets/ for application.conf with namespace in its path."""
    secrets_dir = os.path.join(repo_dir, "secrets")
    if not os.path.isdir(secrets_dir):
        return None
    for root, dirs, files in os.walk(secrets_dir):
        if "application.conf" in files:
            rel_path = os.path.relpath(root, secrets_dir)
            if namespace in rel_path.split(os.sep):
                return os.path.join(root, "application.conf")
    return None

def find_smdp_yaml(repo_dir, namespace):
    """Recursively searches under <repo_dir>/deployments/<namespace>/ for smdp.yaml."""
    deployments_dir = os.path.join(repo_dir, "deployments", namespace)
    if os.path.isdir(deployments_dir):
        for root, dirs, files in os.walk(deployments_dir):
            if "smdp.yaml" in files:
                return os.path.join(root, "smdp.yaml")
    return None

def parse_smdp_yaml(conf_path):
    """Parses smdp.yaml to extract environment variables."""
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        env_dict = {}
        if ("spec" in data and "template" in data["spec"] and
            "spec" in data["spec"]["template"] and
            "containers" in data["spec"]["template"]["spec"]):
            containers = data["spec"]["template"]["spec"]["containers"]
            if containers and isinstance(containers, list):
                env_list = containers[0].get("env", [])
                for item in env_list:
                    if "name" in item and "value" in item:
                        env_dict[item["name"]] = item["value"]
        return env_dict
    except Exception:
        return {}

def is_jumphost_available(jumphost):
    """Checks if jumphost (extracted IP if needed) is reachable on port 22."""
    if "@" in jumphost:
        host = jumphost.split("@")[-1]
    else:
        host = jumphost
    try:
        s = socket.create_connection((host, 22), timeout=5)
        s.close()
        return True
    except Exception:
        return False

def connect_and_run_kubectl(jumphost, context, namespace, command):
    """SSH to jumphost and run a kubectl command in the given namespace."""
    context_cmd = f"--context {context} " if context else ""
    kubectl_cmd = f"kubectl {context_cmd}-n {namespace} {command}"
    ssh_command = f"ssh {jumphost} '{kubectl_cmd}'"
    try:
        subprocess.run(ssh_command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Failed to execute command: {e}")

def run_kubectl_get_pods(jumphost, context, namespace):
    """SSH to jumphost and run 'kubectl get pods --no-headers'."""
    context_cmd = f"--context {context} " if context else ""
    kubectl_cmd = f"kubectl {context_cmd}-n {namespace} get pods --no-headers"
    ssh_command = f"ssh {jumphost} '{kubectl_cmd}'"
    try:
        result = subprocess.run(ssh_command, shell=True, check=True, text=True, stdout=subprocess.PIPE)
        return ssh_command, result.stdout.strip()
    except subprocess.CalledProcessError:
        return ssh_command, ""

def get_user_input(stdscr, prompt):
    """Prompts the user for input and returns the entered string."""
    curses.echo()
    stdscr.addstr(prompt)
    stdscr.refresh()
    s = stdscr.getstr().decode()
    curses.noecho()
    return s

def display_text(stdscr, title, text):
    """
    Displays a scrollable text window with the given title and text.
    Highlights "error" in red, "warning" in yellow, "success" in green.
    Supports scrolling, page navigation, and '/' for search.
    """
    pattern = re.compile(r"(error|warning|success)", re.IGNORECASE)
    lines = text.splitlines()
    current_line = 0
    max_rows, max_cols = stdscr.getmaxyx()
    display_height = max_rows - 2

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, title, curses.A_BOLD | curses.A_UNDERLINE)
        for i in range(display_height):
            if current_line + i < len(lines):
                line = lines[current_line + i]
                col = 0
                pos = 0
                for match in pattern.finditer(line):
                    start, end = match.span()
                    if start > pos:
                        stdscr.addstr(i + 1, col, line[pos:start][:max_cols - col - 1])
                        col += len(line[pos:start])
                    word = match.group(0).lower()
                    if word == "error":
                        color = curses.color_pair(1)
                    elif word == "warning":
                        color = curses.color_pair(2)
                    elif word == "success":
                        color = curses.color_pair(3)
                    else:
                        color = curses.A_NORMAL
                    keyword = line[start:end]
                    if col < max_cols - 1:
                        stdscr.addstr(i + 1, col, keyword[:max_cols - col - 1], color)
                        col += len(keyword)
                    pos = end
                if pos < len(line) and col < max_cols - 1:
                    stdscr.addstr(i + 1, col, line[pos:][:max_cols - col - 1])
        stdscr.addstr(max_rows - 1, 0, "Up/Down: scroll  PageUp/PageDown: page  End: jump to end  '/': search  Any other key: exit", curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP and current_line > 0:
            current_line -= 1
        elif key == curses.KEY_DOWN and current_line < len(lines) - display_height:
            current_line += 1
        elif key == curses.KEY_NPAGE:
            current_line = min(current_line + display_height, max(0, len(lines) - display_height))
        elif key == curses.KEY_PPAGE:
            current_line = max(current_line - display_height, 0)
        elif key == curses.KEY_END:
            current_line = max(0, len(lines) - display_height)
        elif key == ord('/'):
            stdscr.addstr(max_rows - 1, 0, "Enter search query: ", curses.A_BOLD)
            stdscr.refresh()
            query = get_user_input(stdscr, "")
            found = False
            for idx in range(current_line, len(lines)):
                if query.lower() in lines[idx].lower():
                    current_line = idx
                    found = True
                    break
            if not found:
                stdscr.addstr(max_rows - 2, 0, "Not found", curses.A_BOLD)
                stdscr.refresh()
                curses.napms(1000)
        else:
            break

def parse_application_conf(conf_path):
    """
    Opens application.conf and extracts configuration data for reporting and cassandra.
    Returns two dictionaries.
    """
    reporting = {}
    cassandra = {}
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            content = f.read()
        rep_match = re.search(r"reporting\s*\{(.*?)\}", content, re.DOTALL)
        if rep_match:
            rep_block = rep_match.group(1)
            for line in rep_block.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    kv = re.match(r"(\w+)\s*=\s*(\".*?\"|\S+)", line)
                    if kv:
                        key = kv.group(1)
                        val = kv.group(2).strip('"')
                        reporting[key] = val
        cass_match = re.search(r"cassandra\s*\{(.*?)\}", content, re.DOTALL)
        if cass_match:
            cass_block = cass_match.group(1)
            for line in cass_block.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    kv = re.match(r"(\w+)\s*=\s*(\".*?\"|\S+)", line)
                    if kv:
                        key = kv.group(1)
                        val = kv.group(2).strip('"')
                        cassandra[key] = val
    except Exception as e:
        reporting = {}
        cassandra = {}
    return reporting, cassandra

def list_vwan_vpn(stdscr):
    """
    Runs an az CLI command to list S2S VPN connections.
    Resource group is hardcoded as "vwan-connectivity-shared-francecentral-001".
    """
    az_cmd = "az network vpn-connection list --resource-group vwan-connectivity-shared-francecentral-001 --output table"
    try:
        result = subprocess.run(az_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
        output = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        output = f"Error executing az command: {e}"
    display_text(stdscr, "vWAN - VPN (S2S Connections)", f"Command: {az_cmd}\n\nOutput:\n{output}")

def main(stdscr):
    # Initialize color pairs
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)

    # Load configurations
    environments = load_config()
    jump_hosts = load_jump_hosts()  # Load jump hosts definitions

    # Main menu: list environments plus a "Jumphost JIT" option
    env_names = list(environments.keys()) + ["Jumphost JIT"]

    while True:
        selected_main = select_option(stdscr, "Select Environment", env_names, lambda e: e, include_exit=True)
        if selected_main == "Exit":
            return
        if selected_main == "Jumphost JIT":
            # Show list of jump hosts from config
            jump_keys = list(jump_hosts.keys())
            selected_jump = select_option(stdscr, "Select Jump Host", jump_keys, lambda e: e, include_back=True)
            if selected_jump == "Go Back":
                continue
            # Get external IP
            external_ip = get_external_ip()
            if not external_ip:
                display_text(stdscr, "External IP Error", "Unable to retrieve external IP.")
                continue
            az_cmd, output = run_jit(jump_hosts[selected_jump], external_ip)
            display_text(stdscr, f"JIT for Jump Host '{selected_jump}'", f"Command: {az_cmd}\n\nOutput:\n{output}")
            continue

        # Else, process environment normally.
        while True:
            env_options = environments[selected_main]
            selected_env_type_tuple = select_option(stdscr, "Select Environment Type", env_options,
                                                      lambda e: f"{e[0]} ({strip_credentials(e[1])})", include_back=True)
            if selected_env_type_tuple == "Go Back":
                break
            selected_env_type, selected_git_repo, jumphost_key, context = selected_env_type_tuple
            # Check if jumphost_key exists in jump_hosts; if so, get its public IP.
            if jumphost_key in jump_hosts:
                jump_ip = get_jump_host_ip(jumphost_key, jump_hosts)
                if jump_ip:
                    jumphost = jump_ip
                else:
                    jumphost = jumphost_key
            else:
                jumphost = jumphost_key

            stdscr.clear()
            stdscr.addstr(2, 2, "Cloning or pulling repository...", curses.A_BOLD)
            stdscr.refresh()
            repo_dir = clone_or_pull_repo(selected_main, selected_env_type, selected_git_repo)
            namespaces = find_kubernetes_namespaces(repo_dir)
            if not namespaces:
                stdscr.clear()
                stdscr.addstr(2, 2, "No Kubernetes namespaces found in repo.", curses.A_BOLD)
                stdscr.refresh()
                stdscr.getch()
                break
            while True:
                selected_namespace = select_option(stdscr, "Select a Kubernetes Namespace", namespaces,
                                                   lambda e: e, include_back=True, search_enabled=True)
                if selected_namespace == "Go Back":
                    break
                while True:
                    selected_option = select_option(stdscr, "Select an option", ["Kubernetes", "MariaDB", "Cassandra"],
                                                     lambda e: e, include_back=True)
                    if selected_option == "Go Back":
                        break
                    if selected_option == "Kubernetes":
                        while True:
                            kubernetes_option = select_option(stdscr, f"Kubernetes Actions for '{selected_namespace}'",
                                                              ["Show Pods", "Show Logs"], lambda e: e, include_back=True)
                            if kubernetes_option == "Go Back":
                                break
                            if not is_jumphost_available(jumphost):
                                display_text(stdscr, "Jumphost Unavailable",
                                             f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                                continue
                            if kubernetes_option == "Show Pods":
                                cmd_executed, output = run_kubectl_get_pods(jumphost, context, selected_namespace)
                                if not output:
                                    output = "No pods found or error executing command."
                                display_text(stdscr, "Kubectl Get Pods Output", f"Command: {cmd_executed}\n\nOutput:\n{output}")
                            elif kubernetes_option == "Show Logs":
                                cmd_executed, pods_output = run_kubectl_get_pods(jumphost, context, selected_namespace)
                                pods = []
                                for line in pods_output.splitlines():
                                    parts = line.split()
                                    if parts:
                                        pods.append(parts[0])
                                if not pods:
                                    stdscr.clear()
                                    stdscr.addstr(2, 2, f"Command executed: {cmd_executed}", curses.A_BOLD)
                                    stdscr.addstr(3, 2, "No pods found.", curses.A_BOLD)
                                    stdscr.refresh()
                                    stdscr.getch()
                                    continue
                                selected_pod = select_option(stdscr, "Select a Pod for Logs", pods, lambda e: e,
                                                             include_back=True, search_enabled=True)
                                if selected_pod == "Go Back":
                                    continue
                                ssh_cmd = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} logs {selected_pod}'"
                                try:
                                    result = subprocess.run(ssh_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                                    logs_output = result.stdout.strip()
                                except subprocess.CalledProcessError as e:
                                    logs_output = f"Error retrieving logs: {e}"
                                display_text(stdscr, f"Logs for Pod: {selected_pod}", f"Command: {ssh_cmd}\n\nLogs:\n{logs_output}")
                    elif selected_option == "MariaDB":
                        conf_path = find_application_conf(repo_dir, selected_namespace)
                        if not conf_path:
                            smdp_path = find_smdp_yaml(repo_dir, selected_namespace)
                            if smdp_path:
                                env_dict = parse_smdp_yaml(smdp_path)
                                reporting = {
                                    "host": env_dict.get("DB_HOST", "localhost"),
                                    "port": env_dict.get("DB_PORT", "3306"),
                                    "username": env_dict.get("DB_USER", "root"),
                                    "password": env_dict.get("DB_PASSWD", ""),
                                    "dbname": env_dict.get("DB_NAME", "")
                                }
                                cassandra = {
                                    "host": env_dict.get("CASSANDRA_HOST1", "localhost"),
                                    "port": env_dict.get("CASSANDRA_PORT", "9042"),
                                    "keyspace": env_dict.get("CASSANDRA_KEYSPACE", ""),
                                    "username": env_dict.get("CASSANDRA_USER", ""),
                                    "password": env_dict.get("CASSANDRA_PASSWD", "")
                                }
                                source = "smdp.yaml"
                            else:
                                reporting = {}
                                cassandra = {}
                                source = None
                        else:
                            source = "application.conf"
                            try:
                                with open(conf_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                            except Exception as e:
                                content = "Error reading application.conf: " + str(e)
                            reporting = {}
                            cassandra = {}
                            rep_match = re.search(r"reporting\s*\{(.*?)\}", content, re.DOTALL)
                            if rep_match:
                                rep_block = rep_match.group(1)
                                for line in rep_block.splitlines():
                                    line = line.strip()
                                    if line and not line.startswith("#"):
                                        kv = re.match(r"(\w+)\s*=\s*(\".*?\"|\S+)", line)
                                        if kv:
                                            key = kv.group(1)
                                            val = kv.group(2).strip('"')
                                            reporting[key] = val
                            cass_match = re.search(r"cassandra\s*\{(.*?)\}", content, re.DOTALL)
                            if cass_match:
                                cass_block = cass_match.group(1)
                                for line in cass_block.splitlines():
                                    line = line.strip()
                                    if line and not line.startswith("#"):
                                        kv = re.match(r"(\w+)\s*=\s*(\".*?\"|\S+)", line)
                                        if kv:
                                            key = kv.group(1)
                                            if key.lower() == "hosts":
                                                val = kv.group(2).strip('"')
                                                host = val.split(",")[0].strip() if val else "localhost"
                                                cassandra["host"] = host
                                            else:
                                                val = kv.group(2).strip('"')
                                                cassandra[key] = val
                            source = "application.conf"
                        if not source:
                            output = f"Neither application.conf nor smdp.yaml found under secrets for namespace '{selected_namespace}'."
                        else:
                            mariadb_cmd = "Not enough data to build MariaDB command."
                            cassandra_cmd = "Not enough data to build Cassandra command."
                            if reporting:
                                host = reporting.get("host", "localhost")
                                port = reporting.get("port", "3306")
                                username = reporting.get("username", "root")
                                password = reporting.get("password", "")
                                dbname = reporting.get("dbname", "")
                                mariadb_cmd = f"mysql -h {host} -P {port} -u {username} -p{password} {dbname}"
                            if cassandra:
                                host = cassandra.get("host", "localhost")
                                port = cassandra.get("port", "9042")
                                keyspace = cassandra.get("keyspace", "")
                                username = cassandra.get("username", "")
                                password = cassandra.get("password", "")
                                cassandra_cmd = f"cqlsh {host} {port} -u {username} -p {password} {keyspace}"
                            output = (f"Source: {source}\n\nMariaDB Connection Command:\n{mariadb_cmd}\n\n"
                                      f"Cassandra Connection Command:\n{cassandra_cmd}")
                        display_text(stdscr, "Database Connection Commands", output)
                    elif selected_option == "Cassandra":
                        stdscr.clear()
                        stdscr.addstr(2, 2, "Cassandra feature not implemented separately.", curses.A_BOLD)
                        stdscr.refresh()
                        stdscr.getch()
                # End of Action Selection loop.
            # End of Namespace Selection loop.
            return  # Exit after one environment type selection.
            
if __name__ == "__main__":
    curses.wrapper(main)
