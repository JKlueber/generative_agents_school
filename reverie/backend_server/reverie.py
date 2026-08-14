"""
Author: Joon Sung Park (joonspk@stanford.edu)

File: reverie.py
Description: This is the main program for running generative agent simulations
that defines the ReverieServer class. This class maintains and records all
states related to the simulation. The primary mode of interaction for those
running the simulation should be through the open_server function, which
enables the simulator to input command-line prompts for running and saving
the simulation, among other tasks.

Release note (June 14, 2023) -- Reverie implements the core simulation
mechanism described in my paper entitled "Generative Agents: Interactive
Simulacra of Human Behavior." If you are reading through these lines after
having read the paper, you might notice that I use older terms to describe
generative agents and their cognitive modules here. Most notably, I use the
term "personas" to refer to generative agents, "associative memory" to refer
to the memory stream, and "reverie" to refer to the overarching simulation
framework.
"""
import json
import datetime
import time
import os
import shutil
import traceback

from global_methods import *
from utils import *
from maze import *
from persona.persona import *

##############################################################################
#                                  REVERIE                                   #
##############################################################################

class ReverieServer:
  def __init__(self, fork_sim_code, sim_code):
    # FORKING FROM A PRIOR SIMULATION:
    # <fork_sim_code> indicates the simulation we are forking from.
    # Interestingly, all simulations must be forked from some initial
    # simulation, where the first simulation is "hand-crafted".
    self.fork_sim_code = fork_sim_code
    fork_folder = f"{fs_storage}/{self.fork_sim_code}"

    # <sim_code> indicates our current simulation. The first step here is to
    # copy everything that's in <fork_sim_code>, but edit its
    # reverie/meta/json's fork variable.
    self.sim_code = sim_code
    sim_folder = f"{fs_storage}/{self.sim_code}"
    if fork_folder != sim_folder:
      copyanything(fork_folder, sim_folder)

    with open(f"{sim_folder}/reverie/meta.json") as json_file:
      reverie_meta = json.load(json_file)

    if fork_folder != sim_folder:
      with open(f"{sim_folder}/reverie/meta.json", "w") as outfile:
        reverie_meta["fork_sim_code"] = fork_sim_code
        outfile.write(json.dumps(reverie_meta, indent=2))

    # LOADING REVERIE'S GLOBAL VARIABLES
    # <start_datetime> is the datetime instance for the start datetime of the
    # Reverie instance. Once set, this is not really meant to change. It
    # takes a string date in the following example form: "June 25, 2022"
    self.start_time = datetime.datetime.strptime(
                        f"{reverie_meta['start_date']}, 00:00:00",
                        "%B %d, %Y, %H:%M:%S")
    # <curr_time> is the datetime instance that indicates the game's current
    # time. This gets incremented by <sec_per_step> amount everytime the
    # world progresses (that is, everytime curr_env_file is received).
    self.curr_time = datetime.datetime.strptime(reverie_meta['curr_time'],
                                                "%B %d, %Y, %H:%M:%S")
    # <sec_per_step> denotes the number of seconds in game time that each
    # step moves foward.
    self.sec_per_step = reverie_meta['sec_per_step']

    # <maze> is the main Maze instance (e.g. Maze("the_ville")).
    self.maze = Maze(reverie_meta['maze_name'])

    # <step> denotes the number of steps that our game has taken. A step here
    # literally translates to the number of moves our personas made in terms
    # of the number of tiles.
    self.step = reverie_meta['step']

    # SETTING UP PERSONAS IN REVERIE
    # <personas> maps a persona's full name to its Persona instance.
    # e.g., personas["Isabella Rodriguez"] = Persona("Isabella Rodriguez")
    self.personas = dict()
    # <personas_tile> maps a persona's full name to its (row, col) tile
    # coordinate on the maze (NOT pixel coordinates).
    # e.g., personas_tile["Isabella Rodriguez"] = (58, 39)
    self.personas_tile = dict()

    init_env_file = f"{sim_folder}/environment/{str(self.step)}.json"
    init_env = json.load(open(init_env_file))
    for persona_name in reverie_meta['persona_names']:
      persona_folder = f"{sim_folder}/personas/{persona_name}"
      p_x = init_env[persona_name]["x"]
      p_y = init_env[persona_name]["y"]
      curr_persona = Persona(persona_name, persona_folder)

      self.personas[persona_name] = curr_persona
      self.personas_tile[persona_name] = (p_x, p_y)
      self.maze.tiles[p_y][p_x]["events"].add(curr_persona.scratch
                                              .get_curr_event_and_desc())

    # REVERIE SETTINGS PARAMETERS:
    # <server_sleep> is how long our while loop rests each cycle, so we
    # don't peg the CPU.
    self.server_sleep = 0.1

    # HEADLESS MODE:
    # When True, start_server() does not wait for the frontend to report
    # persona tile positions after each step. Instead it writes the
    # environment file for the *next* step itself, using the tile each
    # persona.move() just decided on -- standing in for the frontend's
    # process_environment POST. Lets `run N` advance with no browser
    # attached (e.g. in CI).
    self.headless = False

    # SIGNALING THE FRONTEND SERVER:
    # curr_sim_code.json / curr_step.json communicate the current sim code
    # and step to the frontend. The step file is removed as soon as the
    # frontend opens up the simulation (see views.home()).
    with open(f"{fs_temp_storage}/curr_sim_code.json", "w") as outfile:
      outfile.write(json.dumps({"sim_code": self.sim_code}, indent=2))
    with open(f"{fs_temp_storage}/curr_step.json", "w") as outfile:
      outfile.write(json.dumps({"step": self.step}, indent=2))


  def _write_headless_environment(self, next_step, movements):
    """
    Headless-mode helper: writes storage/<sim_code>/environment/<next_step>.json
    directly from the movement decisions we just made. Each persona is
    placed exactly on the tile persona.move() chose as their next step --
    there's no pixel-level animation to simulate here, so this is a 1:1
    substitute for what the frontend would have posted.
    """
    sim_folder = f"{fs_storage}/{self.sim_code}"
    environment = dict()
    for persona_name, move_info in movements["persona"].items():
      x, y = move_info["movement"]
      environment[persona_name] = {
        "maze": self.maze.maze_name,
        "x": x,
        "y": y,
      }
    env_file = f"{sim_folder}/environment/{next_step}.json"
    with open(env_file, "w") as outfile:
      outfile.write(json.dumps(environment, indent=2))


  def save(self):
    """
    Save all Reverie progress -- Reverie's global state as well as all the
    personas' memory.
    """
    sim_folder = f"{fs_storage}/{self.sim_code}"

    reverie_meta = dict()
    reverie_meta["fork_sim_code"] = self.fork_sim_code
    reverie_meta["start_date"] = self.start_time.strftime("%B %d, %Y")
    reverie_meta["curr_time"] = self.curr_time.strftime("%B %d, %Y, %H:%M:%S")
    reverie_meta["sec_per_step"] = self.sec_per_step
    reverie_meta["maze_name"] = self.maze.maze_name
    reverie_meta["persona_names"] = list(self.personas.keys())
    reverie_meta["step"] = self.step
    with open(f"{sim_folder}/reverie/meta.json", "w") as outfile:
      outfile.write(json.dumps(reverie_meta, indent=2))

    for persona_name, persona in self.personas.items():
      save_folder = f"{sim_folder}/personas/{persona_name}/bootstrap_memory"
      persona.save(save_folder)


  def start_path_tester_server(self):
    """
    Starts the path tester server. This is for generating the spatial memory
    we need for bootstrapping a persona's state. To use this, open the
    server in path-tester mode and open the frontend in a browser.

    Saves the test agent's spatial memory to path_tester_out.json in temp
    storage.
    """
    def print_tree(tree, depth=0):
      if isinstance(tree, list):
        if tree:
          print(" >" * depth, tree)
        return
      for key, val in tree.items():
        if key:
          print(" >" * depth, key)
        print_tree(val, depth + 1)

    curr_vision = 8   # vision radius of the test agent
    s_mem = dict()    # our test spatial memory

    while True:
      try:
        tester_file = fs_temp_storage + "/path_tester_env.json"
        if check_if_file_exists(tester_file):
          with open(tester_file) as json_file:
            curr_dict = json.load(json_file)
          os.remove(tester_file)

          # Current camera location -> tile coordinate.
          curr_sts = self.maze.sq_tile_size
          curr_camera = (int(math.ceil(curr_dict["x"] / curr_sts)),
                         int(math.ceil(curr_dict["y"] / curr_sts)) + 1)
          curr_tile_det = self.maze.access_tile(curr_camera)

          world = curr_tile_det["world"]
          if world not in s_mem:
            s_mem[world] = dict()

          # Walk the nearby tiles and record any sector/arena/game object
          # that shares the current sector+arena with the camera's tile.
          nearby_tiles = self.maze.get_nearby_tiles(curr_camera, curr_vision)
          for i in nearby_tiles:
            i_det = self.maze.access_tile(i)
            if (curr_tile_det["sector"] == i_det["sector"]
                and curr_tile_det["arena"] == i_det["arena"]):
              if i_det["sector"]:
                s_mem[world].setdefault(i_det["sector"], dict())
              if i_det["arena"]:
                s_mem[world][i_det["sector"]].setdefault(i_det["arena"], list())
              if i_det["game_object"]:
                if i_det["game_object"] not in s_mem[world][i_det["sector"]][i_det["arena"]]:
                  s_mem[world][i_det["sector"]][i_det["arena"]] += [i_det["game_object"]]

        # Incrementally write out the s_mem snapshot.
        out_file = fs_temp_storage + "/path_tester_out.json"
        with open(out_file, "w") as outfile:
          outfile.write(json.dumps(s_mem, indent=2))
        print("= " * 15)
        print_tree(s_mem)

      except Exception:
        pass

      time.sleep(self.server_sleep * 10)


  def start_server(self, int_counter):
    """
    The main backend simulation loop. Retrieves the environment file the
    frontend produces (persona positions after the frontend moved them),
    lets each persona decide their next action, and writes the resulting
    movement file back out for the frontend to pick up.

    INPUT
      int_counter: number of steps left to take before this call returns.
    """
    sim_folder = f"{fs_storage}/{self.sim_code}"

    # When a persona arrives at a game object, we tag that object with a
    # unique "in use" event, e.g. ('...:bed', 'is', 'unmade', 'unmade').
    # Before the *next* cycle, we need to reset it back to idle, i.e.
    # ('...:bed', None, None, None). <game_obj_cleanup> tracks which event
    # needs resetting.
    game_obj_cleanup = dict()

    while True:
      self.process_interview_requests()
      self.process_control_requests()

      if int_counter == 0:
        break

      # <curr_env_file> is the file the frontend writes once it has finished
      # moving the personas -- that's our signal there's new perception
      # input for this step. Otherwise, we just wait.
      curr_env_file = f"{sim_folder}/environment/{self.step}.json"
      if not check_if_file_exists(curr_env_file):
        time.sleep(self.server_sleep)
        continue

      try:
        with open(curr_env_file) as json_file:
          new_env = json.load(json_file)
          env_retrieved = True
      except Exception:
        env_retrieved = False

      if env_retrieved:
        # Reset any object-in-use events from the previous cycle back to idle.
        for key, val in game_obj_cleanup.items():
          self.maze.turn_event_from_tile_idle(key, val)
        game_obj_cleanup = dict()

        # Move each persona on the backend tile map to match the frontend.
        for persona_name, persona in self.personas.items():
          curr_tile = self.personas_tile[persona_name]
          new_tile = (new_env[persona_name]["x"], new_env[persona_name]["y"])

          self.personas_tile[persona_name] = new_tile
          self.maze.remove_subject_events_from_tile(persona.name, curr_tile)
          self.maze.add_event_from_tile(persona.scratch
                                       .get_curr_event_and_desc(), new_tile)

          # Once a persona has *arrived* at their destination (no more path
          # left), activate the object-in-use event for their current action.
          if not persona.scratch.planned_path:
            game_obj_cleanup[persona.scratch.get_curr_obj_event_and_desc()] = new_tile
            self.maze.add_event_from_tile(persona.scratch
                                   .get_curr_obj_event_and_desc(), new_tile)
            # Remove the temporary "blank" placeholder event for that object.
            blank = (persona.scratch.get_curr_obj_event_and_desc()[0], None, None, None)
            self.maze.remove_event_from_tile(blank, new_tile)

        # Let each persona perceive, think, and decide where to move next.
        movements = {"persona": dict(), "meta": dict()}
        for persona_name, persona in self.personas.items():
          next_tile, pronunciatio, description = persona.move(
            self.maze, self.personas, self.personas_tile[persona_name],
            self.curr_time)
          movements["persona"][persona_name] = {
            "movement": next_tile,
            "pronunciatio": pronunciatio,
            "description": description,
            "chat": persona.scratch.chat,
          }
        movements["meta"]["curr_time"] = self.curr_time.strftime("%B %d, %Y, %H:%M:%S")

        # Write the movement file for the frontend to consume.
        curr_move_file = f"{sim_folder}/movement/{self.step}.json"
        with open(curr_move_file, "w") as outfile:
          outfile.write(json.dumps(movements, indent=2))

        # HEADLESS MODE: synthesize next step's environment file ourselves
        # instead of waiting for the frontend to post one.
        if self.headless:
          next_env_file = f"{sim_folder}/environment/{self.step + 1}.json"
          if not check_if_file_exists(next_env_file):
            self._write_headless_environment(self.step + 1, movements)

        self.step += 1
        self.curr_time += datetime.timedelta(seconds=self.sec_per_step)
        int_counter -= 1

      time.sleep(self.server_sleep)


  def process_control_requests(self):
    """
    Checks storage/<sim_code>/control for pending *_request.json files
    (dropped by the frontend's Save/Exit buttons) and acts on them
    immediately -- this lets Save/Exit interrupt a long-running "run N"
    loop the same way interview questions do.
    """
    sim_folder = f"{fs_storage}/{self.sim_code}"
    control_dir = f"{sim_folder}/control"
    if not os.path.isdir(control_dir):
      return

    for fname in os.listdir(control_dir):
      if not fname.endswith("_request.json"):
        continue
      req_path = f"{control_dir}/{fname}"

      try:
        with open(req_path) as json_file:
          req = json.load(json_file)
      except Exception:
        continue

      try:
        os.remove(req_path)
      except Exception:
        pass

      action = req.get("action")
      if action == "save":
        print("Frontend requested save -- saving and stopping backend.")
        self.save()
        os._exit(0)
      elif action == "exit":
        print("Frontend requested exit -- discarding and stopping backend.")
        shutil.rmtree(sim_folder, ignore_errors=True)
        os._exit(0)


  def process_interview_requests(self):
    """
    Checks storage/<sim_code>/interview for pending *_request.json files
    and answers them using the persona's stateless "analysis" convo session.
    """
    interview_dir = f"{fs_storage}/{self.sim_code}/interview"
    if not os.path.isdir(interview_dir):
      return

    for fname in os.listdir(interview_dir):
      if not fname.endswith("_request.json"):
        continue
      req_path = f"{interview_dir}/{fname}"
      resp_path = req_path.replace("_request.json", "_response.json")
      if check_if_file_exists(resp_path):
        continue

      try:
        with open(req_path) as json_file:
          req = json.load(json_file)

        persona_name = req["persona_name"]
        if persona_name not in self.personas:
          continue

        step = req.get("step")
        if step is not None:
          target_time = self.start_time + datetime.timedelta(
                          seconds=int(step) * self.sec_per_step)
          answer = self.personas[persona_name].interview(req["question"], target_time=target_time)
        else:
          answer = self.personas[persona_name].interview(req["question"])

      except Exception:
        traceback.print_exc()
        answer = "Sorry, I got a bit distracted there -- could you ask that again?"

      try:
        with open(resp_path, "w") as outfile:
          outfile.write(json.dumps({"response": answer}, indent=2))
        if check_if_file_exists(req_path):
          os.remove(req_path)
      except Exception:
        traceback.print_exc()


  def start_interview_only_server(self):
    """
    Runs forever, only answering pending interview requests. Does NOT
    advance the simulation or write movement/environment files -- movement
    during a replay is driven entirely by the already-saved
    movement/*.json files. Wrapped in try/except so a transient error in
    process_interview_requests can't kill the loop itself.
    """
    print("Interview-only backend ready. Waiting for interview requests...")
    while True:
      try:
        self.process_interview_requests()
      except Exception:
        traceback.print_exc()
      time.sleep(self.server_sleep)


  # ==========================================================================
  # CONSOLE COMMANDS
  #
  # open_server() is an interactive REPL for driving/inspecting a simulation
  # from the terminal. Commands are matched by prefix against
  # sim_command.lower(); most "print ..." commands take a persona's full
  # name as their trailing argument.
  # ==========================================================================

  def _cmd_run(self, sim_command):
    """run <N> -- advances the simulation by N steps."""
    int_count = int(sim_command.split()[-1])
    self.start_server(int_count)
    return ""

  def _cmd_print_persona_schedule(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    return self.personas[persona_name].scratch.get_str_daily_schedule_summary()

  def _cmd_print_all_persona_schedule(self, sim_command):
    out = ""
    for persona_name, persona in self.personas.items():
      out += f"{persona_name}\n"
      out += f"{persona.scratch.get_str_daily_schedule_summary()}\n"
      out += "---\n"
    return out

  def _cmd_print_hourly_org_persona_schedule(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    return self.personas[persona_name].scratch.get_str_daily_schedule_hourly_org_summary()

  def _cmd_print_persona_current_tile(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    return str(self.personas[persona_name].scratch.curr_tile)

  def _cmd_print_persona_chatting_with_buffer(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    out = ""
    for p_n, count in self.personas[persona_name].scratch.chatting_with_buffer.items():
      out += f"{p_n}: {count}"
    return out

  def _cmd_print_persona_a_mem_event(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    persona = self.personas[persona_name]
    return f"{persona}\n{persona.a_mem.get_str_seq_events()}"

  def _cmd_print_persona_a_mem_thought(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    persona = self.personas[persona_name]
    return f"{persona}\n{persona.a_mem.get_str_seq_thoughts()}"

  def _cmd_print_persona_a_mem_chat(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    persona = self.personas[persona_name]
    return f"{persona}\n{persona.a_mem.get_str_seq_chats()}"

  def _cmd_print_persona_spatial_memory(self, sim_command):
    persona_name = " ".join(sim_command.split()[-2:])
    self.personas[persona_name].s_mem.print_tree()
    return ""

  def _cmd_print_current_time(self, sim_command):
    return (f'{self.curr_time.strftime("%B %d, %Y, %H:%M:%S")}\n'
            f'steps: {self.step}')

  def _cmd_print_tile_event(self, sim_command):
    coordinate = [int(i.strip()) for i in sim_command[16:].split(",")]
    out = ""
    for i in self.maze.access_tile(coordinate)["events"]:
      out += f"{i}\n"
    return out

  def _cmd_print_tile_details(self, sim_command):
    coordinate = [int(i.strip()) for i in sim_command[18:].split(",")]
    out = ""
    for key, val in self.maze.access_tile(coordinate).items():
      out += f"{key}: {val}\n"
    return out

  def _cmd_call_analysis(self, sim_command):
    """call -- analysis <persona name> -- opens a stateless chat session with the agent."""
    persona_name = sim_command[len("call -- analysis"):].strip()
    self.personas[persona_name].open_convo_session("analysis")
    return ""

  def _cmd_call_load_history(self, sim_command):
    """call -- load history <csv path relative to maze_assets_loc>."""
    curr_file = maze_assets_loc + "/" + sim_command[len("call -- load history"):].strip()
    rows = read_file_to_list(curr_file, header=True, strip_trail=True)[1]

    clean_whispers = []
    for row in rows:
      agent_name = row[0].strip()
      whispers = [w.strip() for w in row[1].split(";")]
      for whisper in whispers:
        clean_whispers.append([agent_name, whisper])

    for persona in self.personas.values():
      if persona.scratch.curr_time is None:
        persona.scratch.curr_time = datetime.datetime.now()

    load_history_via_whisper(self.personas, clean_whispers)
    return ""

  def _cmd_set_party(self, sim_command):
    parts = sim_command.split()
    if len(parts) >= 3:
      party_name = parts[2].lower()

      party_file = f"{fs_temp_storage}/curr_party.json"
      with open(party_file, "w") as f:
        json.dump({"party": party_name}, f, indent=2)

      return f"Party mode successfully set to '{party_name}' and written to {party_file}."
    return "Invalid syntax. Usage: set party <afd|cdu|gruene|linke|spd>"

  def _cmd_survey(self, sim_command):
    """survey <persona name> -- runs the scripted BFI-2 personality survey
    against the persona in the terminal and saves the answers to a json 
    file under storage/<sim_code>/survey/."""
    persona_name = sim_command[len("survey"):].strip()
    if persona_name not in self.personas:
      return f"Unknown persona: {persona_name}"

    persona = self.personas[persona_name]
    print(f"\nStarting BFI-2 survey with {persona_name}...\n")

    results = []
    for i, statement in enumerate(BFI2_ITEMS):
      question = f"I see myself as someone who {statement}."
      print(f"{i+1}. {question}")
      print("   (1) Disagree strongly   (2) Disagree a little   "
            "(3) Neither agree nor disagree   (4) Agree a little   "
            "(5) Agree strongly")
      rating = generate_survey_rating(persona, statement)
      print(f"   -> {persona_name}'s answer: {rating}\n")
      results += [{"question": question, "rating": rating}]

    sim_folder = f"{fs_storage}/{self.sim_code}"
    survey_dir = f"{sim_folder}/survey"
    os.makedirs(survey_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"{survey_dir}/{persona_name.replace(' ', '_')}_{timestamp}.json"
    with open(out_file, "w") as outfile:
      outfile.write(json.dumps({
        "persona_name": persona_name,
        "survey": "BFI-2",
        "sim_time": self.curr_time.strftime("%B %d, %Y, %H:%M:%S"),
        "answers": results
      }, indent=2))

    return f"Survey complete. Saved to {out_file}"

  # Maps a command *prefix* to the handler that services it. Order matters
  # for prefixes that could otherwise collide (none currently do, but keep
  # longer/more specific prefixes above shorter ones if you add any).
  _PREFIX_COMMANDS = [
    ("run", "_cmd_run"),
    ("survey", "_cmd_survey"),
    ("print persona schedule", "_cmd_print_persona_schedule"),
    ("print all persona schedule", "_cmd_print_all_persona_schedule"),
    ("print hourly org persona schedule", "_cmd_print_hourly_org_persona_schedule"),
    ("print persona current tile", "_cmd_print_persona_current_tile"),
    ("print persona chatting with buffer", "_cmd_print_persona_chatting_with_buffer"),
    ("print persona associative memory (event)", "_cmd_print_persona_a_mem_event"),
    ("print persona associative memory (thought)", "_cmd_print_persona_a_mem_thought"),
    ("print persona associative memory (chat)", "_cmd_print_persona_a_mem_chat"),
    ("print persona spatial memory", "_cmd_print_persona_spatial_memory"),
    ("print current time", "_cmd_print_current_time"),
    ("print tile event", "_cmd_print_tile_event"),
    ("print tile details", "_cmd_print_tile_details"),
    ("call -- analysis", "_cmd_call_analysis"),
    ("call -- load history", "_cmd_call_load_history"),
    ("set party", "_cmd_set_party")
  ]

  def open_server(self):
    """
    Opens an interactive terminal prompt that lets you run the simulation
    step by step and probe agent state.
    """
    print("Note: The agents in this simulation package are computational")
    print("constructs powered by generative agents architecture and LLM. We")
    print("clarify that these agents lack human-like agency, consciousness,")
    print("and independent decision-making.\n---")

    sim_folder = f"{fs_storage}/{self.sim_code}"

    while True:
      sim_command = input("Enter option: ").strip()
      sim_command_lower = sim_command.lower()

      try:
        if sim_command_lower in ("f", "fin", "finish", "save and finish"):
          # Finish the simulation and save progress.
          self.save()
          break

        elif sim_command_lower == "start path tester mode":
          # NOTE: once started, you must exit and restart the session to
          # run anything else.
          shutil.rmtree(sim_folder)
          self.start_path_tester_server()

        elif sim_command_lower == "exit":
          # Finish without saving; erases all data from the current sim.
          shutil.rmtree(sim_folder)
          break

        elif sim_command_lower == "save":
          self.save()

        elif sim_command_lower == "interview mode":
          # Services interview requests only; used to power interviews
          # during a replay session (see views.py).
          self.start_interview_only_server()

        elif sim_command_lower == "headless on":
          self.headless = True
          print("Headless mode enabled -- backend will self-advance "
                "without waiting for a frontend.")

        elif sim_command_lower == "headless off":
          self.headless = False
          print("Headless mode disabled -- backend will wait for "
                "frontend environment updates again.")

        else:
          # Dispatch to the first matching prefix handler.
          for prefix, handler_name in self._PREFIX_COMMANDS:
            if sim_command_lower.startswith(prefix):
              ret_str = getattr(self, handler_name)(sim_command)
              print(ret_str)
              break
          else:
            print("Unrecognized command.")
          continue

        print("")

      except Exception:
        traceback.print_exc()
        print("Error.")


if __name__ == '__main__':
  origin = input("Enter the name of the forked simulation: ").strip()
  target = input("Enter the name of the new simulation: ").strip()

  rs = ReverieServer(origin, target)
  rs.open_server()