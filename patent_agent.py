import sys
import uuid
from dotenv import load_dotenv
from evals import run_eval
from logs_db import init_db
from openai.types.responses.response_input_param import ResponseInputParam
from agent import run_agent

load_dotenv()

def run_repl():
    run_id = str(uuid.uuid4())
    input_list: ResponseInputParam = []
    while True:
        user_input = input("\nN - New chat\nE - Exit\nHow can I help you?\n\n").lower().strip()
        if user_input == "n":
            input_list = []
            run_id = str(uuid.uuid4())
            continue
        elif user_input == "e":
            break
        else:
            input_list.append({
                    "role": "user",
                    "content": user_input
                    })
            run_agent(input_list, run_id, user_input)

def main():

    init_db()

    if "--eval" in sys.argv:
        run_eval(run_agent)
    else:
        run_repl()

if __name__ == "__main__":
    main()
