import curses
import configparser
import os
import subprocess
import yaml  # Requires PyYAML installed
from collections import defaultdict

CONFIG_FILE = "config.ini"
BASE_DIR = os.path.expanduser("~/env_repos")  # Base directory for repositories

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
    Supports:
      - Up/Down arrow keys to scroll line by line.
      - Page Up (KEY_PPAGE) and Page Down (KEY_NPAGE) for page scrolling.
      - End (KEY_END) to jump to the end of the text.
    Any other key exits the display.
    """
    lines = text.splitlines()
    current_line = 0
    max_rows, max_cols = stdscr.getmaxyx()
    display_height = max_rows - 2  # Reserve two lines for title and prompt

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, title, curses.A_BOLD | curses.A_UNDERLINE)
        for i in range(display_height):
            if current_line + i < len(lines):
                stdscr.addstr(i + 1, 0, lines[current_line + i][:max_cols - 1])
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

def main(stdscr):
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
                lambda e: f"{e[0]} ({e[1]})",
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

            # Step 4: Namespace Selection
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

                stdscr.clear()
                stdscr.addstr(2, 2, f"Selected Namespace: {selected_namespace}", curses.A_BOLD)
                stdscr.refresh()
                stdscr.getch()

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
                        # Kubernetes Actions Menu
                        while True:
                            kubernetes_option = select_option(
                                stdscr,
                                "Kubernetes Actions",
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
                                # Run 'kubectl get pods' and capture output
                                cmd_executed, output = run_kubectl_get_pods(jumphost, context, selected_namespace)
                                if not output:
                                    output = "No pods found or error executing command."
                                display_text(stdscr, "Kubectl Get Pods Output", f"Command: {cmd_executed}\n\nOutput:\n{output}")
                            elif kubernetes_option == "Show Logs":
                                # Get list of pods first
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
                                # New menu: select a pod for logs
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
                    else:
                        # Placeholder for MariaDB and Cassandra actions
                        stdscr.clear()
                        stdscr.addstr(2, 2, f"You selected: {selected_option}", curses.A_BOLD)
                        stdscr.addstr(4, 2, "Feature not implemented yet.", curses.A_DIM)
                        stdscr.refresh()
                        stdscr.getch()
                # End of Action Selection loop: return to Namespace selection.
            # End of Namespace Selection loop: break to Environment Type selection.
            return  # Exit after finishing one environment type selection

if __name__ == "__main__":
    curses.wrapper(main)
