import readchar
import sys

class InteractiveMenu:
    def __init__(self, ques, options):
        self.options = options
        self.ques = ques
        self.selected_idx = 0

    def display(self):
        sys.stdout.write(f"\n{self.ques} (Use ↑/↓ and press Enter):\n")
        for option in self.options:
            sys.stdout.write(f"  {option}\n")  # Initial print of options

    def update_menu(self):
        sys.stdout.write("\033[F" * (len(self.options) + 1))  # Move cursor up
        sys.stdout.flush()

        sys.stdout.write(f"\033[K{self.ques} (Use ↑/↓ and press Enter):\n")  # Clear & rewrite prompt
        GREEN = "\033[32m"  # ANSI code for green text
        RESET = "\033[0m"   # ANSI code to reset to default color

        for idx, option in enumerate(self.options):
            # Set the prefix to green for the arrow
            prefix = f"{GREEN}➜ {RESET}" if idx == self.selected_idx else "  "
            sys.stdout.write(f"\033[K{prefix}{option}\n")  # Clear & rewrite option
        sys.stdout.flush()

    def get_input(self):
        key = readchar.readkey()

        if key == readchar.key.UP and self.selected_idx > 0:
            self.selected_idx -= 1
        elif key == readchar.key.DOWN and self.selected_idx < len(self.options) - 1:
            self.selected_idx += 1
        elif key == readchar.key.ENTER:
            return True
        return False

    def run(self):
        self.display()
        while True:
            self.update_menu()
            if self.get_input():
                break
        return self.selected_idx

if __name__ == '__main__':
    options = ["Proceed with the current settings", "Modify settings", "Exit"]

    menu = InteractiveMenu(options)
    selected_option = menu.run()

    print('\n', options[selected_option])