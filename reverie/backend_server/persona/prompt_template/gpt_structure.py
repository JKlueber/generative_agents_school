"""
Author: Joon Sung Park (joonspk@stanford.edu)

File: gpt_structure.py
Description: Wrapper functions for calling OpenAI APIs.
"""
import json
import random
import time 
from openai import OpenAI
from utils import *

# 1. Initialize the modern OpenAI client
# Assumes openai_api_key is still imported from utils.py
client = OpenAI(api_key=openai_api_key, base_url="https://openrouter.ai/api/v1")

def temp_sleep(seconds=0.1):
  time.sleep(seconds)

def ChatGPT_single_request(prompt): 
  temp_sleep()
  completion = client.chat.completions.create(
    model="gpt-3.5-turbo", 
    messages=[{"role": "user", "content": prompt}]
  )
  return completion.choices[0].message.content

# ============================================================================
# #####################[SECTION 1: CHATGPT-3 STRUCTURE] ######################
# ============================================================================

def GPT4_request(prompt): 
  temp_sleep()
  try: 
    completion = client.chat.completions.create(
      model="gpt-4o", # Upgraded from gpt-4 to the faster, cheaper gpt-4o
      messages=[{"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content
  except Exception as e: 
    print (f"ChatGPT ERROR: {e}")
    return "ChatGPT ERROR"

def ChatGPT_request(prompt): 
  try: 
    completion = client.chat.completions.create(
      model="gpt-3.5-turbo", 
      messages=[{"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content
  except Exception as e: 
    print (f"ChatGPT ERROR: {e}")
    return "ChatGPT ERROR"

def GPT4_safe_generate_response(prompt, example_output, special_instruction, repeat=3, fail_safe_response="error", func_validate=None, func_clean_up=None, verbose=False): 
  prompt = 'GPT-3 Prompt:\n"""\n' + prompt + '\n"""\n'
  prompt += f"Output the response to the prompt above in json. {special_instruction}\n"
  prompt += "Example output json:\n"
  prompt += '{"output": "' + str(example_output) + '"}'

  if verbose: print ("CHAT GPT PROMPT\n", prompt)

  for i in range(repeat): 
    try: 
      curr_gpt_response = GPT4_request(prompt).strip()
      end_index = curr_gpt_response.rfind('}') + 1
      curr_gpt_response = curr_gpt_response[:end_index]
      curr_gpt_response = json.loads(curr_gpt_response)["output"]
      
      if func_validate(curr_gpt_response, prompt=prompt): 
        return func_clean_up(curr_gpt_response, prompt=prompt)
      
      if verbose: 
        print ("---- repeat count: \n", i, curr_gpt_response)
    except: 
      pass
  return False

def ChatGPT_safe_generate_response(prompt, example_output, special_instruction, repeat=3, fail_safe_response="error", func_validate=None, func_clean_up=None, verbose=False): 
  prompt = '"""\n' + prompt + '\n"""\n'
  prompt += f"Output the response to the prompt above in json. {special_instruction}\n"
  prompt += "Example output json:\n"
  prompt += '{"output": "' + str(example_output) + '"}'

  if verbose: print ("CHAT GPT PROMPT\n", prompt)

  for i in range(repeat): 
    try: 
      curr_gpt_response = ChatGPT_request(prompt).strip()
      end_index = curr_gpt_response.rfind('}') + 1
      curr_gpt_response = curr_gpt_response[:end_index]
      curr_gpt_response = json.loads(curr_gpt_response)["output"]
      
      if func_validate(curr_gpt_response, prompt=prompt): 
        return func_clean_up(curr_gpt_response, prompt=prompt)
      
      if verbose: 
        print ("---- repeat count: \n", i, curr_gpt_response)
    except: 
      pass
  return False

def ChatGPT_safe_generate_response_OLD(prompt, repeat=3, fail_safe_response="error", func_validate=None, func_clean_up=None, verbose=False): 
  if verbose: print ("CHAT GPT PROMPT\n", prompt)

  for i in range(repeat): 
    try: 
      curr_gpt_response = ChatGPT_request(prompt).strip()
      if func_validate(curr_gpt_response, prompt=prompt): 
        return func_clean_up(curr_gpt_response, prompt=prompt)
      if verbose: 
        print (f"---- repeat count: {i}\n", curr_gpt_response)
    except: 
      pass
  print ("FAIL SAFE TRIGGERED") 
  return fail_safe_response

# ============================================================================
# ###################[SECTION 2: ORIGINAL GPT-3 STRUCTURE] ###################
# ============================================================================

def GPT_request(prompt, gpt_parameter): 
  temp_sleep()
  try: 
    # 2. Intercept legacy parameters
    # The old code used "engine", but OpenAI now expects "model".
    target_model = gpt_parameter.get("model", gpt_parameter.get("engine", "gpt-3.5-turbo-instruct"))
    
    # 3. Swap deprecated davinci models for the modern instruct replacement
    if target_model in ["text-davinci-002", "text-davinci-003"]:
        target_model = "gpt-3.5-turbo-instruct"

    # 4. Use the updated client.completions API
    response = client.completions.create(
      model=target_model,
      prompt=prompt,
      temperature=gpt_parameter["temperature"],
      max_tokens=gpt_parameter["max_tokens"],
      top_p=gpt_parameter["top_p"],
      frequency_penalty=gpt_parameter["frequency_penalty"],
      presence_penalty=gpt_parameter["presence_penalty"],
      stream=gpt_parameter["stream"],
      stop=gpt_parameter["stop"],
    )
    return response.choices[0].text
  except Exception as e: 
    print (f"TOKEN LIMIT EXCEEDED OR API ERROR: {e}")
    return "TOKEN LIMIT EXCEEDED"

def generate_prompt(curr_input, prompt_lib_file): 
  if type(curr_input) == type("string"): 
    curr_input = [curr_input]
  curr_input = [str(i) for i in curr_input]

  f = open(prompt_lib_file, "r")
  prompt = f.read()
  f.close()
  for count, i in enumerate(curr_input):   
    prompt = prompt.replace(f"!<INPUT {count}>!", i)
  if "<commentblockmarker>###</commentblockmarker>" in prompt: 
    prompt = prompt.split("<commentblockmarker>###</commentblockmarker>")[1]
  return prompt.strip()

def safe_generate_response(prompt, gpt_parameter, repeat=5, fail_safe_response="error", func_validate=None, func_clean_up=None, verbose=False): 
  if verbose: print (prompt)

  for i in range(repeat): 
    curr_gpt_response = GPT_request(prompt, gpt_parameter)
    if func_validate(curr_gpt_response, prompt=prompt): 
      return func_clean_up(curr_gpt_response, prompt=prompt)
    if verbose: 
      print ("---- repeat count: ", i, curr_gpt_response)
  return fail_safe_response

def get_embedding(text, model="text-embedding-3-small"):
  # Upgraded default embedding model to text-embedding-3-small (cheaper & better than ada-002)
  text = text.replace("\n", " ")
  if not text: 
    text = "this is blank"
  
  # 5. Updated embeddings API syntax
  response = client.embeddings.create(input=[text], model=model)
  return response.data[0].embedding



















