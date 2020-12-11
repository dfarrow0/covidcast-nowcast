# running the example

Fork `covidcast-nowcast` and clone it locally into
`repos/delphi/covidcast-nowcast`.

Build the image `delphi_python` as described in the backend dev guide.

Build the `delphi_covidcast_nowcast` image:

```
docker build -t delphi_covidcast_nowcast \
  -f repos/delphi/covidcast-nowcast/dev/docker/covidcast_nowcast/Dockerfile .
```

Create a local directory to share with the container. Files therein will
persist when the container exits, which allows things like keeping an Epidata
cache between runs.

```
mkdir data
```

Add static geodata files to the shared data directory:

```
curl 'https://raw.githubusercontent.com/cmu-delphi/covidcast-indicators/main/_delphi_utils_python/delphi_utils/data/fips_msa_table.csv' > data/fips_msa_table.csv
curl 'https://raw.githubusercontent.com/cmu-delphi/covidcast-indicators/main/_delphi_utils_python/delphi_utils/data/fips_state_table.csv' > data/fips_state_table.csv
```

Run the image, bind-mounting the data directory. The following example computes
the nowcast for TX, based on ground truth taken from the case deconv notebook.
(Note that first run will take about an hour. Subsequent runs leverage the
cache and should only take ~15 seconds.)

```
docker run --rm \
  --mount type=bind,source="$(pwd)"/data,target=/usr/src/app/data \
  delphi_covidcast_nowcast \
python3 -u -m delphi.covidcast_nowcast.sf.example_tx
```

Note that you can also bind-mount the python source files to avoid having to
rebuild the image each time you make a code change. But this is strictly
optional.

```
docker run --rm \
  --mount type=bind,source="$(pwd)"/repos/delphi/covidcast-nowcast,target=/usr/src/app/repos/delphi/covidcast-nowcast,readonly \
  --mount type=bind,source="$(pwd)"/repos/delphi/covidcast-nowcast/src,target=/usr/src/app/delphi/covidcast_nowcast,readonly \
  --mount type=bind,source="$(pwd)"/data,target=/usr/src/app/data \
  delphi_covidcast_nowcast \
python3 -u -m delphi.covidcast_nowcast.sf.example_tx
```


# calling this from deconv

I assume that deconv code will produce a giant matrix containing ground truth
in all US locations. So maybe roughly 10x wider than the TX example. The fusion
code should basically handle whatever you throw at it, although it will be
super slow for the first run (see sections below about caching). See the file
`example_tx.py` for an example of how to call the function and to see what it
returns. Alternatively, you could call the nowcast function in a loop over
states.

Some notes:
- one of the parameters is a list of dates to nowcast. currently the code only
makes a nowcast for a single date. if you pass more than one date, it'll raise
and exception.
- the AR "sensor" has some issues. it works well enough for now, but see notes
in the file `ar_sensor.py` for details.
- the associated code is highly unpolished and inefficient.
- sometimes the nowcast is negative. for example: "county 48171 -0.2 (+/-
0.2)". nothing is done to clamp nowcasts to positive values.
- due to injecting noise in the AR "sensor" (see comments in file), nowcasts
aren't deterministic. values will vary slightly each the function is called.


# request caching

Included here is a drop-in replacement for the Epidata client that implements
caching-to-disk. I did this as a test with TX at the county level, and it
reduced signal acquisition time from 344 seconds (hitting the Epidata API) to
0.340 seconds (hitting the cache) -- 1000x speedup.

The downside is that the cache doesn't understand request semantics like dates,
and so each distinct request causes a cache miss even if most of the data is
already cached by a previous query. For example, suppose you request a signal
on dates 2020-12-01 through 2020-12-10. A subsequent request of the same signal
on date 2020-12-05 would miss the cache and go out to the real Epidata API,
even though the value is already stored in the cache as part of the first
request.


# statespace

We need to get matrices H and W. To do this requires some linear algebra. For
numerical stability and exact solution, fractions are used instead of floats.
This means processing is done in pure python rather than numpy.

Example runtime using 9 sensors in 3 locations:
H0 -> H: (9, 3222) -> (9, 3)
W0 -> W: (3, 3222) -> (3, 3)
real	17m33.336s

Example runtime using 541 sensors in 280 locations:
H0 -> H: (541, 3222) -> (541, 255)
W0 -> W: (280, 3222) -> (280, 255)
real	51m59.982s

It's so slow. The bottleneck looks like matrix elimination. Which should be
super fast, except these are matrices of python `Fraction`s, and so everything
is done in pure python. If we could punt this to numpy somehow, we could speed
it up by orders of magnitude.

In the meantime, I cache the values of H and W so that subsequent runs are
~instant (at least the statespace part).


# **didn't work out, only for posterity**

## data dump for local epidata api

This is ~33 million rows as of 2020-12-08, so it will take a few minutes. Note
that we're doing this in a temporary directory, which is actually mounted as
`tmpfs` (meaning it's all in RAM, not on disk). As a result, this will be
faster overall and won't cause a bunch of wear on the hard drive. But this
means it's **very important** to delete the file as soon as you can because
it's consuming a whole lot of RAM on the server for as long as it exists.

(Run these commands as the `automation` user on the server.)

```
cd `mktemp -d`
mysqldump --user epi --password --where \
  '`source` = "fb-survey" and `signal` = "smoothed_hh_cmnty_cli"' \
  epidata covidcast | grep INSERT >> data.sql
mysqldump --user epi --password --where \
  '`source` = "doctor-visits" and `signal` = "smoothed_adj_cli"' \
  epidata covidcast | grep INSERT >> data.sql
```

`du -h *` shows that the file is 4.3G! It should compress fairly well
though. This will also take a few minutes.

```
gzip data.sql
```

`du -h *` shows that the compressed file is 639M. Still big, but better.

Make the temporary working directory and data file accessible to your user
account.

```
chmod o+rx .
chmod o+r data.sql.gz
```

Download the data file to your local computer.

```
scp user@delphi.midas.cs.cmu.edu:/tmp/tmp.T5K01VK9Ks/data.sql.gz .
```

Remember to delete the file on the server because it's eating up a lot of RAM!
(If you're sharing this file with someone else, then be sure to delete it once
they've downloaded it.)

```
rm data.sql.gz
```

Decompress to get the original file (make sure you have enough space!):

```
gunzip data.sql.gz
```

Build the database image per the epidata dev guide at
https://github.com/cmu-delphi/delphi-epidata/blob/main/docs/epidata_development.md

```
docker build -t delphi_database \
  -f repos/delphi/operations/dev/docker/database/Dockerfile .
docker build -t delphi_database_epidata \
  -f repos/delphi/delphi-epidata/dev/docker/database/epidata/Dockerfile .
```

Create the network if you haven't already, then start the database container.
Note that we're launching this with the `--rm` flag, which means that data will
be obliterated when the container exits. This is nice for ensuring that you
don't clutter your hard drive, but it means that if you stop the container
(e.g. restart your computer), you'll have to repeat the following
initialization steps which take many hours.

```
docker run --rm -p 127.0.0.1:13306:3306 \
  --network delphi-net --name delphi_database_epidata \
  delphi_database_epidata
```

Quick test to make sure the database is running and the `covidcast` table has
been created (and is currently empty).

```
echo 'select count(1) from covidcast' | \
docker run --rm -i --network delphi-net mariadb \
mysql --user=user --password=pass \
  --port 3306 --host delphi_database_epidata epidata
```

You should see:

```
count(1)
0
```

Now inject the data dump that you downloaded from the server. This will take a
while and will consume quite a bit of hard drive space. (While this is running,
use the `iotop` command to watch your hard drive _suffer_.)

```
cat data.sql |
docker run --rm -i --network delphi-net mariadb \
mysql --user=user --password=pass \
  --port 3306 --host delphi_database_epidata epidata
```

Turns out this takes an inordinate amount of time. 22 minutes later progress is
at 10%. Once that's done, all that remains is:

- launch the epidata api server container (no modifications needed)
- edit your instance of `Epidata.BASE_URL` to point at `delphi_web_epidata`
- run the nowcasting container, adding the `--network delphi-net` flag
