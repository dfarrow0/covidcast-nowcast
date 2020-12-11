# standard library
from contextlib import contextmanager
import hashlib
import json
import os
import time

# third party
import numpy as np

# first party
from delphi.epidata.client.delphi_epidata import Epidata


class Cache:

  def __init__(self, cache_dir, cache_file):
    self.cache_dir = cache_dir
    self.cache_file = cache_file

  def create_cache_dir(self):
    os.makedirs(self.cache_dir, exist_ok=True)

  def get_cache_path(self):
    return os.path.join(self.cache_dir, self.cache_file)

  def get_hash(obj):
    value = json.dumps(obj, sort_keys=True)
    hash = hashlib.sha256(value.encode('utf-8')).digest()
    return hash.hex()[:16]


class StatespaceCache(Cache):

  def __init__(self, cache_dir, cache_file):
    super().__init__(cache_dir, cache_file)

  def load_statespace(self, signature):
    hash = Cache.get_hash(signature)
    base_path = self.get_cache_path()
    path_h = f'{base_path}-{hash}-h.npy'
    path_w = f'{base_path}-{hash}-w.npy'
    path_i = f'{base_path}-{hash}-i.npy'
    try:
      with open(path_h, 'rb') as f:
        H = np.load(f)
      with open(path_w, 'rb') as f:
        W = np.load(f)
      with open(path_i, 'rb') as f:
        idx = np.load(f)
      return H, W, idx
    except Exception as e:
      return None

  def save_statespace(self, signature, H, W, idx):
    hash = Cache.get_hash(signature)
    base_path = self.get_cache_path()
    path_h = f'{base_path}-{hash}-h.npy'
    path_w = f'{base_path}-{hash}-w.npy'
    path_i = f'{base_path}-{hash}-i.npy'
    print(f'saving {base_path}')
    with open(path_h, 'wb') as f:
      H = np.save(f, H)
    with open(path_w, 'wb') as f:
      W = np.save(f, W)
    with open(path_i, 'wb') as f:
      idx = np.save(f, idx)


class EpidataCache(Cache):

  def __init__(self, cache_dir, cache_file, request_func):
    super().__init__(cache_dir, cache_file)
    self.request_func = request_func
    self.cache = None
    self.save_time = 0

  def __load_cache(self):
    if self.cache is None:
      try:
        path = self.get_cache_path()
        print(f'initial cache load {path}')
        with open(path, 'r') as f:
          self.cache = json.loads(f.read())
      except Exception as e:
        self.cache = {}
    return self.cache

  def __save_cache(self):
    # update at most once every two seconds
    now = time.time()
    if now - self.save_time < 2:
      return
    self.save_time = now
    path = self.get_cache_path()
    print(f'saving {path}')
    self.create_cache_dir()
    with open(path, 'w') as f:
      f.write(json.dumps(self.cache))

  def request(self, params):
    cache = self.__load_cache()
    key = Cache.get_hash(params)
    if key not in cache:
      response = self.request_func(params)
      cache[key] = response
      self.__save_cache()
    return cache[key]

  @contextmanager
  def get_client(cache_dir, cache_file):
    request_func = Epidata._request
    cacher = EpidataCache(cache_dir, cache_file, request_func)
    try:
      Epidata._request = lambda params: cacher.request(params)
      yield Epidata
    finally:
      Epidata._request = request_func
