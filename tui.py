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
        if len(parts) == 3:
            env_name, env_type, git_repo = parts
            environments[env_name].append((env_type, git_repo))

    return environments

def select_option(stdscr, title, options, get_label, include_back=False, include_exit=False, search_enabled=False):
    """Generic function to create a scrollable selection menu in TUI, supports incremental search."""
    curses.curs_set(0)
    stdscr.clear()
    stdscr.refresh()

    original_options = options[:]  # Copy to avoid modifying original
    if include_back and "Go Back" not in original_options:
        original_options.insert(0, "Go Back")  # Add 'Go Back'
    if include_exit and "Exit" not in original_options:
        original_options.append("Exit")  # Add 'Exit' at the bottom

    filtered_options = original_options  # Start with all options
    search_query = ""
    current_row = 0
    scroll_pos = 0
    max_rows, _ = stdscr.getmaxyx()  # Get terminal size
    max_visible_items = max_rows - 4  # Leave space for title and search bar

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, title, curses.A_BOLD | curses.A_UNDERLINE)

        if search_enabled:
            stdscr.addstr(1, 2, f"Search: {search_query}_", curses.A_DIM)

        # Scroll handling
        if current_row >= scroll_pos + max_visible_items:
            scroll_pos = current_row - max_visible_items + 1
        elif current_row < scroll_pos:
            scroll_pos = current_row

        visible_options = filtered_options[scroll_pos:scroll_pos + max_visible_items]

        for idx, option in enumerate(visible_options):
            label = get_label(option) if option not in ["Go Back", "Exit"] else option
            line_pos = idx + 3  # Offset for title & search bar
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
        elif key in [curses.KEY_ENTER, 10, 13]:  # Enter key
            return filtered_options[current_row]
        elif search_enabled and (32 <= key <= 126):  # Printable characters for search
            search_query += chr(key)
            filtered_options = [opt for opt in original_options if search_query.lower() in get_label(opt).lower()]
            current_row = 0  # Reset cursor position
            scroll_pos = 0  # Reset scroll
        elif search_enabled and key in [curses.KEY_BACKSPACE, 127]:  # Handle backspace
            search_query = search_query[:-1]
            filtered_options = [opt for opt in original_options if search_query.lower() in get_label(opt).lower()]
            current_row = 0  # Reset cursor position
            scroll_pos = 0  # Reset scroll

def clone_or_pull_repo(env_name, env_type, git_repo):
    """Creates a directory for the environment and clones/pulls the Git repository."""
    env_dir = os.path.join(BASE_DIR, f"{env_name}_{env_type}")

    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)

    try:
        if os.path.exists(env_dir):
            subprocess.run(["git", "-C", env_dir, "pull"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        else:
            subprocess.run(["git", "clone", git_repo, env_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        pass  # Ignore errors and continue

    return env_dir

def find_kubernetes_namespaces(repo_dir):
    """Scans the repository for Kubernetes namespace YAML files and extracts namespace names."""
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
                    pass  # Ignore errors and continue

    return sorted(namespaces)

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
            return  # Exit the program

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
                break  # Return to environment selection

            selected_env_type, selected_git_repo = selected_env_type_tuple

            stdscr.clear()
            stdscr.addstr(2, 2, "Cloning or pulling repository...", curses.A_BOLD)
            stdscr.refresh()

            repo_dir = clone_or_pull_repo(selected_env_name, selected_env_type, selected_git_repo)

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
                            ["Kubernetes", "MariaDB"],
                            lambda e: e,
                            include_back=True
                        )

                        if selected_option == "Go Back":
                            break  

                        stdscr.clear()
                        stdscr.addstr(2, 2, f"You selected: {selected_option}", curses.A_BOLD)
                        stdscr.refresh()
                        stdscr.getch()

            return  

if __name__ == "__main__":
    curses.wrapper(main)
