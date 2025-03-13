import curses
import configparser
import os
import subprocess
import re
import yaml  # Requires PyYAML installed
import urllib.parse
from collections import defaultdict

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
    Loads config.ini.
    Each environment line must have:
      env name, env type, git repo, jumphost, kubectl context (optional)
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    environments = defaultdict(list)
    for key, value in config["environments"].items():
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 4:
            env_name, env_type, git_repo, jumphost = parts[:4]
            context = parts[4] if len(parts) >= 5 and parts[4] != "" else None
            environments[env_name].append((env_type, git_repo, jumphost, context))
    return environments

def select_option(stdscr, title, options, get_label, include_back=False, include_exit=False, search_enabled=False):
    """
    Displays a scrollable selection menu.
    - title: Menu title.
    - options: List of options.
    - get_label: Function to convert an option to a display string.
    - include_back: If True, inserts "Go Back" at the top.
    - include_exit: If True, appends "Exit" at the bottom.
    - search_enabled: If True, enables incremental search.
    """
    curses.curs_set(0)
    stdscr.clear()
    stdscr.refresh()
    original_options = options[:]  # Copy list
    if include_back and "Go Back" not in original_options:
        original_options.insert(0, "Go Back")
    if include_exit and "Exit" not in original_options:
        original_options.append("Exit")
    filtered_options = original_options
    search_query = ""
    current_row = 0
    scroll_pos = 0
    max_rows, _ = stdscr.getmaxyx()
    max_visible_items = max_rows - 4  # Reserve space for title and search bar

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
            line_pos = idx + 3  # Offset for title/search
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
    """
    Clones or pulls the repository.
    Output is suppressed.
    """
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
    """
    Scans the repository for YAML files defining a Kubernetes Namespace.
    Returns a sorted list of real namespace names.
    """
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
    """
    Recursively searches under <repo_dir>/secrets/ for a file named "application.conf"
    where the relative path (from secrets) contains the given namespace.
    Returns the full path if found, or None otherwise.
    """
    secrets_dir = os.path.join(repo_dir, "secrets")
    if not os.path.isdir(secrets_dir):
        return None
    for root, dirs, files in os.walk(secrets_dir):
        if "application.conf" in files:
            rel_path = os.path.relpath(root, secrets_dir)
            if namespace in rel_path.split(os.sep):
                return os.path.join(root, "application.conf")
    return None

def connect_and_run_kubectl(jumphost, context, namespace, command):
    """
    SSH to the jumphost and run a kubectl command in the given namespace and context.
    If a context is provided, the command will include '--context <context>'.
    """
    context_cmd = f"--context {context} " if context else ""
    kubectl_cmd = f"kubectl {context_cmd}-n {namespace} {command}"
    ssh_command = f"ssh {jumphost} '{kubectl_cmd}'"
    try:
        subprocess.run(ssh_command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Failed to execute command: {e}")

def run_kubectl_get_pods(jumphost, context, namespace):
    """
    SSH to the jumphost and run 'kubectl get pods --no-headers'
    with the specified context and namespace.
    Returns a tuple (command_executed, output).
    """
    context_cmd = f"--context {context} " if context else ""
    kubectl_cmd = f"kubectl {context_cmd}-n {namespace} get pods --no-headers"
    ssh_command = f"ssh {jumphost} '{kubectl_cmd}'"
    try:
        result = subprocess.run(ssh_command, shell=True, check=True, text=True, stdout=subprocess.PIPE)
        return ssh_command, result.stdout.strip()
    except subprocess.CalledProcessError:
        return ssh_command, ""

def display_text(stdscr, title, text):
    """
    Displays a scrollable text window with the given title and text.
    Highlights the words:
      "error" in red,
      "warning" in yellow,
      "success" in green.
    Supports:
      - Up/Down arrow keys to scroll line by line.
      - Page Up (KEY_PPAGE) and Page Down (KEY_NPAGE) for page scrolling.
      - End (KEY_END) to jump to the end of the text.
    Any other key exits the display.
    """
    # Precompile regex pattern for keywords
    pattern = re.compile(r"(error|warning|success)", re.IGNORECASE)
    lines = text.splitlines()
    current_line = 0
    max_rows, max_cols = stdscr.getmaxyx()
    display_height = max_rows - 2  # Reserve two lines for title and prompt

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
                    # Print text before keyword
                    if start > pos:
                        stdscr.addstr(i + 1, col, line[pos:start][:max_cols - col - 1])
                        col += len(line[pos:start])
                    # Determine color for keyword
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
        stdscr.addstr(max_rows - 1, 0, "Up/Down: scroll  PageUp/PageDown: page  End: jump to end  Any other key: exit", curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP and current_line > 0:
            current_line -= 1
        elif key == curses.KEY_DOWN and current_line < len(lines) - display_height:
            current_line += 1
        elif key == curses.KEY_NPAGE:  # Page Down
            current_line = min(current_line + display_height, max(0, len(lines) - display_height))
        elif key == curses.KEY_PPAGE:  # Page Up
            current_line = max(current_line - display_height, 0)
        elif key == curses.KEY_END:  # Jump to end
            current_line = max(0, len(lines) - display_height)
        else:
            break

def parse_application_conf(conf_path):
    """
    Opens the given application.conf file and extracts configuration data for:
      - database -> reporting
      - database -> cassandra
    Returns two dictionaries: (reporting_config, cassandra_config)
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

def main(stdscr):
    # Initialize color pairs for highlighting in logs display
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)      # error: red
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # warning: yellow
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)    # success: green

    environments = load_config()
    if not environments:
        stdscr.addstr(2, 2, "No environments found in config!", curses.A_BOLD)
        stdscr.refresh()
        stdscr.getch()
        return

    # Step 1: Environment Selection
    while True:
        env_names = list(environments.keys())
        selected_env_name = select_option(stdscr, "Select Environment", env_names, lambda e: e, include_exit=True)
        if selected_env_name == "Exit":
            return

        # Step 2: Environment Type Selection
        while True:
            env_options = environments[selected_env_name]
            selected_env_type_tuple = select_option(
                stdscr,
                "Select Environment Type",
                env_options,
                lambda e: f"{e[0]} ({strip_credentials(e[1])})",
                include_back=True
            )
            if selected_env_type_tuple == "Go Back":
                break

            selected_env_type, selected_git_repo, jumphost, context = selected_env_type_tuple

            stdscr.clear()
            stdscr.addstr(2, 2, "Cloning or pulling repository...", curses.A_BOLD)
            stdscr.refresh()
            repo_dir = clone_or_pull_repo(selected_env_name, selected_env_type, selected_git_repo)

            # Step 3: Find Real Kubernetes Namespaces
            namespaces = find_kubernetes_namespaces(repo_dir)
            if not namespaces:
                stdscr.clear()
                stdscr.addstr(2, 2, "No Kubernetes namespaces found in repo.", curses.A_BOLD)
                stdscr.refresh()
                stdscr.getch()
                break

            # Step 4: Namespace Selection (proceed immediately)
            while True:
                selected_namespace = select_option(
                    stdscr,
                    "Select a Kubernetes Namespace",
                    namespaces,
                    lambda e: e,
                    include_back=True,
                    search_enabled=True
                )
                if selected_namespace == "Go Back":
                    break

                # Step 5: Action Selection Menu
                while True:
                    selected_option = select_option(
                        stdscr,
                        "Select an option",
                        ["Kubernetes", "MariaDB", "Cassandra"],
                        lambda e: e,
                        include_back=True
                    )
                    if selected_option == "Go Back":
                        break

                    if selected_option == "Kubernetes":
                        # Kubernetes Actions Menu (include the namespace in the title)
                        while True:
                            kubernetes_option = select_option(
                                stdscr,
                                f"Kubernetes Actions for '{selected_namespace}'",
                                ["Show Pods", "Show Logs"],
                                lambda e: e,
                                include_back=True
                            )
                            if kubernetes_option == "Go Back":
                                break

                            if not jumphost:
                                stdscr.clear()
                                stdscr.addstr(2, 2, "No jumphost defined for this environment!", curses.A_BOLD)
                                stdscr.refresh()
                                stdscr.getch()
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
                                selected_pod = select_option(
                                    stdscr,
                                    "Select a Pod for Logs",
                                    pods,
                                    lambda e: e,
                                    include_back=True,
                                    search_enabled=True
                                )
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
                            output = f"application.conf not found under secrets for namespace '{selected_namespace}'."
                        else:
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
                                            val = kv.group(2).strip('"')
                                            cassandra[key] = val
                            mariadb_cmd = "Not enough data to build MariaDB command."
                            cassandra_cmd = "Not enough data to build Cassandra command."
                            if reporting:
                                host = reporting.get("host", "localhost")
                                port = reporting.get("port", "3306")
                                username = reporting.get("username", "root")
                                password = reporting.get("password", "")
                                mariadb_cmd = f"mysql -h {host} -P {port} -u {username} -p{password}"
                            if cassandra:
                                host = cassandra.get("host", "localhost")
                                port = cassandra.get("port", "9042")
                                username = cassandra.get("username", "")
                                password = cassandra.get("password", "")
                                cassandra_cmd = f"cqlsh {host} {port} -u {username} -p {password}"
                            output = f"MariaDB Connection Command:\n{mariadb_cmd}\n\nCassandra Connection Command:\n{cassandra_cmd}"
                        display_text(stdscr, "Database Connection Commands", output)
                    elif selected_option == "Cassandra":
                        stdscr.clear()
                        stdscr.addstr(2, 2, "Cassandra feature not implemented separately.", curses.A_BOLD)
                        stdscr.refresh()
                        stdscr.getch()
                # End of Action Selection loop: return to Namespace selection.
            # End of Namespace Selection loop: break to Environment Type selection.
            return  # Exit after finishing one environment type selection

if __name__ == "__main__":
    curses.wrapper(main)
