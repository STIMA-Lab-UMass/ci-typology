import json
import os

CONFIG_FILE = os.path.join(
    os.path.dirname(__file__),
    "selected_model.json"
)

MODEL_ENV_VAR = "OPENAI_SELECTED_MODEL"


def _persist_choice(model_name: str):
    os.environ[MODEL_ENV_VAR] = model_name
    with open(CONFIG_FILE, "w") as f:
        json.dump({"model_name": model_name}, f, indent=2)


def choose_model():
    """
    Prompt the user to select an OpenAI model and persist the choice.
    Defaults to 'gpt-5' if no input or invalid choice.
    """
    options = {
        "1": "gpt-4o",
        "2": "gpt-4o-mini",
        "3": "gpt-5",
        "4": "gpt-5-mini"
    }

    print("\nSelect an OpenAI model to use (press Enter for default = gpt-5):")
    for k, v in options.items():
        print(f"{k}. {v}")

    choice = input("Enter option number (1–4): ").strip()
    model_name = options.get(choice, "gpt-5")

    print(f"Selected model: {model_name}")
    _persist_choice(model_name)
    return model_name


def _load_saved_model():
    if MODEL_ENV_VAR in os.environ:
        return os.environ[MODEL_ENV_VAR]
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            model_name = saved.get("model_name")
            if model_name:
                os.environ[MODEL_ENV_VAR] = model_name
                return model_name
        except (json.JSONDecodeError, OSError):
            pass
    return None


def get_model_config(model_name: str = None):
    """
    Returns configuration parameters for the specified OpenAI model.
    Prompts the user only if no prior selection is available.
    """
    configs = {
        "gpt-4o": {
            "model": "gpt-4o",
            "parameters": {"seed": 42, "temperature": 0.1, "top_p": 0.1},
        },
        "gpt-4o-mini": {
            "model": "gpt-4o-mini",
            "parameters": {"seed": 42, "temperature": 0.1, "top_p": 0.1},
        },
        "gpt-5": {
            "model": "gpt-5",
            "parameters": {"reasoning_effort": "minimal"},
        },
        "gpt-5-mini": {
            "model": "gpt-5-mini",
            "parameters": {"reasoning_effort": "minimal"},
        },
    }

    if not model_name:
        model_name = _load_saved_model()
    if not model_name:
        model_name = choose_model()

    return configs.get(model_name, configs["gpt-5"])
