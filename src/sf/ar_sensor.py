# third party
import numpy as np


class ArSensor:

  def predict(idx, values, ar_size, include_intercept, lambda_):
    # predict the value at values[idx] using values[idx - ar_size:idx]
    # to do that, train on all values[:idx]
    # note that an L2 penalty is applied since sometimes there is colinearity,
    # like when `values` is all zeros.
    # TODO: L2 is implemented incorrectly. ideally covariates would be
    # normalized before adding the penalty (so as not to unfairly penalize
    # covariates with high variance), but here they're not being normalized.
    # probably doesn't matter too much for now, but something to fix later.
    num_covariates = ar_size
    if include_intercept:
      num_covariates += 1
    num_observations = idx - ar_size
    if num_observations < 2 * num_covariates:
      # require some minimum number of samples
      return None

    # fairly standard OLS, maybe with intercept, and with L2 penalty
    X = np.zeros((num_observations, num_covariates))
    if include_intercept:
      X[:, -1] = 1
    for j in range(ar_size):
      X[:, j] = values[j:idx - ar_size + j]
    Y = values[ar_size:idx, None]
    X = np.vstack((X, lambda_ * np.eye(num_covariates)))
    Y = np.vstack((Y, np.zeros((num_covariates, 1))))
    B = np.linalg.inv(X.T @ X) @ X.T @ Y

    # given the model fit above, predict the value at `idx`
    x = values[None, idx - ar_size:idx]
    if include_intercept:
      x = np.hstack((x, [[1]]))

    # return model and estimate at `idx`
    return B, (x @ B)[0, 0]

  def get_sensor(dates, values, ar_size, include_intercept, lambda_):
    # get a "sensor" that's actually just an auto-regression. each historical
    # sensor reading (z) is based on an out-of-sample fit, only using data in
    # the past.
    sensor_dates, Yhat = [], []
    for i, date in enumerate(dates):
      result = ArSensor.predict(i, values, ar_size, include_intercept, lambda_)
      if result is None:
        continue
      B, z = result
      sensor_dates.append(date)
      Yhat.append(z)
    Yhat = np.array(Yhat)[:, None]

    # ground truth in some locations is a zero vector, which leads to perfect
    # AR fit, zero variance, and a singular covariance matrix so as a small
    # hack, add some small noise.
    Yhat += np.random.normal(0, 0.1, size=Yhat.shape)

    # as a huge hack, add more noise to prevent AR from unreasonably dominating
    # the nowcast since AR3 can nearly exactly predict some trendfiltered
    # curves.
    Yhat += np.random.normal(
        0, 0.1 * np.maximum(0, np.mean(Yhat)), size=Yhat.shape)

    # in addition to the past values of the sensor, which are used to estimate
    # covairance of sensor error wrt ground truth, we also want to return the
    # current sensor reading. to determing the current reading, just apply the
    # most recent model (i.e. the `B` matrix) to the 3 most recent data points.
    x = values[None, -ar_size:]
    if include_intercept:
      x = np.hstack((x, [[1]]))
    z = (x @ B)[0, 0]
    return None, Yhat, sensor_dates, z
