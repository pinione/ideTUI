import curses
import configparser
import os
import subprocess
import re
import yaml  # Requires PyYAML installed
import urllib.parse
import socket
import json
from collections import defaultdict

CONFIG_FILE = "config.ini"
BASE_DIR = os.path.expanduser("~/env_repos")  # Base directory for repositories

def strip_credentials(url):
    """Strips username and password from a URL."""
    parts = urllib.parse.urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

def load_config():
    """
    Loads the [environments] section.
    Each line must have: env name, env type, git repo, jumphost, kubectl context (optional)
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
    Each jump host is defined with: subscription, resourcegroup, vm name, localization, description.
    Returns a dictionary mapping jump host key to a tuple.
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    jump_hosts = {}
    if "jump-hosts" in config:
        for key, value in config["jump-hosts"].items():
            parts = [p.strip() for p in value.split(",")]
            if len(parts) >= 5:
                subscription, resourcegroup, vm_name, localization, description = parts[:5]
                jump_hosts[key] = (subscription, resourcegroup, vm_name, localization, description)
            elif len(parts) >= 4:
                subscription, resourcegroup, vm_name, localization = parts[:4]
                jump_hosts[key] = (subscription, resourcegroup, vm_name, localization, "")
    return jump_hosts

def get_external_ip():
    """Retrieves your external IP address using curl."""
    try:
        result = subprocess.run("curl -s v2.com7.pl/IP/", shell=True, check=True, text=True, stdout=subprocess.PIPE)
        ip = result.stdout.strip()
        return ip
    except subprocess.CalledProcessError:
        return None

def build_jit_payload(jump_params, external_ip):
    """
    Constructs a JSON payload for initiating JIT access.
    Port number is set to 22.
    """
    subscription, resourcegroup, vm_name, localization, _ = jump_params
    vm_id = f"/subscriptions/{subscription}/resourceGroups/{resourcegroup}/providers/Microsoft.Compute/virtualMachines/{vm_name}"
    payload = {
        "virtualMachines": [
            {
                "id": vm_id,
                "ports": [
                    {
                        "number": 22,
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
    Uses az rest to initiate JIT access.
    Returns the command and its output.
    """
    subscription, resourcegroup, vm_name, localization, _ = jump_params
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

def select_option(stdscr, title, options, get_label, include_back=False, include_exit=False, search_enabled=False, skip_items=None):
    """
    Displays a scrollable selection menu.
    Optional skip_items is a list of items that are visible but not selectable.
    """
    skip_items = skip_items or []
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
            while filtered_options[current_row] in skip_items and current_row > 0:
                current_row -= 1
        elif key == curses.KEY_DOWN and current_row < len(filtered_options) - 1:
            current_row += 1
            while filtered_options[current_row] in skip_items and current_row < len(filtered_options) - 1:
                current_row += 1
        elif key in [curses.KEY_ENTER, 10, 13]:
            if filtered_options[current_row] in skip_items:
                continue
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
    """Scans the repository for Kubernetes Namespace YAML files."""
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
    """Recursively searches under <repo_dir>/secrets/ for application.conf containing the namespace."""
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
    """
    Checks if the jumphost (using only the IP if in 'name@IP' format) is reachable on port 22.
    """
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
    """SSH to jumphost and run a kubectl command."""
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
    Highlights "error" in red, "warning" in yellow, and "success" in green.
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
    Opens application.conf and extracts configuration for reporting and cassandra.
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
    # Initialize color pairs for highlighting
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)

    environments = load_config()
    jump_hosts = load_jump_hosts()
    
    # Build main menu with a divider between environments and "Jumphost JIT"
    env_list = list(environments.keys())
    divider = "--------------------"
    main_menu = env_list + [divider, "Jumphost JIT"]

    while True:
        selected_main = select_option(stdscr, "Select Environment", main_menu, lambda e: e, include_exit=True, skip_items=[divider])
        if selected_main == "Exit":
            return
        if selected_main == "Jumphost JIT":
            jump_keys = list(jump_hosts.keys())
            selected_jump = select_option(stdscr, "Select Jump Host", jump_keys, lambda e: f"{e} ({jump_hosts[e][4]})", include_back=True)
            if selected_jump == "Go Back":
                continue
            external_ip = get_external_ip()
            if not external_ip:
                display_text(stdscr, "External IP Error", "Unable to retrieve external IP.")
                continue
            az_cmd, output = run_jit(jump_hosts[selected_jump], external_ip)
            display_text(stdscr, f"JIT for Jump Host '{selected_jump}'", f"Command: {az_cmd}\n\nOutput:\n{output}")
            continue

        # Process selected environment normally.
        while True:
            env_options = environments[selected_main]
            selected_env_type_tuple = select_option(stdscr, "Select Environment Type", env_options,
                                                      lambda e: f"{e[0]} ({strip_credentials(e[1])})", include_back=True)
            if selected_env_type_tuple == "Go Back":
                break
            selected_env_type, selected_git_repo, jumphost_key, context = selected_env_type_tuple
            if jumphost_key in jump_hosts:
                # Here, you could also call a function like get_jump_host_ip() if desired.
                jump_ip = get_external_ip()  # For simplicity, using external IP as fallback.
                jumphost = jump_ip if jump_ip else jumphost_key
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
                    # Updated Kubernetes actions: now five options.
                    kubernetes_actions = ["Show Pods", "Show Logs", "Describe Pod", "Display Deploys", "Scale Deploy"]
                    selected_option = select_option(stdscr, "Select an option", kubernetes_actions, lambda e: e, include_back=True)
                    if selected_option == "Go Back":
                        break
                    if selected_option == "Show Pods":
                        if not is_jumphost_available(jumphost):
                            display_text(stdscr, "Jumphost Unavailable", f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                            continue
                        cmd_executed, output = run_kubectl_get_pods(jumphost, context, selected_namespace)
                        if not output:
                            output = "No pods found or error executing command."
                        display_text(stdscr, "Kubectl Get Pods Output", f"Command: {cmd_executed}\n\nOutput:\n{output}")
                    elif selected_option == "Show Logs":
                        if not is_jumphost_available(jumphost):
                            display_text(stdscr, "Jumphost Unavailable", f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                            continue
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
                        selected_pod = select_option(stdscr, "Select a Pod for Logs", pods, lambda e: e, include_back=True, search_enabled=True)
                        if selected_pod == "Go Back":
                            continue
                        ssh_cmd = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} logs {selected_pod}'"
                        try:
                            result = subprocess.run(ssh_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                            logs_output = result.stdout.strip()
                        except subprocess.CalledProcessError as e:
                            logs_output = f"Error retrieving logs: {e}"
                        display_text(stdscr, f"Logs for Pod: {selected_pod}", f"Command: {ssh_cmd}\n\nLogs:\n{logs_output}")
                    elif selected_option == "Describe Pod":
                        if not is_jumphost_available(jumphost):
                            display_text(stdscr, "Jumphost Unavailable", f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                            continue
                        # List pods to let user pick one
                        cmd_executed, pods_output = run_kubectl_get_pods(jumphost, context, selected_namespace)
                        pods = []
                        for line in pods_output.splitlines():
                            parts = line.split()
                            if parts:
                                pods.append(parts[0])
                        if not pods:
                            display_text(stdscr, "Describe Pod", "No pods found.")
                            continue
                        selected_pod = select_option(stdscr, "Select a Pod to Describe", pods, lambda e: e, include_back=True, search_enabled=True)
                        if selected_pod == "Go Back":
                            continue
                        ssh_cmd = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} describe pod {selected_pod}'"
                        try:
                            result = subprocess.run(ssh_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                            describe_output = result.stdout.strip()
                        except subprocess.CalledProcessError as e:
                            describe_output = f"Error retrieving description: {e}"
                        display_text(stdscr, f"Describe Pod: {selected_pod}", f"Command: {ssh_cmd}\n\nOutput:\n{describe_output}")
                    elif selected_option == "Display Deploys":
                        if not is_jumphost_available(jumphost):
                            display_text(stdscr, "Jumphost Unavailable", f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                            continue
                        ssh_cmd = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} get deploy -o wide'"
                        try:
                            result = subprocess.run(ssh_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                            deploys_output = result.stdout.strip()
                        except subprocess.CalledProcessError as e:
                            deploys_output = f"Error retrieving deployments: {e}"
                        display_text(stdscr, f"Deployments in '{selected_namespace}'", f"Command: {ssh_cmd}\n\nOutput:\n{deploys_output}")
                    elif selected_option == "Scale Deploy":
                        if not is_jumphost_available(jumphost):
                            display_text(stdscr, "Jumphost Unavailable", f"The jumphost {jumphost} is not reachable on port 22.\nPlease ensure SSH is available.")
                            continue
                        # List deployments
                        ssh_cmd = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} get deploy --no-headers'"
                        try:
                            result = subprocess.run(ssh_cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                            deploys_output = result.stdout.strip()
                        except subprocess.CalledProcessError as e:
                            deploys_output = f"Error retrieving deployments: {e}"
                        deploy_names = []
                        for line in deploys_output.splitlines():
                            parts = line.split()
                            if parts:
                                deploy_names.append(parts[0])
                        if not deploy_names:
                            display_text(stdscr, "Scale Deploy", "No deployments found.")
                            continue
                        selected_deploy = select_option(stdscr, "Select a Deployment to Scale", deploy_names, lambda e: e, include_back=True, search_enabled=True)
                        if selected_deploy == "Go Back":
                            continue
                        # Prompt for number of replicas
                        replicas = get_user_input(stdscr, "Enter desired number of replicas: ")
                        ssh_cmd_scale = f"ssh {jumphost} 'kubectl " + (f"--context {context} " if context else "") + f"-n {selected_namespace} scale deployment {selected_deploy} --replicas={replicas}'"
                        try:
                            result = subprocess.run(ssh_cmd_scale, shell=True, check=True, text=True, stdout=subprocess.PIPE)
                            scale_output = result.stdout.strip()
                        except subprocess.CalledProcessError as e:
                            scale_output = f"Error scaling deployment: {e}"
                        display_text(stdscr, f"Scale Deployment: {selected_deploy}", f"Command: {ssh_cmd_scale}\n\nOutput:\n{scale_output}")
                    # End of Kubernetes actions options.
                # End of Action Selection loop.
            # End of Namespace Selection loop.
            return  # Exit after finishing one environment type selection.
            
if __name__ == "__main__":
    curses.wrapper(main)
