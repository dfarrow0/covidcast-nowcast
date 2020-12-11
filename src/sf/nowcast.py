# standard library
import datetime
import urllib.request

# third party
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# first party
from delphi.covidcast_nowcast.sf.ar_sensor import ArSensor
from delphi.covidcast_nowcast.sf.caches import EpidataCache
from delphi.covidcast_nowcast.sf.caches import StatespaceCache
from delphi.covidcast_nowcast.sf.fusion import Fusion
from delphi.epidata.client.delphi_epidata import Epidata
import delphi.nowcast.fusion.covariance as covariance
import delphi.nowcast.fusion.fusion as fusion


def get_signal(epidata, source, signal, date1, date2, geo_type, geo_value):
  print('fetch', source, signal, date1, date2, geo_type, geo_value)
  response = epidata.covidcast(source, signal, 'day', geo_type, epidata.range(date1, date2), geo_value)
  if response['result'] != 1:
    print(f'api returned {response["result"]}: {response["message"]}')
    return None
  values = [(row['time_value'], row['value']) for row in response['epidata']]
  values = sorted(values, key=lambda ab: ab[0])
  return [ab[0] for ab in values], np.array([ab[1] for ab in values])


def train_model(epidata, source, signal, date1, date2, location_type, truth, input_dates):
  location, geo_type = location_type
  dates, values = get_signal(epidata, source, signal, date1, date2, geo_type, location)
  num_truth, num_values = truth.shape[0], values.shape[0]
  if num_truth != num_values:
    if 14 < num_values < num_truth:
      # signal is missing on a few days, but that's ok
      truth_subset = []
      for date in dates:
        idx = input_dates.index(date)
        truth_subset.append(truth[idx])
      truth = np.array(truth_subset)
      missing = sorted(set(input_dates) - set(dates))
      print(f'signal missing {len(missing)} days starting with {missing[0]}, using truth subset of size {truth.shape[0]}')
    else:
      # something else is happening, debug it
      print(source, signal, date1, date2, location)
      print(truth)
      print(values)
      print(dates)
      raise Exception(f'shape mismatch {num_truth} != {num_values} for {location}')

  # simple regression
  Y = truth[:, None]
  X0 = np.ones(Y.shape)
  X1 = values[:, None]
  X = np.hstack((X0, X1))
  B = np.linalg.inv(X.T @ X) @ X.T @ Y
  Yhat = X @ B
  return B, Yhat, dates


def run_model(epidata, B, source, signal, date, location_type):
  location, geo_type = location_type
  dates, values = get_signal(epidata, source, signal, date, date, geo_type, location)
  X = np.array([1, values[0]])
  return X @ B


def get_sensor(epidata, source, signal, date1, date2, date3, location_type, truth, input_dates):
  B, Yhat, dates = train_model(epidata, source, signal, date1, date2, location_type, truth, input_dates)
  z = run_model(epidata, B, source, signal, date3, location_type)
  return B, Yhat, dates, z[0]


def is_sensor_available(epidata, source, signal, date, location_type):
  location, geo_type = location_type
  result = get_signal(epidata, source, signal, date, date, geo_type, location)
  return result is not None


def nowcast(*args, use_cache=True):
  if use_cache:
    with EpidataCache.get_client('data/cache', 'fusion.json') as epidata:
      return nowcast_impl(epidata, *args)
  else:
    return nowcast_impl(Epidata, *args)


def nowcast_impl(
    epidata,
    input_dates,
    _input_locations,
    _geo_types,
    data_matrix,
    nowcast_dates,
    indicators):

  if len(nowcast_dates) != 1:
    raise Exception('currently nowcasting a single date only')

  # load msa->county mapping
  fips_msa_map = pd.read_csv('data/fips_msa_table.csv', dtype=str)
  counties_from_msa = fips_msa_map.groupby('msa')['fips'].apply(list)
  print('debug: msa 11100 contains', counties_from_msa['11100'])

  # load state->county mapping
  def get_real_counties_sorted(counties):
    return sorted(c for c in counties if not c.endswith('000'))
  fips_state_map = pd.read_csv('data/fips_state_table.csv', dtype=str)
  counties_from_state = fips_state_map.groupby('state_id')['fips'].apply(get_real_counties_sorted)
  print('debug: state ct contains', counties_from_state['ct'])

  # pair up locations with geo_types since they're always used together
  locations_types = list(zip(_input_locations, _geo_types))

  # date1 = first date in training data
  # date2 = last date in training data
  # date3 = date to nowcast, which is *assumed* to be date2 + 1 day
  date1, date2, date3 = input_dates[0], input_dates[-1], nowcast_dates[0]

  # get indicators/signals from the api and turn then into sensors
  api_sensors = []
  for idx, location_type in enumerate(locations_types):
    location, geo_type = location_type
    if idx % 10 == 0:
      print(idx, location, len(locations_types))
    for source, signal in indicators:
      if not is_sensor_available(epidata, source, signal, date3, location_type):
        print('sensor unavailable:', source, signal, date3, location)
        continue
      truth = data_matrix[:, idx]
      B, Yhat, dates, z = get_sensor(epidata, source, signal, date1, date2, date3, location_type, truth, input_dates)
      api_sensors.append((source, signal, date3, location_type, B, Yhat, dates, z))

  # take inventory of sensors so far
  tmp = sorted(set([s[3] for s in api_sensors]))
  print(f'{len(api_sensors)} api_sensors cover {len(tmp)}/{len(locations_types)} locations')

  # create an auto-regression "sensor" for all locations
  ar_sensors = []
  for j, location_type in enumerate(locations_types):
    values = data_matrix[:, j]
    # 3 covariates, no intercept, small L2 penalty
    B, Yhat, dates, z = ArSensor.get_sensor(input_dates, values, 3, False, 0.1)
    ar_sensors.append(('ar', 'ar', date3, location_type, B, Yhat, dates, z))

  # inventory of ar sensors
  tmp = sorted(set([s[3] for s in ar_sensors]))
  print(f'{len(ar_sensors)} ar_sensors cover {len(tmp)}/{len(locations_types)} locations')

  # combined inventory
  sensors = api_sensors + ar_sensors
  print('total sensors:', len(sensors))

  # all counties, i.e. "atoms" (smallest indivisible geographic unit)
  atoms = get_real_counties_sorted(fips_state_map['fips'])

  # build the H and W matrices
  # H0 is like an inventory of all available sensor-location pairs
  # W0 is like a wishlist of all the locations for which we want a nowcast
  # H is like H0, but with redundancy removed
  # W is the subset of the wishlist that we're actually able to get, given H
  # H0 and H rows correspond to input sensors
  # W0 and W rows correspond to output nowcasts
  # H0 and W0 columns correspond to real counties (maybe call it "domain
  #   statespace"), usually rank deficient
  # H and W columns correspond arbitrary linear combinations of counties (i.e.
  #   "latent statespace"), full rank

  num_sensors = len(sensors)
  num_locs = len(locations_types)
  num_atoms = len(atoms)

  # rows for sensors, columns for counties
  H0 = np.zeros((num_sensors, num_atoms))
  for i, sensor in enumerate(sensors):
    source, signal, date3, location_type, B, Yhat, dates, z = sensor
    location, geo_type = location_type
    if geo_type == 'county':
      j = atoms.index(location)
      H0[i, j] = 1
    elif geo_type == 'msa':
      for county in counties_from_msa[location]:
        j = atoms.index(county)
        H0[i, j] = 1
    elif geo_type == 'state':
      for county in counties_from_state[location]:
        j = atoms.index(county)
        H0[i, j] = 1
    else:
      raise Exception(f'unknown geo type for {location}')

  # rows for locations, columns for counties
  # assume that we only care about locations that are part of the input
  # (e.g. if input is TX, we don't care about AL)
  # TODO: in production, we care about everything. e.g. we might be able to
  # infer an MSA given some set of counties, and we'd like to nowcast that MSA
  # even though we don't have ground truth for it.
  W0 = np.zeros((num_locs, num_atoms))
  for i, location_type in enumerate(locations_types):
    location, geo_type = location_type
    if geo_type == 'county':
      j = atoms.index(location)
      W0[i, j] = 1
    elif geo_type == 'msa':
      for county in counties_from_msa[location]:
        j = atoms.index(county)
        W0[i, j] = 1
    elif geo_type == 'state':
      for county in counties_from_state[location]:
        j = atoms.index(county)
        W0[i, j] = 1
    else:
      raise Exception(f'unknown geo type for {location}')

  # get H and W from H0 and W0
  print('coalesce statespace...')

  # `determine_statespace` takes a *really* long time
  # for texas: 71m54.670s
  # ideally optimize `determine_statespace`, but caching works for now
  signature = {'h': [s[3] for s in sensors], 'w': locations_types}
  cache = StatespaceCache('data/cache', 'statespace')
  HWi = cache.load_statespace(signature)
  if HWi:
    print('loaded statespace from cache')
    H, W, output_idx = HWi
  else:
    print('computing statespace')
    H, W, output_idx = fusion.determine_statespace(H0, W0)
    cache.save_statespace(signature, H, W, output_idx)

  output_locations = [locations_types[i] for i in output_idx]
  print('H:', H0.shape, '->', H.shape)
  print('W:', W0.shape, '->', W.shape)

  print('estimate sensor noise covariance...')
  noise = np.zeros((data_matrix.shape[0], len(sensors))) * np.nan
  for j, sensor in enumerate(sensors):
    source, signal, date3, location_type, B, Yhat, dates, z = sensor

    # get ground truth in this location
    Y = data_matrix[:, locations_types.index(location_type)]

    # subtract sensor from truth on each day
    for i, date in enumerate(input_dates):
      if date not in dates:
        # missing data
        continue
      k = dates.index(date)
      noise[i, j] = Y[i] - Yhat[k, 0]

  # noise covariance is highly rank deficient, make a full rank approximation
  R = covariance.mle_cov(noise, covariance.BlendDiagonal2)

  # z is the vector of current sensor readings
  z = np.array([s[-1] for s in sensors])

  # finally, sensor fusion
  y, stdev = Fusion.fuse(H, W, R, z)
  return y, stdev, output_locations
