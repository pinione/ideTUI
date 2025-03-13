import curses
import configparser
import os
import subprocess
import yaml  # PyYAML for parsing YAML files
from collections import defaultdict

CONFIG_FILE = "config.ini"
BASE_DIR = os.path.expanduser("~/env_repos")  # Base directory to store cloned repos

def load_config():
    """Loads the configuration from an INI file and structures it into a dictionary."""
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    environments = defaultdict(list)
    for key, value in config["environments"].items():
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 3:
            env_name, env_type, git_repo = parts[:3]
            jumphost = parts[3] if len(parts) > 3 else None  # Get jumphost if available
            environments[env_name].append((env_type, git_repo, jumphost))

    return environments

def select_option(stdscr, title, options, get_label, include_back=False, include_exit=False, search_enabled=False):
    """Generic function to create a scrollable selection menu in TUI, supports incremental search."""
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

def find_kubernetes_namespaces(repo_dir):
    """Scans the cloned repository for Kubernetes namespace YAML files and extracts real namespace names."""
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
                except Exception as e:
                    print(f"⚠️ Error reading {file_path}: {e}")

    return sorted(namespaces)

def connect_and_run_kubectl(jumphost, namespace, command):
    """SSH into the jumphost and run kubectl commands."""
    ssh_command = f"ssh {jumphost} 'kubectl -n {namespace} {command}'"
    try:
        subprocess.run(ssh_command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Failed to execute command: {e}")

def main(stdscr):
    environments = load_config()

    if not environments:
        stdscr.addstr(2, 2, "No environments found in config!", curses.A_BOLD)
        stdscr.refresh()
        stdscr.getch()
        return

    while True:
        env_names = list(environments.keys())
        selected_env_name = select_option(stdscr, "Select Environment", env_names, lambda e: e, include_exit=True)

        if selected_env_name == "Exit":
            return  

        while True:
            env_options = environments[selected_env_name]
            selected_env_type_tuple = select_option(
                stdscr,
                "Select Environment Type",
                env_options,
                lambda e: f"{e[0]} ({e[1]})" if e[1] else e[0],
                include_back=True
            )

            if selected_env_type_tuple == "Go Back":
                break  

            selected_env_type, selected_git_repo, jumphost = selected_env_type_tuple

            stdscr.clear()
            stdscr.addstr(2, 2, "Cloning or pulling repository...", curses.A_BOLD)
            stdscr.refresh()

            repo_dir = os.path.join(BASE_DIR, f"{selected_env_name}_{selected_env_type}")
            os.makedirs(repo_dir, exist_ok=True)

            namespaces = find_kubernetes_namespaces(repo_dir)  

            if namespaces:
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

                                if kubernetes_option == "Show Pods":
                                    connect_and_run_kubectl(jumphost, selected_namespace, "get pods")
                                elif kubernetes_option == "Show Logs":
                                    connect_and_run_kubectl(jumphost, selected_namespace, "logs --all-containers")

                                stdscr.addstr(2, 2, "Press any key to return...")
                                stdscr.refresh()
                                stdscr.getch()

            return  

if __name__ == "__main__":
    curses.wrapper(main)
