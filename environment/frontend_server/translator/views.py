"""
Author: Joon Sung Park (joonspk@stanford.edu)
File: views.py
"""
import os
import string
import random
import json
from os import listdir
import os

import datetime
from django.shortcuts import render, redirect, HttpResponseRedirect
from django.http import HttpResponse, JsonResponse
from global_methods import *

from django.contrib.staticfiles.templatetags.staticfiles import static
from .models import *

import subprocess
import threading

# Maps the button clicked to the fork sim / new sim / history csv naming
# convention you already use for the CDU example. Add the matching
# storage folders + csvs for the other four parties following the same
# pattern (base_the_ville_n5_school_<party> / agent_history_init_n5_school_<party>.csv).
PARTY_CONFIG = {
    "cdu":    {"label": "CDU",    "fork": "base_the_ville_n5_school_cdu",    "history": "the_ville/agent_history_init_n5_school_cdu.csv"},
    "spd":    {"label": "SPD",    "fork": "base_the_ville_n5_school_spd",    "history": "the_ville/agent_history_init_n5_school_spd.csv"},
    "linke":  {"label": "LINKE",  "fork": "base_the_ville_n5_school_linke",  "history": "the_ville/agent_history_init_n5_school_linke.csv"},
    "afd":    {"label": "AfD",    "fork": "base_the_ville_n5_school_afd",    "history": "the_ville/agent_history_init_n5_school_afd.csv"},
    "gruene": {"label": "GRÜNE",  "fork": "base_the_ville_n5_school_gruene", "history": "the_ville/agent_history_init_n5_school_gruene.csv"},
}

# Path to the reverie backend server directory (adjust if your repo layout differs)
BACKEND_SERVER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "reverie", "backend_server")
)

# Very simple in-process lock so we don't launch two reverie.py at once.
_launch_lock = threading.Lock()
_launch_state = {"running": False, "party": None}
_backend_proc = {"proc": None}


def simulator_start(request):
    """
    Landing page with five party buttons. Clicking one starts the backend
    reverie.py process (forking the party's base simulation + loading its
    agent history) and then forwards to /simulator_home once ready.
    """
    parties = [dict(code=code, **cfg) for code, cfg in PARTY_CONFIG.items()]
    context = {"parties": parties, "already_running": _launch_state["running"]}
    template = "simulator_start/simulator_start.html"
    return render(request, template, context)


def _run_reverie_process(fork_sim_code, new_sim_code, history_csv):
    """
    Runs in a background thread. Spawns `python3 reverie.py`, feeds it the
    same three answers you'd type by hand:
      1) forked simulation name
      2) new simulation name
      3) "call -- load history <csv>"
    ReverieServer.__init__ writes temp_storage/curr_sim_code.json and
    curr_step.json as soon as it finishes forking + loading personas, which
    is what /simulator_home is waiting to see. We then leave the process's
    open_server() loop running (blocked on the next `input()`), so it stays
    alive as your live backend for that simulation.
    """
    log_path = os.path.join(BACKEND_SERVER_DIR, f"launch_{new_sim_code}.log")
    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            ["python3", "reverie.py"],
            cwd=BACKEND_SERVER_DIR,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _backend_proc["proc"] = proc 

        proc.stdin.write(fork_sim_code + "\n")
        proc.stdin.flush()
        proc.stdin.write(new_sim_code + "\n")
        proc.stdin.flush()
        proc.stdin.write(f"call -- load history {history_csv}\n")
        proc.stdin.flush()
        proc.stdin.write("run 0\n")
        proc.stdin.flush()
        proc.stdin.write("run 8640\n")
        proc.stdin.flush()
    finally:
        pass


def simulator_launch(request, party):
    """
    <FRONTEND button click> Kicks off the reverie backend for the chosen
    party in a background thread and returns immediately. The page polls
    simulator_launch_status to know when to redirect to /simulator_home.
    """
    party = party.lower()
    if party not in PARTY_CONFIG:
        return JsonResponse({"error": "unknown party"}, status=404)

    if not _launch_lock.acquire(blocking=False):
        return JsonResponse({"error": "a simulation is already starting"}, status=409)

    try:
        if _launch_state["running"]:
            return JsonResponse({"error": "a simulation is already running"}, status=409)

        cfg = PARTY_CONFIG[party]
        new_sim_code = f"test_{party}"

        stale = "temp_storage/curr_step.json"
        if check_if_file_exists(stale):
            os.remove(stale)

        _backend_proc["proc"] = None
        _launch_state["running"] = True
        _launch_state["party"] = party

        t = threading.Thread(
            target=_run_reverie_process,
            args=(cfg["fork"], new_sim_code, cfg["history"]),
            daemon=True,
        )
        t.start()
    finally:
        _launch_lock.release()

    return JsonResponse({"status": "starting", "party": party})

def simulator_control(request, action):
    """
    <FRONTEND button click>
    Handles the Save / Exit buttons shown under the map. Sends the
    corresponding command ("save" or "exit") to the running reverie.py
    backend process via its stdin (mirroring the commands you'd type by
    hand in open_server()), resets our launch state so a new simulation
    can be started, and lets the frontend JS redirect back to the
    party-picker screen.
    """
    action = action.lower()
    if action not in ("save", "exit"):
        return JsonResponse({"error": "unknown action"}, status=404)

    proc = _backend_proc.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(action + "\n")
            proc.stdin.flush()
        except Exception:
            pass

    _launch_state["running"] = False
    _launch_state["party"] = None
    _backend_proc["proc"] = None

    return JsonResponse({"status": action})

def home(request):
  f_curr_sim_code = "temp_storage/curr_sim_code.json"
  f_curr_step = "temp_storage/curr_step.json"

  if not check_if_file_exists(f_curr_step): 
    context = {}
    template = "home/error_start_backend.html"
    return render(request, template, context)

  with open(f_curr_sim_code) as json_file:  
    sim_code = json.load(json_file)["sim_code"]
  
  with open(f_curr_step) as json_file:  
    step = json.load(json_file)["step"]

  os.remove(f_curr_step)

  persona_names = []
  persona_names_set = set()
  for i in find_filenames(f"storage/{sim_code}/personas", ""): 
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      persona_names += [[x, x.replace(" ", "_")]]
      persona_names_set.add(x)

  persona_init_pos = []
  file_count = []
  for i in find_filenames(f"storage/{sim_code}/environment", ".json"):
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      file_count += [int(x.split(".")[0])]
  curr_json = f'storage/{sim_code}/environment/{str(max(file_count))}.json'
  with open(curr_json) as json_file:  
    persona_init_pos_dict = json.load(json_file)
    for key, val in persona_init_pos_dict.items(): 
      if key in persona_names_set: 
        persona_init_pos += [[key, val["x"], val["y"]]]

  context = {"sim_code": sim_code,
             "step": step, 
             "persona_names": persona_names,
             "persona_init_pos": persona_init_pos,
             "mode": "simulate",
             "party": _launch_state.get("party")} 
  template = "home/home.html"
  return render(request, template, context)

def simulator_launch_status(request):
    """
    <FRONTEND polling> Reports whether the backend has finished forking +
    loading and is ready for /simulator_home to pick it up.
    """
    ready = check_if_file_exists("temp_storage/curr_step.json")
    return JsonResponse({"ready": ready})

def landing(request): 
  context = {}
  template = "landing/landing.html"
  return render(request, template, context)


def demo(request, sim_code, step, play_speed="2"): 
  move_file = f"compressed_storage/{sim_code}/master_movement.json"
  meta_file = f"compressed_storage/{sim_code}/meta.json"
  step = int(step)
  play_speed_opt = {"1": 1, "2": 2, "3": 4,
                    "4": 8, "5": 16, "6": 32}
  if play_speed not in play_speed_opt: play_speed = 2
  else: play_speed = play_speed_opt[play_speed]

  # Loading the basic meta information about the simulation.
  meta = dict() 
  with open (meta_file) as json_file: 
    meta = json.load(json_file)

  sec_per_step = meta["sec_per_step"]
  start_datetime = datetime.datetime.strptime(meta["start_date"] + " 00:00:00", 
                                              '%B %d, %Y %H:%M:%S')
  for i in range(step): 
    start_datetime += datetime.timedelta(seconds=sec_per_step)
  start_datetime = start_datetime.strftime("%Y-%m-%dT%H:%M:%S")

  # Loading the movement file
  raw_all_movement = dict()
  with open(move_file) as json_file: 
    raw_all_movement = json.load(json_file)
 
  # Loading all names of the personas
  persona_names = dict()
  persona_names = []
  persona_names_set = set()
  for p in list(raw_all_movement["0"].keys()): 
    persona_names += [{"original": p, 
                       "underscore": p.replace(" ", "_"), 
                       "initial": p[0] + p.split(" ")[-1][0]}]
    persona_names_set.add(p)

  # <all_movement> is the main movement variable that we are passing to the 
  # frontend. Whereas we use ajax scheme to communicate steps to the frontend
  # during the simulation stage, for this demo, we send all movement 
  # information in one step. 
  all_movement = dict()

  # Preparing the initial step. 
  # <init_prep> sets the locations and descriptions of all agents at the
  # beginning of the demo determined by <step>. 
  init_prep = dict() 
  for int_key in range(step+1): 
    key = str(int_key)
    val = raw_all_movement[key]
    for p in persona_names_set: 
      if p in val: 
        init_prep[p] = val[p]
  persona_init_pos = dict()
  for p in persona_names_set: 
    persona_init_pos[p.replace(" ","_")] = init_prep[p]["movement"]
  all_movement[step] = init_prep

  # Finish loading <all_movement>
  for int_key in range(step+1, len(raw_all_movement.keys())): 
    all_movement[int_key] = raw_all_movement[str(int_key)]

  context = {"sim_code": sim_code,
             "step": step,
             "persona_names": persona_names,
             "persona_init_pos": json.dumps(persona_init_pos), 
             "all_movement": json.dumps(all_movement), 
             "start_datetime": start_datetime,
             "sec_per_step": sec_per_step,
             "play_speed": play_speed,
             "mode": "demo"}
  template = "demo/demo.html"

  return render(request, template, context)


def UIST_Demo(request): 
  return demo(request, "March20_the_ville_n25_UIST_RUN-step-1-141", 2160, play_speed="3")


def home(request):
  f_curr_sim_code = "temp_storage/curr_sim_code.json"
  f_curr_step = "temp_storage/curr_step.json"

  if not check_if_file_exists(f_curr_step): 
    context = {}
    template = "home/error_start_backend.html"
    return render(request, template, context)

  with open(f_curr_sim_code) as json_file:  
    sim_code = json.load(json_file)["sim_code"]
  
  with open(f_curr_step) as json_file:  
    step = json.load(json_file)["step"]

  os.remove(f_curr_step)

  persona_names = []
  persona_names_set = set()
  for i in find_filenames(f"storage/{sim_code}/personas", ""): 
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      persona_names += [[x, x.replace(" ", "_")]]
      persona_names_set.add(x)

  persona_init_pos = []
  file_count = []
  for i in find_filenames(f"storage/{sim_code}/environment", ".json"):
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      file_count += [int(x.split(".")[0])]
  curr_json = f'storage/{sim_code}/environment/{str(max(file_count))}.json'
  with open(curr_json) as json_file:  
    persona_init_pos_dict = json.load(json_file)
    for key, val in persona_init_pos_dict.items(): 
      if key in persona_names_set: 
        persona_init_pos += [[key, val["x"], val["y"]]]

  context = {"sim_code": sim_code,
             "step": step, 
             "persona_names": persona_names,
             "persona_init_pos": persona_init_pos,
             "mode": "simulate"}
  template = "home/home.html"
  return render(request, template, context)


def replay(request, sim_code, step): 
  sim_code = sim_code
  step = int(step)

  persona_names = []
  persona_names_set = set()
  for i in find_filenames(f"storage/{sim_code}/personas", ""): 
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      persona_names += [[x, x.replace(" ", "_")]]
      persona_names_set.add(x)

  persona_init_pos = []
  file_count = []
  for i in find_filenames(f"storage/{sim_code}/environment", ".json"):
    x = i.split("/")[-1].strip()
    if x[0] != ".": 
      file_count += [int(x.split(".")[0])]
  curr_json = f'storage/{sim_code}/environment/{str(max(file_count))}.json'
  with open(curr_json) as json_file:  
    persona_init_pos_dict = json.load(json_file)
    for key, val in persona_init_pos_dict.items(): 
      if key in persona_names_set: 
        persona_init_pos += [[key, val["x"], val["y"]]]

  context = {"sim_code": sim_code,
             "step": step,
             "persona_names": persona_names,
             "persona_init_pos": persona_init_pos, 
             "mode": "replay"}
  template = "home/home.html"
  return render(request, template, context)


def replay_persona_state(request, sim_code, step, persona_name): 
  sim_code = sim_code
  step = int(step)

  persona_name_underscore = persona_name
  persona_name = " ".join(persona_name.split("_"))
  memory = f"storage/{sim_code}/personas/{persona_name}/bootstrap_memory"
  if not os.path.exists(memory): 
    memory = f"compressed_storage/{sim_code}/personas/{persona_name}/bootstrap_memory"

  with open(memory + "/scratch.json") as json_file:  
    scratch = json.load(json_file)

  with open(memory + "/spatial_memory.json") as json_file:  
    spatial = json.load(json_file)

  with open(memory + "/associative_memory/nodes.json") as json_file:  
    associative = json.load(json_file)

  a_mem_event = []
  a_mem_chat = []
  a_mem_thought = []

  for count in range(len(associative.keys()), 0, -1): 
    node_id = f"node_{str(count)}"
    node_details = associative[node_id]

    if node_details["type"] == "event":
      a_mem_event += [node_details]

    elif node_details["type"] == "chat":
      a_mem_chat += [node_details]

    elif node_details["type"] == "thought":
      a_mem_thought += [node_details]
  
  context = {"sim_code": sim_code,
             "step": step,
             "persona_name": persona_name, 
             "persona_name_underscore": persona_name_underscore, 
             "scratch": scratch,
             "spatial": spatial,
             "a_mem_event": a_mem_event,
             "a_mem_chat": a_mem_chat,
             "a_mem_thought": a_mem_thought}
  template = "persona_state/persona_state.html"
  return render(request, template, context)


def path_tester(request):
  context = {}
  template = "path_tester/path_tester.html"
  return render(request, template, context)


def process_environment(request): 
  """
  <FRONTEND to BACKEND> 
  This sends the frontend visual world information to the backend server. 
  It does this by writing the current environment representation to 
  "storage/environment.json" file. 

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse: string confirmation message. 
  """
  # f_curr_sim_code = "temp_storage/curr_sim_code.json"
  # with open(f_curr_sim_code) as json_file:  
  #   sim_code = json.load(json_file)["sim_code"]

  data = json.loads(request.body)
  step = data["step"]
  sim_code = data["sim_code"]
  environment = data["environment"]

  with open(f"storage/{sim_code}/environment/{step}.json", "w") as outfile:
    outfile.write(json.dumps(environment, indent=2))

  return HttpResponse("received")


def update_environment(request): 
  """
  <BACKEND to FRONTEND> 
  This sends the backend computation of the persona behavior to the frontend
  visual server. 
  It does this by reading the new movement information from 
  "storage/movement.json" file.

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse
  """
  # f_curr_sim_code = "temp_storage/curr_sim_code.json"
  # with open(f_curr_sim_code) as json_file:  
  #   sim_code = json.load(json_file)["sim_code"]

  data = json.loads(request.body)
  step = data["step"]
  sim_code = data["sim_code"]

  response_data = {"<step>": -1}
  if (check_if_file_exists(f"storage/{sim_code}/movement/{step}.json")):
    with open(f"storage/{sim_code}/movement/{step}.json") as json_file: 
      response_data = json.load(json_file)
      response_data["<step>"] = step

  return JsonResponse(response_data)


def path_tester_update(request): 
  """
  Processing the path and saving it to path_tester_env.json temp storage for 
  conducting the path tester. 

  ARGS:
    request: Django request
  RETURNS: 
    HttpResponse: string confirmation message. 
  """
  data = json.loads(request.body)
  camera = data["camera"]

  with open(f"temp_storage/path_tester_env.json", "w") as outfile:
    outfile.write(json.dumps(camera, indent=2))

  return HttpResponse("received")


def interview_persona(request):
  """
  <FRONTEND to BACKEND>
  Drops an interview question into a request file. The backend Reverie
  process polls this folder and answers using the persona's existing
  stateless "analysis" convo session (same one used by "call -- analysis").
  """
  data = json.loads(request.body)
  sim_code = data["sim_code"]
  persona_name = data["persona_name"]
  question = data["question"]
  request_id = data["request_id"]

  interview_dir = f"storage/{sim_code}/interview"
  os.makedirs(interview_dir, exist_ok=True)

  req_file = f"{interview_dir}/{persona_name.replace(' ', '_')}_{request_id}_request.json"
  with open(req_file, "w") as outfile:
    outfile.write(json.dumps({
      "persona_name": persona_name,
      "question": question,
      "request_id": request_id
    }, indent=2))

  return HttpResponse("received")


def interview_response(request):
  """
  <BACKEND to FRONTEND>
  Polls for the answer file the backend writes once it has generated a
  response to the interview question.
  """
  data = json.loads(request.body)
  sim_code = data["sim_code"]
  persona_name = data["persona_name"]
  request_id = data["request_id"]

  resp_file = (f"storage/{sim_code}/interview/"
               f"{persona_name.replace(' ', '_')}_{request_id}_response.json")

  if check_if_file_exists(resp_file):
    with open(resp_file) as json_file:
      resp = json.load(json_file)
    return JsonResponse({"ready": True, "response": resp["response"]})

  return JsonResponse({"ready": False})
