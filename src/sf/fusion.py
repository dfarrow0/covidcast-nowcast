# third party
import numpy as np

# first party
import delphi.nowcast.fusion.fusion as fusion


class Fusion:

  def fuse(H, W, R, z):
    x, P = fusion.fuse(z, R, H)
    y, S = fusion.extract(x, P, W)
    stdev = np.sqrt(np.diag(S)).reshape(y.shape)
    return y, stdev
