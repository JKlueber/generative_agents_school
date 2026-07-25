"""
Author: Joon Sung Park (joonspk@stanford.edu)
File: views.py

Django views for the Reverie frontend server. This module is split into
four logical groups (see section headers below):

  1. Party-selection / simulator launch  -- landing page, spawning and
     controlling the reverie.py backend subprocess.
  2. Simulation / replay page rendering  -- home(), replay(), demo(), etc.
  3. Frontend <-> backend polling endpoints -- process_environment,
     update_environment, path_tester_update.
  4. Interview subsystem -- lets a user "chat" with a persona live, backed
     by a lightweight secondary reverie.py process running in "interview
     mode".
"""
import os
import json
import datetime
import threading

import pexpect

from django.shortcuts import render
from django.http import HttpResponse, JsonResponse

from global_methods import *


# ============================================================================
# SECTION 0: CONSTANTS & PROCESS-MANAGEMENT STATE
# ============================================================================

# Maps a party button to the base simulation it forks from and the CSV of
# scripted agent history used to bootstrap that party's persona memories.
# To add a new party, add an entry here and drop a matching CSV under
# maze_assets/the_ville/.
PARTY_CONFIG = {
    "cdu":    {"label": "CDU",    "fork": "base_the_ville_n5_school", "history": "the_ville/agent_history_init_n5_school_cdu.csv"},
    "spd":    {"label": "SPD",    "fork": "base_the_ville_n5_school", "history": "the_ville/agent_history_init_n5_school_spd.csv"},
    "linke":  {"label": "LINKE",  "fork": "base_the_ville_n5_school", "history": "the_ville/agent_history_init_n5_school_linke.csv"},
    "afd":    {"label": "AfD",    "fork": "base_the_ville_n5_school", "history": "the_ville/agent_history_init_n5_school_afd.csv"},
    "gruene": {"label": "GRÜNE",  "fork": "base_the_ville_n5_school", "history": "the_ville/agent_history_init_n5_school_gruene.csv"},
}

# Absolute path to reverie/backend_server, where reverie.py actually lives.
BACKEND_SERVER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "reverie", "backend_server")
)

# In-process bookkeeping for the *live simulation* backend (one at a time).
# _launch_lock / _run_lock guard against double-launching or double-running.
_launch_lock = threading.Lock()
_launch_state = {"running": False, "party": None, "executing": False}
_backend_proc = {"proc": None}
_run_lock = threading.Lock()

# In-process bookkeeping for the *interview-only* backends. Unlike the live
# simulation backend, there can be one of these per sim_code (e.g. one per
# replay tab a user has open), so this is a dict keyed by sim_code rather
# than a single slot.
_interview_backend_procs = {}   # sim_code -> pexpect proc
_interview_backend_lock = threading.Lock()


# ============================================================================
# SECTION 1: PARTY SELECTION / SIMULATOR LAUNCH & CONTROL
# ============================================================================

def simulator_start(request):
    """
    Landing page with the five party buttons. Clicking one starts the
    backend reverie.py process (forking that party's base simulation and
    loading its agent history), then the page polls simulator_launch_status
    until it's ready and forwards to /simulator_home.
    """
    parties = [dict(code=code, **cfg) for code, cfg in PARTY_CONFIG.items()]
    context = {"parties": parties, "already_running": _launch_state["running"]}
    return render(request, "simulator_start/simulator_start.html", context)


def _run_reverie_process(fork_sim_code, new_sim_code, history_csv, resume=False):
    """
    Runs in a background thread. Spawns `python3 reverie.py`, forks the
    party's base simulation (or resumes an already-forked one), loads its
    agent history on a fresh start, and advances one step so the initial
    environment/movement files exist.

    After that, the process idles at the "Enter option:" prompt.
    simulator_run_minutes() drives it forward in user-requested chunks, and
    simulator_control() sends "save"/"exit" directly, since the backend is
    listening on stdin again between runs.
    """
    try:
        proc = pexpect.spawn(
            "python3 reverie.py",
            cwd=BACKEND_SERVER_DIR,
            encoding="utf-8",
            timeout=None,
        )
        _backend_proc["proc"] = proc

        log_path = os.path.join(BACKEND_SERVER_DIR, "reverie_backend.log")
        proc.logfile = open(log_path, "a", encoding="utf-8", buffering=1)

        proc.sendline(fork_sim_code)
        proc.sendline(new_sim_code)
        proc.expect("Enter option:")

        if not resume:
            proc.sendline(f"call -- load history {history_csv}")
            proc.expect("Enter option:")
            proc.sendline("run 1")
            proc.expect("Enter option:")

        threading.Thread(target=_watch_backend_until_exit, args=(proc,), daemon=True).start()

    except Exception as e:
        print(f"[_run_reverie_process] backend launch failed: {e}")
        _backend_proc["proc"] = None
        _launch_state["running"] = False
        _launch_state["party"] = None


def _watch_backend_until_exit(proc):
    """Background watchdog: once the backend process dies, reset launch state."""
    try:
        while proc.isalive():
            time.sleep(0.5)
    except Exception:
        pass
    finally:
        if proc.logfile:
            proc.logfile.close()
        _backend_proc["proc"] = None
        _launch_state["running"] = False
        _launch_state["party"] = None
        _launch_state["executing"] = False


def simulator_launch(request, party):
    """
    <FRONTEND button click>
    Kicks off the reverie backend for the chosen party in a background
    thread and returns immediately; the page polls simulator_launch_status
    to know when to redirect to /simulator_home.

    If this party was Saved previously (its storage/run_<party> folder still
    exists), we resume that simulation in place instead of forking a fresh
    copy of the base template. If it was Exited (or never run), that folder
    won't exist, so we fork fresh.
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
        new_sim_code = f"run_{party}"
        existing_sim_folder = f"storage/{new_sim_code}"
        resume = os.path.isdir(existing_sim_folder)
        fork_sim_code = new_sim_code if resume else cfg["fork"]

        stale_step_file = "temp_storage/curr_step.json"
        if check_if_file_exists(stale_step_file):
            os.remove(stale_step_file)

        with open("temp_storage/curr_party.json", "w") as outfile:
            outfile.write(json.dumps({"party": party}, indent=2))

        _backend_proc["proc"] = None
        _launch_state["running"] = True
        _launch_state["party"] = party

        threading.Thread(
            target=_run_reverie_process,
            args=(fork_sim_code, new_sim_code, cfg["history"]),
            kwargs={"resume": resume},
            daemon=True,
        ).start()
    finally:
        _launch_lock.release()

    return JsonResponse({"status": "starting", "party": party, "resumed": resume})


def simulator_launch_status(request):
    """<FRONTEND polling> Has the backend finished forking + loading yet?"""
    ready = check_if_file_exists("temp_storage/curr_step.json")
    return JsonResponse({"ready": ready})


def simulator_run_minutes(request):
    """
    <FRONTEND button click>
    Advances the backend by a user-specified number of real-world minutes.
    Each step is 10 seconds, so minutes -> steps is a fixed x6 conversion.
    """
    try:
        data = json.loads(request.body or b"{}")
        minutes = int(data.get("minutes"))
    except Exception:
        return JsonResponse({"error": "invalid minutes"}, status=400)

    if minutes <= 0:
        return JsonResponse({"error": "minutes must be positive"}, status=400)

    proc = _backend_proc.get("proc")
    if not proc or not proc.isalive():
        return JsonResponse({"error": "no running backend"}, status=409)

    if _launch_state.get("executing") or not _run_lock.acquire(blocking=False):
        return JsonResponse({"error": "a run is already in progress"}, status=409)

    _launch_state["executing"] = True
    steps = minutes * 6

    def _do_run():
        try:
            proc.sendline(f"run {steps}")
            proc.expect("Enter option:")
        except Exception as e:
            print(f"[simulator_run_minutes] run failed: {e}")
        finally:
            _launch_state["executing"] = False
            if _run_lock.locked():
                _run_lock.release()

    threading.Thread(target=_do_run, daemon=True).start()
    return JsonResponse({"status": "running", "minutes": minutes, "steps": steps})


def simulator_run_status(request):
    """<FRONTEND polling> Is the backend mid-way through a "run N" batch?"""
    return JsonResponse({"executing": _launch_state.get("executing", False)})


def simulator_control(request, action):
    """<FRONTEND button click> Sends 'save' (fin) or 'exit' to the backend."""
    action = action.lower()
    if action not in ("fin", "exit"):
        return JsonResponse({"error": "unknown action"}, status=404)

    if _launch_state.get("executing"):
        return JsonResponse({"error": "backend is busy running steps"}, status=409)

    proc = _backend_proc.get("proc")
    if proc and proc.isalive():
        try:
            proc.sendline(action)
        except Exception:
            pass

    _launch_state["running"] = False
    _launch_state["party"] = None
    _backend_proc["proc"] = None

    curr_party_file = "temp_storage/curr_party.json"
    if check_if_file_exists(curr_party_file):
        os.remove(curr_party_file)

    return JsonResponse({"status": action})


# ============================================================================
# SECTION 2: SIMULATION / REPLAY PAGE RENDERING
# ============================================================================

def landing(request):
    return render(request, "landing/landing.html", {})


def _load_persona_roster(sim_code):
    """
    Shared helper for home() and replay(): scans a sim's persona folders and
    its latest environment snapshot to build the two lists every "world"
    page needs.

    OUTPUT:
      persona_names:    [[full_name, full_name_with_underscores], ...]
      persona_init_pos: [[full_name, x, y], ...]
    """
    persona_names = []
    persona_names_set = set()
    for path in find_filenames(f"storage/{sim_code}/personas", ""):
        name = path.split("/")[-1].strip()
        if name and name[0] != ".":
            persona_names.append([name, name.replace(" ", "_")])
            persona_names_set.add(name)

    # The "latest" environment file is whichever step has the highest number.
    step_numbers = [
        int(path.split("/")[-1].split(".")[0])
        for path in find_filenames(f"storage/{sim_code}/environment", ".json")
        if path.split("/")[-1][0] != "."
    ]
    latest_env_file = f"storage/{sim_code}/environment/{max(step_numbers)}.json"

    persona_init_pos = []
    with open(latest_env_file) as json_file:
        env = json.load(json_file)
    for name, pos in env.items():
        if name in persona_names_set:
            persona_init_pos.append([name, pos["x"], pos["y"]])

    return persona_names, persona_init_pos


def home(request):
    """Renders the live simulation view (mode='simulate')."""
    curr_sim_code_file = "temp_storage/curr_sim_code.json"
    curr_step_file = "temp_storage/curr_step.json"
    curr_party_file = "temp_storage/curr_party.json"

    if not check_if_file_exists(curr_step_file):
        return render(request, "home/error_start_backend.html", {})

    with open(curr_sim_code_file) as json_file:
        sim_code = json.load(json_file)["sim_code"]
    with open(curr_step_file) as json_file:
        step = json.load(json_file)["step"]
    os.remove(curr_step_file)

    party = None
    if check_if_file_exists(curr_party_file):
        with open(curr_party_file) as json_file:
            party = json.load(json_file)["party"]

    persona_names, persona_init_pos = _load_persona_roster(sim_code)

    context = {
        "sim_code": sim_code,
        "step": step,
        "persona_names": persona_names,
        "persona_init_pos": persona_init_pos,
        "mode": "simulate",
        "party": party,
    }
    return render(request, "home/home.html", context)


def replay(request, sim_code, step):
    """Renders a saved simulation for replay (mode='replay')."""
    step = int(step)
    party_name = sim_code.split("_")[1] if sim_code.startswith("run_") else None

    persona_names, persona_init_pos = _load_persona_roster(sim_code)

    context = {
        "sim_code": sim_code,
        "step": step,
        "persona_names": persona_names,
        "persona_init_pos": persona_init_pos,
        "mode": "replay",
        "party": party_name,
    }
    return render(request, "home/home.html", context)


def demo(request, sim_code, step, play_speed="2"):
    """
    Renders the pre-computed, self-contained public demo (no live backend
    required -- movement for the whole simulation is pre-baked into
    compressed_storage/<sim_code>/master_movement.json).
    """
    step = int(step)
    play_speed_options = {"1": 1, "2": 2, "3": 4, "4": 8, "5": 16, "6": 32}
    play_speed = play_speed_options.get(play_speed, 2)

    meta_file = f"compressed_storage/{sim_code}/meta.json"
    with open(meta_file) as json_file:
        meta = json.load(json_file)

    sec_per_step = meta["sec_per_step"]
    start_datetime = datetime.datetime.strptime(
        meta["start_date"] + " 00:00:00", "%B %d, %Y %H:%M:%S"
    ) + datetime.timedelta(seconds=sec_per_step * step)
    start_datetime = start_datetime.strftime("%Y-%m-%dT%H:%M:%S")

    move_file = f"compressed_storage/{sim_code}/master_movement.json"
    with open(move_file) as json_file:
        raw_all_movement = json.load(json_file)

    persona_names = []
    persona_names_set = set()
    for p in raw_all_movement["0"].keys():
        persona_names.append({
            "original": p,
            "underscore": p.replace(" ", "_"),
            "initial": p[0] + p.split(" ")[-1][0],
        })
        persona_names_set.add(p)

    # <all_movement> is sent to the frontend in one shot (unlike the live
    # simulation, which polls step by step via ajax). Step <step> itself is
    # special-cased: we backfill it with each persona's most recent known
    # position/description so the demo doesn't open on an empty frame.
    all_movement = {}
    init_prep = {}
    for i in range(step + 1):
        snapshot = raw_all_movement[str(i)]
        for p in persona_names_set:
            if p in snapshot:
                init_prep[p] = snapshot[p]
    all_movement[step] = init_prep

    persona_init_pos = {
        p.replace(" ", "_"): init_prep[p]["movement"] for p in persona_names_set
    }

    for i in range(step + 1, len(raw_all_movement)):
        all_movement[i] = raw_all_movement[str(i)]

    context = {
        "sim_code": sim_code,
        "step": step,
        "persona_names": persona_names,
        "persona_init_pos": json.dumps(persona_init_pos),
        "all_movement": json.dumps(all_movement),
        "start_datetime": start_datetime,
        "sec_per_step": sec_per_step,
        "play_speed": play_speed,
        "mode": "demo",
    }
    return render(request, "demo/demo.html", context)


def UIST_Demo(request):
    """Convenience shortcut to the canonical UIST paper demo."""
    return demo(request, "March20_the_ville_n25_UIST_RUN-step-1-141", 2160, play_speed="3")


def replay_persona_state(request, sim_code, step, persona_name):
    """Renders the detailed memory/scratch dump for a single persona."""
    step = int(step)
    persona_name_underscore = persona_name
    persona_name = " ".join(persona_name.split("_"))

    memory_dir = f"storage/{sim_code}/personas/{persona_name}/bootstrap_memory"
    if not os.path.exists(memory_dir):
        memory_dir = f"compressed_storage/{sim_code}/personas/{persona_name}/bootstrap_memory"

    with open(f"{memory_dir}/scratch.json") as json_file:
        scratch = json.load(json_file)
    with open(f"{memory_dir}/spatial_memory.json") as json_file:
        spatial = json.load(json_file)
    with open(f"{memory_dir}/associative_memory/nodes.json") as json_file:
        associative = json.load(json_file)

    # Memory nodes are stored newest-first by walking node_N down to node_1.
    a_mem_event, a_mem_chat, a_mem_thought = [], [], []
    for count in range(len(associative), 0, -1):
        node = associative[f"node_{count}"]
        if node["type"] == "event":
            a_mem_event.append(node)
        elif node["type"] == "chat":
            a_mem_chat.append(node)
        elif node["type"] == "thought":
            a_mem_thought.append(node)

    context = {
        "sim_code": sim_code,
        "step": step,
        "persona_name": persona_name,
        "persona_name_underscore": persona_name_underscore,
        "scratch": scratch,
        "spatial": spatial,
        "a_mem_event": a_mem_event,
        "a_mem_chat": a_mem_chat,
        "a_mem_thought": a_mem_thought,
    }
    return render(request, "persona_state/persona_state.html", context)


def path_tester(request):
    return render(request, "path_tester/path_tester.html", {})


# ============================================================================
# SECTION 3: FRONTEND <-> BACKEND POLLING ENDPOINTS
# ============================================================================

def _resolve_sim_code(data):
    """
    Shared helper for the polling endpoints below: prefer the sim_code sent
    in the request body, but fall back to temp_storage/curr_sim_code.json
    for older frontend code that doesn't send one. Returns None if neither
    is available.
    """
    sim_code = data.get("sim_code")
    if sim_code:
        return sim_code
    try:
        with open("temp_storage/curr_sim_code.json") as json_file:
            return json.load(json_file)["sim_code"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None


def process_environment(request):
    """<FRONTEND to BACKEND> Reports current persona tile positions."""
    data = json.loads(request.body)
    step = data["step"]
    environment = data["environment"]

    sim_code = _resolve_sim_code(data)
    if not sim_code:
        return HttpResponse("error: missing simulation code context", status=400)

    with open(f"storage/{sim_code}/environment/{step}.json", "w") as outfile:
        outfile.write(json.dumps(environment, indent=2))
    return HttpResponse("received")


def update_environment(request):
    """<BACKEND to FRONTEND> Returns the backend's computed persona movement, if ready."""
    data = json.loads(request.body)
    step = data["step"]

    sim_code = _resolve_sim_code(data)
    if not sim_code:
        return JsonResponse({"<step>": -1, "error": "missing simulation code context"}, status=400)

    response_data = {"<step>": -1}
    movement_file = f"storage/{sim_code}/movement/{step}.json"
    if check_if_file_exists(movement_file):
        with open(movement_file) as json_file:
            response_data = json.load(json_file)
        response_data["<step>"] = step

    return JsonResponse(response_data)


def path_tester_update(request):
    """Saves the path-tester camera position for the backend to pick up."""
    data = json.loads(request.body)
    with open("temp_storage/path_tester_env.json", "w") as outfile:
        outfile.write(json.dumps(data["camera"], indent=2))
    return HttpResponse("received")


# ============================================================================
# SECTION 4: INTERVIEW SUBSYSTEM
# ============================================================================
#
# Interviewing a persona during replay needs *some* backend alive to answer
# questions, but must NOT advance the simulation (movement during replay
# comes entirely from pre-saved movement/*.json files). So we spawn a
# second, lightweight reverie.py process per sim_code, forked into itself,
# sitting in "interview mode" -- it does nothing but poll for and answer
# interview request files.

def _run_interview_only_backend(sim_code):
    """Background-thread target: spawns and registers an interview-only backend."""
    try:
        proc = pexpect.spawn(
            "python3 reverie.py",
            cwd=BACKEND_SERVER_DIR,
            encoding="utf-8",
            timeout=None,
        )
        log_path = os.path.join(BACKEND_SERVER_DIR, "reverie_backend_interview.log")
        proc.logfile = open(log_path, "a", encoding="utf-8", buffering=1)

        proc.sendline(sim_code)
        proc.sendline(sim_code)   # fork target == itself: no-op copy
        proc.expect("Enter option:")

        # ReverieServer.__init__ writes temp_storage/curr_step.json and
        # curr_sim_code.json, which are meant for the *live* /simulator_home
        # flow. Clean those up so this interview-only process doesn't
        # confuse that flow.
        for fname in ("temp_storage/curr_step.json", "temp_storage/curr_sim_code.json"):
            full_path = os.path.join(BACKEND_SERVER_DIR, fname)
            if os.path.exists(full_path):
                os.remove(full_path)

        proc.sendline("interview mode")
        _interview_backend_procs[sim_code] = proc

        threading.Thread(target=_drain_interview_backend, args=(proc,), daemon=True).start()
    except Exception as e:
        print(f"[_run_interview_only_backend] failed: {e}")
        _interview_backend_procs.pop(sim_code, None)


def _drain_interview_backend(proc):
    """
    Continuously reads whatever the child process prints and forwards it to
    proc.logfile. This is required because "interview mode" loops forever
    inside the child and never returns to the "Enter option:" prompt, so
    nothing else ever calls .expect()/.read() on this pexpect connection.
    Without a drain, the child's stdout pty buffer fills up, its next
    print() blocks, and the whole interview backend silently freezes.
    """
    try:
        while proc.isalive():
            try:
                proc.expect(pexpect.TIMEOUT, timeout=1)
            except Exception:
                break
    except Exception:
        pass


def replay_start_interview_backend(request, sim_code):
    """<FRONTEND, called once when a replay page loads> Ensures an interview backend is up."""
    with _interview_backend_lock:
        proc = _interview_backend_procs.get(sim_code)
        if proc and proc.isalive():
            return JsonResponse({"status": "already running"})
        threading.Thread(target=_run_interview_only_backend, args=(sim_code,), daemon=True).start()
    return JsonResponse({"status": "starting"})


def interview_persona(request):
    """
    <FRONTEND to BACKEND>
    Drops an interview question into a request file. The backend polls this
    folder and answers it using the persona's stateless "analysis" convo
    session (the same logic as "call -- analysis" on the console).
    """
    data = json.loads(request.body)
    sim_code = data["sim_code"]
    persona_name = data["persona_name"]

    interview_dir = f"storage/{sim_code}/interview"
    os.makedirs(interview_dir, exist_ok=True)

    req_file = f"{interview_dir}/{persona_name.replace(' ', '_')}_{data['request_id']}_request.json"
    with open(req_file, "w") as outfile:
        outfile.write(json.dumps({
            "persona_name": persona_name,
            "question": data["question"],
            "request_id": data["request_id"],
            "step": data.get("step"),
        }, indent=2))

    return HttpResponse("received")


def interview_response(request):
    """<BACKEND to FRONTEND> Polls for the answer file the backend writes."""
    data = json.loads(request.body)
    resp_file = (
        f"storage/{data['sim_code']}/interview/"
        f"{data['persona_name'].replace(' ', '_')}_{data['request_id']}_response.json"
    )

    if check_if_file_exists(resp_file):
        with open(resp_file) as json_file:
            resp = json.load(json_file)
        return JsonResponse({"ready": True, "response": resp["response"]})

    return JsonResponse({"ready": False})